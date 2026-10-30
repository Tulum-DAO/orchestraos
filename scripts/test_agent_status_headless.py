"""agent-status reads a headless seat from its supervisor, never from its pane: state mapping
(idle / working / stalled / needs-you / stopped / crashed), the held question's summary, pid,
session id, context from usage, the pane readers' answers for a headless seat, and tmux seats
unchanged. A fake supervisor on a temp unix socket; tmux and ps are faked."""
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import headless_switch as HS  # noqa: E402

SID = "00000000-0000-4000-8000-0000000000aa"


class FakeSupervisor:
    """Answers the supervisor's socket protocol with a canned status."""

    def __init__(self, path, status):
        self.status, self.path = status, str(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(8)
        self.calls = []
        self.t = threading.Thread(target=self._serve, daemon=True)
        self.t.start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            with c:
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = c.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                req = json.loads(buf or b"{}")
                self.calls.append(req.get("cmd"))
                c.sendall((json.dumps({"ok": True, **self.status}) + "\n").encode())

    def close(self):
        self.sock.close()


def base_status(**kw):
    st = {"seat": "dev-demo", "state": "idle", "supervisor_pid": 4242, "pid": 4343,
          "session_id": SID, "generation": 1, "restarts": 0, "fast_exits": 0,
          "last_event": {"type": "result"}, "last_event_age_s": 3.0, "turn_age_s": None,
          "uptime_s": 120.0, "tools_open": [], "last_tool": "", "held": 0, "held_requests": [],
          "model": "sample-model", "context": {"tokens": 50000, "window": 100000, "at": "x"},
          "messages": {"answered": 2}}
    st.update(kw)
    return st


@pytest.fixture
def env(monkeypatch):
    root = Path(tempfile.mkdtemp(prefix="as-", dir="/tmp"))
    orch = root / "d"
    (orch / "state").mkdir(parents=True)
    (orch / "registry.json").write_text(json.dumps({"agents": {
        "dev-demo": {"runtime": "claude", "tmux_session": "dev-demo"},
        "pm-demo": {"runtime": "claude", "tmux_session": "pm-demo"}}}))
    monkeypatch.setenv("ORCH_DIR", str(orch))
    monkeypatch.setenv("ORCHESTRA_DIR", str(orch))
    spec = importlib.util.spec_from_file_location("agent_status_headless", HERE / "agent-status.py")
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)
    monkeypatch.setattr(A, "ORCH_DIR", str(orch))
    monkeypatch.setattr(A, "STATE_DIR", str(orch / "state" / "agent-state"))
    monkeypatch.setattr(A, "EVENTS_DIR", str(orch / "state" / "agent-events" / "panes"))
    # The pane id is still the hook files' join key (the headless CLI carries the pane's
    # TMUX_PANE); faked here so no tmux is asked.
    monkeypatch.setattr(A, "get_pane_id", lambda s: "%77")
    A._ps = []

    def fake_pid_process(pid):
        A._ps.append(pid)
        return {"running": True, "runtime": "claude", "pid": pid, "cpu": 1.5, "rss_mb": 300,
                "elapsed": "02:00", "children": A._children} if pid else None
    A._children = 0
    monkeypatch.setattr(A, "_pid_process", fake_pid_process)
    sups = []

    def supervisor(status):
        s = FakeSupervisor(HS.socket_path("dev-demo", orch), status)
        sups.append(s)
        return s

    def switch_on():
        HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)

    r = types.SimpleNamespace(A=A, orch=orch, supervisor=supervisor, switch_on=switch_on)
    yield r
    for s in sups:
        s.close()
    shutil.rmtree(root, ignore_errors=True)


def no_pane(monkeypatch, A):
    """Any pane read (capture-pane, the tty scan) fails the test."""
    def boom(*a, **k):
        raise AssertionError(f"pane read for a headless seat: {a[0] if a else ''}")
    monkeypatch.setattr(A.subprocess, "run", boom)
    monkeypatch.setattr(A, "get_pane_tty", lambda s: (_ for _ in ()).throw(AssertionError("tty scan")))


