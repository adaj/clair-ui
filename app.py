"""Interface Gradio (v2) para a API do Clair.

Configuracao somente por variaveis de ambiente: CLAIR_URL e CLAIR_TOKEN.
O token nunca e exibido na UI, em logs ou em mensagens de erro.
Porta/host do servidor web: GRADIO_SERVER_PORT ou PORT (padrao 7860); GRADIO_SERVER_NAME (padrao 0.0.0.0).
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

import gradio as gr

import i18n
import modes_guide
import analytics_adapter
import plots_mpl
import sim_bridge
import ui_style
from clair_client import ClairClient, ClairError, build_config_body, extract_interventions
from i18n import Msg, t
from ui_style import HELP, help_html, help_texts, system_avatar

N_PEOPLE = 6
PEOPLE = [f"Pessoa{i}" for i in range(1, N_PEOPLE + 1)]
DEFAULT_LS = "teste01"
DEFAULT_GROUP = "g1"
DEFAULT_PULL_SECONDS = 30
DEFAULT_MODE = "apt-base"
_MODE_LABEL_RE = re.compile(r"^apt-base \(.*\)$")
MAX_HISTORY = 200  # itens do historico do chat guardados por sessao
MAX_RECORDS = 200  # registros (dialogue state) por sessao
DEDUPE_WINDOW_MS = 120_000  # a MESMA intervencao so e descartada se ja apareceu ha menos que isto (ex.: veio em /message e volta no /pull)
CONCURRENCY = 8  # eventos simultaneos (visitantes) ; dentro de uma sessao os envios sao serializados por lock

STATE_COLS = ["L1_DOM", "L1_COO", "L1_OFF", "L2C_IN", "L2C_AR", "L2C_AI", "L2C_AM", "L2C_NOS",
              "L3_IP", "L3_CU", "L3_NV", "L3_NE", "L4_SS", "TSIM", "TACC", "TIME", "PACE"]
def live_headers(lang: str = "pt") -> list[str]:
    return [t("live.h.time", lang), t("live.h.person", lang), t("live.h.text", lang), "intent", "selected_move"] + STATE_COLS + ["fuzzy_output"]


LIVE_HEADERS = live_headers("pt")

SYSTEM_AVATAR = system_avatar()

_BOLT = ('<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
         '<path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>')


# --------------------------------------------------------------- renderizacao
def _fmt_time(ms: int | None) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _tech_html(tech: Any, lang: str = "pt") -> str:
    if not isinstance(tech, dict):
        return ""
    one = f"intent: {_esc(_cell(tech.get('intent')) or '—')} · selected_move: {_esc(_cell(tech.get('selected_move')) or '—')}"
    pretty = html.escape(json.dumps(tech, ensure_ascii=False, indent=2, default=str))
    return (f'<div class="cl-tech"><div class="one">{one}</div>'
            f'<details><summary>{_esc(t("chat.raw_json", lang))}</summary><pre>{pretty}</pre></details></div>')


def _raw_html(raw: Any, lang: str = "pt") -> str:
    if raw is None:
        return ""
    pretty = html.escape(json.dumps(raw, ensure_ascii=False, indent=2, default=str))
    return (f'<div class="cl-tech"><details><summary>{_esc(t("chat.view_raw", lang))}</summary>'
            f'<pre>{pretty}</pre></details></div>')


def _msg_row(m: dict, show_tech: bool, lang: str = "pt") -> str:
    kind = m.get("kind", "pessoa")
    who = m.get("who") or ""
    # textos de sistema (ex.: intervencao nao reconhecida) guardam so a chave e sao renderizados no idioma atual;
    # o texto das pessoas e da Clair nunca e traduzido
    text = m.get("text") if m.get("text") is not None else (t(m["text_key"], lang) if m.get("text_key") else "")
    body = html.escape(text or "").replace("\n", "<br>")
    time_s = _fmt_time(m.get("ts"))
    meta = f'<div class="cl-meta"><b>{_esc("Clair" if kind == "clair" else who)}</b>' + (f"<time>{time_s}</time>" if time_s else "") + "</div>"
    if kind == "clair":
        pro = bool(m.get("proactive"))
        tag = f'<div class="cl-protag">{_BOLT}{_esc(t("chat.pro_tag", lang))}</div>' if pro else ""
        extra = _raw_html(m.get("raw"), lang)
        return (f'<div class="cl-msg clair{" pro" if pro else ""}"><span class="cl-clairimg" aria-hidden="true"></span>'
                f'<div class="cl-bubble">{tag}{meta}<div class="cl-txt">{body}</div>{extra}</div></div>')
    if kind == "info":
        avatar = None
    else:
        pi = m.get("pi")
        if pi is None and who in PEOPLE:
            pi = PEOPLE.index(who)
        av = m.get("av")
        if av is None and pi is not None:
            av = ui_style.DEFAULT_PERSON_AVATARS[int(pi) % N_PEOPLE]
        avatar = f'<span class="cl-avimg cl-av{int(av) + 1}" aria-hidden="true"></span>' if av is not None else None
    tech = _tech_html(m.get("tech"), lang) if show_tech else ""
    avatar = avatar or f'<img src="{SYSTEM_AVATAR}" alt="" width="34" height="34" class="cl-avimg"/>'
    return (f'<div class="cl-msg">{avatar}'
            f'<div class="cl-bubble">{meta}<div class="cl-txt">{body}</div>{tech}</div></div>')


def typing_html(names: list[str] | None, lang: str = "pt") -> str:
    """Indicador "X esta digitando..." (simulacao)."""
    if not names:
        return ""
    who = i18n.join_and([str(n) for n in names], lang)
    key = "sim.typing_many" if len(names) > 1 else "sim.typing"
    return (f'<div class="cl-typing" role="status">{_esc(t(key, lang, names=who))}'
            '<span class="cl-dots" aria-hidden="true"><i></i><i></i><i></i></span></div>')


def render_chat(history: list[dict], show_tech: bool = False, pending: dict | None = None, lang: str = "pt",
                typing: list[str] | None = None) -> str:
    rows = [_msg_row(m, show_tech, lang) for m in history]
    if pending:
        rows.append(_msg_row(pending, False, lang))
    if typing:
        rows.append(typing_html(typing, lang))
    if not rows:
        return ('<div class="cl-hist" role="log" aria-live="polite"><div class="cl-empty">'
                f'<span class="cl-clairimg" aria-hidden="true"></span><h2>{_esc(t("chat.empty.title", lang))}</h2>'
                f'<p>{_esc(t("chat.empty.body", lang))}</p></div></div>')
    return '<div class="cl-hist" role="log" aria-live="polite">' + "".join(rows) + "</div>"


_ICONS = {  # icones de 16 px (stroke), sempre acompanhados de texto
    "ready": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 3 3 5-6"/>',
    "busy": '<path d="M12 3a9 9 0 1 0 9 9" class="spin"/>',
    "dirty": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "unconfigured": '<path d="M12 3 2 20h20z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
    "mismatch": '<path d="M12 3 2 20h20z"/><path d="M12 10v4"/><path d="M12 17.5v.01"/>',
    "inactive": '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
    "offline": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6"/><path d="m15 9-6 6"/>',
    "auth": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "error": '<circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16.5v.01"/>',
}
STATE_KINDS = ["ready", "busy", "dirty", "unconfigured", "mismatch", "inactive", "offline", "auth", "error"]
STATE_TONE = {"ready": "ok", "busy": "busy", "dirty": "warn", "unconfigured": "warn", "mismatch": "warn",
              "inactive": "warn", "offline": "bad", "auth": "bad", "error": "bad"}


def _join_txt(v: Any, lang: str) -> str:
    if isinstance(v, (list, tuple)):
        return " ".join(x for x in (i18n.txt(i, lang).strip() for i in v) if x)
    return i18n.txt(v, lang)


def cfg_state_html(kind: str, hint: Any = "", warn: Any = "", label: Any = None, lang: str = "pt") -> str:
    """Indicador de estado da configuracao: icone + texto (nunca so cor). hint/warn/label: str | Msg | ClairError | lista."""
    kind = kind if kind in STATE_KINDS else "error"
    icon = (f'<svg class="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICONS[kind]}</svg>')
    hint_s, warn_s = _join_txt(hint, lang), _join_txt(warn, lang)
    h = f'<span class="hint">{_esc(hint_s)}</span>' if hint_s else ""
    w = f'<span class="hint warn">{_esc(warn_s)}</span>' if warn_s else ""
    label_s = _join_txt(label, lang) if label else t(f"state.{kind}", lang)
    return (f'<div class="cl-state {STATE_TONE[kind]}" data-state="{kind}" role="status">{icon}'
            f'<span class="txt"><b>{_esc(label_s)}</b>{h}{w}</span></div>')


def display_kind(state: dict | None) -> str:
    cfg = (state or {}).get("cfg")
    if (state or {}).get("offline"):
        return "offline"
    if not cfg:
        return "unconfigured"
    return "dirty" if cfg.get("dirty") else cfg.get("kind", "error")


def err_html(message: Any, lang: str = "pt") -> str:
    message = i18n.txt(message, lang)
    if not message:
        return ""
    return (f'<div class="cl-err" role="alert"><span>{_esc(message)}</span>'
            f'<button type="button" data-cl-close aria-label="{_esc(t("chat.close_warn", lang))}">×</button></div>')


def clairmark_html(busy: bool = False) -> str:
    return (f'<div class="cl-clairmark{" busy" if busy else ""}"><span class="cl-clairimg" aria-hidden="true"></span>'
            '<span>Clair</span></div>')


def brand_html(lang: str = "pt") -> str:
    return (f'<div class="cl-brandrow" data-htmllang="{i18n.HTML_LANG.get(lang, "pt-BR")}" '
            f'data-title="{_esc(t("app.title", lang))}"><span class="cl-clairimg cl-brand-av" aria-hidden="true"></span>'
            f'<span>Clair <small>{_esc(t("brand.sub", lang))}</small></span></div>')


def avatar_warn_html(clair_av: Any, person_avs: list, names: list, lang: str = "pt") -> str:
    """Aviso discreto (nao bloqueia) quando um avatar e escolhido por mais de um participante."""
    names = effective_names(list(names), lang)
    users: dict[int, list[str]] = {}
    n = ui_style.N_AVATARS
    for i in range(N_PEOPLE):
        k = ui_style._idx(person_avs[i] if i < len(person_avs) else None, ui_style.DEFAULT_PERSON_AVATARS[i])
        users.setdefault(k, []).append(names[i])
    # Clair e Pessoa podem compartilhar o avatar sem aviso (o 2 e o padrao de ambos); o aviso e so entre Pessoas
    parts = []
    for k in range(n):
        u = users.get(k, [])
        if len(u) > 1:
            parts.append(t("avwarn.part", lang, n=k + 1, who=i18n.join_and(u, lang)))
    if not parts:
        return ""
    return f'<p class="cl-note" role="status">{_esc(t("avwarn.text", lang, parts="; ".join(parts)))}</p>'


def lang_of(state: dict | None) -> str:
    lg = (state or {}).get("lang")
    return lg if lg in i18n.LANGS else i18n.DEFAULT


def chat_status_html(ls: str, grp: str, pull_on: bool, secs: Any, state: dict | None) -> str:
    state = state or {}
    lang = lang_of(state)
    kind = display_kind(state)
    live = t(f"chat.state.{kind}", lang)
    cls = {"ok": "", "busy": " off", "warn": " warn", "bad": " bad"}[STATE_TONE[kind]]
    try:
        s = int(max(5, float(secs or DEFAULT_PULL_SECONDS)))
    except (TypeError, ValueError):
        s = DEFAULT_PULL_SECONDS
    pull = i18n.rich(t("chat.status.pull_on", lang, s=s)) if pull_on else _esc(t("chat.status.pull_off", lang))
    note = i18n.txt(state.get("pull_note"), lang)
    note_html = f'<span class="note">{_esc(note)}</span>' if note else ""
    return (f'<div class="cl-statusline">{_esc(t("chat.status.ls", lang))} <b>{_esc(ls)}</b><span class="sep">·</span>'
            f'{_esc(t("chat.status.group", lang))} <b>{_esc(grp)}</b><span class="sep">·</span><span class="pull">{pull}</span><span class="sep">·</span>'
            f'<span class="live{cls}">{_esc(live)}</span>{note_html}</div>')


# ------------------------------------------------------------------ utilitarios
def _now_ms() -> int:
    return int(time.time() * 1000)


# Travas por sessao (a sessao e um dict compartilhado entre eventos simultaneos do mesmo visitante):
#   send  = exclusao de /message e /pull (um por vez por sessao);  mu = secao curta que confirma/limpa o estado.
_LOCKS_GUARD = threading.Lock()
_SESS_LOCKS: "OrderedDict[str, tuple[threading.Lock, threading.RLock]]" = OrderedDict()


def session_locks(state: dict) -> tuple:
    with _LOCKS_GUARD:
        sid = state.get("sid")
        if not sid or sid not in _SESS_LOCKS:
            sid = state["sid"] = secrets.token_hex(8)  # criado sob demanda: o valor inicial do gr.State e copiado por sessao
            _SESS_LOCKS[sid] = (threading.Lock(), threading.RLock())
            while len(_SESS_LOCKS) > 2000:
                _SESS_LOCKS.popitem(last=False)
        return _SESS_LOCKS[sid]


def new_session() -> dict:
    return {"history": [], "seen": [], "records": [], "n_total": 0, "last": None, "epoch": 0, "plots": None,
            "cfg": None, "offline": False, "pull_note": None, "lang": i18n.DEFAULT, "notice": None, "mode_codes": None}


def _lines(text: str | None) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def normalize_choices(obj: Any) -> list[str]:
    """Aceita lista de strings, lista de objetos ou dict (usa as chaves). Formato real nao observado."""
    if isinstance(obj, dict):
        return [str(k) for k in obj.keys()]
    out: list[str] = []
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, (str, int)):
                out.append(str(x))
            elif isinstance(x, dict):
                for key in ("code", "id", "name"):
                    if isinstance(x.get(key), (str, int)):
                        out.append(str(x[key]))
                        break
                else:
                    out.append(json.dumps(x, ensure_ascii=False))
    return out


def live_rows(records: list[dict]) -> list[list]:
    rows = []
    for r in reversed(records):
        ds = r.get("dialogue_state") or {}
        fz = r.get("fuzzy_output") or {}
        fz_txt = ", ".join(f"{k}={_cell(v)}" for k, v in fz.items()) if isinstance(fz, dict) else _cell(fz)
        rows.append([r["hora"], r["pessoa"], r["texto"], _cell(r.get("intent")), _cell(r.get("selected_move"))]
                    + [_cell(ds.get(c)) if isinstance(ds, dict) else "" for c in STATE_COLS] + [fz_txt])
    return rows


def default_names(lang: str = "pt") -> list[str]:
    """Nomes padrao dos participantes no idioma do site (Pessoa1.., Person1.., Persona1.., Persoon1.., 成员1..)."""
    return [t("people.default", lang, n=i) for i in range(1, N_PEOPLE + 1)]


def _all_default_names() -> set[str]:
    return {n for lg in i18n.LANGS for n in default_names(lg)}


def names_for_lang(names: list[Any], lang: str) -> list[str]:
    """Nomes efetivos: vazio ou ainda no padrao (de qualquer idioma) vira o padrao do idioma atual; nomes digitados ficam."""
    out = []
    dn, alld = default_names(lang), _all_default_names()
    for i in range(N_PEOPLE):
        n = str(names[i]).strip() if i < len(names) and names[i] is not None else ""
        out.append(dn[i] if (not n or n in alld) else n[:50])
    return out


def effective_names(names: list[Any], lang: str = "pt") -> list[str]:
    out = []
    dn = default_names(lang)
    for i in range(N_PEOPLE):
        n = str(names[i]).strip() if i < len(names) and names[i] is not None else ""
        out.append(n[:50] or dn[i])
    return out


def mode_label(lang: str = "pt") -> str:
    """Rotulo so de exibicao do modo padrao, no idioma do site (o valor enviado a API continua 'apt-base')."""
    return f"{DEFAULT_MODE} ({t('mode.default', lang)})"


def normalize_mode(mode: Any) -> str:
    """O rotulo 'apt-base (padrão)' (em qualquer idioma) e so exibicao: o valor da API e sempre 'apt-base'."""
    m = (str(mode) if mode is not None else "").strip()
    return DEFAULT_MODE if _MODE_LABEL_RE.match(m) else m


def mode_choices(codes: list[str], labels: dict[str, str] | None = None, lang: str = "pt") -> list[tuple[str, str]]:
    """(rotulo, valor). O rotulo do modo padrao e so exibicao; o valor continua 'apt-base'."""
    out = []
    for c in codes:
        label = mode_label(lang) if c == DEFAULT_MODE else c
        out.append((label, c))
    if DEFAULT_MODE not in codes:
        out.insert(0, (mode_label(lang), DEFAULT_MODE))
    return out


# --------------------------------------------------------------------- app
def build_app(client: ClairClient | None = None, modes_help_path: str | None = None) -> gr.Blocks:
    client = client or ClairClient.from_env()

    def call(fn, *a, **kw):
        """(valor, ClairError|None). Nunca propaga excecao nem vaza detalhes."""
        try:
            return fn(*a, **kw), None
        except ClairError as e:
            return None, e
        except Exception:  # nao vazar detalhes (podem conter dados sensiveis)
            return None, ClairError(kind="error", key="err.unexpected")

    def mark_net(state: dict, err: ClairError | None) -> None:
        """Atualiza o estado 'offline' a partir do resultado da ultima chamada."""
        state["offline"] = bool(err and err.kind in ("connection", "timeout"))

    def guide_html(mode, lang="pt"):
        data, warn = modes_guide.load_modes_help(modes_help_path, lang)
        return modes_guide.render_guide(data, warn, mode, lang)

    def help_labels(lang="pt"):
        data, _ = modes_guide.load_modes_help(modes_help_path, lang)
        return data

    # ---- Chat
    def person_of(who, names, lang="pt"):
        try:
            i = min(max(int(who), 0), N_PEOPLE - 1)
        except (TypeError, ValueError):
            i = 0
        return i, effective_names(list(names), lang)[i]

    def person_av(i, avs):
        return ui_style._idx(avs[i], ui_style.DEFAULT_PERSON_AVATARS[i])

    def on_send_pre(text, who, show_tech, state, *rest):
        """Mostra a mensagem da pessoa antes da chamada a API (sem alterar o estado)."""
        if not (text or "").strip():
            return gr.update(), gr.update()
        state = state or new_session()
        names, avs = rest[:N_PEOPLE], rest[N_PEOPLE:]
        i, name = person_of(who, names, lang_of(state))
        pending = {"who": name, "kind": "pessoa", "text": text.strip(), "ts": _now_ms(), "pi": i,
                   "av": person_av(i, avs)}
        return render_chat(state["history"], show_tech, pending=pending, lang=lang_of(state)), clairmark_html()

    def on_send(text, who, ls, grp, state, show_tech, pull_on, secs, *rest):
        state = state or new_session()
        send_lock, mu = session_locks(state)
        with send_lock:  # um envio por vez por sessao; outras sessoes seguem em paralelo
            return _on_send_locked(text, who, ls, grp, state, mu, show_tech, pull_on, secs, *rest)

    def _on_send_locked(text, who, ls, grp, state, mu, show_tech, pull_on, secs, *rest):
        lang = lang_of(state)
        names, avs = rest[:N_PEOPLE], rest[N_PEOPLE:]
        stat = lambda: chat_status_html(ls, grp, pull_on, secs, state)  # noqa: E731
        if not (text or "").strip():  # vazia: nao envia e nao acusa erro
            return (render_chat(state["history"], show_tech, lang=lang), state, gr.update(), gr.update(),
                    live_rows(state["records"]), state["last"], gr.update(), clairmark_html())
        i, name = person_of(who, names, lang)
        ts = _now_ms()
        epoch = state.get("epoch", 0)  # Limpar / Novo grupo / troca de grupo durante a chamada invalida o resultado
        resp, err = call(client.send_message, ls, grp, name, text, ts)
        with mu:
            if state.get("epoch", 0) != epoch:
                return (render_chat(state["history"], show_tech, lang=lang), state, "", err_html("", lang),
                        live_rows(state["records"]), state["last"], stat(), clairmark_html())
            mark_net(state, err)
            if err:
                return (render_chat(state["history"], show_tech, lang=lang), state, gr.update(value=text), err_html(err, lang),
                        live_rows(state["records"]), state["last"], stat(), clairmark_html())
            state["history"].append({"who": name, "kind": "pessoa", "text": text.strip(), "ts": ts, "pi": i,
                                     "av": person_av(i, avs),
                                     "tech": resp if isinstance(resp, dict) else None})
            state["last"] = resp
            state["group"] = (grp or "").strip()
            state["pull_note"] = None
            if isinstance(resp, dict):
                ivs = extract_interventions(resp.get("agent_intervention"))
                state["records"].append({"hora": _fmt_time(ts), "pessoa": name, "texto": text.strip()[:80],
                                         "full_text": text.strip(),
                                         "intent": resp.get("intent"), "selected_move": resp.get("selected_move"),
                                         "dialogue_state": resp.get("dialogue_state"),
                                         "fuzzy_output": resp.get("fuzzy_output"),
                                         "intervention": bool(ivs)})
                state["n_total"] = state.get("n_total", 0) + 1
                state["records"] = state["records"][-MAX_RECORDS:]
                for iv in ivs:
                    mark_seen(state, iv.key, ts)
                    state["history"].append(_iv_message(iv, proactive=False))
            state["history"] = state["history"][-MAX_HISTORY:]
            return (render_chat(state["history"], show_tech, lang=lang), state, "", err_html("", lang),
                    live_rows(state["records"]), state["last"], stat(), clairmark_html())

    def mark_seen(state, key, now):
        """Lembra a intervencao (chave estavel) por DEDUPE_WINDOW_MS; depois disso a mesma saudacao vale de novo."""
        sim_bridge.mark_seen(state["seen"], key, now, DEDUPE_WINDOW_MS)

    def is_seen(state, key, now):
        return sim_bridge.is_seen(state["seen"], key, now, DEDUPE_WINDOW_MS)

    def _iv_message(iv, proactive: bool) -> dict:
        base = {"who": "Clair", "kind": "clair", "ts": _now_ms(), "proactive": proactive}
        if iv.text is not None:
            return {**base, "text": iv.text}
        return {**base, "text": None, "text_key": "chat.iv_unknown", "raw": iv.raw}

    def on_tick(ls, grp, enabled, state, secs, show_tech):
        state = state or new_session()
        lang = lang_of(state)
        if not enabled or not (ls or "").strip() or not (grp or "").strip():
            return gr.update(), state, gr.update()
        if sim_bridge.REGISTRY.active(state.get("sid")):  # simulacao em andamento: a ponte faz o /pull
            return gr.update(), state, gr.update()
        cfg_kind = (state.get("cfg") or {}).get("kind")
        if cfg_kind in ("unconfigured", "auth", "inactive"):  # nao ha agente ativo (verificado): nao consulta o servidor
            return gr.update(), state, gr.update()
        send_lock, mu = session_locks(state)
        if not send_lock.acquire(blocking=False):  # ha um envio em andamento nesta sessao: pula este ciclo
            return gr.update(), state, gr.update()
        try:
            started = _now_ms()
            epoch = state.get("epoch", 0)
            payload, err = call(client.pull, ls, grp)
            with mu:
                if state.get("epoch", 0) != epoch:  # sessao limpa / grupo trocado durante a chamada
                    return gr.update(), state, gr.update()
                mark_net(state, err)
                if err:
                    # offline ja aparece como "Clair offline"; os demais erros viram uma nota discreta
                    state["pull_note"] = None if state["offline"] else Msg("chat.pull_failed", time=_fmt_time(started), msg=err)
                    return gr.update(), state, chat_status_html(ls, grp, enabled, secs, state)
                state["pull_note"] = None
                new = 0
                for iv in extract_interventions(payload):
                    if is_seen(state, iv.key, started):
                        continue
                    mark_seen(state, iv.key, started)
                    state["history"].append(_iv_message(iv, proactive=True))
                    state["group"] = (grp or "").strip()
                    new += 1
                state["history"] = state["history"][-MAX_HISTORY:]
                return ((render_chat(state["history"], show_tech, lang=lang) if new else gr.update()), state,
                        chat_status_html(ls, grp, enabled, secs, state))
        finally:
            send_lock.release()

    def on_rerender(state, show_tech):
        state = state or new_session()
        return render_chat(state["history"], show_tech, lang=lang_of(state))

    def _fresh_session(old):
        """Zera history/records/seen/last/pull_note NO MESMO dict (handlers em voo seguram o mesmo objeto) e incrementa
        `epoch`: quem estava em voo ve que mudou e descarta o resultado. Preserva cfg, idioma e o resto da configuracao."""
        _, mu = session_locks(old)
        sim_bridge.REGISTRY.close(old.get("sid"))  # Limpar / Novo grupo / troca de grupo encerram a simulacao
        with mu:
            keep = {k: old.get(k) for k in ("applied", "cfg", "lang", "notice", "mode_codes", "sid", "offline") if k in old}
            epoch = old.get("epoch", 0) + 1
            fresh = new_session()
            old.clear()
            old.update(fresh)
            old.update(keep)
            old["epoch"] = epoch
        return old

    def on_clear(state, show_tech, ls, grp, pull_on, secs):
        state = _fresh_session(state or new_session())
        return (render_chat([], show_tech, lang=lang_of(state)), state, err_html("", lang_of(state)), [], None,
                chat_status_html(ls, grp, pull_on, secs, state))

    def new_group_id(current):
        cur = (current or "").strip()
        while True:
            gid = "g-" + secrets.token_hex(secrets.choice((2, 3)))  # 4 ou 6 hex
            if gid != cur:
                return gid

    def on_new_group(current, state, show_tech, ls, pull_on, secs):
        gid = new_group_id(current)
        state = _fresh_session(state or new_session())
        return (gid, render_chat([], show_tech, lang=lang_of(state)), state, err_html("", lang_of(state)), [], None,
                chat_status_html(ls, gid, pull_on, secs, state))

    def on_group_commit(grp, state, show_tech, ls, pull_on, secs):
        """Grupo editado a mao (ao sair do campo / Enter): o historico mostrado era de outro grupo, entao limpa."""
        state = state or new_session()
        g = (grp or "").strip()
        prev = state.get("group")
        if prev is None or prev == g:
            return (gr.update(),) * 6
        state = _fresh_session(state)  # historico/leitura em voo eram do grupo anterior
        state["group"] = g
        return (render_chat([], show_tech, lang=lang_of(state)), state, err_html("", lang_of(state)), [], None,
                chat_status_html(ls, grp, pull_on, secs, state))

    def on_status(ls, grp, pull_on, secs, state):
        return chat_status_html(ls, grp, pull_on, secs, state or new_session())

    def on_timer_cfg(enabled, secs):
        try:
            secs = max(5, float(secs or DEFAULT_PULL_SECONDS))
        except (TypeError, ValueError):
            secs = DEFAULT_PULL_SECONDS
        return gr.Timer(value=secs, active=bool(enabled))

    def on_pull_toggle(enabled, secs):
        return gr.update(interactive=bool(enabled)), on_timer_cfg(enabled, secs)

    # ---- Quem fala / nomes / avatar
    def on_names(who, *rest):
        lang = lang_of(rest[N_PEOPLE] if len(rest) > N_PEOPLE else None)
        names = effective_names(list(rest[:N_PEOPLE]), lang)
        try:
            i = min(max(int(who), 0), N_PEOPLE - 1)
        except (TypeError, ValueError):
            i = 0
        return (gr.update(choices=[(n, str(k)) for k, n in enumerate(names)]),
                gr.update(placeholder=t("chat.placeholder", lang, name=names[i])))

    def on_who(who, *rest):
        lang = lang_of(rest[N_PEOPLE] if len(rest) > N_PEOPLE else None)
        _, name = person_of(who, rest[:N_PEOPLE], lang)
        return gr.update(placeholder=t("chat.placeholder", lang, name=name))

    def on_style(avatar, *avs):
        return ui_style.dyn_style(list(avs), avatar)

    def on_avwarn(avatar, *rest):
        lang = lang_of(rest[2 * N_PEOPLE] if len(rest) > 2 * N_PEOPLE else None)
        return avatar_warn_html(avatar, list(rest[:N_PEOPLE]), list(rest[N_PEOPLE:2 * N_PEOPLE]), lang)

    # ---- Configuracao
    def langs_for(mode):
        """(choices, aviso Msg|None). Se /languages falhar usa a lista do guia (rotulada como tal) para nao travar a demo."""
        if not (mode or "").strip():
            return [], None
        data, err = call(client.languages, mode)
        if not err:
            return normalize_choices(data), None
        fallback = modes_guide.mode_languages(help_labels(), mode)
        if fallback:
            return fallback, Msg("notice.langs_guide", err=err)
        return [], Msg("notice.langs_manual", err=err)

    def notice_html(state):
        n = (state or {}).get("notice")
        text = i18n.txt(n, lang_of(state)) if n else ""
        return f'<p class="cl-note warn" role="status">{_esc(text)}</p>' if text else ""

    def on_init(state):
        state = state or new_session()
        lang = lang_of(state)
        modes, err = call(client.modes)
        codes = normalize_choices(modes) if not err else []
        state["mode_codes"] = codes
        choices = mode_choices(codes, None, lang)
        value = DEFAULT_MODE
        langs, lwarn = langs_for(value)
        if err:
            state["notice"] = Msg("notice.modes_fail", err=err)
        else:
            state["notice"] = lwarn
        return (gr.update(choices=choices, value=value),
                gr.update(choices=langs, value=langs[0] if langs else None),
                notice_html(state), guide_html(value, lang), state)

    def on_mode_input(mode, state):
        state = state or new_session()
        langs, warn = langs_for(mode)
        state["notice"] = warn
        return gr.update(choices=langs, value=langs[0] if langs else None), notice_html(state), state

    def on_mode_change(mode, state):
        return guide_html(mode, lang_of(state))

    def hhmm():
        return datetime.now().strftime("%H:%M")

    def snapshot(ls, active, mode, lang, kws):
        """Valores da coluna esquerda que o POST /configuration usa (para detectar 'alteracoes nao salvas')."""
        seen: list[str] = []
        for k in _lines(kws):
            if k not in seen:
                seen.append(k)
        return ((ls or "").strip(), bool(active), normalize_mode(mode), (lang or "").strip().upper(), tuple(seen))

    def set_cfg(state, kind, hint, snap, warn=None):
        state["cfg"] = {"kind": kind, "hint": hint, "warn": warn, "snap": snap, "dirty": False, "at": hhmm()}
        state["offline"] = kind == "offline"

    def cfg_html(state):
        """HTML do indicador a partir do estado da sessao (no idioma da sessao)."""
        lang = lang_of(state)
        kind = display_kind(state)
        cfg = state.get("cfg") or {}
        if kind == "offline":
            return cfg_state_html("offline", cfg.get("hint") or Msg("hint.offline_ui"), lang=lang)
        if kind == "dirty":
            return cfg_state_html("dirty", Msg("hint.dirty"), lang=lang)
        if kind == "unconfigured" and not cfg:
            return cfg_state_html("unconfigured", Msg("hint.not_verified"), lang=lang)
        return cfg_state_html(kind, cfg.get("hint") or "", cfg.get("warn") or "", lang=lang)

    def net_kind(err):
        """ClairError -> (kind, hint Msg|ClairError) do indicador."""
        if err.kind in ("connection", "timeout"):
            return "offline", Msg("hint.offline_ui")
        if err.kind == "auth":
            return "auth", Msg("hint.auth")
        if err.kind == "no_agent" or err.status == 404:
            return "unconfigured", Msg("hint.no_cfg")
        return "error", err

    def verify_against_server(ls, active, mode, lang, kws, saved_now=False):
        """GET /configuration/{ls} e confronto com a tela. Nunca devolve 'ready' sem confirmar tudo."""
        snap = snapshot(ls, active, mode, lang, kws)
        cfg, err = call(client.get_configuration, ls)
        if err and err.status == 404:
            err, cfg = None, None
        if err:
            kind, hint = net_kind(err)
            if saved_now and kind in ("offline", "error"):
                hint = Msg("hint.saved_unconfirmed", hint=hint)
            return kind, hint, snap
        if not isinstance(cfg, dict):
            return "unconfigured", Msg("hint.none_returned"), snap
        s_active = cfg.get("is_active")
        s_mode = normalize_mode(cfg.get("mode") if isinstance(cfg.get("mode"), str) else "")
        s_lang = (cfg.get("language") if isinstance(cfg.get("language"), str) else "").strip()
        if s_active is not True:
            return "inactive", Msg("hint.inactive_server"), snap
        srv_kws = cfg.get("keywords")
        s_kws = tuple(dict.fromkeys(k.strip() for k in srv_kws if isinstance(k, str) and k.strip())) if isinstance(srv_kws, list) else ()
        want_mode, want_lang = snap[2], snap[3]
        if s_mode != want_mode or s_lang.upper() != want_lang:
            return ("mismatch", Msg("hint.mismatch", s_mode=s_mode or "—", s_lang=s_lang or "—",
                                    w_mode=want_mode or "—", w_lang=want_lang or "—"), snap)
        if s_kws != snap[4]:
            return "mismatch", Msg("hint.mismatch_kw", n_srv=len(s_kws), n_scr=len(snap[4])), snap
        if not snap[1]:
            return "inactive", Msg("hint.inactive_screen"), snap
        return "ready", Msg("hint.ready", time=hhmm(), mode=s_mode, cl_lang=s_lang), snap

    def on_busy(state=None, label_key="state.busy"):
        lang = lang_of(state)
        return cfg_state_html("busy", Msg("cfg.busy.wait"), label=Msg(label_key), lang=lang)

    def on_busy_save(state=None):
        return on_busy(state, "state.saving")

    def on_verify(ls, active, mode, lang, kws, state):
        state = state or new_session()
        kind, hint, snap = verify_against_server(ls, active, mode, lang, kws)
        set_cfg(state, kind, hint, snap)
        return cfg_html(state), state

    def on_verify_chat(ls, active, mode, lang, kws, state):
        """Ao entrar na aba Chat: se ha alteracoes nao salvas mantem o aviso (sem GET); senao confirma no servidor."""
        state = state or new_session()
        cfg = state.get("cfg")
        if cfg and cfg.get("snap") is not None and cfg["snap"] != snapshot(ls, active, mode, lang, kws):
            cfg["dirty"] = True
            return cfg_html(state), state
        return on_verify(ls, active, mode, lang, kws, state)

    def on_dirty(ls, active, mode, lang, kws, state):
        """Qualquer edicao (so por acao do usuario: evento .input) na coluna esquerda."""
        state = state or new_session()
        cfg = state.get("cfg")
        if cfg and cfg.get("snap") is not None:
            cfg["dirty"] = cfg["snap"] != snapshot(ls, active, mode, lang, kws)
        return cfg_html(state), state

    def on_cfg_load(ls, state):
        state = state or new_session()
        cfg, err = call(client.get_configuration, ls)
        mark_net(state, err)
        if err or not isinstance(cfg, dict):
            return (gr.update(),) * 4 + (cfg if not err else None, state)
        mode = cfg.get("mode") if isinstance(cfg.get("mode"), str) else None
        langs, _ = langs_for(mode)
        lang = cfg.get("language") if isinstance(cfg.get("language"), str) and cfg.get("language") else None
        if lang:  # compara sem diferenciar caixa: usa o codigo da lista se existir
            match = next((c for c in langs if c.upper() == lang.upper()), None)
            if match:
                lang = match
            else:
                langs = langs + [lang]
        kws = cfg.get("keywords")
        kw_lines = [k for k in kws if isinstance(k, str)] if isinstance(kws, list) else []
        return (gr.update(value=cfg.get("is_active") is True), gr.update(value=mode), gr.update(choices=langs, value=lang),
                "\n".join(kw_lines), cfg, state)

    def check_choices(mode, lang):
        """Valida modo e idioma contra /modes e /languages/{modo} ANTES de enviar.
        -> (problema Msg|None, lang_canonico, avisos [Msg], ClairError|None)"""
        warn: list[Msg] = []
        modes, merr = call(client.modes)
        if merr and merr.kind in ("connection", "timeout", "auth"):
            return None, lang, [], merr
        if not merr:
            codes = normalize_choices(modes)
            if codes and mode not in codes:
                return Msg("chk.mode_missing", mode=mode, codes=", ".join(codes)), lang, [], None
        else:
            warn.append(Msg("chk.warn_mode"))
        langs, lerr = call(client.languages, mode)
        if lerr and lerr.kind in ("connection", "timeout", "auth"):
            return None, lang, [], lerr
        if lerr:
            return None, lang, warn + [Msg("chk.warn_lang")], None
        codes = normalize_choices(langs)
        if not codes:
            return None, lang, warn, None
        if not lang:
            return Msg("chk.lang_choose", mode=mode, codes=", ".join(codes)), lang, [], None
        match = next((c for c in codes if c.upper() == lang.upper()), None)
        if match is None:
            return Msg("chk.lang_unavailable", cl_lang=lang, mode=mode, codes=", ".join(codes)), lang, [], None
        return None, match, warn, None

    def on_cfg_save(ls, active, mode, lang, kws, state):
        state = state or new_session()
        ui_lang = lang_of(state)
        mode = normalize_mode(mode)
        lang = (lang or "").strip()
        snap0 = snapshot(ls, active, mode, lang, kws)
        # 0) validacao local (limites do contrato) sem rede
        _, verr = call(build_config_body, ls, active, mode, lang, _lines(kws), None)
        if verr:
            return cfg_state_html("error", verr, label=Msg("state.fix"), lang=ui_lang), state
        # 1) modo e idioma contra o servidor
        problem, lang, cwarn, nerr = check_choices(mode, lang)
        if nerr:
            kind, hint = net_kind(nerr)
            set_cfg(state, kind, hint, snap0)
            return cfg_html(state), state
        if problem:
            return cfg_state_html("error", problem, label=Msg("state.fix"), lang=ui_lang), state
        # 2) ler a blacklist atual para reenvia-la (a sub-aba Demo nao edita a blacklist).
        #    Falha de leitura (exceto 404) aborta: nunca apagar uma lista por engano.
        cfg, gerr = call(client.get_configuration, ls)
        blacklist: list[str] = []
        if gerr and gerr.status == 404:
            gerr = None
            cfg = None  # sem configuracao anterior: nada a preservar
        if gerr:
            kind, hint = net_kind(gerr) if gerr.kind in ("connection", "timeout", "auth") else ("error", gerr)
            set_cfg(state, kind, Msg("hint.nothing_saved_read", hint=hint), snap0)
            return cfg_html(state), state
        if isinstance(cfg, dict):
            bl = cfg.get("blacklist")
            if bl is None:
                blacklist = []
            elif isinstance(bl, list) and all(isinstance(x, str) for x in bl):
                blacklist = list(bl)
            else:
                set_cfg(state, "error", Msg("hint.nothing_saved_fmt"), snap0)
                return cfg_html(state), state
        elif cfg is not None:
            set_cfg(state, "error", Msg("hint.nothing_saved_unreadable"), snap0)
            return cfg_html(state), state
        # 3) salvar
        _, err = call(client.configure, ls, active, mode, lang, _lines(kws), blacklist)
        if err:
            kind, hint = net_kind(err) if err.kind != "validation" else ("error", Msg("hint.save_rejected", msg=err))
            set_cfg(state, kind, hint, snap0, cwarn)
            return cfg_html(state), state
        # 4) confirmar no servidor (sem falso positivo)
        kind, hint, snap = verify_against_server(ls, active, mode, lang, kws, saved_now=True)
        set_cfg(state, kind, hint, snapshot(ls, active, mode, lang, kws), cwarn)
        return cfg_html(state), state

    def an_empty_html(lang: str) -> str:
        return f'<div class="cl-plots-slot"><span>{_esc(t("an.empty", lang))}</span></div>'

    def status_html(text: str) -> str:
        """Mensagem transitoria como HTML escapado (o texto pode trazer detalhes do servidor)."""
        return f'<p class="cl-note" role="status">{_esc(text)}</p>' if text else ""

    # ---- Blacklist
    def bl_refresh(state=None):
        lang = lang_of(state)
        g, e1 = call(client.list_blacklist_groups)
        u, e2 = call(client.list_blacklist_users)
        errs = " ".join(x for x in (i18n.txt(e1, lang), i18n.txt(e2, lang)) if x)
        return g, u, status_html(t("bl.status.error", lang, err=errs) if errs else t("bl.status.updated", lang))

    def bl_do(action, ls, grp, user, state=None):
        lang = lang_of(state)
        fn, args = {
            "g_add": (client.blacklist_group, (ls, grp)),
            "g_rm": (client.remove_blacklist_group, (ls, grp)),
            "u_add": (client.blacklist_user, (user,)),
            "u_rm": (client.remove_blacklist_user, (user,)),
        }[action]
        _, err = call(fn, *args)
        if err:
            return gr.update(), gr.update(), status_html(t("bl.status.error", lang, err=err))
        g, u, _ = bl_refresh(state)
        return g, u, status_html(t("bl.status.done", lang))

    # ---- Analytics
    def on_analytics(ls, state=None):
        lang = lang_of(state)
        data, err = call(client.analytics, ls)
        if err:
            return status_html(t("bl.status.error", lang, err=err)), gr.update(visible=False), gr.update(visible=False), an_empty_html(lang)
        # tabela bruta (lista de dicts) fica na propria aba "Analytics do servidor"; adapter cuida de graficos/tabelas/JSON
        try:
            page = analytics_adapter.render(data, lang)
        except Exception:
            page = ""
        shown = data if isinstance(data, (dict, list)) else {"value": data}  # gr.JSON nao aceita texto solto
        return status_html(t("an.loaded", lang)), gr.update(value=shown, visible=True), gr.update(visible=False), page

    # ---- Simulacao (collabsim): a logica fica em sim_bridge.py; aqui so a ligacao com os componentes
    def sim_key_html(lang):
        if sim_bridge.has_api_key():
            return f'<p class="cl-note" role="status">{_esc(t("sim.key.ok", lang))}</p>'
        return f'<p class="cl-note warn" role="status">{i18n.rich(t("sim.key.missing", lang))}</p>'

    def sim_tasks(lang):
        return sim_bridge.list_tasks(sim_bridge.sim_language("auto", lang))

    def sim_bridge_of(state):
        return sim_bridge.REGISTRY.get((state or {}).get("sid"))

    def sim_status_html(state):
        lang = lang_of(state)
        b = sim_bridge_of(state)
        line = sim_bridge.status_text(b.info() if b else None, lang)
        note = i18n.txt((state or {}).get("sim_note"), lang)
        warn = " warn" if (state or {}).get("sim_note_warn") else ""
        extra = f'<span class="hint{warn}">{_esc(note)}</span>' if note else ""
        return f'<div class="cl-simstatus" role="status"><b>{_esc(line)}</b>{extra}</div>'

    def sim_note(state, msg, warn=False):
        state["sim_note"], state["sim_note_warn"] = msg, bool(warn)

    def composer(sim_on):
        """(linha de quem fala, caixa de mensagem, barra da simulacao): na simulacao o dono so observa."""
        return gr.update(visible=not sim_on), gr.update(visible=not sim_on), gr.update(visible=sim_on)

    def on_sim_start(ls, grp, n, task, slang, conflict, pace, speed, seed, backend, clair_av, state, show_tech, pull_on, secs):
        state = state or new_session()
        lang = lang_of(state)
        session_locks(state)  # garante state["sid"]
        keep = (gr.update(),) * 2
        ls = (ls or "").strip()
        if not ls:
            sim_note(state, Msg("sim.err.no_ls"), warn=True)
            h = sim_status_html(state)
            return (gr.update(), gr.update(), state, gr.update(), h, h, gr.update(), *(gr.update(),) * 3, gr.update(),
                    *keep, gr.update())
        sim_bridge.REGISTRY.close(state.get("sid"))  # a anterior sai antes (conta no max_sessions)
        g = (grp or "").strip() or new_group_id("")
        settings = sim_bridge.SimSettings(n_students=n, task=task, language=sim_bridge.sim_language(slang, lang),
                                          conflict=conflict, pace=pace, speed=speed, seed=seed, backend=backend)
        bridge = sim_bridge.SimBridge(client, ls, g, settings,
                                      clair_avatar=ui_style._idx(clair_av, ui_style.DEFAULT_CLAIR_AVATAR))
        try:
            bridge.start()
        except sim_bridge.SimUnavailable as e:
            bridge.close()
            sim_note(state, e, warn=True)
            h = sim_status_html(state)
            return (gr.update(), gr.update(), state, gr.update(), h, h, gr.update(), *(gr.update(),) * 3, gr.update(),
                    *keep, gr.update())
        state = _fresh_session(state)  # o chat passa a mostrar so a simulacao
        state["group"] = g
        sim_bridge.REGISTRY.put(state["sid"], bridge)
        sim_note(state, Msg("sim.started", ls=ls, group=g))
        h = sim_status_html(state)
        return (g, render_chat([], show_tech, lang=lang), state, err_html("", lang), h, h,
                gr.Timer(value=sim_bridge.TICK_SECONDS, active=True), *composer(True), gr.Tabs(selected="chat"),
                [], None, chat_status_html(ls, g, pull_on, secs, state))

    def on_sim_tick(ls, grp, state, show_tech, pull_on, secs):
        state = state or new_session()
        lang = lang_of(state)
        b = sim_bridge_of(state)
        idle = (gr.update(),) * 2
        if b is None or b.session is None:  # nada rodando (Limpar / Novo grupo / troca de grupo encerraram)
            h = sim_status_html(state)
            return (gr.update(), state, gr.update(), h, h, gr.Timer(active=False), *composer(False), *idle, gr.update())
        send_lock, mu = session_locks(state)
        if not send_lock.acquire(blocking=False):
            return (gr.update(), state) + (gr.update(),) * 10
        try:
            epoch = state.get("epoch", 0)
            try:
                every = max(5.0, float(secs or DEFAULT_PULL_SECONDS)) if pull_on else None
            except (TypeError, ValueError):
                every = DEFAULT_PULL_SECONDS
            res = b.tick(state["seen"], pull_every_s=every, lang=lang)
            with mu:
                if res.skipped or state.get("epoch", 0) != epoch or sim_bridge_of(state) is not b:
                    return (gr.update(), state) + (gr.update(),) * 10
                errs = list(res.errors)
                mark_net(state, errs[-1] if errs else None)
                state["history"].extend(res.messages)
                state["history"] = state["history"][-MAX_HISTORY:]
                if res.records:
                    state["records"].extend(res.records)
                    state["n_total"] = state.get("n_total", 0) + len(res.records)
                    state["records"] = state["records"][-MAX_RECORDS:]
                if res.last_response is not None:
                    state["last"] = res.last_response
                if res.error:
                    errs.append(res.error)
                    sim_note(state, res.error, warn=True)
                ended = res.ended
                if ended:
                    b.close()  # libera a vaga no Simulator; contadores e custo continuam legiveis
                    if not res.error:
                        sim_note(state, Msg("sim.ended"))
                chat = (render_chat(state["history"], show_tech, lang=lang, typing=None if ended else res.typing)
                        if (res.messages or res.typing_changed or ended) else gr.update())
                h = sim_status_html(state)
                rec_out = (live_rows(state["records"]), state["last"]) if res.records else idle
                return (chat, state, err_html(errs[-1], lang) if errs else gr.update(), h, h,
                        gr.Timer(active=False) if ended else gr.update(),
                        *(composer(False) if ended else (gr.update(),) * 3), *rec_out,
                        chat_status_html(ls, grp, pull_on, secs, state))
        finally:
            send_lock.release()

    def on_sim_ctl(action, state):
        state = state or new_session()
        b = sim_bridge_of(state)
        if b is not None:
            try:
                {"pause": b.pause, "resume": b.resume, "stop": b.stop}[action]()
            except Exception:
                pass
            sim_note(state, Msg(f"sim.note.{action}"))
        h = sim_status_html(state)
        return h, h, state

    def on_sim_speed(speed, state):
        b = sim_bridge_of(state)
        if b is not None:
            b.set_speed(speed)
        h = sim_status_html(state)
        return h, h

    def on_sim_check(backend, state):
        lang = lang_of(state)
        try:
            ok, txt = sim_bridge.connection_text(sim_bridge.get_simulator(backend).check_connection(), lang)
        except sim_bridge.SimUnavailable as e:
            ok, txt = False, e.text(lang)
        except Exception:
            ok, txt = False, t("sim.conn.fail", lang, err="?")
        return f'<p class="cl-note{"" if ok else " warn"}" role="status">{_esc(txt)}</p>'

    # ================================ layout
    # Registro de tudo que muda com o idioma: (componente, fn(ctx) -> update). Um unico handler (on_lang) atualiza
    # so label/info/value/placeholder/choices; NAO toca no valor digitado pelo usuario nem no historico do chat.
    reg: list[tuple[Any, Any]] = []

    def tr(comp, fn):
        reg.append((comp, fn))
        return comp

    def tab_lbl(comp, key):
        return tr(comp, lambda c: gr.update(label=t(key, c.lang)))

    def btn_lbl(comp, key):
        return tr(comp, lambda c: gr.update(value=t(key, c.lang)))

    def fld(comp, key):
        """label + info do campo vindos do catalogo (help.<key>.label/.info)."""
        return tr(comp, lambda c: gr.update(label=help_texts(key, c.lang)[0], info=help_texts(key, c.lang)[1]))

    def sim_lbl(cls, key, extra=None, **kw):
        """Cria um controle da Simulacao com label + info de sim.<x>.label/.info (traduzidos na troca de idioma).
        extra(c) -> dict: mais campos a atualizar na troca de idioma (ex.: choices)."""
        comp = cls(label=t(f"{key}.label", "pt"), info=t(f"{key}.info", "pt"), **kw)
        return tr(comp, lambda c: gr.update(label=t(f"{key}.label", c.lang), info=t(f"{key}.info", c.lang),
                                            **(extra(c) if extra else {})))

    def sim_opt_choices(group, values, lang):
        return [(t(f"sim.{group}.{v}", lang), v) for v in values]

    def sim_lang_choices(lang):
        return [(t("sim.lang.auto", lang), "auto"), ("Português (pt-BR)", "pt-BR"), ("English", "en")]

    def field(key: str):
        """Coluna 'cl-field' com o '(?)' ao lado do rotulo; o componente nativo vai dentro do `with`."""
        return gr.Column(elem_classes="cl-field", elem_id=f"cl-f-{key}")

    def helper(key: str):
        tr(gr.HTML(help_html(key), elem_classes="cl-helpwrap"), lambda c: help_html(key, c.lang))

    def card_title(key: str):
        def html_for(lang):
            small = t(f"{key}.small", lang)
            return f"<h2>{html.escape(t(f'{key}.title', lang))} <small>{html.escape(small)}</small></h2>"
        tr(gr.HTML(html_for("pt"), elem_classes="cl-h2"), lambda c: html_for(c.lang))

    def intro_html(lang):
        return (f'<h1 class="cl-page-title">{html.escape(t("cfg.page_title", lang))}</h1>'
                f'<p class="cl-lede">{i18n.rich(t("cfg.lede", lang))}</p>')

    def avatar_choices(lang):
        return [(t("avatar.label", lang, n=i + 1), str(i)) for i in range(ui_style.N_AVATARS)]

    def people_head(lang):
        label, info, _ = help_texts("people", lang)
        return f'<div class="cl-flabel"><b>{html.escape(label)}</b><div class="cl-hint">{html.escape(info)}</div></div>'

    def plots_html(lang, records=None, items=None, total=None, stale=False):
        """Cartao da aba Graficos. `items` (de plots_mpl.render_all) ja renderizados; None = ainda sem render."""
        e = html.escape
        head = (f'<section id="cl-plots" class="cl-plots" aria-labelledby="cl-plots-h">'
                f'<h2 id="cl-plots-h">{e(t("plots.title", lang))}</h2>'
                f'<p class="cl-note">{e(t("plots.intro", lang))}</p>')
        n = len(records or [])
        if not records:
            return head + f'<div class="cl-plots-slot"><span>{e(t("plots.empty", lang))}</span></div></section>'
        if items is None:  # ha registros mas nada renderizado ainda para esta sessao
            return head + f'<div class="cl-plots-slot"><span>{e(t("plots.click", lang))}</span></div></section>'
        try:
            sm = plots_mpl.summary(records, total)
        except Exception:
            sm = {"messages": n, "people": 0, "interventions": 0, "gini": None, "words": 0}
        gini = "n/a" if sm["gini"] is None else f"{sm['gini']:.2f}"
        tiles = "".join(f'<div class="v-kpi"><b>{e(str(v))}</b><span>{e(t("plots.kpi." + k, lang))}</span></div>'
                        for k, v in (("messages", sm["messages"]), ("people", sm["people"]),
                                     ("interventions", sm["interventions"]), ("gini", gini), ("words", sm["words"])))
        out = [head, f'<div class="v-kpis">{tiles}</div>']
        if stale:
            out.append(f'<p class="v-warn" role="status">{e(t("plots.stale", lang))}</p>')
        if n < 5:
            out.append(f'<p class="v-warn" role="status">{e(t("plots.few", lang, n=n))}</p>')
        out.append(f'<p class="cl-note">{e(t("plots.en_note", lang))}</p><div class="v-grid">')
        for it in items:
            pid = it["id"]
            title, desc = e(t(f"plots.{pid}.title", lang)), e(t(f"plots.{pid}.desc", lang))
            if it.get("light") and it.get("dark"):
                alt = e(it["alt"], quote=True)
                body = (f'<img class="v-img v-lt" src="data:image/png;base64,{it["light"]}" alt="{alt}" loading="lazy">'
                        f'<img class="v-img v-dk" src="data:image/png;base64,{it["dark"]}" alt="{alt}" loading="lazy">')
            else:
                body = f'<p class="v-none">{e(t("plots.error" if it.get("error") else "plots.nodata", lang))}</p>'
            out.append(f'<figure class="v-card v-{pid}"><figcaption><h3>{title}</h3><p>{desc}</p></figcaption>{body}</figure>')
        out.append("</div></section>")
        return "".join(out)

    def on_plots(state=None):
        lang = lang_of(state)
        recs = list(state["records"]) if state else []
        if not recs:
            return plots_html(lang, recs, None)
        try:
            items = plots_mpl.render_all(recs)
        except Exception:
            items = [{"id": pid, "light": None, "dark": None, "alt": "", "error": True} for pid, _ in plots_mpl.PLOT_IDS]
        state["plots"] = {"items": items, "key": plots_mpl.records_key(recs)}  # a troca de idioma redesenha so o cartao
        return plots_html(lang, recs, items, state.get("n_total"))

    def on_plots_sub(sub, state=None):
        """Selecao da aba Analytics: so renderiza se a sub-aba visivel e a de graficos."""
        if sub not in (None, "plots"):
            return gr.update()
        return on_plots(state)

    def plots_for_lang(c):
        st = c.state
        recs = list(st["records"])
        pl = st.get("plots") if isinstance(st.get("plots"), dict) else None
        if not recs or not pl:
            return plots_html(c.lang, recs, None)
        return plots_html(c.lang, recs, pl["items"], st.get("n_total"), stale=pl.get("key") != plots_mpl.records_key(recs))

    with gr.Blocks(title=t("app.title", "pt")) as demo:
        with gr.Row(elem_id="cl-head"):
            tr(gr.HTML(brand_html("pt"), elem_id="cl-brand"), lambda c: brand_html(c.lang))
            lang_dd = gr.Dropdown(choices=[(i18n.LANG_LABELS[x], x) for x in i18n.LANGS], value=i18n.DEFAULT,
                                  label=t("lang.label", "pt"), show_label=False, container=False, filterable=False,
                                  elem_id="cl-lang", scale=0, min_width=104)
            tr(lang_dd, lambda c: gr.update(label=t("lang.label", c.lang), value=c.lang))
        if not client.has_token:
            tr(gr.HTML(f'<div role="alert">{i18n.rich(t("banner.no_token", "pt"))}</div>', elem_classes="cl-banner"),
               lambda c: f'<div role="alert">{i18n.rich(t("banner.no_token", c.lang))}</div>')
        # ao descartar a sessao do navegador, a simulacao dela e encerrada (o collabsim tambem para sessoes ociosas)
        session = gr.State(new_session(), delete_callback=lambda st: sim_bridge.REGISTRY.close((st or {}).get("sid")))
        dyn = gr.HTML(ui_style.dyn_style(ui_style.DEFAULT_PERSON_AVATARS, ui_style.DEFAULT_CLAIR_AVATAR), elem_id="cl-dyn", padding=False)

        with gr.Tabs(selected="cfg", elem_id="cl-main") as main_tabs:
            # ------------------------------------------------ Configuracao
            with tab_lbl(gr.Tab(t("tab.config", "pt"), id="cfg"), "tab.config"):
                with gr.Column(elem_classes="cl-col cl-colwide"):
                    tr(gr.HTML(intro_html("pt"), elem_id="cl-cfgintro"), lambda c: intro_html(c.lang))
                    cfg_notice = tr(gr.HTML("", elem_id="cl-notice", padding=False), lambda c: notice_html(c.state))
                    with gr.Tabs(elem_id="cl-sub"):
                        # ---- Demo
                        with tab_lbl(gr.Tab(t("sub.demo", "pt")), "sub.demo"):
                            with gr.Row(elem_classes="cl-twocol", equal_height=False):
                                # Configuração da Clair (DOM primeiro: vem antes no celular e no teclado; no desktop o CSS a põe à direita)
                                with gr.Column(elem_classes="cl-half cl-half-cfg", scale=1, min_width=0):
                                    with gr.Column(elem_classes="cl-card"):
                                        card_title("cfg.card")
                                        with field("ls"):
                                            cfg_ls = fld(gr.Textbox(label=HELP["ls"][0], info=HELP["ls"][1], value=DEFAULT_LS, max_length=50), "ls")
                                            helper("ls")
                                        with field("active"):
                                            cfg_active = fld(gr.Checkbox(label=HELP["active"][0], info=HELP["active"][1], value=True,
                                                                         elem_classes="cl-switch"), "active")
                                            helper("active")
                                        with field("mode"):
                                            cfg_mode = gr.Dropdown(label=HELP["mode"][0], info=HELP["mode"][1],
                                                                   choices=[(mode_label("pt"), DEFAULT_MODE)], value=DEFAULT_MODE,
                                                                   allow_custom_value=True)
                                            tr(cfg_mode, lambda c: gr.update(
                                                label=help_texts("mode", c.lang)[0], info=help_texts("mode", c.lang)[1],
                                                **({"choices": mode_choices(c.state["mode_codes"], None, c.lang)}
                                                   if c.state.get("mode_codes") is not None else {})))
                                            helper("mode")
                                        with tab_lbl(gr.Accordion(t("cfg.guide", "pt"), open=False, elem_id="cl-guide-acc"), "cfg.guide"):
                                            guide = tr(gr.HTML(guide_html(DEFAULT_MODE), padding=False), lambda c: guide_html(c.mode, c.lang))
                                        with field("lang"):
                                            cfg_lang = fld(gr.Dropdown(label=HELP["lang"][0], info=HELP["lang"][1], choices=[],
                                                                       allow_custom_value=True), "lang")
                                            helper("lang")
                                        with field("kw"):
                                            cfg_kw = fld(gr.Textbox(label=HELP["kw"][0], info=HELP["kw"][1], lines=5), "kw")
                                            helper("kw")
                                        with tab_lbl(gr.Accordion(t("cfg.raw.acc", "pt"), open=False), "cfg.raw.acc"):
                                            cfg_json = tr(gr.JSON(label=t("cfg.raw.label", "pt")), lambda c: gr.update(label=t("cfg.raw.label", c.lang)))
                                    # barra de salvar: fim da coluna, opaca e acima de qualquer rotulo do Gradio
                                    with gr.Column(elem_id="cl-savebar"):
                                        cfg_status = tr(gr.HTML(cfg_state_html("busy", Msg("cfg.busy.wait")), padding=False),
                                                        lambda c: cfg_html(c.state) if c.state.get("cfg") else
                                                        cfg_state_html("busy", Msg("cfg.busy.wait"), lang=c.lang))
                                        with gr.Row(elem_classes="cl-btnrow"):
                                            cfg_save = btn_lbl(gr.Button(t("cfg.save", "pt"), variant="primary", scale=0, min_width=150), "cfg.save")
                                            cfg_verify = btn_lbl(gr.Button(t("cfg.verify", "pt"), variant="secondary", scale=0, min_width=100), "cfg.verify")
                                            cfg_load = btn_lbl(gr.Button(t("cfg.load", "pt"), variant="secondary", scale=0, min_width=150), "cfg.load")
                                            cfg_test = btn_lbl(gr.Button(t("cfg.test", "pt"), variant="secondary", scale=0, min_width=90,
                                                                         elem_id="cl-test-btn"), "cfg.test")
                                # opcoes so da interface
                                with gr.Column(elem_classes="cl-half cl-half-opt", scale=1, min_width=0):
                                    with gr.Column(elem_classes="cl-card"):
                                        card_title("opt.card")
                                        with field("avatar"):
                                            avatar = gr.Radio(choices=avatar_choices("pt"), value=str(ui_style.DEFAULT_CLAIR_AVATAR),
                                                              label=HELP["avatar"][0], info=HELP["avatar"][1], elem_id="cl-avatar")
                                            tr(avatar, lambda c: gr.update(label=help_texts("avatar", c.lang)[0], info=help_texts("avatar", c.lang)[1],
                                                                           choices=avatar_choices(c.lang)))
                                            helper("avatar")
                                        gr.HTML('<div class="cl-divider"></div>', padding=False)
                                        with field("group"):
                                            with gr.Row(elem_id="cl-grouprow"):
                                                grp_in = fld(gr.Textbox(label=HELP["group"][0], info=HELP["group"][1],
                                                                        value=DEFAULT_GROUP, max_length=50, scale=1), "group")
                                                new_grp_cfg = btn_lbl(gr.Button(t("grp.new", "pt"), size="sm", scale=0,
                                                                                elem_classes="cl-newgrp", elem_id="cl-newgrp-cfg"), "grp.new")
                                            helper("group")
                                        with field("people"):
                                            tr(gr.HTML(people_head("pt"), padding=False), lambda c: people_head(c.lang))
                                            helper("people")
                                            name_boxes, pavs = [], []
                                            with gr.Column(elem_classes="cl-people"):
                                                for i, n in enumerate(PEOPLE):
                                                    with gr.Column(elem_classes="cl-person"):
                                                        nb = gr.Textbox(value=n, max_length=50, lines=1, max_lines=1, show_label=False,
                                                                        container=False, placeholder=default_names("pt")[i],
                                                                        label=t("people.name_label", "pt", n=i + 1), elem_id=f"cl-pname-{i + 1}")
                                                        # nomes padrao seguem o idioma do site; nomes digitados nunca sao alterados
                                                        tr(nb, lambda c, i=i: gr.update(
                                                            label=t("people.name_label", c.lang, n=i + 1), placeholder=default_names(c.lang)[i],
                                                            **({"value": c.names[i]} if c.names[i] != c.raw_names[i] else {})))
                                                        name_boxes.append(nb)
                                                        pv = gr.Radio(
                                                            choices=avatar_choices("pt"),
                                                            value=str(ui_style.DEFAULT_PERSON_AVATARS[i]), show_label=False,
                                                            container=False, label=t("people.avatar_label", "pt", name=n), info=HELP["people"][1],
                                                            elem_classes="cl-pav", elem_id=f"cl-pav-{i + 1}")
                                                        tr(pv, lambda c, i=i: gr.update(
                                                            label=t("people.avatar_label", c.lang, name=c.names[i]),
                                                            info=help_texts("people", c.lang)[1], choices=avatar_choices(c.lang)))
                                                        pavs.append(pv)
                                            avwarn = tr(gr.HTML("", elem_id="cl-avwarn", padding=False),
                                                        lambda c: avatar_warn_html(c.avatar, list(c.pavs), list(c.names), c.lang))
                                        gr.HTML('<div class="cl-divider"></div>', padding=False)
                                        with gr.Row(elem_classes="cl-pullrow"):
                                            with field("pull"):
                                                pull_on = fld(gr.Checkbox(label=HELP["pull"][0], info=HELP["pull"][1], value=True,
                                                                          elem_classes="cl-switch"), "pull")
                                                helper("pull")
                                            pull_secs = fld(gr.Number(label=HELP["secs"][0], info=HELP["secs"][1], value=DEFAULT_PULL_SECONDS,
                                                                      minimum=5, precision=0, scale=0, min_width=150), "secs")
                                        gr.HTML('<div class="cl-divider"></div>', padding=False)
                                        with field("tech"):
                                            show_tech = fld(gr.Checkbox(label=HELP["tech"][0], info=HELP["tech"][1], value=False,
                                                                        elem_classes="cl-switch"), "tech")
                                            helper("tech")

                        # ---- Simulacao (alunos simulados pelo collabsim conversam com a Clair real)
                        with tab_lbl(gr.Tab(t("sub.sim", "pt"), id="sim"), "sub.sim"):
                            with gr.Column(elem_classes="cl-card", elem_id="cl-simcard"):
                                card_title("sim.card")
                                tr(gr.HTML(f'<p class="cl-note">{i18n.rich(t("sim.lede", "pt"))}</p>', padding=False),
                                   lambda c: f'<p class="cl-note">{i18n.rich(t("sim.lede", c.lang))}</p>')
                                tr(gr.HTML(sim_key_html("pt"), padding=False, elem_id="cl-simkey"), lambda c: sim_key_html(c.lang))
                                _tasks = sim_tasks("pt")
                                _task_ids = [v for _, v in _tasks]
                                with gr.Row(equal_height=False):
                                    sim_n = sim_lbl(gr.Slider, "sim.n", minimum=sim_bridge.MIN_STUDENTS, maximum=sim_bridge.MAX_STUDENTS,
                                                    value=4, step=1, elem_id="cl-sim-n")
                                    sim_task = sim_lbl(gr.Dropdown, "sim.task", lambda c: {"choices": sim_tasks(c.lang)}, choices=_tasks,
                                                       value="sci-enzyme-01" if "sci-enzyme-01" in _task_ids else (_task_ids[0] if _task_ids else None),
                                                       elem_id="cl-sim-task")
                                with gr.Row(equal_height=False):
                                    sim_lang = sim_lbl(gr.Radio, "sim.lang", lambda c: {"choices": sim_lang_choices(c.lang)},
                                                       choices=sim_lang_choices("pt"), value="auto", elem_id="cl-sim-lang")
                                    sim_conflict = sim_lbl(gr.Radio, "sim.conflict",
                                                           lambda c: {"choices": sim_opt_choices("conflict", sim_bridge.CONFLICTS, c.lang)},
                                                           choices=sim_opt_choices("conflict", sim_bridge.CONFLICTS, "pt"), value="normal",
                                                           elem_id="cl-sim-conflict")
                                with gr.Row(equal_height=False):
                                    sim_pace = sim_lbl(gr.Radio, "sim.pace", lambda c: {"choices": sim_opt_choices("pace", sim_bridge.PACES, c.lang)},
                                                       choices=sim_opt_choices("pace", sim_bridge.PACES, "pt"), value="pilot", elem_id="cl-sim-pace")
                                    sim_speed = sim_lbl(gr.Slider, "sim.speed", minimum=sim_bridge.SPEED_MIN, maximum=sim_bridge.SPEED_MAX,
                                                        value=sim_bridge.DEFAULT_SPEED, step=0.5, elem_id="cl-sim-speed")
                                with gr.Row(equal_height=False):
                                    # Textbox (nao Number): vazio = semente sorteada; gr.Number mostraria e enviaria 0
                                    sim_seed = sim_lbl(gr.Textbox, "sim.seed", value="", max_length=12, lines=1, max_lines=1,
                                                       elem_id="cl-sim-seed")
                                    sim_backend = sim_lbl(gr.Radio, "sim.backend",
                                                          lambda c: {"choices": sim_opt_choices("backend", sim_bridge.BACKENDS, c.lang)},
                                                          choices=sim_opt_choices("backend", sim_bridge.BACKENDS, "pt"),
                                                          value="llm" if sim_bridge.has_api_key() else "mock", elem_id="cl-sim-backend")
                                sim_status = tr(gr.HTML(sim_status_html(None), padding=False, elem_id="cl-sim-status"),
                                                lambda c: sim_status_html(c.state))
                                with gr.Row(elem_classes="cl-btnrow"):
                                    sim_start = btn_lbl(gr.Button(t("sim.start", "pt"), variant="primary", scale=0, min_width=120,
                                                                  elem_id="cl-sim-start"), "sim.start")
                                    sim_pause = btn_lbl(gr.Button(t("sim.pause", "pt"), scale=0, min_width=100), "sim.pause")
                                    sim_resume = btn_lbl(gr.Button(t("sim.resume", "pt"), scale=0, min_width=100), "sim.resume")
                                    sim_stop = btn_lbl(gr.Button(t("sim.stop", "pt"), variant="stop", scale=0, min_width=100), "sim.stop")
                                    sim_check = btn_lbl(gr.Button(t("sim.check", "pt"), scale=0, min_width=200, elem_id="cl-sim-check"), "sim.check")
                                sim_conn = tr(gr.HTML("", padding=False, elem_id="cl-sim-conn"), lambda c: "")

                        # ---- Blacklist
                        with tab_lbl(gr.Tab(t("sub.blacklist", "pt")), "sub.blacklist"):
                            bl_refresh_btn = btn_lbl(gr.Button(t("bl.refresh", "pt")), "bl.refresh")
                            with gr.Row():
                                bl_groups = tab_lbl(gr.JSON(label=t("bl.groups_label", "pt")), "bl.groups_label")
                                bl_users = tab_lbl(gr.JSON(label=t("bl.users_label", "pt")), "bl.users_label")
                            with gr.Row():
                                bl_ls = tab_lbl(gr.Textbox(label=t("bl.ls", "pt"), value=DEFAULT_LS, max_length=50), "bl.ls")
                                bl_grp = tab_lbl(gr.Textbox(label=t("bl.group", "pt"), value=DEFAULT_GROUP, max_length=50), "bl.group")
                            with gr.Row():
                                g_add = btn_lbl(gr.Button(t("bl.add_group", "pt")), "bl.add_group")
                                g_rm = btn_lbl(gr.Button(t("bl.rm_group", "pt")), "bl.rm_group")
                            bl_user = tab_lbl(gr.Textbox(label=t("bl.user", "pt"), max_length=50), "bl.user")
                            with gr.Row():
                                u_add = btn_lbl(gr.Button(t("bl.add_user", "pt")), "bl.add_user")
                                u_rm = btn_lbl(gr.Button(t("bl.rm_user", "pt")), "bl.rm_user")
                            bl_status = tr(gr.HTML("", padding=False), lambda c: "")  # mensagem transitoria: some ao trocar de idioma

            # ------------------------------------------------ Chat
            with tab_lbl(gr.Tab(t("tab.chat", "pt"), id="chat"), "tab.chat") as chat_tab:
                with gr.Column(elem_id="cl-chat"):
                    with gr.Row(elem_id="cl-chat-head"):
                        chat_status = tr(gr.HTML(chat_status_html(DEFAULT_LS, DEFAULT_GROUP, True, DEFAULT_PULL_SECONDS, None),
                                                 elem_classes="cl-statuswrap", padding=False),
                                         lambda c: chat_status_html(c.ls, c.grp, c.pull_on, c.secs, c.state))
                        goto_cfg = btn_lbl(gr.Button(t("chat.goto_cfg", "pt"), size="sm", elem_classes="cl-linkbtn", scale=0), "chat.goto_cfg")
                        new_grp_chat = btn_lbl(gr.Button(t("grp.new", "pt"), size="sm", elem_classes="cl-clear", scale=0,
                                                         elem_id="cl-newgrp-chat"), "grp.new")
                        clear_btn = btn_lbl(gr.Button(t("chat.clear", "pt"), size="sm", elem_classes="cl-clear", scale=0), "chat.clear")
                    chat_html = tr(gr.HTML(render_chat([]), elem_id="cl-history", padding=False),
                                   lambda c: render_chat(c.state["history"], bool(c.show_tech), lang=c.lang))
                    with gr.Column(elem_id="cl-composer"):
                        err_box = tr(gr.HTML("", elem_id="cl-errwrap", padding=False), lambda c: "")
                        # na simulacao o composer some e o dono so observa (status + Pausar/Retomar/Parar)
                        with gr.Row(elem_id="cl-simbar", visible=False) as sim_bar:
                            sim_status_chat = tr(gr.HTML(sim_status_html(None), padding=False), lambda c: sim_status_html(c.state))
                            sim_pause_c = btn_lbl(gr.Button(t("sim.pause", "pt"), size="sm", scale=0, min_width=96), "sim.pause")
                            sim_resume_c = btn_lbl(gr.Button(t("sim.resume", "pt"), size="sm", scale=0, min_width=96), "sim.resume")
                            sim_stop_c = btn_lbl(gr.Button(t("sim.stop", "pt"), size="sm", variant="stop", scale=0, min_width=96,
                                                           elem_id="cl-sim-stop-chat"), "sim.stop")
                        with gr.Row(elem_id="cl-whorow") as whorow:
                            who = gr.Radio(choices=[(n, str(i)) for i, n in enumerate(PEOPLE)], value="0",
                                           label=t("chat.who", "pt"), show_label=False, container=False, elem_id="cl-who")
                            tr(who, lambda c: gr.update(label=t("chat.who", c.lang), choices=[(n, str(k)) for k, n in enumerate(c.names)]))
                            clair_mark = gr.HTML(clairmark_html(), elem_classes="cl-markwrap", padding=False)
                        msg = gr.Textbox(lines=1, max_lines=1, show_label=False, submit_btn=True, container=False,
                                         elem_id="cl-msg", max_length=client.max_message_chars, placeholder=t("chat.placeholder", "pt", name="Pessoa1"))
                        tr(msg, lambda c: gr.update(placeholder=t("chat.placeholder", c.lang, name=person_of(c.who, c.names, c.lang)[1])))
                timer = gr.Timer(value=DEFAULT_PULL_SECONDS, active=True)
                sim_timer = gr.Timer(value=sim_bridge.TICK_SECONDS, active=False)

            # ------------------------------------------------ Analytics
            with tab_lbl(gr.Tab(t("tab.analytics", "pt"), id="analytics"), "tab.analytics") as an_tab:
                with gr.Column(elem_classes="cl-col cl-colwide"):
                    sub_sel = gr.State("plots")  # sub-aba visivel de Analytics (para nao renderizar graficos ao abrir outra)
                    with gr.Tabs(elem_id="cl-subanalytics"):
                        with tab_lbl(gr.Tab(t("sub.plots", "pt"), elem_id="cl-tab-plots", id="plots"), "sub.plots") as plots_tab:
                            plots_btn = btn_lbl(gr.Button(t("plots.refresh", "pt"), elem_id="cl-plots-refresh"), "plots.refresh")
                            plots_out = tr(gr.HTML(plots_html("pt"), padding=False), plots_for_lang)
                        with tab_lbl(gr.Tab(t("sub.live", "pt")), "sub.live") as live_tab:
                            tr(gr.Markdown(t("live.intro", "pt")), lambda c: t("live.intro", c.lang))
                            live_df = tr(gr.Dataframe(headers=LIVE_HEADERS, value=[], interactive=False, wrap=False),
                                         lambda c: gr.update(headers=live_headers(c.lang), value=live_rows(c.state["records"])))
                            live_json = tab_lbl(gr.JSON(label=t("live.json", "pt")), "live.json")
                        with tab_lbl(gr.Tab(t("sub.server", "pt")), "sub.server") as server_tab:
                            an_ls = tab_lbl(gr.Textbox(label=t("an.ls", "pt"), value=DEFAULT_LS, max_length=50), "an.ls")
                            an_btn = btn_lbl(gr.Button(t("an.btn", "pt")), "an.btn")
                            an_status = tr(gr.HTML("", padding=False), lambda c: "")
                            an_df = gr.Dataframe(visible=False, interactive=False)
                            an_page = tr(gr.HTML(an_empty_html("pt"), padding=False, elem_id="cl-an-page"),
                                         lambda c: an_empty_html(c.lang))
                            an_json = tr(gr.JSON(label=t("an.json", "pt"), open=False, visible=False),
                                         lambda c: gr.update(label=t("an.json", c.lang), visible=False))

        # ================================ eventos
        st_in = [cfg_ls, grp_in, pull_on, pull_secs, session]
        names_in = list(name_boxes)
        pav_in = list(pavs)

        # Idioma do site: um handler atualiza todos os componentes registrados (so rotulos/textos; nao os valores digitados)
        lang_in = [lang_dd, session, cfg_mode, cfg_ls, grp_in, pull_on, pull_secs, show_tech, who, avatar] + names_in + pav_in
        lang_outs = [comp for comp, _ in reg] + [session]

        def on_lang(lang, state, mode, ls, grp, pull_on_v, secs, show_tech_v, who_v, avatar_v, *rest):
            state = state or new_session()
            lang = i18n.norm_lang(lang)
            state["lang"] = lang
            from types import SimpleNamespace
            ctx = SimpleNamespace(lang=lang, state=state, mode=mode, ls=ls, grp=grp, pull_on=pull_on_v, secs=secs,
                                  show_tech=show_tech_v, who=who_v, avatar=avatar_v,
                                  names=names_for_lang(list(rest[:N_PEOPLE]), lang), raw_names=[str(x or "").strip() for x in rest[:N_PEOPLE]],
                                  pavs=list(rest[N_PEOPLE:]))
            return (*[fn(ctx) for _, fn in reg], state)

        lang_dd.input(on_lang, lang_in, lang_outs, js=ui_style.LANG_SAVE_JS, show_progress="hidden", concurrency_id="ui") \
            .then(None, None, None, js=ui_style.LANG_SYNC_JS)

        # Chat: envio em dois passos (mostra a mensagem e depois chama a API)
        send_in = [msg, who, cfg_ls, grp_in, session, show_tech, pull_on, pull_secs] + names_in + pav_in
        send_outs = [chat_html, session, msg, err_box, live_df, live_json, chat_status, clair_mark]
        pre_in = [msg, who, show_tech, session] + names_in + pav_in
        msg.submit(on_send_pre, pre_in, [chat_html, clair_mark], concurrency_id="ui", show_progress="hidden") \
            .then(on_send, send_in, send_outs, concurrency_id="chat", concurrency_limit=CONCURRENCY, show_progress="hidden")
        timer.tick(on_tick, [cfg_ls, grp_in, pull_on, session, pull_secs, show_tech], [chat_html, session, chat_status],
                   concurrency_id="chat", concurrency_limit=CONCURRENCY, show_progress="hidden")
        clear_btn.click(on_clear, [session, show_tech, cfg_ls, grp_in, pull_on, pull_secs],
                        [chat_html, session, err_box, live_df, live_json, chat_status])
        ng_in = [grp_in, session, show_tech, cfg_ls, pull_on, pull_secs]
        ng_out = [grp_in, chat_html, session, err_box, live_df, live_json, chat_status]
        new_grp_cfg.click(on_new_group, ng_in, ng_out)
        new_grp_chat.click(on_new_group, ng_in, ng_out)
        gc_in = [grp_in, session, show_tech, cfg_ls, pull_on, pull_secs]
        gc_out = [chat_html, session, err_box, live_df, live_json, chat_status]
        grp_in.blur(on_group_commit, gc_in, gc_out, show_progress="hidden")
        grp_in.submit(on_group_commit, gc_in, gc_out, show_progress="hidden")
        goto_cfg.click(lambda: gr.Tabs(selected="cfg"), None, main_tabs)
        show_tech.change(on_rerender, [session, show_tech], chat_html, show_progress="hidden")
        pull_on.change(on_pull_toggle, [pull_on, pull_secs], [pull_secs, timer])
        pull_secs.change(on_timer_cfg, [pull_on, pull_secs], timer)
        for comp in (cfg_ls, grp_in, pull_on, pull_secs):
            comp.change(on_status, st_in, chat_status, show_progress="hidden", queue=False)

        # Quem fala, nomes e avatar
        names_outs = [who, msg]
        for nb in name_boxes:
            nb.change(on_names, [who] + names_in + [session], names_outs, show_progress="hidden", queue=False,
                      trigger_mode="always_last")
        who.change(on_who, [who] + names_in + [session], msg, show_progress="hidden", queue=False)
        for comp in [avatar] + pav_in:
            comp.change(on_style, [avatar] + pav_in, dyn, show_progress="hidden", queue=False)
        for comp in [avatar] + pav_in + name_boxes:
            comp.change(on_avwarn, [avatar] + pav_in + names_in + [session], avwarn, show_progress="hidden", queue=False,
                        trigger_mode="always_last")

        # Configuracao: estado "Pronta" so depois de confirmar no servidor
        left = [cfg_ls, cfg_active, cfg_mode, cfg_lang, cfg_kw]
        # carga: 1) idioma (localStorage -> navigator.language -> pt), 2) modos/idiomas da API, 3) verificacao no servidor
        demo.load(on_lang, lang_in, lang_outs, js=ui_style.LANG_PICK_JS, show_progress="hidden") \
            .then(None, None, None, js=ui_style.LANG_SYNC_JS) \
            .then(lambda: new_group_id(""), None, grp_in, show_progress="hidden") \
            .then(on_init, [session], [cfg_mode, cfg_lang, cfg_notice, guide, session]) \
            .then(on_verify, left + [session], [cfg_status, session]) \
            .then(on_status, st_in, chat_status, show_progress="hidden")
        cfg_mode.input(on_mode_input, [cfg_mode, session], [cfg_lang, cfg_notice, session]) \
            .then(on_dirty, left + [session], [cfg_status, session], show_progress="hidden") \
            .then(on_status, st_in, chat_status, show_progress="hidden")
        for comp in (cfg_ls, cfg_active, cfg_lang, cfg_kw):
            comp.input(on_dirty, left + [session], [cfg_status, session], show_progress="hidden", trigger_mode="always_last") \
                .then(on_status, st_in, chat_status, show_progress="hidden")
        cfg_mode.change(on_mode_change, [cfg_mode, session], guide, show_progress="hidden")
        cfg_load.click(on_busy, [session], cfg_status, queue=False, show_progress="hidden") \
            .then(on_cfg_load, [cfg_ls, session], [cfg_active, cfg_mode, cfg_lang, cfg_kw, cfg_json, session]) \
            .then(on_verify, left + [session], [cfg_status, session]) \
            .then(on_status, st_in, chat_status, show_progress="hidden")
        cfg_verify.click(on_busy, [session], cfg_status, queue=False, show_progress="hidden") \
            .then(on_verify, left + [session], [cfg_status, session]) \
            .then(on_status, st_in, chat_status, show_progress="hidden")
        cfg_save.click(on_busy_save, [session], cfg_status, queue=False, show_progress="hidden") \
            .then(on_cfg_save, left + [session], [cfg_status, session]) \
            .then(on_status, st_in, chat_status, show_progress="hidden")
        chat_tab.select(on_verify_chat, left + [session], [cfg_status, session], show_progress="hidden") \
            .then(on_status, st_in, chat_status, show_progress="hidden")
        # "Testar": so troca para a aba Chat (nao salva nada, nao bloqueia); o chat mostra Pronta / Alteracoes nao salvas
        cfg_test.click(lambda: gr.Tabs(selected="chat"), None, main_tabs) \
            .then(on_verify_chat, left + [session], [cfg_status, session], show_progress="hidden") \
            .then(on_status, st_in, chat_status, show_progress="hidden")

        # Simulacao
        sim_start_in = [cfg_ls, grp_in, sim_n, sim_task, sim_lang, sim_conflict, sim_pace, sim_speed, sim_seed, sim_backend,
                        avatar, session, show_tech, pull_on, pull_secs]
        sim_start_out = [grp_in, chat_html, session, err_box, sim_status, sim_status_chat, sim_timer, whorow, msg, sim_bar,
                         main_tabs, live_df, live_json, chat_status]
        sim_start.click(on_sim_start, sim_start_in, sim_start_out, concurrency_id="chat", concurrency_limit=CONCURRENCY)
        sim_timer.tick(on_sim_tick, [cfg_ls, grp_in, session, show_tech, pull_on, pull_secs],
                       [chat_html, session, err_box, sim_status, sim_status_chat, sim_timer, whorow, msg, sim_bar,
                        live_df, live_json, chat_status], concurrency_id="chat", concurrency_limit=CONCURRENCY,
                       show_progress="hidden")
        for action, btns in (("pause", (sim_pause, sim_pause_c)), ("resume", (sim_resume, sim_resume_c)),
                             ("stop", (sim_stop, sim_stop_c))):
            for btn in btns:
                btn.click(lambda st, a=action: on_sim_ctl(a, st), [session], [sim_status, sim_status_chat, session],
                          show_progress="hidden")
        sim_speed.release(on_sim_speed, [sim_speed, session], [sim_status, sim_status_chat], show_progress="hidden")
        sim_check.click(on_sim_check, [sim_backend, session], sim_conn)

        bl_outs = [bl_groups, bl_users, bl_status]
        bl_refresh_btn.click(bl_refresh, [session], bl_outs)
        g_add.click(lambda a, b, c, s: bl_do("g_add", a, b, c, s), [bl_ls, bl_grp, bl_user, session], bl_outs)
        g_rm.click(lambda a, b, c, s: bl_do("g_rm", a, b, c, s), [bl_ls, bl_grp, bl_user, session], bl_outs)
        u_add.click(lambda a, b, c, s: bl_do("u_add", a, b, c, s), [bl_ls, bl_grp, bl_user, session], bl_outs)
        u_rm.click(lambda a, b, c, s: bl_do("u_rm", a, b, c, s), [bl_ls, bl_grp, bl_user, session], bl_outs)

        for ev in (plots_btn.click, plots_tab.select):
            ev(on_plots, [session], [plots_out], concurrency_id="plots", concurrency_limit=2, show_progress="minimal")
        live_tab.select(lambda: "live", None, sub_sel, show_progress="hidden", queue=False)
        server_tab.select(lambda: "server", None, sub_sel, show_progress="hidden", queue=False)
        plots_tab.select(lambda: "plots", None, sub_sel, show_progress="hidden", queue=False)
        an_tab.select(on_plots_sub, [sub_sel, session], [plots_out], concurrency_id="plots", concurrency_limit=2,
                      show_progress="minimal")
        an_btn.click(on_analytics, [an_ls, session], [an_status, an_json, an_df, an_page])

    demo.queue(default_concurrency_limit=CONCURRENCY, max_size=64)
    return demo


def server_options() -> dict:
    """Host/porta a partir do ambiente (Hugging Face Spaces/Docker); sem caminhos absolutos."""
    port_s = os.environ.get("GRADIO_SERVER_PORT") or os.environ.get("PORT") or "7860"
    try:
        port = int(port_s)
    except ValueError:
        port = 7860
    return {"server_name": os.environ.get("GRADIO_SERVER_NAME") or "0.0.0.0", "server_port": port}


def launch(demo: gr.Blocks, **kw):
    """Lanca com o CSS/JS da UI (em Gradio 6 vao no launch(), nao no Blocks)."""
    opts = {**server_options(), "css": ui_style.build_css(), "js": ui_style.JS, **kw}
    return demo.launch(**opts)


if __name__ == "__main__":
    launch(build_app())
