"""Step 2.7 for headless seats: every path that brings a seat back in a pane (recovery, revive, red alert's dead-pane
repair) starts a headless seat as its supervisor, never a TUI; rotation refuses a headless seat with the way
through; boundary delivery waits while a question is held. Temp data dirs, fake tmux and supervisor; nothing touches
a real pane, seat or the live data dir."""
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(HERE), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)
import headless_switch as HS  # noqa: E402
import seat_supervisor as SS  # noqa: E402


@pytest.fixture
def orch(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {
        "dev-demo": {"runtime": "claude", "tmux_session": "dev-demo", "cwd": str(tmp_path)},
        "qa-demo": {"runtime": "codex", "tmux_session": "qa-demo"}}}))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setenv("ORCH_DIR", str(tmp_path))
    return tmp_path


# --- the launch line ---------------------------------------------------------------------------------

def test_launch_line_only_for_a_switched_on_claude_seat(orch):
    assert SS.launch_line("dev-demo", orch) is None                       # default off
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    line = SS.launch_line("dev-demo", orch)
    assert line.startswith("env -u CLAUDECODE ") and line.endswith("seat_supervisor.py run dev-demo")
    assert f"ORCHESTRA_DIR={orch}" in line                                 # spawn's environment rides the line
    HS.switch_path(orch).write_text(json.dumps({"seats": {"qa-demo": {"headless": True, "runtime": "claude"}}}))
    assert SS.launch_line("qa-demo", orch) is None                        # a codex seat stays on tmux


def test_launch_line_cli_exit_codes(orch):
    run = lambda: subprocess.run([sys.executable, str(HERE / "seat_supervisor.py"), "launch-line", "dev-demo"],
                                 capture_output=True, text=True, env={**os.environ, "ORCHESTRA_DIR": str(orch)})
    assert run().returncode == 3
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    r = run()
    assert r.returncode == 0 and r.stdout.strip().endswith("seat_supervisor.py run dev-demo")


# --- boot and crash recovery -------------------------------------------------------------------------

def test_recovery_asks_for_the_launch_line_before_starting_claude(orch):
    src = (HERE / "agent-recovery.sh").read_text()
    fn = src[src.index("headless_launch_line() {"):]
    fn = fn[:fn.index("\n}\n") + 3]
    sh = f'BIN_DIR={HERE}\n{fn}\nheadless_launch_line dev-demo && echo OK || echo NO'
    env = {**os.environ, "ORCHESTRA_DIR": str(orch)}
    assert subprocess.run(["bash", "-c", sh], capture_output=True, text=True, env=env).stdout.strip() == "NO"
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    out = subprocess.run(["bash", "-c", sh], capture_output=True, text=True, env=env).stdout
    assert "seat_supervisor.py run dev-demo" in out and out.strip().endswith("OK")
    # Both launch branches put it first: the supervisor line, else claude --resume as before.
    for marker in ('log "AUTO-RECOVER $agent_id as headless', 'log "RECOVERING $agent_id as headless'):
        at = src.index(marker)
        assert src.rfind('if HL_LINE=$(headless_launch_line "$agent_id"); then', 0, at) > src.rfind("tmux new-session", 0, at)
        assert src.index("--resume $session_id", at) > at


# --- revive ------------------------------------------------------------------------------------------

class RealSeatStart(AssertionError):
    """A test reached a path that starts a real seat (tmux, spawn-agent.sh, claude)."""


