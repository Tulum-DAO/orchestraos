"""RED-first tests for reaper.py — the grandchild-safe Blue reaper (gm bar #2).

The load-bearing proof gm will personally scrutinize: `kill -pgid` (killpg) alone
is INSUFFICIENT for a setsid-detached grandchild, because setsid gives the
grandchild a NEW process group (so killpg on the parent's pgid never reaches it)
while KEEPING its PPID (so a ps --ppid BFS still finds it). The reaper snapshots
descendants by PPID-BFS BEFORE signaling, then signals the group AND each pid, and
verifies zero survivors.

These use REAL processes (not mocks) — a real setsid grandchild is spawned and the
insufficiency of killpg is demonstrated by effect before the reaper runs.
"""
import os
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import reaper  # noqa: E402


# A real 3-level tree: root (own session) -> child -> setsid grandchild.
# The grandchild calls os.setsid() so it escapes the root's process group but
# stays a PPID-descendant of child. Each level prints its pid then sleeps.
_TREE_SRC = r"""
import os, sys, time
# root: new session so its pgid is isolated from the test runner
os.setsid()
sys.stderr.write(f"ROOT {os.getpid()}\n"); sys.stderr.flush()
child = os.fork()
if child == 0:
    # child of root (same pgroup as root)
    sys.stderr.write(f"CHILD {os.getpid()}\n"); sys.stderr.flush()
    gc = os.fork()
    if gc == 0:
        os.setsid()   # GRANDCHILD escapes into its OWN new session/pgid
        sys.stderr.write(f"GRANDCHILD {os.getpid()}\n"); sys.stderr.flush()
        time.sleep(300)
        os._exit(0)
    time.sleep(300)
    os._exit(0)
time.sleep(300)
os._exit(0)
"""


def _spawn_tree():
    """Spawn the real 3-level tree; return (proc, {root,child,grandchild} pids)."""
    proc = subprocess.Popen([sys.executable, "-c", _TREE_SRC],
                            stderr=subprocess.PIPE, text=True)
    pids = {}
    deadline = time.time() + 5
    while len(pids) < 3 and time.time() < deadline:
        line = proc.stderr.readline()
        if not line:
            time.sleep(0.01)
            continue
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("ROOT", "CHILD", "GRANDCHILD"):
            pids[parts[0].lower()] = int(parts[1])
    assert len(pids) == 3, f"tree did not fully spawn: {pids}"
    time.sleep(0.1)   # let setsid settle
    return proc, pids


