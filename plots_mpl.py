"""Plots da aba Analytics (matplotlib, backend Agg). Funcoes puras: registros -> Figure.

DECISAO DO AUTOR: todo texto DENTRO dos graficos (titulos, eixos, legendas, rotulos) e SEMPRE em ingles, qualquer que seja
o idioma do site; por isso nao ha i18n nem fonte CJK aqui (DejaVu Sans, que vem com o matplotlib, basta). Titulos e perguntas
dos cartoes ficam no HTML da UI (idioma do site).

Convencoes
- Largura de projeto 360 css px (3.6 in a dpi=100): cabe em 380 px de celular.
- Cores: paleta validada (categorical 8 slots + rampa azul sequencial), claro e escuro; cada plot e renderizado nos dois temas.
- Chamar sempre sob `RENDER_LOCK` (pyplot nao e thread-safe) e fechar a figura (`plt.close`) -- ver `render_png`.
"""
from __future__ import annotations

import math
import os
from collections import Counter, OrderedDict
from dataclasses import dataclass

import io
import threading

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager as fm  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

FONT_FAMILY = ["DejaVu Sans"]
RENDER_LOCK = threading.Lock()
PNG_DPI = 150
SHORT = 12  # nomes de pessoa/rotulos longos: reticencias


def short(s, n: int = SHORT) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


_GLYPHS: dict = {}


def _has_glyph(ch: str) -> bool:
    """O DejaVu Sans tem o glifo? (nomes CJK etc. viram '?' em vez de caixinhas)."""
    if ch not in _GLYPHS:
        try:
            from matplotlib import ft2font
            f = _GLYPHS.setdefault("\0font", ft2font.FT2Font(fm.findfont(FONT_FAMILY[0])))
            _GLYPHS[ch] = f.get_char_index(ord(ch)) != 0
        except Exception:
            _GLYPHS[ch] = ord(ch) < 0x250
    return _GLYPHS[ch]


_DEFAULT_RES: list = []


def display_name(name) -> str:
    """Nome para o grafico: nomes padrao de qualquer idioma ('Pessoa1', '成员1', 'Persona1'...) viram 'PersonN' (EN);
    caracteres sem glifo viram '?'."""
    import re
    import i18n
    if not _DEFAULT_RES:
        for lg in i18n.LANGS:
            try:
                tpl = i18n.t("people.default", lg, n="\0")
                pre, _, post = tpl.partition("\0")
                _DEFAULT_RES.append(re.compile("^" + re.escape(pre) + r"(\d+)" + re.escape(post) + "$"))
            except Exception:
                pass
    s = str(name if name not in (None, "") else "?")
    for rx in _DEFAULT_RES:
        m = rx.match(s)
        if m:
            return f"Person{m.group(1)}"
    return "".join(c if (c.isprintable() and _has_glyph(c)) else "?" for c in s)


# textos dos graficos: sempre em ingles
EN = {
    "an.msg_axis": "Message (# in session)", "an.clair": "Clair", "an.unit.s": "s",
    "an.legend.value01": "0 to 1", "an.p3.group.l1l2": "Role and speech type", "an.p3.group.other": "Other signals",
    "an.p4.messages": "Messages", "an.p4.words": "Words", "an.p5.gini": "GINI (0 = equal, 1 = one person only)",
    "an.p5.gini_local": "GINI of words (computed here)", "an.p5.share": "Cumulative share of words (dashed = equal shares)", "an.p5.equal": "equal shares",
    "an.l1.DOM": "Dominant (DOM)", "an.l1.COO": "Cooperative (COO)", "an.l1.OFF": "Off-task (OFF)",
    "an.p8.size": "Dot size = number of words", "an.p9.moves": "Talk moves chosen (times)",
    "an.p9.intents": "Intents detected (messages)", "an.p10.chosen": "● talk move chosen",
    "an.l2c.IN": "IN", "an.l2c.AR": "AR", "an.l2c.AI": "AI", "an.l2c.AM": "AM", "an.l2c.NOS": "NOS",
    "an.p1.clipped": "values outside 0 to 1 were clipped",
}


def tr(_lang=None):
    """Sempre ingles (a decisao do autor); o argumento e ignorado e existe so por compatibilidade com o prototipo."""
    return lambda key, **kw: (EN.get(key, key).format(**kw) if kw else EN.get(key, key))

W_IN = 3.6  # 360 css px
DPI = 100


def px(n: float) -> float:  # css px -> pontos (dpi=100)
    return n * 72 / DPI


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    ink: str
    ink2: str
    muted: str
    grid: str
    axis: str
    cat: tuple
    seq: tuple  # rampa sequencial baixo -> alto
    na: str


_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]  # 100..700 (palette.md)
LIGHT = Theme("light", "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7",
              ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"),
              tuple(_BLUE), "#ecebe6")
# escuro: a ancora inverte (baixo = quase superficie, alto = claro); mesmos degraus, ordem invertida
DARK = Theme("dark", "#1a1a19", "#f0efec", "#c3c2b7", "#898781", "#2c2c2a", "#383835",
             ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"),
             ("#14243b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"), "#262624")
THEMES = {"light": LIGHT, "dark": DARK}


def cmap_of(T: Theme):
    return LinearSegmentedColormap.from_list("seq_" + T.name, list(T.seq))


def lum(hexc: str) -> float:
    r, g, b = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def text_on(rgba, T: Theme) -> str:
    from matplotlib.colors import to_hex
    h = to_hex(rgba)
    return "#0b0b0b" if lum(h) > 0.30 else "#fcfcfb"


