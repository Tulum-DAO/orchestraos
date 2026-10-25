"""A STALLED seat accepts a message (gm RULING msg_1e8a5f91).

`stalled` is DERIVED from working by agent-status.py's `STALL_S = 600` timer — working + an
unchanged screen + no hook events for ten minutes. It is a live process at a prompt where the
CLI queues natively, not a dead pane. The gateway refused it while the WEB COMPOSER already
promised the operator it would be queued, so the two disagreed; ios-watch-dev found it.

Mirrors test_gateway_send_gate_composer.py's harness so these drive the REAL verified_inject.
"""
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
G = importlib.import_module("watch_gateway")

# A stalled pane looks like a working one that stopped moving: composer present, no spinner.
STALLED_PANE = ("  ⎿ running a subagent\n"
                "❯ \n")
CLEARED_PANE = ("  ⎿ running a subagent\n"
                "❯ \n")
MENU_PANE = ("Claude needs your permission to run Bash\n"
             " 1. Yes\n 2. Yes, and don't ask again\n 3. No\n")


class _Det:
    def __init__(self, result): self._r = result
    def get_agent_status(self, sess): return self._r


class _Tmux:
    def __init__(self, captures):
        self.calls = []
        self._c = list(captures)
    def __call__(self, *args, timeout=5):
        self.calls.append(args)
        out = self._c.pop(0) if (args[0] == "capture-pane" and self._c) else ""
        return types.SimpleNamespace(returncode=0, stdout=out)


def _env(monkeypatch, state, captures, activity="unchanged for 10 min"):
    t = _Tmux(captures)
    monkeypatch.setattr(G, "_tmux", t)
    monkeypatch.setattr(G, "INJECT_INGEST_WAIT_S", 0.0)
    monkeypatch.setattr(G, "_agent_status",
                        lambda: _Det({"state": state, "activity": activity, "process": {"runtime": "claude"}}))
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda s: None)
    return t


def _sent_text(tmux):
    return [c for c in tmux.calls if c[0] == "send-keys" and "-l" in c]


# ── the ruling itself ────────────────────────────────────────────────────────
def test_a_STALLED_seat_is_INJECTED(monkeypatch):
    """THE CHANGE. Before the ruling this was refused with {'reason': 'busy'}."""
    t = _env(monkeypatch, "stalled", [STALLED_PANE, CLEARED_PANE])
    ok, info = G.verified_inject("tgt", "are you still on this?")
    assert ok, f"a stalled pane has a live composer and must be injected, got {info}"
    assert _sent_text(t), "nothing was actually typed into the pane"


def test_the_answer_NAMES_stalled_so_a_client_can_say_so(monkeypatch):
    """gm condition (1): the answer must say the target was stalled, so web and iOS can render
    'queued — the agent looks stalled' instead of implying a normal turn end."""
    _env(monkeypatch, "stalled", [STALLED_PANE, CLEARED_PANE])
    ok, info = G.verified_inject("tgt", "still there?")
    assert ok
    assert info.get("state") == "stalled", f"the answer must carry the state, got {info}"


def test_stalled_is_in_the_allowed_set_as_its_OWN_name(monkeypatch):
    """It is deliberately NOT folded into MID_TURN_STATES: mid-turn means a turn is running,
    stalled means we have not seen one move for 600 s. Different claims, same permission."""
    assert "stalled" in G.STALLED_STATES
    assert "stalled" not in G.MID_TURN_STATES
    assert "stalled" not in G.INJECTABLE_STATES


# ── gm condition (2): what must STILL be refused ─────────────────────────────
def test_stopped_is_STILL_REFUSED(monkeypatch):
    _env(monkeypatch, "stopped", [STALLED_PANE])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok and info.get("reason") == "busy", info


def test_crashed_is_STILL_REFUSED(monkeypatch):
    _env(monkeypatch, "crashed", [STALLED_PANE])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok, info


def test_offline_is_STILL_REFUSED(monkeypatch):
    _env(monkeypatch, "offline", [STALLED_PANE])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok, info


def test_waiting_permission_is_STILL_REFUSED(monkeypatch):
    """A keystroke here ANSWERS the prompt — the one case where sending is actively harmful."""
    _env(monkeypatch, "waiting_permission", [MENU_PANE])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok, info


def test_a_MENU_on_a_stalled_pane_is_STILL_REFUSED(monkeypatch):
    """The independent menu refusal must survive the widened state gate: a stalled seat sitting
    on a permission prompt is still a pane where a keystroke answers the question."""
    _env(monkeypatch, "stalled", [MENU_PANE, MENU_PANE])
    ok, info = G.verified_inject("tgt", "hello")
    assert not ok, f"a menu must hold the line even when the state gate allows the seat: {info}"
