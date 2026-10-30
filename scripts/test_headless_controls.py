"""Step 2.5 for a headless seat: the stop button interrupts through the supervisor (never an Esc into the pane), a
model switch goes through the supervisor (never typed, never the host settings file), hooks are read by the pane
id the supervisor reports (a synthetic one without a pane), and Claude's "0% used" is no reading. Fakes and temp
files only."""
import asyncio
import importlib.util
import json
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import headless_switch as HS  # noqa: E402


@pytest.fixture
def G(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("watch_gateway_hlctl", HERE / "watch_gateway.py")
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    monkeypatch.setattr(g, "_authorized", lambda r: True)
    monkeypatch.setattr(g, "_tmux_session_names", lambda: ["dev-demo"])
    monkeypatch.setattr(g, "_headless_seat_for", lambda s: "dev-demo" if s == "dev-demo" else None)
    monkeypatch.setattr(g, "_tmux", lambda *a, **k: pytest.fail(f"tmux used for a headless seat: {a}"))
    g._sent = []

    def request(seat, payload, **kw):
        g._sent.append((seat, payload, kw))
        return g._reply.get(payload["cmd"], {"ok": True})
    g._reply = {}
    monkeypatch.setattr(HS, "request", request)
    return g


class Req:
    headers = {}

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def test_interrupt_goes_to_the_supervisor(G, monkeypatch):
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(
        get_agent_status=lambda s: {"state": "working"},
        mark_turn_interrupted=lambda s: pytest.fail("a hook marker for a headless seat")))
    resp = asyncio.run(G.handle_agent_interrupt(Req({"session": "dev-demo"})))
    body = json.loads(resp.text)
    assert resp.status == 200 and body["interrupted"] and body["headless"]
    assert G._sent == [("dev-demo", {"cmd": "interrupt"}, {})]


def test_interrupt_refused_when_not_mid_turn_or_supervisor_down(G, monkeypatch):
    state = {"s": "idle"}
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(
        get_agent_status=lambda s: {"state": state["s"]}))
    assert asyncio.run(G.handle_agent_interrupt(Req({"session": "dev-demo"}))).status == 409
    state["s"] = "working"

    def down(*a, **k):
        raise HS.SupervisorDown("no socket")
    monkeypatch.setattr(HS, "request", down)
    assert asyncio.run(G.handle_agent_interrupt(Req({"session": "dev-demo"}))).status == 503


def _switch(G, monkeypatch, reply):
    G._reply["set_model"] = reply
    import identity_store.identity_writer as IW  # noqa: F401 — resolved by the gateway's own import
    monkeypatch.setitem(sys.modules, "identity_store.identity_writer",
                        types.SimpleNamespace(update_session=lambda *a, **k: True))
    import identity_store
    monkeypatch.setattr(identity_store, "identity_writer", sys.modules["identity_store.identity_writer"],
                        raising=False)
    desc = {"how": "live"}
    return G._switch_model_now("dev-demo", "sample-model-2", "dev-demo", {}, "claude", "sample-model",
                               [], desc, "operator")


def test_model_switch_goes_to_the_supervisor(G, monkeypatch):
    import scripts.agent_model as AM
    monkeypatch.setattr(AM, "switch_live", lambda **k: pytest.fail("typed /model into a headless seat"))
    status, body = _switch(G, monkeypatch, {"ok": True, "model": "sample-model-2", "how": "set_model"})
    assert status == 200 and body["switching"] == "switched" and body["stored"] is True
    seat, payload, kw = G._sent[0]
    assert payload == {"cmd": "set_model", "model": "sample-model-2"} and kw["timeout"] >= 10


def test_a_busy_headless_seat_answers_409_with_its_state(G, monkeypatch):
    status, body = _switch(G, monkeypatch, {"ok": False, "reason": "busy", "state": "working"})
    assert status == 409 and body["state"] == "working"         # _put_model defers this to the turn's end
    assert body["state"] in G.MID_TURN_STATES | G.STALLED_STATES


def _status_module(monkeypatch, tmp_path):
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("agent_status_hlctl", HERE / "agent-status.py")
    a = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)
    return a