@pytest.fixture
def revive(orch, monkeypatch):
    spec = importlib.util.spec_from_file_location("revive_agent_hl", HERE / "revive_agent.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.briefing, "seat_record", lambda root, od: {"tmux_session": root, "cwd": str(orch), "name": root})
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    # FENCE: revive falls back to the terminal path when the headless check says no (and that check reads any error
    # as no), and the terminal path starts a REAL seat: a fresh start, or spawn-agent.sh through Popen, in a real tmux
    # session. A wrong turn here once left a real `claude` running in a tmux session named after a test seat. Every
    # way out to a real seat raises instead.
    def real(*a, **k):
        raise RealSeatStart(f"a test tried to start a real seat: {a!r}")
    monkeypatch.setattr(m.fs, "start", real)
    monkeypatch.setattr(m, "subprocess", types.SimpleNamespace(Popen=real, run=real, DEVNULL=None, STDOUT=None))
    monkeypatch.setattr(m, "_tmux", real)
    return m


def test_the_revive_fixture_never_reaches_a_real_seat(revive, orch, monkeypatch):
    # The headless check failing (as a wrong keyword once made it) drops into the terminal path: fenced.
    monkeypatch.setattr(revive, "_headless", lambda root, od: False)
    for mode in ("auto", "fresh", "resume"):
        with pytest.raises((RealSeatStart, revive.NotStartable)):
            revive.revive("dev-demo", mode, orch, wait_s=0)
    with pytest.raises(RealSeatStart):
        revive.resume_headless.__globals__["_tmux"]("has-session", "-t", "=dev-demo")


class _T:
    def __init__(self, has=True):
        self.has, self.calls = has, []

    def __call__(self, *a):
        self.calls.append(a)
        rc = 0 if (a[0] != "has-session" or self.has) else 1
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="")


def _down_then_up(n_down=1):
    state = {"n": 0}

    def status():
        state["n"] += 1
        if state["n"] <= n_down:
            raise HS.SupervisorDown("no socket")
        return {"state": "idle"}
    return status


def test_revive_brings_a_headless_seat_back_as_its_supervisor(revive, orch):
    from scripts import headless_switch as SHS
    SHS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    t, took = _T(has=True), []
    status = _down_then_up(1)

    def st():
        try:
            return status()
        except HS.SupervisorDown as e:
            raise SHS.SupervisorDown(str(e))
    out = revive.revive("dev-demo", "auto", orch, tmux=t, takeover=lambda: took.append(1) or (True, "line"),
                        status=st, wait_s=1)
    assert out == {"id": "dev-demo", "name": "dev-demo", "mode": "resume", "headless": True, "session_up": True}
    assert took == [1] and not [c for c in t.calls if c[0] == "new-session"]


def test_revive_makes_its_session_when_gone_and_refuses_when_running_or_fresh(revive, orch):
    from scripts import headless_switch as SHS
    SHS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    t = _T(has=False)

    def down():
        raise SHS.SupervisorDown("x")
    calls = {"n": 0}

    def st():
        calls["n"] += 1
        if calls["n"] == 1:
            down()
        return {}
    revive.revive("dev-demo", "resume", orch, tmux=t, takeover=lambda: (True, "ok"), status=st, wait_s=1)
    assert [c for c in t.calls if c[0] == "new-session"][0][:4] == ("new-session", "-d", "-s", "dev-demo")
    with pytest.raises(revive.NotStartable, match="already running"):
        revive.revive("dev-demo", "resume", orch, tmux=t, takeover=lambda: (True, ""), status=lambda: {}, wait_s=0)
    with pytest.raises(revive.NotStartable, match="fresh start is not available"):
        revive.revive("dev-demo", "fresh", orch)
    with pytest.raises(revive.NotStartable, match="could not be started in its pane: the pane is running"):
        revive.revive("dev-demo", "resume", orch, tmux=_T(), status=st if False else (lambda: down()),
                      takeover=lambda: (False, "the pane is running 'vim'"), wait_s=0)


# --- red alert's dead-pane repair --------------------------------------------------------------------

