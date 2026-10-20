"""Internacionalizacao: catalogos em locales/<lang>.json (chaves estaveis, placeholders nomeados {x}).

PT-BR e a fonte da verdade. Fallback: idioma pedido -> en -> pt -> a propria chave.
Os textos dos catalogos sao texto simples; so `rich()` aceita **negrito** e `codigo` (depois de escapar o HTML).
"""
from __future__ import annotations

import html
import json
import re
import string
from pathlib import Path
from typing import Any

LANGS = ["pt", "en", "es", "nl", "de", "zh"]
LANG_LABELS = {"pt": "PT", "en": "EN", "es": "ES", "nl": "NL", "de": "DE", "zh": "中文"}  # no proprio idioma
HTML_LANG = {"pt": "pt-BR", "en": "en", "es": "es", "nl": "nl", "de": "de", "zh": "zh-Hans"}
DEFAULT = "pt"
LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# marcadores "a confirmar" em todos os idiomas (so ganham destaque visual; o texto nunca e alterado)
TBC_RE = re.compile(r"(\[(?:a confirmar|to be confirmed|por confirmar|te bevestigen|zu bestätigen|待确认)[^\]]*\])")

_CACHE: dict[str, dict[str, str]] = {}


def set_locales_dir(path: str | Path | None) -> None:
    """Troca a pasta dos catalogos (testes) e limpa o cache."""
    global LOCALES_DIR
    LOCALES_DIR = Path(path) if path else Path(__file__).resolve().parent / "locales"
    _CACHE.clear()


def catalog(lang: str) -> dict[str, str]:
    if lang not in _CACHE:
        try:
            data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
            _CACHE[lang] = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError):
            _CACHE[lang] = {}
    return _CACHE[lang]


def norm_lang(value: Any) -> str:
    """'pt-BR' -> 'pt', 'zh-CN' -> 'zh'...; nao suportado -> pt."""
    v = str(value or "").strip().lower().replace("_", "-")
    base = v.split("-")[0]
    return base if base in LANGS else DEFAULT


def lookup(key: str, lang: str = DEFAULT) -> str | None:
    for lg in (lang, "en", DEFAULT):
        s = catalog(lg).get(key)
        if isinstance(s, str) and (s != "" or lg == lang):
            return s
    return None


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def t(key: str, lang: str = DEFAULT, **fmt: Any) -> str:
    """Texto traduzido. Chave ausente em todos os catalogos -> a propria chave."""
    s = lookup(key, lang)
    if s is None:
        return key
    if fmt:
        try:
            return string.Formatter().vformat(s, (), _Safe({k: resolve(v, lang) for k, v in fmt.items()}))
        except (ValueError, IndexError, KeyError, AttributeError):
            return s
    return s


def resolve(v: Any, lang: str) -> Any:
    """Parametros que sao Msg/ClairError (ou qualquer objeto com .text(lang)) sao traduzidos junto."""
    fn = getattr(v, "text", None)
    return fn(lang) if callable(fn) else v


class Msg:
    """Mensagem adiada: guarda chave e parametros e so vira texto ao renderizar (permite trocar o idioma depois)."""

    def __init__(self, key: str, **params: Any):
        self.key, self.params = key, params

    def text(self, lang: str = DEFAULT) -> str:
        return t(self.key, lang, **self.params)

    def __str__(self) -> str:
        return self.text(DEFAULT)


def txt(v: Any, lang: str) -> str:
    """str | Msg | ClairError | None -> texto no idioma."""
    if v is None:
        return ""
    return str(resolve(v, lang))


_RICH_B = re.compile(r"\*\*(.+?)\*\*")
_RICH_C = re.compile(r"`([^`]+)`")


def rich(text: str) -> str:
    """Escapa o HTML e so entao converte **negrito** e `codigo` em tags."""
    s = html.escape(text)
    s = _RICH_B.sub(r"<b>\1</b>", s)
    return _RICH_C.sub(r"<code>\1</code>", s)


def placeholders(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def join_and(items: list[str], lang: str) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return t("list.and2", lang, a=", ".join(items[:-1]), b=items[-1])
