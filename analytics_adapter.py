"""Adaptador DEFENSIVO de GET /analytics/{learning_space}: qualquer JSON -> blocos -> HTML com graficos matplotlib.

Base: o adaptador de plots do prototipo de design (plan() igual), trocando o SVG proprio por matplotlib (PNG claro +
escuro, textos dentro do grafico em ingles) e os textos do cartao pelo catalogo i18n.
  plan(payload, lang=...) -> blocos {"kind": empty|kpi|bars|line|multiline|heat|table|text|json, ...}
  render(payload, lang) -> str  # HTML; NUNCA levanta excecao (qualquer falha vira cartao com o JSON)
Regras de plan(): ver docstring do prototipo (dict de numeros -> bars/kpi; lista de numeros -> line; dict de listas ->
multiline/lines; lista de listas -> heat; lista de dicts -> tabela + line/bars; aninhado -> expande 1 nivel; resto -> json).
NaN/inf descartados, textos escapados, no maximo 12 blocos.
"""
from __future__ import annotations

import base64
import html
import io
import json
import math
import re
from datetime import datetime, timezone

import i18n
import plots_mpl as pm
from plots_mpl import plt, np

e = html.escape

TIME_NAMES = re.compile(r"(^|_)(date|time|timestamp|ts|day|hour|week|month|t|hora|data|dia)($|_)", re.I)
isnumish = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731  (inclui NaN/inf)
isnum = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _label(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e11:  # epoch em ms
        try: return datetime.fromtimestamp(v / 1000, timezone.utc).strftime("%m-%d %H:%M")
        except Exception: pass
    if isinstance(v, str):
        m = re.match(r"\d{4}-(\d\d)-(\d\d)(?:[T ](\d\d:\d\d))?", v)
        if m: return f"{m.group(1)}-{m.group(2)}" + (f" {m.group(3)}" if m.group(3) else "")
    return str(v)[:14]


def _num_cols(rows, cols):
    out = []
    for c in cols:
        vals = [r.get(c) for r in rows if isinstance(r, dict) and r.get(c) is not None]
        if vals and sum(isnum(v) for v in vals) / len(vals) >= .8 and not re.search(r"(^|_)id$", c, re.I):
            out.append(c)
    return out


def plan(x, *, max_blocks=12, max_rows=500, max_bars=24, max_depth=2, title="", depth=0, lang="pt") -> list:
    B = []
    T = lambda k, **f: i18n.t(k, lang, **f)  # noqa: E731
    add = lambda kind, data, ttl=title, note="": B.append({"kind": kind, "title": ttl, "data": data, "note": note})
    try:
        if x is None or x == "" or x == [] or x == {}:
            add("empty", None)
        elif isinstance(x, bool):
            add("text", str(x))
        elif isnum(x):
            add("kpi", [(title or T("an.ad.value1"), x)])
        elif isinstance(x, float):  # nan/inf
            add("text", str(x))
        elif isinstance(x, str):
            add("text", x[:2000])
        elif isinstance(x, dict):
            leaves = {k: v for k, v in x.items() if isnum(v)}
            nested = {k: v for k, v in x.items() if isinstance(v, (dict, list)) and v not in ({}, [])}
            others = {k: v for k, v in x.items() if k not in leaves and k not in nested}
            if leaves and not nested:
                if len(leaves) == 1: add("kpi", list(leaves.items()))
                elif any(v < 0 for v in leaves.values()): add("table", {"cols": [T("an.ad.key"), T("an.ad.value")], "rows": [[k, v] for k, v in leaves.items()]})
                else:
                    items = list(leaves.items())
                    if len(items) > max_bars:
                        items.sort(key=lambda kv: -kv[1]); rest = sum(v for _, v in items[max_bars - 1:]); items = items[:max_bars - 1] + [(T("an.ad.others"), rest)]
                    add("bars", items)
            elif nested and all(isinstance(v, list) and v and all(isnumish(i) for i in v) and any(isnum(i) for i in v) for v in nested.values()) and not leaves:
                n = {len(v) for v in nested.values()}
                if len(n) == 1:
                    mags = [max(abs(i) for i in v if isnum(i)) or 1 for v in nested.values()]
                    if max(mags) / min(mags) < 10 and len(nested) <= 6: add("multiline", nested)
                    else:
                        for k, v in nested.items(): add("line", v, f"{title}.{k}" if title else str(k))
                else:
                    for k, v in nested.items(): add("line", v, f"{title}.{k}" if title else str(k))
            else:
                if leaves: add("bars" if len(leaves) > 1 else "kpi", list(leaves.items()), f"{title} ({T('an.ad.summary')})" if title else T("an.ad.summary"))
                for k, v in nested.items():
                    ttl = f"{title}.{k}" if title else str(k)
                    if depth + 1 >= max_depth: add("json", v, ttl)
                    else: B += plan(v, max_blocks=max_blocks, max_rows=max_rows, max_bars=max_bars, max_depth=max_depth, title=ttl, depth=depth + 1, lang=lang)
                if others: add("table", {"cols": [T("an.ad.key"), T("an.ad.value")], "rows": [[k, json.dumps(v, ensure_ascii=False, default=str)[:120]] for k, v in others.items()]}, f"{title} ({T('an.ad.others')})" if title else T("an.ad.others"))
        elif isinstance(x, list):
            if all(isnumish(i) for i in x) and any(isnum(i) for i in x):
                add("line" if len(x) >= 2 else "kpi", x if len(x) >= 2 else [(T("an.ad.value1"), x[0])])
            elif all(isinstance(r, list) and r and all(isnumish(i) for i in r) and any(isnum(i) for i in r) for r in x) and len({len(r) for r in x}) == 1 and len(x) <= 60 and len(x[0]) <= 60:
                add("heat", x)
            elif sum(isinstance(r, dict) for r in x) / len(x) >= .7:
                rows = [r for r in x if isinstance(r, dict)][:max_rows]
                cols = []
                for r in rows:
                    for k in r:
                        if k not in cols: cols.append(k)
                cols = cols[:30]
                add("table", {"cols": cols, "rows": [[json.dumps(r.get(c), ensure_ascii=False, default=str) if isinstance(r.get(c), (dict, list)) else r.get(c) for c in cols] for r in rows]}, note=T("an.ad.rows", n=len(x)) + (" " + T("an.ad.showing", n=max_rows) if len(x) > max_rows else ""))
                nc = _num_cols(rows, cols)
                tc = next((c for c in cols if TIME_NAMES.search(c) or any(isinstance(r.get(c), str) and re.match(r"\d{4}-\d\d-\d\d", r.get(c) or "") for r in rows[:3])), None)
                cc = next((c for c in cols if c not in nc and all(isinstance(r.get(c), str) for r in rows if r.get(c) is not None)), None)
                if len(rows) == 1 and nc: add("kpi", [(c, rows[0][c]) for c in nc if isnum(rows[0].get(c))][:8], title)
                elif len(rows) >= 2 and nc:
                    xc = tc or (cc if cc and len({r.get(cc) for r in rows}) == len(rows) else None)
                    ynum = [c for c in nc if c != tc][:6]
                    if tc and ynum:
                        for c in ynum: add("line", [r.get(c) if isnum(r.get(c)) else float("nan") for r in rows], f"{c} × {tc}", xs=None) if False else B.append({"kind": "line", "title": f"{c} × {tc}", "data": [r.get(c) if isnum(r.get(c)) else float("nan") for r in rows], "xs": [_label(r.get(tc)) for r in rows], "note": ""})
                    elif xc and ynum:
                        for c in ynum[:4]:
                            items = [(str(r.get(xc))[:18], r.get(c)) for r in rows if isnum(r.get(c))]
                            if len(items) > max_bars: items.sort(key=lambda kv: -kv[1]); items = items[:max_bars]
                            if items and all(v >= 0 for _, v in items): B.append({"kind": "bars", "title": T("an.ad.by", c=c, x=xc), "data": items, "note": ""})
                    elif ynum:
                        for c in ynum[:4]: B.append({"kind": "line", "title": c, "data": [r.get(c) if isnum(r.get(c)) else float("nan") for r in rows], "note": ""})
            else:
                add("json", x)
        else:
            add("json", x)
    except Exception as ex:  # pragma: no cover
        B.append({"kind": "json", "title": title or T("an.ad.answer"), "data": x, "note": T("an.ad.plan_err")})
    return B[:max_blocks] if depth == 0 else B




def nice(v, d=3):
    try:
        s = f"{v:.{d}f}".rstrip("0").rstrip(".")
        return s or "0"
    except Exception:
        return str(v)


def _fmt(v):
    return f"{v:,.0f}" if isinstance(v, int) or (isinstance(v, float) and abs(v) >= 1000) else nice(v, 3)


def _b64(fig, T):
    return pm.png_b64(fig, T)


def _pair(draw, alt: str) -> str:
    """Desenha nos dois temas (sob RENDER_LOCK; figura sempre fechada) e devolve o par <img> claro/escuro."""
    imgs = {}
    with pm.RENDER_LOCK:
        for name, T in pm.THEMES.items():
            fig = None
            try:
                pm.setup(T)
                fig = draw(T)
                imgs[name] = _b64(fig, T)
            finally:
                if fig is not None:
                    plt.close(fig)
    a = e(alt, quote=True)
    return "".join(f'<img class="v-img v-{k}" src="data:image/png;base64,{imgs[n]}" alt="{a}" loading="lazy">'
                   for k, n in (("lt", "light"), ("dk", "dark")))


W = 6.4


def _style(ax, T, grid="y"):
    ax.tick_params(length=0, labelsize=pm.px(11))
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(T.axis)
    ax.grid(axis=grid, color=T.grid, linewidth=pm.px(1))
    ax.set_axisbelow(True)


def draw_bars(items, T):
    labs = [pm.short(str(a), 22) for a, _ in items]
    vals = [float(v) for _, v in items]
    h = max(1.6, 0.34 * len(items) + 0.7)
    fig = plt.figure(figsize=(W, h), dpi=pm.DPI)
    ax = fig.add_axes([0.30, 0.12, 0.64, 0.84])
    ys = np.arange(len(vals))[::-1]
    ax.barh(ys, vals, color=T.cat[0], height=0.62, zorder=3)
    ax.set_yticks(ys)
    ax.set_yticklabels(labs, color=T.ink2)
    _style(ax, T, "x")
    top = max(vals) if vals else 1
    for y, v in zip(ys, vals):
        ax.text(v + top * 0.01, y, _fmt(v), va="center", ha="left", color=T.ink, fontsize=pm.px(11))
    ax.set_xlim(0, (top or 1) * 1.18)
    return fig


def draw_lines(series, xs, T):
    """series: [(nome, [valores])]; um unico eixo y (nunca dois eixos)."""
    fig = plt.figure(figsize=(W, 2.6), dpi=pm.DPI)
    ax = fig.add_axes([0.09, 0.18, 0.88, 0.62 if len(series) > 1 else 0.74])
    n = len(series[0][1])
    x = np.arange(n)
    allv = [v for _, s in series for v in s if isinstance(v, (int, float)) and math.isfinite(v)]
    for i, (nm, s) in enumerate(series):
        y = np.array([v if isinstance(v, (int, float)) and math.isfinite(v) else np.nan for v in s], float)
        pm._line(ax, x, y, T.cat[i % len(T.cat)], T, label=pm.short(str(nm), 18), dot=True)
    lo, hi = (min(allv), max(allv)) if allv else (0, 1)
    lo = 0 if (lo >= 0 and lo < hi * 0.6) else lo
    pad = (hi - lo) * 0.08 or 1
    ax.set_ylim(lo, hi + pad)
    step = max(1, math.ceil(n / 8))
    ax.set_xticks(x[::step])
    if xs:
        ax.set_xticklabels([pm.short(str(v), 12) for v in list(xs)[::step]], color=T.ink2)
    ax.set_xlim(-0.5, n - 0.5)
    _style(ax, T, "y")
    if len(series) > 1:
        pm._legend(ax, T, ax.get_lines() and [h for h in ax.get_lines()][: len(series)], ncol=min(len(series), 4))
    return fig


def draw_heat(M, T):
    arr = np.array(M, float)
    rows, cols = arr.shape
    fig = plt.figure(figsize=(W, min(5.2, max(1.8, 0.28 * rows + 0.9))), dpi=pm.DPI)
    ax = fig.add_axes([0.07, 0.10, 0.80, 0.86])
    mn, mx = float(np.nanmin(arr)), float(np.nanmax(arr))
    im = ax.imshow(arr, aspect="auto", cmap=pm.cmap_of(T), vmin=mn, vmax=mx if mx > mn else mn + 1, interpolation="nearest")
    ax.set_xticks(range(0, cols, max(1, math.ceil(cols / 12))))
    ax.set_yticks(range(0, rows, max(1, math.ceil(rows / 12))))
    ax.set_xticklabels([str(i + 1) for i in ax.get_xticks()], color=T.ink2)
    ax.set_yticklabels([str(i + 1) for i in ax.get_yticks()], color=T.ink2)
    ax.tick_params(length=0, labelsize=pm.px(11))
    for s in ax.spines.values():
        s.set_visible(False)
    cax = fig.add_axes([0.90, 0.10, 0.025, 0.86])
    cb = fig.colorbar(im, cax=cax)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0, labelsize=pm.px(10), colors=T.ink2)
    return fig