def test_idle_from_the_supervisor_with_pid_session_and_context(env, monkeypatch):
    env.switch_on()
    env.supervisor(base_status())
    no_pane(monkeypatch, env.A)
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == "idle" and st["confidence"] == "headless"
    assert st["process"]["pid"] == 4343 and st["process"]["runtime"] == "claude"
    assert st["session_id"] == SID and st["model"] == "sample-model"
    assert st["context_pct_of_window"] == 50 and st["context_pct_of_budget"] == 62
    assert st["headless"]["supervisor_pid"] == 4242 and st["headless"]["messages"] == {"answered": 2}
    assert "pending_menu" not in st


def test_working_names_the_tool_and_stalls_only_when_quiet(env, monkeypatch):
    env.switch_on()
    sup = env.supervisor(base_status(state="working", tools_open=["Bash"], last_tool="Bash",
                                     turn_age_s=30.0, last_event_age_s=2.0))
    no_pane(monkeypatch, env.A)
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == "working" and st["activity"] == "Active turn (Bash)" and st["tool"] == "Bash"
    assert st["elapsed"] == "30s"
    # Quiet past STALL_S, but the tool still has a child process running: not stalled.
    env.A._children = 1
    sup.status = base_status(state="working", tools_open=["Bash"], last_event_age_s=900.0, turn_age_s=900.0)
    assert env.A.get_agent_status("dev-demo")["state"] == "working"
    # Quiet, nothing running under the CLI, no hook event: stalled.
    env.A._children = 0
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == "stalled" and "No stream events for 15m" in st["activity"]


def test_a_recent_hook_event_keeps_a_quiet_turn_working(env, monkeypatch):
    env.switch_on()
    env.supervisor(base_status(state="working", last_event_age_s=900.0, turn_age_s=900.0))
    ev = env.orch / "state" / "agent-events" / "panes"
    ev.mkdir(parents=True)
    import time
    (ev / "77.json").write_text(json.dumps({"state": "working", "event": "PreToolUse",
                                            "tool": "Read", "ts": time.time() - 5}))
    no_pane(monkeypatch, env.A)
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == "working" and st["hook"]["event"] == "PreToolUse"


def test_a_held_question_is_needs_you_with_its_summary(env, monkeypatch):
    env.switch_on()
    q = {"request_id": "r1", "tool": "AskUserQuestion", "kind": "question",
         "question": "Which size?", "options": ["Small", "Large"], "parts": 1}
    env.supervisor(base_status(state="waiting", held=1, held_requests=[q]))
    no_pane(monkeypatch, env.A)
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == "waiting_permission"
    assert st["activity"] == "Needs you: Which size?" and st["held_request"] == q
    assert "pending_menu" not in st          # nothing may bridge or type keys into the pane


@pytest.mark.parametrize("sst,state,words", [
    ("restarting", "stopped", "restarting"), ("starting", "stopped", "starting"),
    ("stopping", "stopped", "stopping"), ("failed", "crashed", "3 quick exits")])
def test_down_states(env, monkeypatch, sst, state, words):
    env.switch_on()
    env.supervisor(base_status(state=sst, pid=None, fast_exits=3))
    no_pane(monkeypatch, env.A)
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == state and words in st["activity"]


def test_no_supervisor_is_stopped_never_the_pane(env, monkeypatch):
    env.switch_on()
    no_pane(monkeypatch, env.A)
    st = env.A.get_agent_status("dev-demo")
    assert st["state"] == "stopped" and st["activity"] == "Headless supervisor not running"
    assert st["process"]["pid"] is None and st["headless"]["supervisor"] is False


