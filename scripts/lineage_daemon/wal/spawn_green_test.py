"""RED-first tests for spawn_green.py — the setsid-detached Green spawn seam.

`tmux new-session -d` (what spawn-agent.sh does) IS the detached spawn: the tmux
server owns the pane in its OWN session/pgid, so the Green survives the beat
process exiting. This seam wraps that with (a) idempotency (never a second live
pane for one green_alias — tmux has-session guard) and (b) recording the Green
pane pid into bg_state so the reaper can PPID-BFS it later.

Tests use a REAL scratch tmux session (via an injected spawn_runner that does
`tmux new-session -d`) — proving detachment/pgid/pid-recording/idempotency by
effect without launching a full claude CLI.
"""
import os
import subprocess
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import spawn_green  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402


def _tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=5)


def _scratch_runner(session, env=None):
    """A spawn_runner stand-in: creates a REAL detached tmux session running a
    long sleep (a stand-in for the green agent), like spawn-agent.sh's
    `tmux new-session -d`. Returns after the session exists."""
    _tmux("new-session", "-d", "-s", session, "sleep 3000")
    return {"session": session}


def _kill(session):
    _tmux("kill-session", "-t", session)


def test_spawn_creates_one_detached_session_and_records_pane_pid(tmp_path):
    root = "bg-drill-victim"
    alias = f"{root}-g-green"
    try:
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=_scratch_runner)
        # exactly one detached session for the alias
        r = _tmux("has-session", "-t", alias)
        assert r.returncode == 0, "the green session must exist (detached)"
        assert out["detached"] is True
        # pane pid recorded into bg_state for reap reachability
        pid = out["pane_pid"]
        assert isinstance(pid, int) and pid > 0
        assert BgStateStore(str(tmp_path), root).read_meta("green_pane_pid") == pid
        # detached = its own session, pgid distinct from this test process
        assert os.getpgid(pid) != os.getpgid(os.getpid())
    finally:
        _kill(alias)


def test_spawn_is_idempotent_no_second_pane(tmp_path):
    root = "bg-drill-victim"
    alias = f"{root}-g-green"
    calls = []

    def counting_runner(session, env=None):
        calls.append(session)
        return _scratch_runner(session)

    try:
        first = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=counting_runner)
        # second spawn (crash re-entry): must NOT create a second pane
        second = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=counting_runner)
        assert calls == [alias], "runner must be invoked exactly once (idempotent)"
        assert second["pane_pid"] == first["pane_pid"]
        assert second["reused"] is True
    finally:
        _kill(alias)


def test_spawn_records_pid_even_on_idempotent_reuse(tmp_path):
    """On a re-entry where the session already exists but bg_state lost the pid
    (crash between spawn and record), the seam re-resolves + records it."""
    root = "bg-drill-victim"
    alias = f"{root}-g-green"
    try:
        _scratch_runner(alias)   # session pre-exists, bg_state has NO pid
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=_scratch_runner)
        assert out["reused"] is True
        assert isinstance(out["pane_pid"], int)
        assert BgStateStore(str(tmp_path), root).read_meta("green_pane_pid") == out["pane_pid"]
    finally:
        _kill(alias)


def test_bg_state_meta_roundtrip(tmp_path):
    """The generic per-seat meta store (used by spawn pid / hydrate since_seq /
    verify stall count) round-trips and is independent of the state machine."""
    store = BgStateStore(str(tmp_path), "seat-x")
    assert store.read_meta("green_pane_pid") is None
    store.write_meta("green_pane_pid", 4242)
    store.write_meta("last_hydrated_seq", 17)
    assert store.read_meta("green_pane_pid") == 4242
    assert store.read_meta("last_hydrated_seq") == 17
    # writing meta must not clobber the state-machine state
    store.write_state("PREWARMING", reason="x")
    assert store.read()["state"] == "PREWARMING"
    assert store.read_meta("green_pane_pid") == 4242