def _alive(pid):
    """Zombie-aware liveness: a SIGKILL'd child of this test process becomes a
    zombie until wait()'d; os.kill(pid,0) still succeeds on it, so read /proc and
    treat 'Z'/'X' as dead (matches the reaper's own _alive)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        state = data[data.rfind(b")") + 2:data.rfind(b")") + 3]
        return state not in (b"Z", b"X", b"x")
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def _hard_cleanup(pids):
    for pid in pids.values():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


# ---- the load-bearing proof: killpg is INSUFFICIENT, the reaper is sufficient ----

def test_killpg_alone_misses_the_setsid_grandchild(tmp_path):
    """BY EFFECT: killpg on the root's pgid kills root+child but NOT the setsid
    grandchild (it has its own pgid). This proves the naive reaper is unsafe."""
    proc, pids = _spawn_tree()
    try:
        root_pgid = os.getpgid(pids["root"])
        gc_pgid = os.getpgid(pids["grandchild"])
        assert gc_pgid != root_pgid, "grandchild must have escaped the root pgroup"
        # naive reap: signal only the root's process group
        os.killpg(root_pgid, signal.SIGKILL)
        time.sleep(0.3)
        assert not _alive(pids["root"]), "root should be dead"
        # THE INSUFFICIENCY: the setsid grandchild is STILL ALIVE
        assert _alive(pids["grandchild"]), (
            "killpg alone left the setsid grandchild alive — this is exactly the "
            "hazard the BFS reaper must close")
    finally:
        _hard_cleanup(pids)
        proc.wait(timeout=5)


def test_reaper_kills_the_whole_tree_including_setsid_grandchild(tmp_path):
    """The reaper snapshots PPID descendants BEFORE signaling, then signals group
    AND each pid -> zero survivors, grandchild included."""
    proc, pids = _spawn_tree()
    try:
        result = reaper.reap_tree(pids["root"], timeout_s=5)
        time.sleep(0.2)
        assert not _alive(pids["root"])
        assert not _alive(pids["child"])
        assert not _alive(pids["grandchild"]), (
            "the reaper MUST reach the setsid grandchild (PPID-BFS from root)")
        assert result["survivors"] == []
        assert set(result["killed"]) >= set(pids.values())
    finally:
        _hard_cleanup(pids)
        proc.wait(timeout=5)


def test_reaper_snapshots_descendants_before_signaling(tmp_path):
    """snapshot_descendants(root) returns the full PPID-descendant set (child +
    setsid grandchild) — the grandchild's setsid does NOT hide it from PPID-BFS."""
    proc, pids = _spawn_tree()
    try:
        desc = reaper.snapshot_descendants(pids["root"])
        assert pids["child"] in desc
        assert pids["grandchild"] in desc, (
            "setsid keeps PPID, so BFS must still find the grandchild")
    finally:
        _hard_cleanup(pids)
        proc.wait(timeout=5)


def test_reaper_idempotent_on_empty_tree(tmp_path):
    """A second reap of an already-dead tree is a clean no-op (success)."""
    proc, pids = _spawn_tree()
    reaper.reap_tree(pids["root"], timeout_s=5)
    time.sleep(0.2)
    result = reaper.reap_tree(pids["root"], timeout_s=5)   # already gone
    assert result["survivors"] == []
    proc.wait(timeout=5)


def test_reaper_does_not_touch_a_sibling_tree(tmp_path):
    """The reaper kills ONLY the target root's descendants; a sibling seat's tree
    is untouched (no over-broad name-glob / pgroup collateral)."""
    victim_proc, victim = _spawn_tree()
    sibling_proc, sibling = _spawn_tree()
    try:
        reaper.reap_tree(victim["root"], timeout_s=5)
        time.sleep(0.3)
        assert not _alive(victim["grandchild"])
        # sibling entirely untouched
        assert _alive(sibling["root"])
        assert _alive(sibling["grandchild"])
    finally:
        _hard_cleanup(victim)
        _hard_cleanup(sibling)
        victim_proc.wait(timeout=5)
        sibling_proc.wait(timeout=5)


# ---- identity resolution: blue_generation_id INT -> exact pane/pgid, fail-closed ----

def test_resolve_blue_pane_raises_on_ambiguous_or_absent(tmp_path):
    """reap gets a generations.id INT, not a handle. Resolving it to a pane pid
    must RAISE (fail-closed) when the mapping is absent/ambiguous — NEVER fall
    back to a root-name glob (which could kill Green or a sibling)."""
    with pytest.raises(reaper.ReapResolutionError):
        reaper.resolve_blue_pane_pid(
            str(tmp_path), "no-such-seat", blue_generation_id=999999,
            pane_pid_fn=lambda s: None)   # no live pane for the resolved session


def test_reap_blue_generation_fail_closed_never_globs(tmp_path):
    """The top-level reap_blue_generation refuses (raises, zero kills) when it
    cannot uniquely resolve Blue — the name-glob path does not exist."""
    killed = []
    with pytest.raises(reaper.ReapResolutionError):
        reaper.reap_blue_generation(
            str(tmp_path), "some-seat", blue_generation_id=None,
            _reap_fn=lambda pid, **kw: killed.append(pid))
    assert killed == [], "nothing may be killed when Blue cannot be resolved"
