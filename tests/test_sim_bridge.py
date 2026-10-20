"""SimBridge: collabsim (backend mock, sem custo) <-> ClairClient falso (sem servidor)."""
from __future__ import annotations

import time

import pytest
from collabsim import Simulator

import sim_bridge
from clair_client import ClairError
from sim_bridge import BridgeRegistry, SimBridge, SimSettings, student_avatar

LS, GROUP = "teste01", "g-sim1"
IV_TEXT = "Pessoal, o que cada um descobriu até agora?"


class FakeClair:
    """Imita ClairClient: grava send_message e devolve respostas/pulls roteirizados."""

    has_token = True
    max_message_chars = 2000

    def __init__(self, send_script=None, pull_script=None):
        self.sent: list[dict] = []
        self.pulls = 0
        self.send_script = list(send_script or [])
        self.pull_script = list(pull_script or [])

    def send_message(self, learning_space, group, username, text, timestamp=None, **kw):
        self.sent.append({"ls": learning_space, "group": group, "username": username, "text": text, "ts": timestamp})
        item = self.send_script.pop(0) if self.send_script else {"intent": "x", "agent_intervention": None}
        if isinstance(item, Exception):
            raise item
        return item

    def pull(self, learning_space, group, timestamp=None):
        self.pulls += 1
        item = self.pull_script.pop(0) if self.pull_script else None
        if isinstance(item, Exception):
            raise item
        return item

    # o que build_app pode chamar ao montar a tela (nada de rede)
    def modes(self):
        return ["apt-base"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def sim():
    s = Simulator(backend="mock", max_sessions=4)
    yield s
    s.close()


def make(sim, client, *, n=3, seed=1, clock=None, pacing="manual", speed=3.0, clair_avatar=1):
    b = SimBridge(client, LS, GROUP, SimSettings(n_students=n, seed=seed, backend="mock", pacing=pacing, speed=speed,
                                                 language="pt-BR"),
                  simulator=sim, clair_avatar=clair_avatar, clock=clock or Clock())
    return b.start()


def step_n(b, k):
    evs = []
    for _ in range(k):
        evs += [e for e in b.session.step() if e.kind == "student"]
    return evs


def clair_events(b):
    return [e for e in b.session.events() if e.kind == "external" and e.display_name == "Clair"]


# ------------------------------------------------------------------ encaminhamento
def test_student_messages_forwarded_with_usernames(sim):
    fc = FakeClair()
    b = make(sim, fc)
    evs = step_n(b, 3)
    res = b.tick([], pull_every_s=None)
    assert not res.errors
    assert [c["username"] for c in fc.sent] == [e.display_name for e in evs]
    assert [c["text"] for c in fc.sent] == [e.text for e in evs]
    assert all(c["ls"] == LS and c["group"] == GROUP for c in fc.sent)
    assert [c["ts"] for c in fc.sent] == [e.ts_ms for e in evs]
    names = {r["name"] for r in b.roster if r["kind"] == "student"}
    assert {c["username"] for c in fc.sent} <= names
    assert "Clair" not in names
    # o mesmo evento nunca e reenviado
    assert b.tick([], pull_every_s=None).messages == []
    assert len(fc.sent) == 3
    assert len(res.records) == 3 and res.records[0]["pessoa"] == evs[0].display_name


def test_render_mapping_names_and_avatars(sim):
    b = make(sim, FakeClair(), n=4, clair_avatar=1)
    step_n(b, 6)
    res = b.tick([], pull_every_s=None)
    roster = {r["id"]: r for r in b.roster if r["kind"] == "student"}
    by_name = {r["name"]: r for r in roster.values()}
    studs = [m for m in res.messages if m["kind"] == "pessoa"]
    assert studs
    for m in studs:
        r = by_name[m["who"]]
        assert m["av"] == student_avatar(r["avatar"], 1)
        assert m["av"] != 1  # com ate 5 alunos ninguem usa o rosto da Clair
        assert m["sim"] is True and m["ts"]


def test_student_avatar_mapping():
    assert [student_avatar(i, 1) for i in range(6)] == [0, 2, 3, 4, 5, 1]
    assert sorted(student_avatar(i, 3) for i in range(6)) == list(range(6))
    assert student_avatar(7, None) == 1  # sem avatar da Clair: i % 6
    assert student_avatar("x", 1) == 0


# ------------------------------------------------------------------ intervencoes
def test_interventions_relayed_once_dedupe(sim):
    payload = {"agent_intervention": {"message": IV_TEXT}, "intent": "x"}
    fc = FakeClair(send_script=[payload], pull_script=[{"agent_intervention": {"message": IV_TEXT}}, [IV_TEXT]])
    clock = Clock()
    b = make(sim, fc, clock=clock)
    seen: list = []
    step_n(b, 1)
    res = b.tick(seen, pull_every_s=5)
    clair_rows = [m for m in res.messages if m["kind"] == "clair"]
    assert len(clair_rows) == 1 and clair_rows[0]["text"] == IV_TEXT and clair_rows[0]["proactive"] is False
    assert [e.text for e in clair_events(b)] == [IV_TEXT]
    # o mesmo texto volta pelo /pull (duas vezes, em formatos diferentes): nao repete no chat nem no simulador
    for _ in range(2):
        clock.t += 10
        res = b.tick(seen, pull_every_s=5)
        assert not [m for m in res.messages if m["kind"] == "clair"]
    assert fc.pulls == 2
    assert [e.text for e in clair_events(b)] == [IV_TEXT]
    # o evento "external" da Clair nao e reenviado ao Clair
    assert all(c["username"] != "Clair" for c in fc.sent)


def test_proactive_pull_intervention_relayed(sim):
    fc = FakeClair(pull_script=[{"agent_intervention": {"message": "Bem-vindos!"}}])
    clock = Clock()
    b = make(sim, fc, clock=clock)
    assert b.tick([], pull_every_s=30).messages == [] and fc.pulls == 0  # ainda nao venceu o intervalo
    clock.t += 31
    res = b.tick([], pull_every_s=30)
    assert fc.pulls == 1
    assert [m["text"] for m in res.messages if m["kind"] == "clair"] == ["Bem-vindos!"]
    assert res.messages[-1]["proactive"] is True
    assert [e.text for e in clair_events(b)] == ["Bem-vindos!"]
    # pull desligado
    clock.t += 100
    b.tick([], pull_every_s=None)
    assert fc.pulls == 1


def test_unrecognized_intervention_rendered_not_relayed(sim):
    fc = FakeClair(send_script=[{"agent_intervention": {"weird": [1, 2]}}])
    b = make(sim, fc)
    step_n(b, 1)
    res = b.tick([], pull_every_s=None)
    rows = [m for m in res.messages if m["kind"] == "clair"]
    assert len(rows) == 1 and rows[0]["text"] is None and rows[0]["text_key"] == "chat.iv_unknown"
    assert clair_events(b) == []


# ------------------------------------------------------------------ erros
def test_client_errors_do_not_kill_the_session(sim):
    fc = FakeClair(send_script=[ClairError(kind="connection", key="err.offline"), RuntimeError("boom")],
                   pull_script=[ClairError(kind="timeout", key="err.timeout")])
    clock = Clock()
    b = make(sim, fc, clock=clock)
    step_n(b, 2)
    clock.t += 60
    res = b.tick([], pull_every_s=30)
    assert len(fc.sent) == 2 and fc.pulls == 1
    kinds = [getattr(e, "kind", None) for e in res.errors]
    assert kinds == ["connection", "error", "timeout"]
    assert res.errors[1].key == "err.unexpected"  # excecao generica: sem detalhes vazados
    assert b.active and res.status == "running" and not res.ended
    # as mensagens dos alunos aparecem mesmo assim
    assert len([m for m in res.messages if m["kind"] == "pessoa"]) == 2
    assert b.n_send_errors == 2
    # o Clair volta: o proximo envio funciona
    step_n(b, 1)
    res = b.tick([], pull_every_s=None)
    assert not res.errors and len(fc.sent) == 3


def test_tick_never_raises_on_broken_session(sim):
    b = make(sim, FakeClair())

    class Broken:
        status = "running"
        error = None

        def poll(self, after):
            raise RuntimeError("x")

    b.session = Broken()
    res = b.tick([])
    assert res.errors and res.errors[0].key == "sim.err.poll"


# ------------------------------------------------------------------ ciclo de vida
def test_stop_and_close(sim):
    b = make(sim, FakeClair())
    sid = b.session.id
    assert sim.get_session(sid) is not None
    b.stop()
    res = b.tick([], lang="pt")
    assert res.ended and res.status == "done"
    assert any(m["kind"] == "info" and m["who"] == "Simulação" for m in res.messages)  # aviso "sessão encerrada"
    b.close()
    assert sim.get_session(sid) is None
    assert not b.active
    assert b.tick([]).status == "idle"
    b.close()  # idempotente


def test_registry_replaces_and_closes(sim):
    reg = BridgeRegistry()
    b1, b2 = make(sim, FakeClair()), make(sim, FakeClair(), seed=2)
    reg.put("s", b1)
    assert reg.active("s")
    reg.put("s", b2)
    assert not b1.active and b1.session.status == "done"
    assert reg.get("s") is b2
    assert reg.close("s") and not b2.active and reg.get("s") is None
    assert not reg.close("s") and not reg.active(None)


def test_realtime_session_forwards(sim):
    """Pacing real (thread do collabsim), velocidade maxima da UI (4x)."""
    fc = FakeClair()
    b = make(sim, fc, pacing="realtime", speed=4.0)
    try:
        deadline = time.time() + 30
        while time.time() < deadline and len(fc.sent) < 2:
            res = b.tick([], pull_every_s=None)
            assert not res.errors
            time.sleep(0.2)
        assert len(fc.sent) >= 2
        b.pause()
        assert b.status == "paused"
        b.resume()
        b.set_speed(10)  # limitado a 4x
        assert b.settings.speed == 4.0
    finally:
        b.close()


# ------------------------------------------------------------------ configuracao e textos
def test_settings_normalized():
    s = SimSettings(n_students=9, speed=10, seed="12", pace="x", conflict="huge", backend="other", language="fr").normalized()
    assert (s.n_students, s.speed, s.seed, s.pace, s.conflict, s.backend, s.language) == (6, 4.0, 12, "pilot", "normal", "mock", "pt-BR")
    assert SimSettings(n_students=1, speed=0.1, seed=None).normalized().n_students == 2
    assert SimSettings(seed="").normalized().seed is None
    assert SimSettings(seed=" abc ").normalized().seed is None and SimSettings(seed=" 7 ").normalized().seed == 7
    assert sim_bridge.sim_language("auto", "pt") == "pt-BR"
    assert sim_bridge.sim_language("auto", "de") == "en"
    assert sim_bridge.sim_language("en", "pt") == "en"


def test_dedupe_helpers_in_place():
    seen: list = []
    sim_bridge.mark_seen(seen, "k", 1000)
    assert sim_bridge.is_seen(seen, "k", 1000 + 119_000)
    assert not sim_bridge.is_seen(seen, "k", 1000 + 121_000)
    ref = seen
    sim_bridge.mark_seen(seen, "j", 1000 + 200_000)
    assert ref is seen and [x[0] for x in seen] == ["j"]


def test_missing_key_means_llm_unavailable(monkeypatch):
    for k in sim_bridge.KEY_ENV:
        monkeypatch.delenv(k, raising=False)
    sim_bridge.reset_simulators()
    assert not sim_bridge.has_api_key()
    with pytest.raises(sim_bridge.SimUnavailable) as e:
        sim_bridge.get_simulator("llm")
    assert e.value.key == "sim.err.no_key"
    assert "COLLABSIM_ANTHROPIC_API_KEY" in e.value.text("pt")
    # o fallback ANTHROPIC_API_KEY tambem vale (sem rede: so le o ambiente)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    assert sim_bridge.api_key() == "sk-ant-test-not-real"
    monkeypatch.setenv("COLLABSIM_ANTHROPIC_API_KEY", "sk-ant-test-2")
    assert sim_bridge.api_key() == "sk-ant-test-2"


def test_start_errors_become_sim_unavailable(sim):
    b = SimBridge(FakeClair(), LS, GROUP, SimSettings(task="no-such-task", backend="mock", pacing="manual"), simulator=sim)
    with pytest.raises(sim_bridge.SimUnavailable) as e:
        b.start()
    assert e.value.key == "sim.err.config"
    small = Simulator(backend="mock", max_sessions=1)
    try:
        make(small, FakeClair())
        with pytest.raises(sim_bridge.SimUnavailable) as e:
            make(small, FakeClair(), seed=2)
        assert e.value.key == "sim.err.capacity"
    finally:
        small.close()


def test_status_and_connection_texts(sim):
    b = make(sim, FakeClair())
    step_n(b, 2)
    b.tick([], pull_every_s=None)
    info = b.info()
    assert info["messages"] == 2 and info["backend"] == "mock" and info["minutes"] >= 0
    line = sim_bridge.status_text(info, "pt")
    assert "2 mensagens" in line and "mock" in line
    assert "student messages" in sim_bridge.status_text(info, "en")
    ok, txt = sim_bridge.connection_text(sim.check_connection(), "pt")
    assert ok and "mock" in txt.lower()
    ok, txt = sim_bridge.connection_text({"ok": False, "backend": "llm", "error": "sem crédito", "hint": "ssl"}, "en")
    assert not ok and "sem crédito" in txt and "ssl" in txt
    assert sim_bridge.list_tasks("pt-BR") and all(isinstance(v, str) for _, v in sim_bridge.list_tasks("en"))


def test_locales_have_sim_keys():
    import i18n
    keys = {k for k in i18n.catalog("pt") if k.startswith("sim.") or k == "sub.sim"}
    assert len(keys) > 40
    for lg in i18n.LANGS:
        missing = keys - set(i18n.catalog(lg))
        assert not missing, (lg, missing)
        for k in keys:  # mesmos placeholders em todos os idiomas
            assert i18n.placeholders(i18n.catalog(lg)[k]) == i18n.placeholders(i18n.catalog("pt")[k]), (lg, k)


# ------------------------------------------------------------------ app
def test_build_app_has_simulation_tab():
    import gradio as gr

    import app
    demo = app.build_app(client=FakeClair())
    ids = {getattr(b, "elem_id", None) for b in demo.blocks.values()}
    for want in ("cl-simcard", "cl-sim-start", "cl-sim-n", "cl-sim-task", "cl-sim-speed", "cl-sim-backend", "cl-simbar",
                 "cl-sim-check"):
        assert want in ids
    tabs = [b for b in demo.blocks.values() if isinstance(b, gr.Tab)]
    assert any(b.label == "Simulação" for b in tabs)
    bar = next(b for b in demo.blocks.values() if getattr(b, "elem_id", None) == "cl-simbar")
    assert bar.visible is False  # o composer normal aparece fora da simulacao
    backend = next(b for b in demo.blocks.values() if getattr(b, "elem_id", None) == "cl-sim-backend")
    assert backend.value == "mock"  # sem chave no ambiente
    speed = next(b for b in demo.blocks.values() if getattr(b, "elem_id", None) == "cl-sim-speed")
    assert speed.value == 3.0 and speed.minimum == 1 and speed.maximum == 4
    seed = next(b for b in demo.blocks.values() if getattr(b, "elem_id", None) == "cl-sim-seed")
    assert seed.value == ""  # vazio = semente sorteada (nunca 0 fixo)


def test_render_chat_typing_and_sim_rows():
    import app
    rows = [{"who": "Lara", "kind": "pessoa", "text": "oi <b>", "ts": 1792501590983, "av": 2, "sim": True}]
    html = app.render_chat(rows, typing=["Caio", "Enzo"], lang="pt")
    assert "cl-av3" in html and "Lara" in html and "oi &lt;b&gt;" in html
    assert "Caio e Enzo estão digitando" in html
    assert "Lara está digitando" in app.render_chat([], typing=["Lara"], lang="pt")
    assert "cl-typing" not in app.render_chat(rows, lang="pt")