def setup(T: Theme):
    plt.rcParams.update({
        "font.family": FONT_FAMILY, "font.size": px(11), "text.color": T.ink2, "axes.labelcolor": T.ink2,
        "xtick.color": T.ink2, "ytick.color": T.ink2, "axes.edgecolor": T.axis, "axes.linewidth": px(1),
        "axes.facecolor": "none", "figure.facecolor": "none", "savefig.facecolor": "none",
        "xtick.labelsize": px(11), "ytick.labelsize": px(11), "legend.fontsize": px(11),
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "svg.fonttype": "path", "axes.unicode_minus": True, "lines.solid_capstyle": "round",
    })


# ------------------------------------------------------------------ dados
class Data:
    """Extrai series dos registros de forma defensiva (qualquer campo pode faltar ou ser lixo)."""

    def __init__(self, records: list[dict], last: int = 40):
        def _s(v):  # so texto entra nos agrupamentos; qualquer outro tipo vira ausente
            return v if isinstance(v, str) and v else None
        self.all = [dict(r, pessoa=display_name(r.get("pessoa")), selected_move=_s(r.get("selected_move")),
                         intent=_s(r.get("intent")), texto=_s(r.get("texto")) or "",
                         full_text=_s(r.get("full_text")) or "") if isinstance(r, dict) else {}
                    for r in (records or [])]
        self.n_total = len(self.all)
        self.recs = self.all[-last:]
        self.offset = self.n_total - len(self.recs)  # numero da primeira mensagem mostrada = offset+1
        self.n = len(self.recs)
        self.persons = list(OrderedDict.fromkeys(r.get("pessoa") or "?" for r in self.all))
        self.who = [self.persons.index(r.get("pessoa") or "?") for r in self.recs]
        self.names = [short(x) for x in self.persons]  # rotulos curtos (reticencias) para os eixos
        self.x = np.arange(self.offset + 1, self.offset + self.n + 1)

    @staticmethod
    def num(v):
        try:
            f = float(v)
            return f if (math.isfinite(f) and abs(f) <= 1e9) else float("nan")  # absurdos viram lacuna
        except (TypeError, ValueError):
            return float("nan")

    def sig(self, key: str, recs=None) -> np.ndarray:
        out = []
        for r in (recs if recs is not None else self.recs):
            ds = r.get("dialogue_state")
            out.append(self.num(ds.get(key)) if isinstance(ds, dict) else float("nan"))
        return np.array(out, dtype=float)

    @staticmethod
    def intervened(r) -> bool:
        """A Clair interveio? Usa `intervention` (bool guardado pelo app); sem ele, `selected_move` nao nulo."""
        if isinstance(r, dict) and "intervention" in r:
            return bool(r.get("intervention"))
        return bool(isinstance(r, dict) and r.get("selected_move"))

    def has(self, key: str) -> bool:
        return bool(np.isfinite(self.sig(key)).any())

    def words(self, recs=None) -> list[int]:
        out = []
        for r in (recs if recs is not None else self.recs):
            ds = r.get("dialogue_state")
            t = ds.get("text") if isinstance(ds, dict) and isinstance(ds.get("text"), str) else (r.get("full_text") or r.get("texto") or "")
            t = t if isinstance(t, str) else ""
            out.append(len(t.split()))
        return out

    def fuzzy_keys(self) -> list[str]:
        keys: list[str] = []
        for r in self.recs:
            fz = r.get("fuzzy_output")
            if isinstance(fz, dict):
                for k in fz:
                    if k not in keys:
                        keys.append(k)
        return keys

    def fuzzy(self, key: str) -> np.ndarray:
        out = []
        for r in self.recs:
            fz = r.get("fuzzy_output")
            out.append(self.num(fz.get(key)) if isinstance(fz, dict) and key in fz else float("nan"))
        return np.array(out, dtype=float)

    def pcolor(self, T: Theme, i: int) -> str:
        return T.cat[i % len(T.cat)]


def _fig(h_px: float, T: Theme):
    setup(T)
    fig = plt.figure(figsize=(W_IN, h_px / DPI), dpi=DPI)
    return fig


def A(fig, l, t, w, hgt, **kw):
    """eixos em px (origem no topo-esquerda da figura); l,w em fracao da largura."""
    H = fig.get_figheight() * DPI
    return fig.add_axes([l, 1 - (t + hgt) / H, w, hgt / H], **kw)


def _title(fig, T, text, sub=None, on=True):
    if not on:
        return 0
    fig.text(0.0, 0.985, text, ha="left", va="top", fontsize=px(14), fontweight="bold", color=T.ink)
    if sub:
        fig.text(0.0, 0.985 - px(20) / (fig.get_figheight() * 72), sub, ha="left", va="top", fontsize=px(11), color=T.ink2)
    return 1


def _style_axes(ax, T, grid_y=True, bottom=True):
    ax.tick_params(length=0, pad=px(4))
    ax.spines["bottom"].set_visible(bottom)
    ax.spines["bottom"].set_color(T.axis)
    if grid_y:
        ax.grid(axis="y", color=T.grid, linewidth=px(1))
    ax.set_axisbelow(True)


def _xticks(ax, D: Data, maxn=8):
    if D.n == 0:
        return
    step = max(1, math.ceil(D.n / maxn))
    ax.set_xticks(D.x[::step])
    ax.set_xlim(D.x[0] - 0.5, D.x[-1] + 0.5)


def _legend(ax, T, handles, ncol=None, loc="lower left", anchor=(0, 1.0)):
    leg = ax.legend(handles=handles, ncol=ncol or len(handles), frameon=False, loc=loc, bbox_to_anchor=anchor,
                    handlelength=px(14) / px(11), handletextpad=0.5, columnspacing=1.1, borderaxespad=0.1)
    for t in leg.get_texts():
        t.set_color(T.ink2)
    return leg