def test_spawn_registers_green_sid_before_returning(tmp_path):
    """(a) GREEN-SIDE #1 (Blocker-1 applied to the green): after the pane is confirmed,
    spawn_green MUST invoke the injected provider-agnostic register_green_sid_fn(alias)
    (which resolves the green's live cid and writes it DB-first + flat + .sid) BEFORE
    returning, and surface the resolved sid. RED: the fn is called with the alias and
    its sid appears on the result + in bg_state (green_session_id)."""
    root = "bg-drill-victim"
    alias = f"{root}-g-sidreg"
    seen = []

    def register_green_sid(green_alias):
        seen.append(green_alias)
        return "206f75be-cbcd-44c6-8c86-8f87ef3c7eff"

    try:
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=_scratch_runner,
            register_green_sid_fn=register_green_sid)
        assert seen == [alias], "register_green_sid_fn must be called once with the alias"
        assert out["green_sid"] == "206f75be-cbcd-44c6-8c86-8f87ef3c7eff"
        assert BgStateStore(str(tmp_path), root).read_meta("green_session_id") == \
            "206f75be-cbcd-44c6-8c86-8f87ef3c7eff"
    finally:
        _kill(alias)


def test_spawn_green_sid_register_failure_is_failsoft_with_breadcrumb(tmp_path):
    """(a) fail-soft: a sid-registration hiccup (green not yet resolvable) must NOT abort
    the spawn — record a breadcrumb, return green_sid None; verify/(b) then fail-closes and
    (d) prunes. Never crash the beat on a not-yet-booted green."""
    root = "bg-drill-victim"
    alias = f"{root}-g-sidfail"

    def register_boom(green_alias):
        raise RuntimeError("green cid not resolvable yet")

    try:
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=_scratch_runner, register_green_sid_fn=register_boom)
        assert out["green_sid"] is None
        assert "not resolvable" in (
            BgStateStore(str(tmp_path), root).read_meta("green_sid_register_error") or "")
    finally:
        _kill(alias)


def test_no_pane_spawn_surfaces_runner_returncode_and_stderr(tmp_path):
    """OBSERVABILITY (gm-approved): a spawn that produces NO reap-reachable pane must
    fail-closed with a RuntimeError that CARRIES the spawn runner's returncode + the
    tail of its captured stderr — so the driver log shows WHAT broke, not just that
    the pane is missing. A blind fail-closed spawn is itself a defect."""
    root = "bg-drill-victim"
    alias = f"{root}-g-noshow"
    sentinel_err = "REFUSE-STUB: ready-signature wait timed out; session torn down"

    def failing_runner(session, env=None):
        # spawn attempted, exited non-zero, produced NO tmux session
        return {"session": session, "returncode": 42,
                "stderr_tail": sentinel_err, "stdout_tail": ""}

    try:
        spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=failing_runner,
            has_session_fn=lambda s: False,   # not reused
            pane_pid_fn=lambda s: None,       # no reachable pane
            pane_id_fn=lambda s: None)
        raise AssertionError("expected fail-closed RuntimeError on a no-pane spawn")
    except RuntimeError as e:
        msg = str(e)
        assert "42" in msg, f"returncode must be surfaced, got: {msg}"
        assert sentinel_err in msg, f"stderr tail must be surfaced, got: {msg}"


def test_default_spawn_runner_captures_returncode_and_stderr(tmp_path):
    """The live default runner must CAPTURE (not discard) spawn-agent.sh's returncode
    and stderr/stdout tails, so a no-pane spawn is diagnosable. Uses a stub
    spawn-agent.sh that exits non-zero on stderr — no tmux, no agent CLI."""
    stub = tmp_path / "spawn-agent.sh"
    stub.write_text(
        "#!/bin/sh\n"
        "echo 'stub stdout line' \n"
        "echo 'stub stderr: launch failed' 1>&2\n"
        "exit 7\n")
    stub.chmod(0o755)
    runner = spawn_green._default_spawn_runner(str(tmp_path), timeout_s=10)
    res = runner("some-green-alias")
    assert res["returncode"] == 7
    assert "stub stderr: launch failed" in res["stderr_tail"]
    assert "stub stdout line" in res["stdout_tail"]


def test_spawn_records_pane_id_and_spawned_at_for_liveness_gate(tmp_path):
    """Layer-2 gate (DEC-1788655588): spawn records green_pane_id (%N) + green_spawned_at
    (captured BEFORE the spawn as the ts-fence lower bound)."""
    root = "bg-drill-victim"
    alias = f"{root}-g-liveness"
    try:
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=_scratch_runner, now=lambda: 12345.0)
        st = BgStateStore(str(tmp_path), root)
        assert out["pane_id"] and out["pane_id"].startswith("%")
        assert st.read_meta("green_pane_id") == out["pane_id"]
        assert st.read_meta("green_spawned_at") == 12345.0
        assert out["spawned_at"] == 12345.0
    finally:
        _kill(alias)