def _card(ttl, note, body, T=None):
    return (f'<section class="v-card"><h3>{e(str(ttl) or "-")}</h3>' + (f'<p class="q">{e(note)}</p>' if note else "")
            + body + "</section>")


def _table(cols, rows, lang, limit=50):
    th = "".join(f"<th>{e(str(c))}</th>" for c in cols)
    tr_ = "".join("<tr>" + "".join(f"<td>{e(str(c if not isinstance(c, float) else nice(c, 4)))}</td>" for c in r) + "</tr>"
                  for r in rows[:limit])
    more = f'<p class="q">{e(i18n.t("an.ad.more", lang, n=len(rows) - limit))}</p>' if len(rows) > limit else ""
    return f'<div class="v-tblwrap"><table><tr>{th}</tr>{tr_}</table></div>{more}'


def _render_block(b, lang):
    T = lambda k, **f: i18n.t(k, lang, **f)  # noqa: E731
    k, d, ttl, note = b["kind"], b["data"], b["title"], b.get("note", "")
    if k == "empty":
        return _card(T("an.ad.empty"), "", f'<div class="v-none">{e(T("an.ad.empty_body"))}</div>')
    if k == "kpi":
        return '<div class="v-kpis">' + "".join(
            f'<div class="v-kpi"><b>{e(_fmt(v))}</b><span>{e(str(n))}</span></div>' for n, v in d) + "</div>"
    if k == "text":
        return _card(ttl or T("an.ad.text"), "", f"<p>{e(str(d))}</p>")
    if k == "bars":
        alt = "Horizontal bars: " + "; ".join(f"{a}={_fmt(v)}" for a, v in d)[:300]
        return _card(ttl, note, _pair(lambda Th: draw_bars(d, Th), alt))
    if k in ("line", "multiline"):
        series = list(d.items()) if k == "multiline" else [(ttl or "series", d)]
        series = [(str(nm), list(s)) for nm, s in series]
        n = len(series[0][1])
        alt = f"Line chart, {len(series)} series, {n} points. Last: " + ", ".join(
            f"{nm}={_fmt(s[-1])}" for nm, s in series if s and isinstance(s[-1], (int, float)) and math.isfinite(s[-1]))
        return _card(ttl, note, _pair(lambda Th: draw_lines(series, b.get("xs"), Th), alt[:300]))
    if k == "heat":
        flat = [v for r in d for v in r if math.isfinite(v)]
        mn, mx = (min(flat), max(flat)) if flat else (0, 0)
        return _card(ttl or T("an.ad.matrix"), T("an.ad.scale", lo=_fmt(mn), hi=_fmt(mx)),
                     _pair(lambda Th: draw_heat(d, Th), f"Heatmap, {len(d)} by {len(d[0])}, from {_fmt(mn)} to {_fmt(mx)}. Stronger color = higher value."))
    if k == "table":
        return _card(ttl or T("an.ad.table"), note, _table(d["cols"], d["rows"][:500], lang))
    s = json.dumps(d, ensure_ascii=False, indent=1, default=str)
    return _card(ttl or T("an.ad.answer"), note or T("an.ad.unknown"),
                 f'<details class="v-json" open><summary>JSON</summary><pre>{e(s[:20000])}</pre></details>')


def render(payload, lang: str = "pt", **kw) -> str:
    """HTML para gr.HTML. Nunca levanta excecao."""
    try:
        lang = i18n.norm_lang(lang) if hasattr(i18n, "norm_lang") else lang
        blocks = plan(payload, lang=lang, **kw)
        out = []
        for b in blocks:
            try:
                out.append(_render_block(b, lang))
            except Exception:
                try:
                    raw = e(json.dumps(b.get("data"), default=str, ensure_ascii=False)[:2000])
                except Exception:
                    raw = ""
                out.append(_card(b.get("title") or "?", i18n.t("an.ad.err", lang), f"<pre>{raw}</pre>"))
        return '<div class="v-grid">' + "".join(out) + "</div>"
    except Exception:
        try:
            return f'<div class="v-card"><pre>{e(repr(payload)[:2000])}</pre></div>'
        except Exception:
            return '<div class="v-card"></div>'
