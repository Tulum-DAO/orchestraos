"""The per-seat headless switch (scripts/headless_switch.py) and the two pane writers that consult it:
the gateway's verified_inject and the message router's delivery. Default off; Claude only; a
switched-on seat goes to its supervisor and nothing is typed into its pane. Temp data dirs, fake
tmux and a fake supervisor client only."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import headless_switch as HS  # noqa: E402


def _registry(orch, rows):
    (orch / "registry.json").write_text(json.dumps({"agents": rows}))


@pytest.fixture
def orch(tmp_path):
    (tmp_path / "state").mkdir()
    _registry(tmp_path, {"dev-demo": {"runtime": "claude", "tmux_session": "dev-demo"},
                         "qa-demo": {"runtime": "codex", "tmux_session": "qa-demo"},
                         "pm-demo": {"runtime": "claude", "tmux_session": "pm-demo-g2"}})
    return tmp_path


# --- the switch ---------------------------------------------------------------------------------

def test_default_off_with_no_file(orch):
    assert not HS.switch_path(orch).exists()
    assert HS.headless_seat("dev-demo", orch_dir=orch) is None
    assert HS.is_headless("dev-demo", orch_dir=orch) is False


def test_unreadable_file_reads_off(orch):
    HS.switch_path(orch).write_text("{not json")
    assert HS.headless_seat("dev-demo", orch_dir=orch) is None


def test_on_for_a_claude_seat_only(orch):
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    assert HS.headless_seat("dev-demo", orch_dir=orch) == "dev-demo"
    assert HS.headless_seat("pm-demo", orch_dir=orch) is None        # listed seats only
    HS.set_switch("dev-demo", False, runtime="claude", orch_dir=orch)
    assert HS.headless_seat("dev-demo", orch_dir=orch) is None       # read again, no restart


def test_non_claude_runtime_cannot_be_switched_on(orch):
    with pytest.raises(ValueError):
        HS.set_switch("qa-demo", True, runtime="codex", orch_dir=orch)
    for rt in ("gemini", "grok", ""):
        with pytest.raises(ValueError):
            HS.set_switch("qa-demo", True, runtime=rt, orch_dir=orch)


def test_non_claude_runtime_stays_on_tmux_even_if_listed(orch):
    # Hand-edited file, or a seat whose runtime changed after it was switched on.
    HS.switch_path(orch).write_text(json.dumps({"seats": {
        "qa-demo": {"headless": True, "runtime": "claude"},
        "dev-demo": {"headless": True, "runtime": "gemini"}}}))
    assert HS.headless_seat("qa-demo", orch_dir=orch) is None        # registry says codex
    assert HS.headless_seat("dev-demo", orch_dir=orch) is None       # recorded runtime gemini


def test_callers_runtime_must_be_claude(orch):
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    assert HS.headless_seat("dev-demo", "claude", orch_dir=orch) == "dev-demo"
    assert HS.headless_seat("dev-demo", "codex", orch_dir=orch) is None


def test_session_name_resolves_through_the_registry(orch):
    HS.set_switch("pm-demo", True, runtime="claude", orch_dir=orch)
    assert HS.headless_seat("pm-demo-g2", orch_dir=orch) == "pm-demo"


def test_supervisor_down_when_no_socket(orch):
    with pytest.raises(HS.SupervisorDown):
        HS.status("dev-demo", orch_dir=orch)


# --- the gateway --------------------------------------------------------------------------------

@pytest.fixture
def G(monkeypatch, orch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(orch))
    monkeypatch.setenv("ORCH_DIR", str(orch))
    monkeypatch.setattr(sys, "path", [str(HERE), str(ROOT)] + list(sys.path))
    spec = importlib.util.spec_from_file_location("watch_gateway_headless", HERE / "watch_gateway.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "ORCH_DIR", orch)
    m._typed = []

    def no_tmux(*a, timeout=5):
        m._typed.append(a)
        return types.SimpleNamespace(returncode=1, stdout="")
    monkeypatch.setattr(m, "_tmux", no_tmux)
    return m


class _FakeHS:
    """Stands in for the supervisor client inside headless_switch."""

    def __init__(self, monkeypatch, reply=None, down=False):
        self.sent = []
        self.reply, self.down = reply or {"ok": True, "state": "queued", "duplicate": False}, down
        monkeypatch.setattr(HS, "send", self.send)

    def send(self, seat, text, msg_id, **kw):
        if self.down:
            raise HS.SupervisorDown("no socket")
        self.sent.append((seat, text, msg_id))
        return dict(self.reply, id=msg_id)


def test_gateway_switch_off_takes_the_pane_path(G, monkeypatch):
    fake = _FakeHS(monkeypatch)
    seen = []
    monkeypatch.setattr(G, "_verified_inject_locked",
                        lambda s, t, f=False, i=False: seen.append((s, t)) or (True, {"state": "idle"}))
    ok, info = G.verified_inject("dev-demo", "Sample hello")
    assert ok and seen == [("dev-demo", "Sample hello")] and fake.sent == []


def test_gateway_switch_on_goes_to_the_supervisor_not_the_pane(G, monkeypatch, orch):
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    fake = _FakeHS(monkeypatch)
    monkeypatch.setattr(G, "_verified_inject_locked",
                        lambda *a, **k: pytest.fail("the pane path ran for a headless seat"))
    ok, info = G.verified_inject("dev-demo", "Sample hello\n")
    assert ok and info["headless"] and info["msg_state"] == "queued"
    assert fake.sent == [("dev-demo", "Sample hello", info["message_id"])]
    assert G._typed == []                                    # nothing sent to tmux at all


def test_gateway_non_claude_seat_stays_on_the_pane(G, monkeypatch, orch):
    HS.switch_path(orch).write_text(json.dumps({"seats": {"qa-demo": {"headless": True, "runtime": "claude"}}}))
    fake = _FakeHS(monkeypatch)
    seen = []
    monkeypatch.setattr(G, "_verified_inject_locked",
                        lambda s, t, f=False, i=False: seen.append(s) or (True, {}))
    G.verified_inject("qa-demo", "Sample")
    assert seen == ["qa-demo"] and fake.sent == []


def test_gateway_headless_refusals(G, monkeypatch, orch):
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    fake = _FakeHS(monkeypatch)
    ok, info = G.verified_inject("dev-demo", "/exit")
    assert not ok and info["reason"] == "slash_command_refused"
    ok, info = G.verified_inject("dev-demo", "/model sample", idle_only=True)
    assert not ok and info["reason"] == "busy"        # the model switch reads busy: nothing typed
    assert fake.sent == []
    fake.reply = {"ok": False, "reason": "waiting", "pending": 1}
    ok, info = G.verified_inject("dev-demo", "Sample")
    assert not ok and info["reason"] == "busy" and info["state"] == "waiting"
    fake.down = True
    ok, info = G.verified_inject("dev-demo", "Sample")
    assert not ok and info["reason"] == "busy" and info["state"] == "offline"
    assert G._typed == []


# --- the router ---------------------------------------------------------------------------------

@pytest.fixture
def MR(monkeypatch, orch):
    spec = importlib.util.spec_from_file_location("mr_headless", HERE / "message-router.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "ORCHESTRA_DIR", orch)
    m._typed = []
    monkeypatch.setattr(m, "tmux", lambda *a, **k: m._typed.append(a) or types.SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setattr(m, "log", lambda *a, **k: None)
    return m


class _Store:
    def __init__(self, claim=True):
        self.calls, self._claim = [], claim

    def claim(self, i):
        self.calls.append(("claim", i))
        return self._claim

    def __getattr__(self, name):
        if name in ("deliver", "acknowledge", "defer", "fail"):
            return lambda i, **k: self.calls.append((name, i))
        raise AttributeError(name)


class _Sup:
    SupervisorDown = HS.SupervisorDown
    DELIVERED_STATES = HS.DELIVERED_STATES

    def __init__(self, state="idle", reply=None, echo="delivered", down=False):
        self.state, self.reply, self.echo, self.down, self.sent = state, reply, echo, down, []

    def status(self, seat, **kw):
        if self.down:
            raise HS.SupervisorDown("no socket")
        return {"state": self.state}

    def send(self, seat, text, mid, **kw):
        self.sent.append((seat, text, mid))
        return self.reply or {"ok": True, "state": "queued", "duplicate": False}

    def wait_delivered(self, seat, mid, **kw):
        return self.echo


MSG = {"id": "msg_sample1", "from_agent": "pm-demo", "to_agent": "dev-demo", "subject": "Sample",
       "body": "Example body", "priority": "medium", "type": "message", "created_at": "2026-01-01T00:00:00"}


def test_router_switch_reads_default_off(MR, orch):
    assert MR.headless_seat_for("dev-demo") is None


def test_router_inject_never_pastes_into_a_headless_pane(MR, orch):
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    assert MR.inject("dev-demo", "Sample text", "claude") == "held"
    assert MR._typed == []


def test_router_routing_runtime_guard(MR, orch):
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    assert MR.headless_seat_for("dev-demo", "claude") == "dev-demo"
    assert MR.headless_seat_for("dev-demo", "codex") is None


def test_router_headless_idle_delivers_and_acks_on_echo(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: f"[MSG from {msg['from_agent']}] {msg['subject']}")
    st, sup = _Store(), _Sup()
    assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", hs=sup) == "submitted"
    assert sup.sent == [("dev-demo", "[MSG from pm-demo] Sample", "msg_sample1")]   # row id = message id
    assert st.calls == [("claim", "msg_sample1"), ("deliver", "msg_sample1"), ("acknowledge", "msg_sample1")]


def test_router_headless_no_echo_stays_delivered_not_acked(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: "x")
    st = _Store()
    assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", hs=_Sup(echo="queued")) == "submitted"
    assert ("acknowledge", "msg_sample1") not in st.calls and ("deliver", "msg_sample1") in st.calls


def test_router_headless_holds_without_claiming(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: "x")
    for sup, want in ((_Sup(state="working"), "not-idle"), (_Sup(state="waiting"), "waiting"),
                      (_Sup(down=True), "down"), (_Sup(state="failed"), "down")):
        st = _Store()
        assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", hs=sup) == want
        assert st.calls == [] and sup.sent == []


def test_router_deliver_now_lets_a_running_turn_take_it(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: "x")
    st, sup = _Store(), _Sup(state="working")
    assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", deliver_now=True, hs=sup) == "submitted"
    assert len(sup.sent) == 1


def test_router_refusal_after_claim_requeues(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: "x")
    st = _Store()
    sup = _Sup(reply={"ok": False, "reason": "waiting"})
    assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", hs=sup) == "held"
    assert st.calls == [("claim", "msg_sample1"), ("defer", "msg_sample1")]


def test_router_duplicate_id_is_not_rewritten(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: "x")
    st = _Store()
    sup = _Sup(reply={"ok": True, "state": "answered", "duplicate": True})
    assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", hs=sup) == "submitted"
    assert ("acknowledge", "msg_sample1") in st.calls


def test_router_interrupted_counts_as_delivered(MR, monkeypatch):
    monkeypatch.setattr(MR, "injection_text", lambda msg, store: "x")
    st = _Store()
    assert MR.headless_deliver(st, MSG, "dev-demo", "dev-demo", hs=_Sup(echo="interrupted")) == "submitted"
    assert ("acknowledge", "msg_sample1") in st.calls