def test_pane_readers_answer_for_a_headless_seat(env, monkeypatch):
    env.switch_on()
    env.supervisor(base_status())
    no_pane(monkeypatch, env.A)
    assert env.A.get_tmux_output("dev-demo") is None
    proc = env.A.check_claude_process("dev-demo")
    assert proc["running"] and proc["pid"] == 4343 and proc["runtime"] == "claude"
    assert env.A.mark_turn_interrupted("dev-demo") is False


def test_listing_keeps_a_headless_seat_even_when_down(env, monkeypatch):
    env.switch_on()                          # no supervisor running
    calls = []

    def run(args, **kw):
        calls.append(args)
        return types.SimpleNamespace(returncode=0, stdout="dev-demo\npm-demo\n")
    monkeypatch.setattr(env.A.subprocess, "run", run)
    monkeypatch.setattr(env.A, "check_claude_process",
                        lambda s: {"running": False} if s == "dev-demo" else {"running": True})
    assert env.A.list_claude_sessions() == ["dev-demo", "pm-demo"]


def test_a_tmux_seat_is_unchanged(env, monkeypatch):
    env.switch_on()
    seen = []
    monkeypatch.setattr(env.A, "check_claude_process",
                        lambda s: seen.append(s) or {"running": False, "pid": None})
    monkeypatch.setattr(env.A, "_supervisor_status", lambda s: pytest.fail("supervisor asked for a tmux seat"))
    st = env.A.get_agent_status("pm-demo")
    assert seen == ["pm-demo"] and st["state"] == "stopped" and st["activity"] == "No claude process running"


def test_switch_off_seat_reads_its_pane_as_before(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.A.subprocess, "run",
                        lambda args, **kw: calls.append(args) or types.SimpleNamespace(returncode=0, stdout="❯ \n"))
    assert env.A.get_tmux_output("dev-demo") == "❯ \n"
    assert calls and calls[0][:2] == ["tmux", "capture-pane"]


def test_context_needs_a_known_window():
    spec = importlib.util.spec_from_file_location("agent_status_ctx", HERE / "agent-status.py")
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)
    assert A._headless_context({"tokens": 1000, "window": None}) == {
        "context_pct_of_window": None, "context_pct_of_budget": None}
    assert A._headless_context({"tokens": 900000, "window": 1000000}) == {
        "context_pct_of_window": 90, "context_pct_of_budget": 100}


def test_gateway_rows_map_crashed_and_carry_headless_fields(monkeypatch, env):
    monkeypatch.setattr(sys, "path", [str(HERE), str(ROOT)] + list(sys.path))
    spec = importlib.util.spec_from_file_location("watch_gateway_hl22", HERE / "watch_gateway.py")
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    assert G._STATE_MAP["crashed"] == "crashed" and G._STATE_MAP["waiting_permission"] == "waiting"
    q = {"request_id": "r1", "kind": "question", "question": "Which size?"}
    row = {"session": "dev-demo", "state": "waiting_permission", "activity": "Needs you: Which size?",
           "process": {"pid": 4343, "runtime": "claude", "cpu": 0.0}, "headless": {"seat": "dev-demo"},
           "held_request": q, "context_pct": ""}
    monkeypatch.setattr(G, "ORCH_DIR", env.orch)
    monkeypatch.setattr(G, "_agent_status", lambda: types.SimpleNamespace(get_agent_status=lambda s: row))
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["dev-demo"])
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    monkeypatch.setattr(G, "_proc_start_epoch", lambda pid: None)
    monkeypatch.setattr(G, "_reap_absent_instances", lambda *a: None)
    for name in ("_stamp_recency_and_subagents", "_stamp_last_user_message", "_stamp_lineage", "_stamp_chips"):
        monkeypatch.setattr(G, name, lambda *a, **k: None)
    rows = [r for r in G.compute_agents() if r["id"] == "dev-demo"]
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "waiting" and r["headless"] is True and r["held_request"] == q
    assert r["provider"] == "claude" and "has_pending_menu" not in r