def test_pane_id_of_a_headless_seat_comes_from_its_supervisor(monkeypatch, tmp_path):
    A = _status_module(monkeypatch, tmp_path)
    monkeypatch.setattr(A, "_headless_seat", lambda s: "dev-demo" if s == "dev-demo" else None)
    monkeypatch.setattr(A, "_supervisor_status", lambda seat: {"pane_id": "%hl-dev-demo"})
    monkeypatch.setattr(A.subprocess, "run", lambda *a, **k: pytest.fail("tmux asked for a headless seat"))
    assert A.get_pane_id("dev-demo") == "%hl-dev-demo"
    # Its hook file is read by that id.
    ev = tmp_path / "panes"
    ev.mkdir()
    (ev / "hl-dev-demo.json").write_text(json.dumps({"state": "working", "event": "PreToolUse", "ts": time.time()}))
    monkeypatch.setattr(A, "EVENTS_DIR", str(ev))
    assert A._read_hook_event("dev-demo")["event"] == "PreToolUse"


def test_no_usage_yet_is_no_reading(monkeypatch, tmp_path):
    A = _status_module(monkeypatch, tmp_path)
    assert A._headless_context({"tokens": 0, "window": 1000000}) == {
        "context_pct_of_window": None, "context_pct_of_budget": None}


def test_claude_zero_percent_used_is_no_reading(tmp_path):
    import context_reading as CR
    sid = "00000000-0000-4000-8000-0000000000cc"
    tx = tmp_path / "t.jsonl"
    tx.write_text(json.dumps({"type": "user", "timestamp": "2020-01-01T00:00:00Z",
                              "message": {"role": "user", "content": "x"}}) + "\n")
    bridge = Path(CR.bridge_path(sid, str(tmp_path)))

    def reading(remaining, used):
        bridge.write_text(json.dumps({"session_id": sid, "remaining_percentage": remaining, "used_pct": used,
                                      "timestamp": int(time.time())}))
        return CR.claude_context(sid, str(tx), tmp_dir=str(tmp_path))
    assert reading(100, 0) == (None, None)
    assert reading(60, 50) == (40, 50)


# --- the Dev round trip route ------------------------------------------------------------------------

@pytest.fixture
def D(G, monkeypatch, tmp_path):
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_protected_refusal", lambda r, s: None)
    seats = {"dev-demo"}
    monkeypatch.setattr(HS, "headless_seat", lambda s, orch_dir=None, include_dev=False, runtime=None:
                        s if s in seats else None)
    G._dev = {"on": False}
    monkeypatch.setattr(HS, "in_dev", lambda seat, orch_dir=None: G._dev["on"])
    G._screen = {"state": "idle"}
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(get_agent_status=lambda s: G._screen))
    return G


def test_dev_on_and_off_go_to_the_supervisor(D):
    D._reply["dev"] = {"ok": True, "tui_pid": 42, "stopped_ms": 400}
    r = asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo", "on": True})))
    assert r.status == 200 and json.loads(r.text)["mode"] == "dev"
    assert D._sent[-1][1] == {"cmd": "dev"} and D._sent[-1][2]["timeout"] >= 30
    D._dev["on"] = True
    D._reply["chat"] = {"ok": True, "exit_ms": 500, "headless_ms": 900, "state": "idle"}
    r = asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo", "on": False})))
    assert r.status == 200 and json.loads(r.text)["mode"] == "chat" and D._sent[-1][1] == {"cmd": "chat"}


def test_back_to_chat_waits_for_a_busy_tui_unless_forced(D):
    D._dev["on"] = True
    for state in ("working", "waiting_permission", "stranded_input"):
        D._screen = {"state": state}
        r = asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo", "on": False})))
        assert r.status == 409 and json.loads(r.text)["busy"]
    assert D._sent == []
    D._reply["chat"] = {"ok": True}
    r = asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo", "on": False, "force": True})))
    assert r.status == 200


def test_dev_route_refusals(D, monkeypatch):
    assert asyncio.run(D.handle_agent_dev(Req({"session": "pm-demo", "on": True}))).status == 404
    assert asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo"}))).status == 400
    D._reply["dev"] = {"ok": False, "reason": "busy", "state": "working"}
    assert asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo", "on": True}))).status == 409

    def down(*a, **k):
        raise HS.SupervisorDown("no socket")
    monkeypatch.setattr(HS, "request", down)
    assert asyncio.run(D.handle_agent_dev(Req({"session": "dev-demo", "on": True}))).status == 503


def test_dev_route_has_a_scope(G):
    assert G.ROUTE_SCOPES[("POST", "/agent-dev")] == "inject"
