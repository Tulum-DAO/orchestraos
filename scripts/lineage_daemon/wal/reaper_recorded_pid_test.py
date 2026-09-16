"""RED (BG leg-(ii) P0.1 — reap by the IMMUTABLE prewarm-recorded blue pane pid).

`_resolve_blue_tmux` returns the ARCHIVE name `{root}-gen{N}`, which does NOT exist for a
LIVE blue (pre-swap it sits in `{root}` / `{root}-g{N}`), so the autonomous
`reap_blue_generation` fail-closes and never reaps blue (the dead-pane landmine; the
verified-pid reap lived ONLY in bg_live_beat). P0.1 records blue's REAL pane pid at PREWARM
and reaps by that binding, with the LOAD-BEARING never-reap-green guard.

DELIVERY-CRITICAL (reap). Every decisive case drives the REAL reaper against REAL processes
(leg-(i) fake-only lesson) — a real blue subprocess must actually die, a real green
subprocess must actually survive.
"""
import os
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import reaper  # noqa: E402


def _spawn_proc():
    # start_new_session=True => the child calls setsid(), so it is in its OWN session /
    # process group. CRITICAL: reap_tree signals the pid AND its group; without this the
    # child shares the pytest runner's group and reaping it would SIGTERM the runner.
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"],
                            start_new_session=True)


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        state = data[data.rfind(b")") + 2:data.rfind(b")") + 3]
        return state not in (b"Z", b"X", b"x")
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _kill(*procs):
    for p in procs:
        try:
            p.kill()
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass


# ── the recorded pid IS reaped; the green is NEVER touched ─────────────────────

def test_recorded_pid_reaps_blue_and_leaves_green_alive():
    blue = _spawn_proc()
    green = _spawn_proc()
    try:
        # orchestra_dir is nonexistent on purpose: with a recorded pid the DB/tmux-name
        # resolver (the dead-pane path) MUST NOT be consulted at all.
        reaper.reap_blue_generation("/nonexistent-dir", "root", 5,
                                    recorded_blue_pid=blue.pid,
                                    green_pane_pid=green.pid, timeout_s=5)
        time.sleep(0.1)
        assert not _alive(blue.pid), "the recorded blue pid must be reaped"
        assert _alive(green.pid), "the promoted GREEN must NEVER be reaped"
    finally:
        _kill(blue, green)


def test_recorded_pid_equal_green_refuses_with_zero_kills():
    """The load-bearing guard: if the recorded pid resolves to the GREEN pane, REFUSE
    (raise) and kill NOTHING — never reap the successor."""
    green = _spawn_proc()
    try:
        with pytest.raises(reaper.ReapResolutionError):
            reaper.reap_blue_generation("/nonexistent-dir", "root", 5,
                                        recorded_blue_pid=green.pid,
                                        green_pane_pid=green.pid, timeout_s=5)
        time.sleep(0.1)
        assert _alive(green.pid), "zero kills when the guard fires"
    finally:
        _kill(green)


def test_recorded_pid_already_dead_is_noop_not_raise():
    p = _spawn_proc()
    pid = p.pid
    p.kill()
    p.wait(timeout=2)                       # reap the zombie -> /proc gone -> dead
    res = reaper.reap_blue_generation("/nonexistent-dir", "root", 5,
                                      recorded_blue_pid=pid,
                                      green_pane_pid=999999, timeout_s=5)
    assert res.get("already_dead") is True, "a dead recorded pid is a no-op, not a kill"
    assert res.get("killed") == [] and res.get("survivors") == []


@pytest.mark.parametrize("bad", [0, -1, "x", 3.5])
def test_recorded_pid_invalid_raises(bad):
    with pytest.raises(reaper.ReapResolutionError):
        reaper.reap_blue_generation("/nonexistent-dir", "root", 5,
                                    recorded_blue_pid=bad, green_pane_pid=None)


# ── no recorded pid => legacy tmux-name resolver path (back-compat) ────────────

def test_no_recorded_pid_falls_back_to_legacy_resolver():
    blue = _spawn_proc()
    try:
        seen = {}

        def resolver(od, r, bid):
            seen["resolver"] = (r, bid)
            return "blue-session"

        def pane_fn(session):
            seen["pane"] = session
            return blue.pid

        reaper.reap_blue_generation("/x", "root", 5, recorded_blue_pid=None,
                                    tmux_resolver=resolver, pane_pid_fn=pane_fn,
                                    timeout_s=5)
        time.sleep(0.1)
        assert seen.get("resolver") == ("root", 5), "legacy resolver still used when no pid"
        assert seen.get("pane") == "blue-session"
        assert not _alive(blue.pid)
    finally:
        _kill(blue)
