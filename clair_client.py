"""Cliente HTTP puro (sem Gradio) para a API do Clair.

Regras de seguranca: o token so e enviado no header `access_token` e nunca
aparece em mensagens de erro, reprs ou logs.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

import i18n

DEFAULT_URL = "http://clair:8000"
TIMEOUT_MESSAGE = 60  # /message pode ser lento na primeira chamada
TIMEOUT_DEFAULT = 20

MAX_LEN_ID = 50
MAX_LEN_LANGUAGE = 5
MAX_LEN_KEYWORD = 200
DEFAULT_MAX_MESSAGE_CHARS = 2000  # configuravel: CLAIR_MAX_MESSAGE_CHARS ou ClairClient(max_message_chars=...)

# Mensagens (fonte: locales/pt.json). Rede: sem URL e sem detalhes da excecao (podem expor host interno).
TOKEN_HINT = i18n.t("err.token", "pt")
NO_AGENT_HINT = i18n.t("err.no_agent", "pt")
OFFLINE_HINT = i18n.t("err.offline", "pt")
TIMEOUT_HINT = i18n.t("err.timeout", "pt")


class ClairError(Exception):
    """Erro ja traduzido para o usuario. `message` e pt-BR; `text(lang)` devolve o mesmo erro em outro idioma.
    `kind` ajuda a UI a decidir o tratamento."""

    def __init__(self, message: str | None = None, kind: str = "error", status: int | None = None,
                 key: str | None = None, **params: Any):
        self.key, self.params = key, params
        if key:
            message = i18n.t(key, "pt", **params).strip()
        super().__init__(message or "")
        self.message = message or ""
        self.kind = kind  # auth | no_agent | timeout | connection | validation | http | config
        self.status = status

    def text(self, lang: str = "pt") -> str:
        return i18n.t(self.key, lang, **self.params).strip() if self.key else self.message

    def __str__(self) -> str:
        return self.message


@dataclass
class Intervention:
    text: str | None  # texto a mostrar como balao; None se so houver JSON bruto
    raw: Any  # valor original (para bloco recolhivel quando text e None)
    key: str  # chave estavel para deduplicacao


def _check_len(value: str, limit: int, label: str, segment: bool = False) -> str:
    """Valida texto obrigatorio. `segment=True`: vai num segmento de caminho da URL, entao "." e ".." (que o
    cliente HTTP resolveria como diretorio) sao recusados; "/" ja e codificado por _seg."""
    if not isinstance(value, str) or not value.strip():
        raise ClairError(kind="validation", key="err.required", label=label)
    value = value.strip()
    if segment and (value in (".", "..") or set(value) <= {"."}):
        raise ClairError(kind="validation", key="err.segment", label=label)
    if len(value) > limit:
        raise ClairError(kind="validation", key="err.maxlen", label=label, limit=limit)
    return value


def _seg(value: str) -> str:
    return quote(value, safe="")


def _detail_text(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return json.dumps(detail, ensure_ascii=False)[:500]
    return ""


def build_config_body(learning_space: str, is_active: bool, mode: str, language: str,
                      keywords: list[str] | None = None, blacklist: list[str] | None = None) -> dict:
    """JSON exato do POST /configuration (AgentConfiguration), ja validado.

    - learning_space: str, 1..50 caracteres, sem espacos nas pontas
    - is_active: bool
    - keywords: lista de str (cada uma <= 200), sem vazios e sem duplicadas exatas (ordem preservada)
    - mode / language (<= 5): omitidos se vazios; language e normalizado so por strip
    - blacklist: SEMPRE enviada; None vira [] (padrao, nada a apagar); se lista, cada item <= 50
    """
    ls = _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True)
    language = (language or "").strip()
    if len(language) > MAX_LEN_LANGUAGE:
        raise ClairError(kind="validation", key="err.language_max", limit=MAX_LEN_LANGUAGE)
    kws: list[str] = []
    for k in keywords or []:
        k = str(k).strip() if k is not None else ""
        if k and k not in kws:
            kws.append(k)
    blacklist = [str(b).strip() for b in (blacklist or []) if b and str(b).strip()]
    if any(len(k) > MAX_LEN_KEYWORD for k in kws):
        raise ClairError(kind="validation", key="err.keyword_max", limit=MAX_LEN_KEYWORD)
    if any(len(b) > MAX_LEN_ID for b in blacklist):
        raise ClairError(kind="validation", key="err.blacklist_max", limit=MAX_LEN_ID)
    body: dict[str, Any] = {"learning_space": ls, "is_active": bool(is_active), "keywords": kws,
                            "blacklist": blacklist}
    # mode/language vazios sao omitidos (string vazia poderia ser rejeitada como valor invalido)
    if (mode or "").strip():
        body["mode"] = mode.strip()
    if language:
        body["language"] = language
    return body


class ClairClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, max_message_chars: int | None = None):
        url = (base_url or DEFAULT_URL).strip()
        if "://" not in url:  # ex.: host de um tunel colado sem esquema
            url = "https://" + url
        self.base_url = url.rstrip("/")
        self._token = (token or "").strip()  # .env com CRLF/espaco geraria header invalido
        if max_message_chars is None:
            try:
                max_message_chars = int(os.environ.get("CLAIR_MAX_MESSAGE_CHARS") or DEFAULT_MAX_MESSAGE_CHARS)
            except ValueError:
                max_message_chars = DEFAULT_MAX_MESSAGE_CHARS
        self.max_message_chars = max(1, int(max_message_chars))

    @classmethod
    def from_env(cls) -> "ClairClient":
        return cls(os.environ.get("CLAIR_URL") or DEFAULT_URL, os.environ.get("CLAIR_TOKEN") or "")

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def __repr__(self) -> str:
        return f"ClairClient(base_url={self.base_url!r}, token={'***' if self._token else None})"

    # ------------------------------------------------------------------ nucleo
    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json_body: Any = None, timeout: float = TIMEOUT_DEFAULT) -> Any:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["access_token"] = self._token
        try:
            # Sem redirecionamentos (o header access_token nao pode seguir para outro host) e sem proxies/netrc do ambiente.
            with requests.Session() as sess:
                sess.trust_env = False
                resp = sess.request(method, self.base_url + path, params=params, json=json_body, headers=headers,
                                    timeout=timeout, allow_redirects=False)
        except requests.exceptions.Timeout:
            raise ClairError(kind="timeout", key="err.timeout") from None
        except requests.exceptions.RequestException:
            raise ClairError(kind="connection", key="err.offline") from None

        status = resp.status_code
        if 300 <= status < 400:  # redirecionamento nao e seguido: tratado como erro HTTP
            raise ClairError(kind="http", status=status, key="err.http", status_code=status, detail="")
        if status >= 400:
            detail = self._scrub(_detail_text(resp))
            if status in (401, 403):
                raise ClairError(kind="auth", status=status, key="err.token")
            if status == 404 and "no active agent" in detail.lower():
                raise ClairError(kind="no_agent", status=status, key="err.no_agent")
            if status == 422:
                raise ClairError(kind="validation", status=status, key="err.rejected_422", detail=detail)
            raise ClairError(kind="http", status=status, key="err.http", status_code=status, detail=detail)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return self._scrub(resp.text)

    # --------------------------------------------------------------- endpoints
    def root(self) -> Any:
        return self._request("GET", "/")

    def modes(self) -> Any:
        return self._request("GET", "/modes")

    def languages(self, mode: str) -> Any:
        return self._request("GET", f"/languages/{_seg(mode)}")

    def talk_moves(self, mode: str, language: str) -> Any:
        return self._request("GET", f"/talk_moves/{_seg(mode)}/{_seg(language)}")

    def configure(self, learning_space: str, is_active: bool, mode: str, language: str,
                  keywords: list[str] | None = None, blacklist: list[str] | None = None) -> Any:
        """A chave `blacklist` e sempre enviada: `None` vira `[]` (so use None se nao houver lista a preservar)."""
        body = build_config_body(learning_space, is_active, mode, language, keywords, blacklist)
        return self._request("POST", "/configuration", json_body=body)

    def get_configuration(self, learning_space: str) -> Any:
        ls = _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True)
        return self._request("GET", f"/configuration/{_seg(ls)}")

    def blacklist_group(self, learning_space: str, group: str) -> Any:
        ls = _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True)
        g = _check_len(group, MAX_LEN_ID, "group", segment=True)
        return self._request("POST", f"/blacklist/{_seg(ls)}/{_seg(g)}")

    def remove_blacklist_group(self, learning_space: str, group: str) -> Any:
        ls = _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True)
        g = _check_len(group, MAX_LEN_ID, "group", segment=True)
        return self._request("POST", f"/blacklist/{_seg(ls)}/{_seg(g)}/remove")

    def list_blacklist_groups(self) -> Any:
        return self._request("GET", "/blacklist")

    def blacklist_user(self, username: str) -> Any:
        u = _check_len(username, MAX_LEN_ID, "username", segment=True)
        return self._request("POST", f"/blacklist-user/{_seg(u)}")

    def remove_blacklist_user(self, username: str) -> Any:
        u = _check_len(username, MAX_LEN_ID, "username", segment=True)
        return self._request("POST", f"/blacklist-user/{_seg(u)}/remove")

    def list_blacklist_users(self) -> Any:
        return self._request("GET", "/blacklist-user")

    def send_message(self, learning_space: str, group: str, username: str, text: str,
                     timestamp: int | None = None, retrieve_details: bool = True,
                     save: bool = True) -> Any:
        body = {
            "learning_space": _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True),
            "group": _check_len(group, MAX_LEN_ID, "group", segment=True),
            "username": _check_len(username, MAX_LEN_ID, "username", segment=True),
            "text": _check_len(text, self.max_message_chars, "text"),
            "timestamp": int(timestamp if timestamp is not None else time.time() * 1000),
        }
        params = {"retrieve_details": str(bool(retrieve_details)).lower(),
                  "save": str(bool(save)).lower()}
        return self._request("POST", "/message", params=params, json_body=body,
                             timeout=TIMEOUT_MESSAGE)

    def pull(self, learning_space: str, group: str, timestamp: int | None = None) -> Any:
        ls = _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True)
        g = _check_len(group, MAX_LEN_ID, "group", segment=True)
        # O parametro `timestamp` NAO e enviado: o servidor (v0.4.x) repassa o inteiro direto a
        # update_time_since(), que subtrai um pd.Timestamp e quebra com HTTP 500 ("int - Timestamp").
        # Sem o parametro, o servidor usa pd.Timestamp.now(tz='utc'), que funciona. `timestamp` fica na
        # assinatura so por compatibilidade e e ignorado.
        return self._request("GET", f"/pull/{_seg(ls)}/{_seg(g)}", params=None)

    def analytics(self, learning_space: str) -> Any:
        ls = _check_len(learning_space, MAX_LEN_ID, "learning_space", segment=True)
        return self._request("GET", f"/analytics/{_seg(ls)}")


# ------------------------------------------------------- tratamento defensivo
def _stable_key(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _is_empty(v: Any) -> bool:
    """None, "", {}, [] ou dict/lista cujos valores sao todos vazios (ex.: {"message": null})."""
    if v is None or v == "" or v == [] or v == {}:
        return True
    if isinstance(v, dict):
        return all(_is_empty(x) for x in v.values())
    if isinstance(v, list):
        return all(_is_empty(x) for x in v)
    return False


def _one_intervention(item: Any) -> Intervention | None:
    if _is_empty(item):
        return None
    if isinstance(item, str):
        return Intervention(item, item, _stable_key(item))
    if isinstance(item, dict):
        # resposta de /message ou /pull pode embrulhar a intervencao
        inner = item.get("agent_intervention") if "agent_intervention" in item else item
        if inner is not item:
            return _one_intervention(inner) if inner is not None else None
        for k in ("message", "text"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                return Intervention(v, item, _stable_key(item))
        return Intervention(None, item, _stable_key(item))
    return Intervention(None, item, _stable_key(item))


def extract_interventions(payload: Any) -> list[Intervention]:
    """Aceita null, string, objeto ou lista e devolve zero ou mais intervencoes."""
    if payload is None:
        return []
    items = payload if isinstance(payload, list) else [payload]
    out = []
    for it in items:
        iv = _one_intervention(it)
        if iv is not None:
            out.append(iv)
    return out