def _line(ax, x, y, color, T, label=None, dot=True):
    ax.plot(x, y, color=color, lw=px(2), solid_joinstyle="round", label=label, zorder=3)
    fin = np.isfinite(y)
    if dot and fin.any():
        i = np.where(fin)[0][-1]
        ax.scatter([x[i]], [y[i]], s=(px(9)) ** 2, color=color, edgecolor=T.surface, linewidth=px(2), zorder=4, clip_on=False)


def _empty(fig, T, t, key="an.empty.body"):
    plt.close(fig)
    return None  # sem dados para este grafico: o cartao mostra o aviso (idioma do site)


def label_of(t, prefix, key, width=20):
    """Rotulo legivel em ingles a partir da chave (sem catalogo): underscores viram espacos; longos quebram em 2 linhas."""
    import textwrap
    v = str(key).replace("_", " ")
    return "\n".join(textwrap.wrap(v, width)[:2]) if len(v) > width else v


# ------------------------------------------------------------------ P1 sinais 0..1 no tempo
P1_GROUPS = [("TSIM", "TACC"), ("L3_IP", "L3_CU", "L3_NV", "L3_NE"), ("L4_SS",)]


def plot_signals(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    groups = [[k for k in g if D.has(k)] for g in P1_GROUPS]
    groups = [g for g in groups if g]
    if not groups or D.n == 0:
        return _empty(_fig(120, T), T, t)
    top = 40 if title else 4
    h = top + len(groups) * 112 + 34
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p1.title"), t("an.p1.q"))
    axes = []
    clipped = False
    for gi, g in enumerate(groups):
        ax = A(fig, 0.10, top + gi * 112 + 24, 0.86, 70, **({"sharex": axes[0]} if axes else {}))
        axes.append(ax)
        hs = []
        for si, k in enumerate(g):
            y = D.sig(k)
            with np.errstate(invalid="ignore"):
                if (np.nan_to_num(y, nan=0.5) < 0).any() or (np.nan_to_num(y, nan=0.5) > 1).any():
                    clipped = True
            y = np.clip(y, 0, 1)  # escala fixa 0..1: valores fora do intervalo sao recortados (e sinalizados)
            _line(ax, D.x, y, T.cat[si], T)
            hs.append(plt.Line2D([0], [0], color=T.cat[si], lw=px(2), label=k))
        ax.set_ylim(-0.04, 1.04)
        ax.set_yticks([0, 0.5, 1])
        ax.set_yticklabels(["0", "0.5", "1"])
        _style_axes(ax, T)
        _xticks(ax, D)
        _legend(ax, T, hs, anchor=(0, 1.06))
        if gi < len(groups) - 1:
            plt.setp(ax.get_xticklabels(), visible=False)
    axes[-1].set_xlabel(t("an.msg_axis"), fontsize=px(11), labelpad=px(4))
    if clipped:
        fig.text(0.99, 2 / h, t("an.p1.clipped"), ha="right", va="bottom", fontsize=px(10), color=T.ink2)
    return fig


# ------------------------------------------------------------------ P2 ritmo (escalas proprias)
P2_KEYS = [("PACE", None), ("TSLM", "an.unit.s"), ("TSLQ", None), ("TIME", None)]


def plot_pace(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    if D.n == 0:
        return _empty(_fig(120, T), T, t)
    top = 40 if title else 4
    h = top + 270
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p2.title"), t("an.p2.q"))
    gs = fig.add_gridspec(2, 2, left=px(40) / (W_IN * 72), right=1 - px(10) / (W_IN * 72), top=1 - (top + 18) / h,
                          bottom=34 / h, hspace=0.9, wspace=0.42)
    for i, (k, unit) in enumerate(P2_KEYS):
        ax = fig.add_subplot(gs[i // 2, i % 2])
        y = D.sig(k)
        name = k + (f" ({t(unit)})" if unit else "")
        ax.set_title(name, loc="left", fontsize=px(11), color=T.ink, fontweight="bold", pad=px(6))
        if np.isfinite(y).any():
            _line(ax, D.x, y, T.cat[0], T)
            lo, hi = np.nanmin(y), np.nanmax(y)
            pad = (hi - lo) * 0.15 or max(abs(hi) * 0.1, 0.5)
            lower = 0 if (lo >= 0 and lo < hi * 0.6) else (max(lo - pad, 0) if lo >= 0 else lo - pad)  # nunca abaixo de 0 se o sinal e >= 0
            ax.set_ylim(lower, hi + pad)
            ax.yaxis.set_major_locator(plt.MaxNLocator(3))
        else:
            ax.text(0.5, 0.5, "—", transform=ax.transAxes, ha="center", va="center", color=T.ink2)
            ax.set_yticks([])
        _style_axes(ax, T)
        _xticks(ax, D, maxn=4)
    fig.text(0.5, 4 / h, t("an.msg_axis"), ha="center", va="bottom", fontsize=px(11), color=T.ink2)
    return fig


# ------------------------------------------------------------------ P3 mapa de calor do dialogue state
P3_ROWS = [
    ("an.p3.group.l1l2", ["L1_DOM", "L1_COO", "L1_OFF", "L2C_IN", "L2C_AR", "L2C_AI", "L2C_AM", "L2C_NOS"]),
    ("an.p3.group.other", ["TSIM", "TACC", "L3_IP", "L3_CU", "L3_NV", "L3_NE", "L4_SS", "COFF", "GINI", "FIP", "FCU", "FCD",
                           "FCC", "FNV", "FNE", "FCQ"]),
]


def _heat(ax, M, T, vmin=0, vmax=1):
    cm = cmap_of(T)
    cm.set_bad(T.na)
    mesh = ax.pcolormesh(np.ma.masked_invalid(M), cmap=cm, vmin=vmin, vmax=vmax, edgecolors=T.surface,
                         linewidth=px(1.5) if M.shape[1] <= 24 else px(1))
    ax.set_xlim(0, M.shape[1])
    ax.set_ylim(M.shape[0], 0)
    return mesh


def _colorbar_strip(fig, T, x0, y0, w, hgt, lo="0", hi="1"):
    cax = fig.add_axes([x0, y0, w, hgt])
    g = np.linspace(0, 1, 64)[None, :]
    cax.imshow(g, aspect="auto", cmap=cmap_of(T), extent=[0, 1, 0, 1])
    cax.set_yticks([])
    cax.set_xticks([0, 1])
    cax.set_xticklabels([lo, hi], fontsize=px(10))
    cax.tick_params(length=0, pad=px(2))
    for s in cax.spines.values():
        s.set_visible(False)


def plot_heatmap_state(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    rows, brk = [], []
    for gname, keys in P3_ROWS:
        ks = [k for k in keys if D.has(k)]
        if ks:
            brk.append((len(rows), t(gname)))
            rows += ks
    if not rows or D.n == 0:
        return _empty(_fig(120, T), T, t)
    rh = 14
    gaps = len(brk) - 1
    h = 12 + len(rows) * rh + gaps * 22 + len(brk) * 18 + 96 + (40 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p3.title"), t("an.p3.q"))
    left = 0.19
    top = 40 if title else 4
    ax = A(fig, left, top, 1 - left - 0.02, len(rows) * rh + gaps * 22 + len(brk) * 18)
    Hax = len(rows) * rh + gaps * 22 + len(brk) * 18
    # montar matriz com linhas-separadoras (NaN) e cabecalhos de grupo
    M, ylabels, yy = [], [], []
    r = 0
    for gi, (start, gname) in enumerate(brk):
        end = brk[gi + 1][0] if gi + 1 < len(brk) else len(rows)
        M.append(np.full(D.n, np.nan))  # linha do cabecalho de grupo
        ylabels.append(None)
        for k in rows[start:end]:
            M.append(D.sig(k))
            ylabels.append(k)
        if gi + 1 < len(brk):
            M.append(np.full(D.n, np.nan))
            ylabels.append(False)
    M = np.array(M)
    # linhas NaN de cabecalho/separador ficam transparentes (sem cor "NA")
    cm = cmap_of(T)
    cm.set_bad((0, 0, 0, 0))
    Mm = np.ma.masked_invalid(M)
    # celulas sem dado de linha real = cinza NA; cabecalho = transparente
    ax.pcolormesh(Mm, cmap=cm, vmin=0, vmax=1, edgecolors=T.surface, linewidth=px(1))
    ax.set_xlim(0, D.n)
    ax.set_ylim(M.shape[0], 0)
    ax.set_yticks([i + 0.5 for i, l in enumerate(ylabels) if l])
    ax.set_yticklabels([l for l in ylabels if l], fontsize=px(10.5))
    for i, l in enumerate(ylabels):
        if l is None:
            gname = [g for s, g in brk if s == sum(1 for x in ylabels[:i] if x) ][0] if False else None
    # titulos de grupo
    gi = 0
    for i, l in enumerate(ylabels):
        if l is None:
            ax.text(-0.01, i + 0.5, [g for _, g in brk][gi], transform=ax.get_yaxis_transform(), ha="right", va="center",
                    fontsize=px(10.5), fontweight="bold", color=T.ink) if False else None
            ax.text(0, i + 0.5, [g for _, g in brk][gi], ha="left", va="center", fontsize=px(10.5), fontweight="bold", color=T.ink)
            gi += 1
    ax.tick_params(length=0, pad=px(3))
    for s in ax.spines.values():
        s.set_visible(False)
    step = max(1, math.ceil(D.n / 8))
    ax.set_xticks([i + 0.5 for i in range(0, D.n, step)])
    ax.set_xticklabels([str(D.x[i]) for i in range(0, D.n, step)], fontsize=px(10.5))
    ax.xaxis.tick_bottom()
    # faixa de quem falou (cor = pessoa) acima do eixo x
    sx = A(fig, left, top + Hax + 24, 1 - left - 0.02, 9)
    for i, w in enumerate(D.who):
        sx.add_patch(plt.Rectangle((i + 0.08, 0), 0.84, 1, color=D.pcolor(T, w), lw=0))
    sx.set_xlim(0, D.n)
    sx.set_ylim(0, 1)
    sx.axis("off")
    _colorbar_strip(fig, T, 1 - 0.02 - 0.34, 24 / h, 0.34, 7 / h)
    fig.text(1 - 0.02 - 0.36, 10 / h, t("an.legend.value01").split(" · ")[0].replace("0 = ", "0 "), ha="right", va="center", fontsize=px(10), color=T.ink2) if False else None
    # legenda das pessoas
    cw = (1 - left - 0.02) / 3  # 3 nomes por linha, ate 2 linhas (nomes ja vem encurtados)
    for pi, name in enumerate(D.names):
        if pi >= 6:
            fig.text(left + 2 * cw, 40 / h, "…", ha="left", va="center", fontsize=px(10.5), color=T.ink2)
            break
        xx, yy = left + (pi % 3) * cw, (56 - 16 * (pi // 3)) / h
        fig.patches.append(plt.Rectangle((xx, yy - 4 / h), 0.025, 8 / h, transform=fig.transFigure, color=D.pcolor(T, pi), lw=0))
        fig.text(xx + 0.035, yy, name, ha="left", va="center", fontsize=px(10.5), color=T.ink2)
    return fig


# ------------------------------------------------------------------ P4 participacao
def counts_by_person(D: Data, use_all=True):
    recs = D.all if use_all else D.recs
    words = D.words(recs)
    msgs = Counter(r.get("pessoa") or "?" for r in recs)
    wc = Counter()
    for r, w in zip(recs, words):
        wc[r.get("pessoa") or "?"] += w
    return msgs, wc


def plot_participation(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    if not D.all:
        return _empty(_fig(120, T), T, t)
    msgs, wc = counts_by_person(D)
    P = D.persons
    h = 70 + 2 * (len(P) * 24 + 26) + (36 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p4.title"), t("an.p4.q"))
    top = (36 if title else 0)
    ph = len(P) * 24 + 26
    for pi, (key, cnt) in enumerate((("an.p4.messages", msgs), ("an.p4.words", wc))):
        y0 = 1 - (top + 6 + pi * (ph + 12) + ph) / h
        ax = fig.add_axes([0.20, y0, 0.70, (len(P) * 24) / h])
        vals = [cnt.get(p, 0) for p in P]
        ypos = np.arange(len(P))
        mx = max(vals) or 1
        ax.barh(ypos, vals, height=px(20) / (24 * 0.72) * 0.72 * 1.0, color=[D.pcolor(T, i) for i in range(len(P))],
                edgecolor="none")
        for i, v in enumerate(vals):
            ax.text(v + mx * 0.02, i, f"{v}", va="center", ha="left", fontsize=px(11), color=T.ink)
        ax.set_yticks(ypos)
        ax.set_yticklabels(D.names, fontsize=px(11))
        ax.set_xlim(0, mx * 1.18)
        ax.set_ylim(len(P) - 0.5, -0.5)
        ax.set_xticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(length=0, pad=px(4))
        fig.text(0.0, y0 + (len(P) * 24) / h + 4 / h, t(key), ha="left", va="bottom", fontsize=px(11), fontweight="bold", color=T.ink)
    return fig


# ------------------------------------------------------------------ P5 equilibrio
def gini(vals):
    v = sorted(float(x) for x in vals)
    n, s = len(v), sum(v)
    if n == 0 or s == 0:
        return float("nan")
    return sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(v)) / (n * s)


def plot_balance(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    if D.n == 0:
        return _empty(_fig(120, T), T, t)
    srv = D.sig("GINI")
    use_srv = np.isfinite(srv).any()
    P = D.persons
    cum = np.zeros((len(P), D.n))
    words = D.words()
    run = np.zeros(len(P))
    for i, (w, wh) in enumerate(zip(words, D.who)):
        run[wh] += w
        cum[:, i] = run
    if use_srv:
        g = srv
    else:
        g = np.array([gini(cum[:, i]) if len(P) > 1 else float("nan") for i in range(D.n)])
    top = 40 if title else 4
    h = top + 340
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p5.title"), t("an.p5.q"))
    fig.text(0.0, 1 - (top) / h, t("an.p5.gini" if use_srv else "an.p5.gini_local"), ha="left", va="top",
             fontsize=px(11), fontweight="bold", color=T.ink)
    a1 = A(fig, 0.10, top + 22, 0.70, 100)
    _line(a1, D.x, g, T.cat[0], T)
    a1.set_ylim(-0.04, 1.04)
    a1.set_yticks([0, 0.5, 1])
    a1.set_yticklabels(["0", "0.5", "1"])
    _style_axes(a1, T)
    _xticks(a1, D, 6)
    plt.setp(a1.get_xticklabels(), visible=False)
    fin = np.where(np.isfinite(g))[0]
    if len(fin):
        a1.annotate(f"{g[fin[-1]]:.2f}", (D.x[fin[-1]], g[fin[-1]]), xytext=(px(8), 0),
                    textcoords="offset points", va="center", fontsize=px(11), color=T.ink, annotation_clip=False)
    fig.text(0.0, 1 - (top + 150) / h, t("an.p5.share"), ha="left", va="top", fontsize=px(11), fontweight="bold", color=T.ink)
    a2 = A(fig, 0.10, top + 172, 0.70, 130)
    share = cum / np.maximum(cum.sum(axis=0, keepdims=True), 1)
    a2.axhline(1 / len(P), color=T.ink2, lw=px(1), ls=(0, (4, 3)), zorder=1)
    ends = []
    for pi, p in enumerate(P):
        _line(a2, D.x, share[pi], D.pcolor(T, pi), T)
        ends.append((share[pi, -1], p, pi))
    ends.sort()
    placed = []
    for v, p, pi in ends:
        y = v
        if placed and y - placed[-1] < 0.09:
            y = placed[-1] + 0.09
        placed.append(y)
        a2.annotate(short(p, 9), (D.x[-1], y), xytext=(px(8), 0), textcoords="offset points", va="center",
                    fontsize=px(10.5), color=D.pcolor(T, pi), fontweight="bold", annotation_clip=False)
    a2.set_ylim(0, 1)
    a2.set_yticks([0, 0.5, 1])
    a2.set_yticklabels(["0%", "50%", "100%"])
    _style_axes(a2, T)
    _xticks(a2, D, 6)
    a2.set_xlabel(t("an.msg_axis"), fontsize=px(11), labelpad=px(3))
    return fig


# ------------------------------------------------------------------ P6 L1 empilhado 100%
L1_KEYS = ["L1_DOM", "L1_COO", "L1_OFF"]
L2_KEYS = ["L2C_IN", "L2C_AR", "L2C_AI", "L2C_AM", "L2C_NOS"]


def mean_by_person(D: Data, keys, use_all=True):
    recs = D.all if use_all else D.recs
    out, ns = {}, {}
    for p in D.persons:
        rows = [r for r in recs if (r.get("pessoa") or "?") == p]
        arr = np.array([[Data.num((r.get("dialogue_state") or {}).get(k)) if isinstance(r.get("dialogue_state"), dict) else float("nan")
                         for k in keys] for r in rows], dtype=float) if rows else np.zeros((0, len(keys)))
        ns[p] = len(rows)
        out[p] = np.nanmean(arr, axis=0) if arr.size and np.isfinite(arr).any() else np.full(len(keys), np.nan)
    return out, ns


def plot_l1(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    if not D.all or not any(D.has(k) for k in L1_KEYS):
        return _empty(_fig(120, T), T, t)
    means, ns = mean_by_person(D, L1_KEYS)
    P = D.persons
    h = 64 + len(P) * 34 + (36 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p6.title"), t("an.p6.q"))
    top = 36 if title else 0
    ax = fig.add_axes([0.20, 14 / h, 0.76, (len(P) * 34) / h])
    slots = (4, 5, 6)  # magenta, verde, violeta: evita colidir com as cores de pessoa 1-3
    for pi, p in enumerate(P):
        v = means[p]
        s = np.nansum(v)
        shares = v / s if s > 0 else np.zeros(3)
        left = 0.0
        for ki, k in enumerate(L1_KEYS):
            w = float(shares[ki]) if np.isfinite(shares[ki]) else 0
            gap = 0.006
            if w > 0:
                ax.barh(pi, max(w - gap, 0), left=left, height=0.62, color=T.cat[slots[ki]], edgecolor="none")
                if w >= 0.14:
                    col = text_on(plt.matplotlib.colors.to_rgba(T.cat[slots[ki]]), T)
                    ax.text(left + w / 2, pi, f"{w * 100:.0f}%", ha="center", va="center", fontsize=px(10.5), color=col, fontweight="bold")
            left += w
    ax.set_xlim(0, 1)
    ax.set_ylim(len(P) - 0.5, -0.5)
    ax.set_yticks(range(len(P)))
    ax.set_yticklabels(D.names, fontsize=px(11))
    ax.set_xticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0, pad=px(4))
    hs = [plt.Rectangle((0, 0), 1, 1, color=T.cat[slots[i]], label=t(f"an.l1.{k[3:]}")) for i, k in enumerate(L1_KEYS)]
    leg = fig.legend(handles=hs, ncol=1 if W_IN < 3 else 3, frameon=False, loc="upper left",
                     bbox_to_anchor=(0.0, 1 - (top + 2) / h), fontsize=px(10.5), handlelength=1, handleheight=1, columnspacing=1.0,
                     handletextpad=0.4)
    for tx in leg.get_texts():
        tx.set_color(T.ink2)
    return fig


# ------------------------------------------------------------------ P7 L2C heatmap pessoa x categoria
def plot_l2c(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    keys = [k for k in L2_KEYS if D.has(k)]
    if not D.all or not keys:
        return _empty(_fig(120, T), T, t)
    means, ns = mean_by_person(D, keys)
    P = D.persons
    M = np.array([means[p] for p in P])
    h = 56 + len(P) * 34 + (36 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p7.title"), t("an.p7.q"))
    top = 36 if title else 0
    ax = fig.add_axes([0.20, 8 / h, 0.78, (len(P) * 34) / h])
    mesh = _heat(ax, M, T, 0, 1)
    cm = cmap_of(T)
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            v = M[i, j]
            if np.isfinite(v):
                ax.text(j + 0.5, i + 0.5, f"{v * 100:.0f}%", ha="center", va="center", fontsize=px(10.5),
                        color=text_on(cm(v), T), fontweight="bold" if v >= 0.3 else "normal")
    ax.set_xticks([j + 0.5 for j in range(len(keys))])
    ax.set_xticklabels([t(f"an.l2c.{k[4:]}") for k in keys], fontsize=px(11))
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.5 for i in range(len(P))])
    ax.set_yticklabels(D.names, fontsize=px(11))
    ax.tick_params(length=0, pad=px(4))
    for s in ax.spines.values():
        s.set_visible(False)
    return fig


# ------------------------------------------------------------------ P8 raias (quem falou quando + Clair)
def plot_swimlane(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    if D.n == 0:
        return _empty(_fig(120, T), T, t)
    P = D.persons
    lanes = [t("an.clair")] + D.names
    h = 84 + len(lanes) * 30 + (36 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p8.title"), t("an.p8.q"))
    top = 36 if title else 0
    ax = fig.add_axes([0.17, 58 / h, 0.80, (len(lanes) * 30) / h])
    words = np.array(D.words(), dtype=float)
    wmax = max(words.max(), 1)
    for i, (w, wh) in enumerate(zip(words, D.who)):
        s = (px(7) + px(13) * math.sqrt(w / wmax)) ** 2
        ax.scatter(D.x[i], wh + 1, s=s, color=D.pcolor(T, wh), edgecolor=T.surface, linewidth=px(1.5), zorder=3)
    k = 0
    for i, r in enumerate(D.recs):
        if D.intervened(r):
            k += 1
            ax.scatter(D.x[i], 0, s=px(12) ** 2, marker="D", color=T.ink, edgecolor=T.surface, linewidth=px(1.5), zorder=3)
            ax.plot([D.x[i], D.x[i]], [0, D.who[i] + 1], color=T.axis, lw=px(1), zorder=1)
    ax.set_ylim(len(lanes) - 0.5, -0.5)
    ax.set_yticks(range(len(lanes)))
    ax.set_yticklabels(lanes, fontsize=px(11))
    ax.grid(axis="y", color=T.grid, linewidth=px(1))
    _style_axes(ax, T, grid_y=False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.grid(axis="y", color=T.grid, linewidth=px(1))
    _xticks(ax, D, 8)
    ax.set_xlabel(t("an.msg_axis"), fontsize=px(11), labelpad=px(3))
    fig.text(0.0, 4 / h, t("an.p8.size"), ha="left", va="bottom", fontsize=px(10), color=T.ink2)
    return fig


# ------------------------------------------------------------------ P9 talk moves + intencoes
def plot_moves(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    if not D.all:
        return _empty(_fig(120, T), T, t)
    keys = D.fuzzy_keys()
    mv = Counter(r.get("selected_move") for r in D.all if r.get("selected_move"))
    for k in mv:
        if k not in keys:
            keys.append(k)
    it = Counter(r.get("intent") or "—" for r in D.all)
    ik = [k for k, _ in it.most_common()]
    h = 110 + (len(keys) + len(ik)) * 32 + (36 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p9.title"), t("an.p9.q"))
    top = 36 if title else 0
    col = T.ink2
    def bars(y0px, items, counts, header, prefix):
        hh = len(items) * 32
        ax = fig.add_axes([0.42, 1 - (y0px + hh) / h, 0.50, hh / h])
        vals = [counts.get(k, 0) for k in items]
        mx = max(vals, default=0) or 1
        ax.barh(range(len(items)), vals, height=0.62, color=col, edgecolor="none")
        for i, v in enumerate(vals):
            ax.text(v + mx * 0.03, i, str(v), va="center", fontsize=px(11), color=T.ink)
        ax.set_yticks(range(len(items)))
        ax.set_yticklabels([label_of(t, prefix, k) for k in items], fontsize=px(10.5))
        ax.set_xlim(0, mx * 1.2)
        ax.set_ylim(len(items) - 0.5, -0.5)
        ax.set_xticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(length=0, pad=px(4))
        fig.text(0.0, 1 - (y0px - 6) / h, header, ha="left", va="bottom", fontsize=px(11), fontweight="bold", color=T.ink)
        return hh
    hh = bars(top + 34, keys, mv, t("an.p9.moves"), "an.move")
    bars(top + 34 + hh + 44, ik, it, t("an.p9.intents"), "an.intent")
    return fig


# ------------------------------------------------------------------ P10 pesos fuzzy
def plot_fuzzy(D: Data, T: Theme, lang="pt", title=False):
    t = tr(lang)
    keys = D.fuzzy_keys()
    if D.n == 0 or not keys:
        return _empty(_fig(120, T), T, t)
    M = np.array([D.fuzzy(k) for k in keys])
    rh = 18
    h = 60 + len(keys) * rh + 24 + (36 if title else 0)
    fig = _fig(h, T)
    if title:
        _title(fig, T, t("an.p10.title"), t("an.p10.q"))
    top = 36 if title else 0
    left = 0.34
    rh = 26
    h = 60 + len(keys) * rh + 24 + (36 if title else 0)
    fig.set_figheight(h / DPI)
    ax = fig.add_axes([left, 42 / h, 1 - left - 0.02, (len(keys) * rh) / h])
    cm = cmap_of(T)
    cm.set_bad(T.na)
    ax.pcolormesh(np.ma.masked_invalid(M), cmap=cm, vmin=0, vmax=1, edgecolors=T.surface, linewidth=px(1))
    ax.set_xlim(0, D.n)
    ax.set_ylim(len(keys), 0)
    for i, r in enumerate(D.recs):
        mv = r.get("selected_move")
        if mv in keys:
            j = keys.index(mv)
            ax.scatter(i + 0.5, j + 0.5, s=px(7) ** 2, color=T.ink if lum(plt.matplotlib.colors.to_hex(cm(M[j, i] if np.isfinite(M[j, i]) else 0))) > 0.2 else "#ffffff",
                       edgecolor=T.surface if False else "none", zorder=4, marker="o")
    ax.set_yticks([j + 0.5 for j in range(len(keys))])
    ax.set_yticklabels([label_of(t, "an.move", k) for k in keys], fontsize=px(10.5))
    step = max(1, math.ceil(D.n / 8))
    ax.set_xticks([i + 0.5 for i in range(0, D.n, step)])
    ax.set_xticklabels([str(D.x[i]) for i in range(0, D.n, step)], fontsize=px(10.5))
    ax.tick_params(length=0, pad=px(3))
    for s in ax.spines.values():
        s.set_visible(False)
    _colorbar_strip(fig, T, 1 - 0.02 - 0.30, 20 / h, 0.30, 7 / h)
    fig.text(left, 24 / h, t("an.p10.chosen"), ha="left", va="center", fontsize=px(10.5), color=T.ink2)
    return fig


PLOTS = OrderedDict([
    ("p1_sinais", plot_signals), ("p2_ritmo", plot_pace), ("p3_heatmap_state", plot_heatmap_state),
    ("p4_participacao", plot_participation), ("p5_equilibrio", plot_balance), ("p6_l1", plot_l1),
    ("p7_l2c", plot_l2c), ("p8_raias", plot_swimlane), ("p9_moves_intents", plot_moves), ("p10_fuzzy", plot_fuzzy),
])


# ====================================================================== renderizacao (PNG claro + escuro) e cache
import base64  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402

PLOT_IDS = [("p1", "p1_sinais"), ("p2", "p2_ritmo"), ("p3", "p3_heatmap_state"), ("p4", "p4_participacao"),
            ("p5", "p5_equilibrio"), ("p6", "p6_l1"), ("p7", "p7_l2c"), ("p8", "p8_raias"), ("p9", "p9_moves_intents"),
            ("p10", "p10_fuzzy")]


def _fmt(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and not math.isfinite(v)) else f"{v:.2f}"


def alt_text(pid: str, D: Data) -> str:
    """Alt text em ingles (decisao do autor), com os numeros reais."""
    n, P = D.n, D.persons
    try:
        if pid == "p1":
            ks = [k for g in P1_GROUPS for k in g if D.has(k)]
            last = ", ".join(f"{k}={_fmt(D.sig(k)[np.isfinite(D.sig(k))][-1])}" for k in ks[:8])
            return f"Line charts with {len(ks)} signals from 0 to 1 over {n} messages. Last values: {last}."
        if pid == "p2":
            ks = [k for k, _ in P2_KEYS if D.has(k)]
            last = ", ".join(f"{k}={_fmt(D.sig(k)[np.isfinite(D.sig(k))][-1])}" for k in ks)
            return f"Line panels ({', '.join(ks) or 'none'}), each with its own scale, over {n} messages. Last: {last}."
        if pid == "p3":
            r = sum(1 for _, keys in P3_ROWS for k in keys if D.has(k))
            return f"Heatmap: {r} signals by {n} messages, fixed scale from 0 to 1. Stronger color = higher value."
        if pid == "p4":
            msgs, wc = counts_by_person(D)
            return (f"Bars per person. Messages: {', '.join(f'{p}={msgs.get(p, 0)}' for p in P)}. "
                    f"Words: {', '.join(f'{p}={wc.get(p, 0)}' for p in P)}.")
        if pid == "p5":
            g = D.sig("GINI")
            fin = g[np.isfinite(g)]
            return f"Two line charts: GINI (last {_fmt(fin[-1]) if len(fin) else 'computed from words'}) and cumulative share of words per person."
        if pid == "p6":
            means, _ = mean_by_person(D, L1_KEYS)
            det = "; ".join(f"{p}: " + "/".join(_fmt(x) for x in means[p]) for p in P[:6])
            return f"100% stacked bars per person with mean L1 probabilities (DOM/COO/OFF). {det}."
        if pid == "p7":
            return f"Heatmap of mean L2C probabilities, {len(P)} people by speech-type category."
        if pid == "p8":
            k = sum(1 for r in D.recs if D.intervened(r))
            return f"Timeline with one lane per person and one for Clair. Clair intervened {k} times in {n} messages."
        if pid == "p9":
            mv = Counter(r.get("selected_move") for r in D.all if r.get("selected_move"))
            return f"Horizontal bars: talk moves chosen ({sum(mv.values())} in total) and intents detected."
        if pid == "p10":
            return f"Heatmap of fuzzy weights from 0 to 1 for {len(D.fuzzy_keys())} talk moves over {n} messages; dots mark the chosen move."
    except Exception:
        pass
    return "Chart"


def png_b64(fig, T: Theme) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=PNG_DPI, facecolor=T.surface, pil_kwargs={"optimize": True})
    return base64.b64encode(buf.getvalue()).decode("ascii")


def records_key(records) -> str:
    try:
        raw = json.dumps(records or [], sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        raw = repr(records)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


_CACHE: "OrderedDict[str, list]" = OrderedDict()


def render_all(records, use_cache: bool = True) -> list[dict]:
    """10 plots x 2 temas (PNG base64). Cada plot e isolado: uma excecao vira {"error": True} so naquele cartao.
    Sob RENDER_LOCK; toda figura e fechada (sem vazamento)."""
    key = records_key(records)
    if use_cache and key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    out: list[dict] = []
    with RENDER_LOCK:
        try:
            D = Data(list(records or []))
            for pid, name in PLOT_IDS:
                fn = PLOTS[[k for k in PLOTS if k.startswith(pid + "_")][0]]
                item = {"id": pid, "light": None, "dark": None, "alt": alt_text(pid, D), "error": False}
                try:
                    for tname, T in THEMES.items():
                        fig = None
                        try:
                            fig = fn(D, T, "en", title=False)
                            if fig is None:  # sem dado para este grafico
                                break
                            item[tname] = png_b64(fig, T)
                        finally:
                            if fig is not None:
                                plt.close(fig)
                except Exception:
                    item.update(light=None, dark=None, error=True)
                out.append(item)
        finally:
            plt.close("all")
    _CACHE[key] = out
    while len(_CACHE) > 4:
        _CACHE.popitem(last=False)
    return out


def summary(records, total: int | None = None) -> dict:
    """KPIs do cabecalho. `total` = numero real de mensagens da sessao (os registros guardados sao limitados).
    Gini: o do servidor (ultimo valor) ou, sem ele, o calculado aqui sobre as palavras (como no grafico P5)."""
    D = Data(list(records or []))
    g = D.sig("GINI", D.all)
    fin = g[np.isfinite(g)] if len(g) else g
    gv = float(fin[-1]) if len(fin) else None
    if gv is None and D.all:
        try:
            _, wc = counts_by_person(D)
            vals = [wc.get(p, 0) for p in D.persons]
            gv = float(gini(vals)) if sum(vals) > 0 else None
        except Exception:
            gv = None
    return {"messages": max(D.n_total, int(total or 0)), "people": len(D.persons) if D.n_total else 0,
            "interventions": sum(1 for r in D.all if D.intervened(r)), "gini": gv, "words": sum(D.words(D.all))}


def cached(records):
    """Resultado ja renderizado para estes registros (None se nao houver); nunca renderiza."""
    key = records_key(records)
    return _CACHE.get(key)
