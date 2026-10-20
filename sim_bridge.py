"""Ponte entre o simulador de alunos (collabsim) e a API do Clair, sem Gradio.

Fluxo (um `SimBridge` por sessao do navegador):

    alunos simulados --(Session.poll)--> SimBridge.tick --(clair.send_message, username = nome do aluno)--> Clair
    Clair --(resposta de /message e GET /pull)--> SimBridge.tick --(Session.say(texto, sender="Clair"))--> alunos

* As mensagens dos alunos simulados vao ao Clair pela API real, exatamente como as de alunos reais.
* As intervencoes da Clair (da resposta de /message e do /pull) aparecem no chat e sao repassadas ao
  simulador como fala do participante "Clair" (declarado ao criar a sessao), uma unica vez cada
  (deduplicacao pela mesma janela de chaves estaveis usada pela app).
* Erros do Clair ou do collabsim nunca derrubam o `tick`: voltam em `TickResult.errors` / `.error`.
* A chave da Anthropic vem do ambiente (COLLABSIM_ANTHROPIC_API_KEY, senao ANTHROPIC_API_KEY) e nunca
  e exibida nem registrada.

As mensagens devolvidas tem o mesmo formato do historico da app (`app._msg_row`): dicts com
who/kind/text/ts/av/tech/proactive.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import i18n
from clair_client import ClairError, extract_interventions

N_AVATARS = 6
MAX_STUDENTS = 6  # a UI tem 6 avatares; o collabsim aceita ate 10 (avatares repetidos com anel de outra cor)
MIN_STUDENTS = 2
DEDUPE_WINDOW_MS = 120_000
CLAIR_NAME = "Clair"
TICK_SECONDS = 1.0
DEFAULT_SPEED = 3.0
SPEED_MIN, SPEED_MAX = 1.0, 4.0
DEFAULT_PULL_SECONDS = 30
KEY_ENV = ("COLLABSIM_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
BACKENDS = ("llm", "mock")
PACES = ("pilot", "brisk")
CONFLICTS = ("low", "normal", "high")
SIM_LANGS = ("pt-BR", "en")


# ------------------------------------------------------------------ dedupe (compartilhado com app.py)
def is_seen(seen: list, key: str, now: int, window_ms: int = DEDUPE_WINDOW_MS) -> bool:
    return any(isinstance(x, (list, tuple)) and x[0] == key and now - x[1] < window_ms for x in seen)


def mark_seen(seen: list, key: str, now: int, window_ms: int = DEDUPE_WINDOW_MS) -> None:
    """Lembra a intervencao (chave estavel) por `window_ms`; altera a lista NO LUGAR."""
    keep = [x for x in seen if isinstance(x, (list, tuple)) and now - x[1] < window_ms][-200:]
    seen[:] = keep
    seen.append([key, now])


# ------------------------------------------------------------------ chave / Simulator por processo
def api_key() -> str | None:
    for k in KEY_ENV:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return None


def has_api_key() -> bool:
    return api_key() is not None


class SimUnavailable(Exception):
    """O backend pedido nao pode ser usado (ex.: 'llm' sem chave). `key` e uma chave de i18n."""

    def __init__(self, key: str, **params: Any):
        super().__init__(key)
        self.key, self.params = key, params

    def text(self, lang: str = "pt") -> str:
        return i18n.t(self.key, lang, **self.params)


_SIM_LOCK = threading.Lock()
_SIMS: dict[str, Any] = {}


def _max_sessions() -> int:
    try:
        return max(1, int(os.environ.get("COLLABSIM_MAX_SESSIONS") or 4))
    except ValueError:
        return 4


def get_simulator(backend: str = "llm"):
    """Um `collabsim.Simulator` por processo e por backend, criado sob demanda (thread-safe)."""
    if backend not in BACKENDS:
        backend = "mock"
    with _SIM_LOCK:
        sim = _SIMS.get(backend)
        if sim is not None:
            return sim
        try:
            from collabsim import Simulator
            from collabsim.errors import BackendUnavailable
        except ImportError:
            raise SimUnavailable("sim.err.not_installed") from None
        kw = {"max_sessions": _max_sessions(), "idle_ttl_s": 600.0}
        if backend == "llm":
            key = api_key()
            if not key:
                raise SimUnavailable("sim.err.no_key")
            try:
                sim = Simulator(api_key=key, backend="llm", **kw)
            except BackendUnavailable:
                raise SimUnavailable("sim.err.backend") from None
        else:
            sim = Simulator(backend="mock", **kw)
        _SIMS[backend] = sim
        return sim


def reset_simulators() -> None:
    """Testes: fecha e esquece os Simulators do processo."""
    with _SIM_LOCK:
        sims = list(_SIMS.values())
        _SIMS.clear()
    for s in sims:
        try:
            s.close()
        except Exception:
            pass


def list_tasks(language: str = "pt-BR") -> list[tuple[str, str]]:
    """(titulo, id) das tarefas da biblioteca do collabsim no idioma dado (lista vazia se indisponivel)."""
    try:
        sim = get_simulator("mock")  # a lista de tarefas nao depende do backend (e o mock nao gasta nada)
        return [(t.get("title") or t["id"], t["id"]) for t in sim.list_tasks(language)]
    except Exception:
        return []


def sim_language(choice: str | None, ui_lang: str) -> str:
    """'auto' segue o idioma da interface: pt -> pt-BR; os demais -> en."""
    if choice in SIM_LANGS:
        return choice
    return "pt-BR" if i18n.norm_lang(ui_lang) == "pt" else "en"


def student_avatar(roster_avatar: Any, clair_avatar: Any = None) -> int:
    """Indice 0..5 da imagem assets/pessoa_{k+1}.png para um aluno do roster (avatar = i % 6).
    A imagem da Clair vai para o fim da ordem: com ate 5 alunos nenhum aluno usa o rosto da Clair."""
    try:
        i = int(roster_avatar) % N_AVATARS
    except (TypeError, ValueError):
        i = 0
    try:
        c = int(clair_avatar)
    except (TypeError, ValueError):
        return i
    if not 0 <= c < N_AVATARS:
        return i
    order = [k for k in range(N_AVATARS) if k != c] + [c]
    return order[i]


# ------------------------------------------------------------------ configuracao / resultados
@dataclass
class SimSettings:
    n_students: int = 4
    task: str | None = None
    language: str = "pt-BR"
    conflict: str = "normal"
    pace: str = "pilot"
    speed: float = DEFAULT_SPEED
    seed: int | None = None
    backend: str = "llm"
    pacing: str = "realtime"  # "manual" so em testes

    def normalized(self) -> "SimSettings":
        def clamp(v, lo, hi, d):
            try:
                return min(max(type(d)(v), lo), hi)
            except (TypeError, ValueError):
                return d
        seed = None
        if self.seed not in (None, ""):
            try:
                seed = int(float(self.seed))
            except (TypeError, ValueError):
                seed = None
        return SimSettings(
            n_students=clamp(self.n_students, MIN_STUDENTS, MAX_STUDENTS, 4),
            task=self.task or None,
            language=self.language if self.language in SIM_LANGS else "pt-BR",
            conflict=self.conflict if self.conflict in CONFLICTS else "normal",
            pace=self.pace if self.pace in PACES else "pilot",
            speed=clamp(self.speed, SPEED_MIN, SPEED_MAX, DEFAULT_SPEED),
            seed=seed,
            backend=self.backend if self.backend in BACKENDS else "mock",
            pacing=self.pacing if self.pacing in ("realtime", "manual") else "realtime",
        )


@dataclass
class TickResult:
    messages: list[dict] = field(default_factory=list)  # novas linhas do historico do chat
    records: list[dict] = field(default_factory=list)  # registros (Estado ao vivo / Graficos)
    errors: list[Any] = field(default_factory=list)  # ClairError (ou outro objeto com .text(lang))
    typing: list[str] = field(default_factory=list)  # nomes de quem esta digitando
    typing_changed: bool = False
    status: str = "idle"  # ready | running | paused | done | error | idle (sem sessao)
    error: str | None = None  # erro do simulador (texto ja legivel, do collabsim)
    last_response: Any = None
    skipped: bool = False  # outro tick estava em andamento

    @property
    def ended(self) -> bool:
        return self.status in ("done", "error", "idle")


def _fmt_time(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S") if ms else ""


def _now_ms() -> int:
    return int(time.time() * 1000)


# ------------------------------------------------------------------ a ponte
class SimBridge:
    """Uma simulacao ligada a um (learning space, grupo) do Clair.

    `client`: objeto com send_message(ls, group, username, text, timestamp) e pull(ls, group)
    (o ClairClient da app; nos testes, um falso)."""

    def __init__(self, client, learning_space: str, group: str, settings: SimSettings, *,
                 clair_avatar: int | None = None, simulator=None,
                 now_ms: Callable[[], int] = _now_ms, clock: Callable[[], float] = time.monotonic):
        self.client = client
        self.ls = (learning_space or "").strip()
        self.group = (group or "").strip()
        self.settings = settings.normalized()
        self.clair_avatar = clair_avatar
        self._simulator = simulator
        self.now_ms = now_ms
        self.clock = clock
        self.session = None
        self.after = 0
        self.n_student_msgs = 0
        self.n_clair_msgs = 0
        self.n_sent = 0
        self.n_send_errors = 0
        self._last_pull = None
        self._typing: list[str] = []
        self._tick_lock = threading.Lock()
        self._closed = False
        self._names: dict[str, str] = {}
        self._avatars: dict[str, int] = {}
        self.seen: list = []  # usado se o chamador nao passar a lista dele

    # ---- ciclo de vida
    @property
    def simulator(self):
        if self._simulator is None:
            self._simulator = get_simulator(self.settings.backend)
        return self._simulator

    def start(self) -> "SimBridge":
        """Cria e inicia a sessao do collabsim. Qualquer falha vira SimUnavailable (chave de i18n legivel)."""
        try:
            return self._start()
        except SimUnavailable:
            raise
        except ValueError as e:  # configuracao/tarefa invalida (mensagem do collabsim, sem segredos)
            raise SimUnavailable("sim.err.config", err=str(e)[:200]) from None
        except Exception as e:
            if type(e).__name__ == "CapacityError":
                raise SimUnavailable("sim.err.capacity") from None
            if type(e).__name__ == "BackendUnavailable":
                raise SimUnavailable("sim.err.backend") from None
            raise SimUnavailable("sim.err.start", err=type(e).__name__) from None

    def _start(self) -> "SimBridge":
        s = self.settings
        av = f"pessoa_{int(self.clair_avatar) + 1}.png" if isinstance(self.clair_avatar, int) else None
        kw = dict(n_students=s.n_students, language=s.language, conflict=s.conflict, pace=s.pace,
                  speed=s.speed, pacing=s.pacing, participants=[{"name": CLAIR_NAME, "avatar": av}])
        if s.task:
            kw["task"] = s.task
        if s.seed is not None:
            kw["seed"] = s.seed
        self.session = self.simulator.new_session(**kw)
        for r in self.session.roster:
            if r.get("kind") == "student":
                self._names[r["id"]] = r["name"]
                self._avatars[r["id"]] = student_avatar(r.get("avatar"), self.clair_avatar)
        self._last_pull = self.clock()
        self.session.start()
        return self

    @property
    def active(self) -> bool:
        return self.session is not None and not self._closed and self.session.status in ("ready", "running", "paused")

    @property
    def status(self) -> str:
        if self.session is None:
            return "idle"
        return self.session.status

    def pause(self) -> None:
        if self.active:
            self.session.pause()

    def resume(self) -> None:
        if self.active:
            self.session.resume()

    def set_speed(self, speed: Any) -> None:
        try:
            v = min(max(float(speed), SPEED_MIN), SPEED_MAX)
        except (TypeError, ValueError):
            return
        self.settings.speed = v
        if self.active:
            try:
                self.session.set_speed(v)
            except Exception:
                pass

    def stop(self) -> None:
        if self.session is not None and self.active:
            try:
                self.session.stop()
            except Exception:
                pass

    def close(self) -> None:
        """Para a sessao, junta a thread e tira do Simulator. Idempotente."""
        self._closed = True
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass

    # ---- roster / renderizacao
    @property
    def roster(self) -> list[dict]:
        return self.session.roster if self.session is not None else []

    def student_message(self, ev, resp: Any = None) -> dict:
        return {"who": ev.display_name, "kind": "pessoa", "text": ev.text, "ts": ev.ts_ms,
                "av": self._avatars.get(ev.speaker, student_avatar(0, self.clair_avatar)), "sim": True,
                "tech": resp if isinstance(resp, dict) else None}

    def clair_message(self, iv, proactive: bool) -> dict:
        base = {"who": CLAIR_NAME, "kind": "clair", "ts": self.now_ms(), "proactive": proactive}
        if iv.text is not None:
            return {**base, "text": iv.text}
        return {**base, "text": None, "text_key": "chat.iv_unknown", "raw": iv.raw}

    def system_message(self, ev, lang: str = "pt") -> dict:
        return {"who": i18n.t("sim.system_who", lang), "kind": "info", "text": ev.text, "ts": ev.ts_ms}

    # ---- o ciclo
    def _relay(self, iv, proactive: bool, seen: list, now: int, out: TickResult) -> None:
        # chave estavel da app (hash do payload) + o texto normalizado: o mesmo texto em outro formato
        # (string vs {"message": ...}) tambem nao se repete no chat nem para os alunos
        keys = [iv.key] + (["text:" + " ".join(iv.text.split()).lower()] if iv.text else [])
        if any(is_seen(seen, k, now) for k in keys):
            return
        for k in keys:
            mark_seen(seen, k, now)
        out.messages.append(self.clair_message(iv, proactive))
        self.n_clair_msgs += 1
        if iv.text and self.active:
            try:
                self.session.say(iv.text, sender=CLAIR_NAME)
            except Exception as e:  # sessao encerrada, texto vazio...: o chat continua
                out.errors.append(ClairError(kind="error", key="sim.err.relay", detail=type(e).__name__))

    def _forward(self, ev, seen: list, out: TickResult) -> None:
        resp, err = None, None
        try:
            resp = self.client.send_message(self.ls, self.group, ev.display_name, ev.text, ev.ts_ms)
        except ClairError as e:
            err = e
        except Exception:
            err = ClairError(kind="error", key="err.unexpected")
        msg = self.student_message(ev, resp)
        out.messages.append(msg)
        self.n_student_msgs += 1
        if err is not None:
            self.n_send_errors += 1
            out.errors.append(err)
            return
        self.n_sent += 1
        out.last_response = resp
        ivs = extract_interventions(resp.get("agent_intervention")) if isinstance(resp, dict) else []
        if isinstance(resp, dict):
            out.records.append({"hora": _fmt_time(ev.ts_ms), "pessoa": ev.display_name, "texto": ev.text[:80],
                                "full_text": ev.text, "intent": resp.get("intent"),
                                "selected_move": resp.get("selected_move"),
                                "dialogue_state": resp.get("dialogue_state"),
                                "fuzzy_output": resp.get("fuzzy_output"), "intervention": bool(ivs)})
        now = self.now_ms()
        for iv in ivs:
            self._relay(iv, False, seen, now, out)

    def _pull(self, seen: list, out: TickResult) -> None:
        started = self.now_ms()
        try:
            payload = self.client.pull(self.ls, self.group)
        except ClairError as e:
            out.errors.append(e)
            return
        except Exception:
            out.errors.append(ClairError(kind="error", key="err.unexpected"))
            return
        for iv in extract_interventions(payload):
            self._relay(iv, True, seen, started, out)

    def pull_due(self, pull_every_s: float | None) -> bool:
        if not pull_every_s or self._last_pull is None:
            return False
        return self.clock() - self._last_pull >= max(5.0, float(pull_every_s))

    def tick(self, seen: list | None = None, *, pull_every_s: float | None = DEFAULT_PULL_SECONDS,
             lang: str = "pt") -> TickResult:
        """Um ciclo: novos eventos do simulador -> chat + Clair; intervencoes -> chat + simulador; /pull
        no intervalo. Nunca levanta excecao. `seen`: lista de chaves ja vistas (alterada no lugar)."""
        seen = self.seen if seen is None else seen
        out = TickResult(status=self.status)
        if self.session is None or self._closed:
            out.status = "idle"
            return out
        if not self._tick_lock.acquire(blocking=False):
            out.skipped = True
            return out
        try:
            try:
                polled = self.session.poll(self.after)
            except Exception:
                out.errors.append(ClairError(kind="error", key="sim.err.poll"))
                return out
            self.after = polled.get("next_after", self.after)
            for ev in polled.get("events", []):
                if ev.kind == "student":
                    self._forward(ev, seen, out)
                elif ev.kind == "system":
                    out.messages.append(self.system_message(ev, lang))
                # kind "external": as falas da Clair que a ponte repassou (ja estao no chat)
            if self.active and self.pull_due(pull_every_s):
                self._last_pull = self.clock()
                self._pull(seen, out)
            typing = [self._names.get(x, x) for x in (polled.get("typing") or [])]
            out.typing_changed = typing != self._typing
            self._typing = typing
            out.typing = typing
            out.status = self.session.status
            out.error = polled.get("error") or self.session.error
            return out
        finally:
            self._tick_lock.release()

    # ---- painel
    def info(self) -> dict:
        """Estado para a linha de status: status, minutos simulados, mensagens, custo."""
        d = {"status": self.status, "minutes": 0.0, "messages": self.n_student_msgs, "clair": self.n_clair_msgs,
             "usd": 0.0, "est_usd": 0.0, "backend": self.settings.backend, "speed": self.settings.speed,
             "send_errors": self.n_send_errors}
        if self.session is None:
            return d
        try:
            d["minutes"] = round(float(self.session.summary().get("t_sim") or 0.0) / 60.0, 1)
        except Exception:
            pass
        try:
            u = self.session.usage()
            d["usd"] = float(u.get("usd") or 0.0)
            d["est_usd"] = float(u.get("est_usd") or 0.0)
            d["calls"] = int(u.get("calls") or 0)
        except Exception:
            pass
        return d


def status_text(info: dict | None, lang: str = "pt") -> str:
    """Linha de status (texto simples) a partir de SimBridge.info()."""
    if not info:
        return i18n.t("sim.st.none", lang)
    st = i18n.t(f"sim.st.{info.get('status', 'idle')}", lang)
    cost = (i18n.t("sim.cost.mock", lang, est=f"{info.get('est_usd', 0.0):.4f}") if info.get("backend") == "mock"
            else i18n.t("sim.cost.llm", lang, usd=f"{info.get('usd', 0.0):.4f}"))
    return i18n.t("sim.status_line", lang, status=st, min=f"{info.get('minutes', 0.0):.1f}",
                  n=info.get("messages", 0), clair=info.get("clair", 0), cost=cost,
                  speed=f"{info.get('speed', DEFAULT_SPEED):g}")


def connection_text(result: dict | None, lang: str = "pt") -> tuple[bool, str]:
    """(ok, texto amigavel) a partir de Simulator.check_connection(); nunca expoe a chave."""
    if not result:
        return False, i18n.t("sim.conn.fail", lang, err="?")
    if result.get("ok"):
        if result.get("backend") != "llm":
            return True, i18n.t("sim.conn.mock", lang)
        lat = result.get("latency_s")
        return True, i18n.t("sim.conn.ok", lang, model=result.get("model") or "?",
                            lat=f"{lat:.1f}" if isinstance(lat, (int, float)) else "?")
    err = result.get("error") or result.get("error_kind") or "?"
    hint = result.get("hint")
    txt = i18n.t("sim.conn.fail", lang, err=err)
    if hint:
        txt += " " + i18n.t("sim.conn.hint", lang, hint=hint)
    return False, txt


# ------------------------------------------------------------------ registro por sessao do navegador
class BridgeRegistry:
    """sid (id da sessao da app) -> SimBridge. Fecha a ponte anterior ao trocar."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, SimBridge] = {}

    def get(self, sid: str | None) -> SimBridge | None:
        if not sid:
            return None
        with self._lock:
            return self._items.get(sid)

    def put(self, sid: str, bridge: SimBridge) -> None:
        with self._lock:
            old = self._items.get(sid)
            self._items[sid] = bridge
        if old is not None and old is not bridge:
            old.close()

    def close(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            b = self._items.pop(sid, None)
        if b is not None:
            b.close()
        return b is not None

    def active(self, sid: str | None) -> bool:
        b = self.get(sid)
        return bool(b and b.active)

    def close_all(self) -> None:
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
        for b in items:
            b.close()


REGISTRY = BridgeRegistry()
