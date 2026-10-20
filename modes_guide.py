"""Guia de modos: le modes_help.json em tempo de execucao e renderiza HTML escapado.

Os textos NAO ficam no codigo: vem do arquivo (rascunho do orquestrador, a confirmar pelo autor).
Marcadores "[a confirmar ...]" sao preservados literalmente (so ganham destaque visual).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import i18n
from i18n import Msg

DEFAULT_PATH = Path(__file__).resolve().parent / "modes_help.json"
NO_HELP = i18n.t("guide.no_help", "pt")
_TBC = i18n.TBC_RE


def help_path(lang: str = "pt") -> Path:
    """modes_help.<lang>.json (mesmo esquema); sem o arquivo, usa o modes_help.json (PT)."""
    if lang and lang != "pt":
        p = DEFAULT_PATH.with_name(f"modes_help.{lang}.json")
        if p.exists():
            return p
    return DEFAULT_PATH


def load_modes_help(path: str | Path | None = None, lang: str = "pt") -> tuple[dict | None, Msg | None]:
    """Devolve (dados, aviso). Arquivo ausente/invalido -> (None, Msg curta). Sem `path`, escolhe o arquivo do idioma."""
    p = Path(path) if path else help_path(lang)
    name = p.name
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, Msg("guide.file_missing", file=name)
    except (OSError, ValueError):
        return None, Msg("guide.file_invalid", file=name)
    if not isinstance(data, dict) or not isinstance(data.get("modos"), list):
        return None, Msg("guide.file_structure", file=name)
    data["modos"] = [m for m in data["modos"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
    return data, None


def mode_labels(data: dict | None) -> dict[str, str]:
    """id -> rotulo do JSON (ex.: apt-base -> 'apt-base (padrão)'), so para exibicao."""
    out: dict[str, str] = {}
    for m in (data or {}).get("modos", []):
        if isinstance(m.get("rotulo"), str):
            out[m["id"]] = m["rotulo"]
    return out


def mode_languages(data: dict | None, mode: str | None) -> list[str]:
    for m in (data or {}).get("modos", []):
        if m.get("id") == mode and isinstance(m.get("idiomas"), list):
            return [str(x) for x in m["idiomas"] if isinstance(x, (str, int))]
    return []


def _t(value: Any) -> str:
    """Escapa e destaca [a confirmar ...] sem mudar o texto."""
    return _TBC.sub(r'<em class="tbc">\1</em>', html.escape(str(value)))


def _p(label: str, value: Any) -> str:  # label ja traduzido
    if not isinstance(value, str) or not value.strip():
        return ""
    return f"<p><b>{html.escape(label)}:</b> {_t(value)}</p>"


def _ul(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return ""
    return "<ul>" + "".join(f"<li>{_t(i)}</li>" for i in items) + "</ul>"


def _card(m: dict, current: str | None, lang: str = "pt") -> str:
    cur = m.get("id") == current
    label = m.get("rotulo") if isinstance(m.get("rotulo"), str) else m["id"]
    badge = f'<span class="cl-badge cur">{html.escape(i18n.t("guide.in_use", lang))}</span>' if cur else ""
    parts = [f'<div class="cl-mode{" cur" if cur else ""}"><h3>{html.escape(label)}{badge}</h3>']
    if isinstance(m.get("em_uma_frase"), str):
        parts.append(f"<p>{_t(m['em_uma_frase'])}</p>")
    if m.get("indicado_para"):
        parts.append(f"<p><b>{html.escape(i18n.t('guide.suitable', lang))}</b></p>" + _ul(m["indicado_para"]))
    det = [_p(i18n.t("guide.name", lang), m.get("o_nome")), _p(i18n.t("guide.set", lang), m.get("o_conjunto")),
           _p(i18n.t("guide.how", lang), m.get("como_age"))]
    jog = m.get("jogadas")
    if isinstance(jog, list) and jog:
        lis = []
        for j in jog:
            if not isinstance(j, dict):
                continue
            ex = f' <span class="ex">{html.escape(i18n.t("guide.example", lang))} {_t(j["exemplo"])}</span>' if j.get("exemplo") else ""
            lis.append(f"<li>{_t(j.get('nome', ''))}{ex}</li>")
        det.append(f"<p><b>{html.escape(i18n.t('guide.moves', lang, n=len(lis)))}</b></p><ul>{''.join(lis)}</ul>")
    if m.get("cuidados"):
        det.append(f"<p><b>{html.escape(i18n.t('guide.cautions', lang))}</b></p>" + _ul(m["cuidados"]))
    if isinstance(m.get("idiomas"), list) and m["idiomas"]:
        det.append(f"<p><b>{html.escape(i18n.t('guide.languages', lang))}</b> " + ", ".join(html.escape(str(i)) for i in m["idiomas"]) + "</p>")
    det_html = "".join(d for d in det if d)
    if det_html:
        parts.append(f"<details><summary>{html.escape(i18n.t('guide.details', lang))}</summary>{det_html}</details>")
    parts.append("</div>")
    return "".join(parts)


def _table(comp: Any) -> str:
    if not isinstance(comp, dict) or not isinstance(comp.get("colunas"), list) or not isinstance(comp.get("linhas"), list):
        return ""
    head = "".join(f"<th>{_t(c)}</th>" for c in comp["colunas"])
    rows = "".join("<tr>" + "".join(f"<td>{_t(c)}</td>" for c in r) + "</tr>"
                   for r in comp["linhas"] if isinstance(r, list))
    return f'<div class="cl-tscroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'


def render_guide(data: dict | None, warn: Any, current: str | None = None, lang: str = "pt") -> str:
    """HTML do corpo do acordeao 'Qual modo escolher?'."""
    if data is None:
        return (f'<div class="cl-guide"><p class="cl-note">'
                f'{html.escape(i18n.t("guide.unavailable", lang, warn=i18n.txt(warn, lang)))}</p></div>')
    out = ['<div class="cl-guide">']
    if isinstance(data.get("_status"), str):
        out.append(f'<p class="cl-note warn">{_t(data["_status"])}</p>')
    geral = data.get("geral")
    if isinstance(geral, dict) and geral.get("texto"):
        out.append(f'<div class="gen"><h3>{_t(geral.get("titulo", ""))}</h3><p>{_t(geral["texto"])}</p></div>')
    ids = [m["id"] for m in data["modos"]]
    out.append('<div class="cl-modes">' + "".join(_card(m, current, lang) for m in data["modos"]) + "</div>")
    if current and current not in ids:
        out.append(f'<p class="cl-note">{html.escape(current)}: {html.escape(i18n.t("guide.no_help", lang))}</p>')
    out.append(_table(data.get("comparativo")))
    if isinstance(data.get("legenda_placeholders"), str):
        out.append(f'<p class="cl-note" style="margin-top:12px">{_t(data["legenda_placeholders"])}</p>')
    inc = data.get("incertezas")
    if isinstance(inc, list) and inc:
        out.append(f'<details class="src"><summary>{html.escape(i18n.t("guide.sources", lang))}</summary>' + _ul(inc) + "</details>")
    out.append("</div>")
    return "".join(out)