def test_red_alert_respawns_a_dead_headless_pane_with_its_supervisor(orch, monkeypatch):
    spec = importlib.util.spec_from_file_location("red_alert_watch_hl", HERE / "red_alert_watch.py")
    W = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(W)
    calls = []
    monkeypatch.setattr(W, "tmux", lambda *a: calls.append(a) or types.SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    monkeypatch.setattr(W, "_healthy", lambda seat: (True, {"process_state": {seat: [{"pid": 1}]}}))
    monkeypatch.setattr(W.RA, "classify", lambda ev, seat: None)
    ev = {"process_state": {"dev-demo": []}, "registry_rows": {"dev-demo": {"cwd": str(orch), "session_id": "s1"}}}
    monkeypatch.setattr(W, "headless_launch_line", lambda seat: "env X=1 python3 seat_supervisor.py run dev-demo")
    ok, _ = W.repair_respawn("dev-demo", "dev-demo", ev)
    assert ok and calls[0][0] == "respawn-pane" and calls[0][-1].endswith("seat_supervisor.py run dev-demo")
    calls.clear()
    monkeypatch.setattr(W, "headless_launch_line", lambda seat: None)          # a tmux seat: its resume, as before
    monkeypatch.setattr(W, "resume_cmd", lambda a, row: "claude --resume s1")
    W.repair_respawn("dev-demo", "dev-demo", ev)
    assert calls[0][-1] == "claude --resume s1"


# --- rotation ----------------------------------------------------------------------------------------

def test_rotation_refuses_a_headless_seat_before_doing_anything(orch, monkeypatch):
    spec = importlib.util.spec_from_file_location("rotate_agent_hl", HERE / "rotate_agent.py")
    RA = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RA)
    assert RA.headless_rotation_refusal("dev-demo") is None
    HS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    why = RA.headless_rotation_refusal("dev-demo")
    assert "runs headless" in why and "seat_supervisor.py rotate dev-demo" in why
    monkeypatch.setattr(RA, "log", lambda *a, **k: None)
    with pytest.raises(RA.RotationRefused):
        RA.execute_rotation("dev-demo", dry_run=True)


def test_a_headless_rotation_goes_to_its_supervisor(capsys):
    spec = importlib.util.spec_from_file_location("rotate_agent_hl2", HERE / "rotate_agent.py")
    RA = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(RA)
    asked = []
    assert RA.rotate_headless("dev-demo", request=lambda: asked.append(1) or {"ok": True, "pending": True}) == 0
    assert asked == [1] and '"via": "supervisor"' in capsys.readouterr().out

    def down():
        raise HS.SupervisorDown("no socket")
    assert RA.rotate_headless("dev-demo", request=down) == 1
    assert "did not answer" in capsys.readouterr().out


# --- boundary delivery -------------------------------------------------------------------------------

def test_boundary_delivery_waits_while_a_question_is_held(monkeypatch):
    sys.path.insert(0, str(HERE / "lineage_daemon"))
    spec = importlib.util.spec_from_file_location("boundary_delivery_hl", HERE / "lineage_daemon" / "boundary_delivery.py")
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)
    row = {"state": "waiting_permission", "held_request": {"kind": "question", "question": "Which size?"}}
    fake = types.SimpleNamespace(get_agent_status=lambda s: row)
    real_import = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda name, *a: fake if name == "agent-status" else real_import(name, *a))
    assert B._real_f11_ok("dev-demo") is False
    row.pop("held_request")
    row["state"] = "idle"
    assert B._real_f11_ok("dev-demo") is True


def test_revive_with_its_real_status_and_takeover_seams(revive, orch, monkeypatch):
    """The default seams (no injection): the supervisor's real status client, and the real takeover typing the
    supervisor line into the seat's shell, through a fake tmux only."""
    from scripts import headless_switch as SHS
    SHS.set_switch("dev-demo", True, runtime="claude", orch_dir=orch)
    keys = []

    def tmux(*a, timeout=5):
        keys.append(a)
        out = "bash\n" if a[0] == "display-message" else ""
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
    out = revive.resume_headless("dev-demo", orch, tmux=tmux, wait_s=0)
    sent = [k for k in keys if k[0] == "send-keys"]
    assert sent[0][-1].endswith("seat_supervisor.py run dev-demo") and sent[1][-1] == "Enter"
    assert out["headless"] is True and out["session_up"] is False      # no real supervisor answers in a test
