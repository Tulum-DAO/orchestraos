"""RED-first tests for the pre-spawn free-RAM floor guard in spawn_green (item 2c,
gm gate msg_8f6cd0b2).

WHY: a broad arm spawns MANY greens at once. g15's pred4 proof-fire green was
OOM-KILLED at boot under swap thrash (/proc/vmstat oom_kill, swap ~12.4/16 GB) and
the seat then silently never rotated (#3/#4 fail-closed but no rotation). The guard
makes spawn_green FAIL-CLOSED + LOUD *before* it spawns when free RAM is under a
floor, instead of minting a green that OOM-dies mid-boot.

Contract:
  * an INJECTED probe fn (default reads /proc/meminfo MemAvailable + swap-free), like
    every other spawn_green seam, so tests inject values by effect;
  * ONE floor constant (SPAWN_MIN_AVAIL_MB); no runtime literals;
  * under floor -> RuntimeError naming the floor + observed avail (and swap for
    diagnosis), and the spawn runner is NEVER called (fail-closed = no green minted);
  * over floor -> normal spawn (unchanged behaviour);
  * the guard only gates a NEW spawn: a reused (crash-re-entry) pane is never blocked.
"""
import subprocess
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import spawn_green  # noqa: E402


def _tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=5)


def _scratch_runner_factory():
    calls = {"n": 0}

    def runner(session, env=None):
        calls["n"] += 1
        _tmux("new-session", "-d", "-s", session, "sleep 3000")
        return {"session": session}

    return runner, calls


def _kill(session):
    _tmux("kill-session", "-t", session)


def test_under_floor_fails_closed_loud_and_never_spawns(tmp_path):
    root = "bg-ramguard-victim"
    alias = f"{root}-g-green"
    runner, calls = _scratch_runner_factory()
    # observed available RAM well UNDER the floor -> must refuse before spawning
    low = spawn_green.SPAWN_MIN_AVAIL_MB - 1
    try:
        with pytest.raises(RuntimeError) as ei:
            spawn_green.spawn_green(
                root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
                spawn_runner=runner,
                free_ram_fn=lambda: {"avail_mb": low, "swap_free_mb": 10})
        msg = str(ei.value)
        # LOUD: names the floor AND the observed value
        assert str(spawn_green.SPAWN_MIN_AVAIL_MB) in msg
        assert str(low) in msg
        # fail-closed: the spawn runner was NEVER invoked (no green minted)
        assert calls["n"] == 0
        assert _tmux("has-session", "-t", alias).returncode != 0
    finally:
        _kill(alias)


def test_over_floor_spawns_normally(tmp_path):
    root = "bg-ramguard-victim"
    alias = f"{root}-g-green"
    runner, calls = _scratch_runner_factory()
    high = spawn_green.SPAWN_MIN_AVAIL_MB + 4096
    try:
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=runner,
            free_ram_fn=lambda: {"avail_mb": high, "swap_free_mb": 8000})
        assert calls["n"] == 1
        assert isinstance(out["pane_pid"], int) and out["pane_pid"] > 0
        assert out["reused"] is False
    finally:
        _kill(alias)


def test_reused_pane_is_not_blocked_by_low_ram(tmp_path):
    """A crash re-entry (session already exists) must NOT be refused by the guard —
    no new green is being minted, so there is no OOM risk to gate."""
    root = "bg-ramguard-victim"
    alias = f"{root}-g-green"
    runner, calls = _scratch_runner_factory()
    try:
        _tmux("new-session", "-d", "-s", alias, "sleep 3000")  # pre-existing pane
        out = spawn_green.spawn_green(
            root, alias, orchestra_dir=str(tmp_path), wal_dir=str(tmp_path),
            spawn_runner=runner,
            free_ram_fn=lambda: {"avail_mb": 1, "swap_free_mb": 0})  # would fail if gated
        assert out["reused"] is True
        assert calls["n"] == 0  # never spawned a second pane
    finally:
        _kill(alias)


def test_default_probe_reads_real_meminfo():
    got = spawn_green._default_free_ram()
    assert isinstance(got, dict)
    assert isinstance(got["avail_mb"], int) and got["avail_mb"] > 0
    assert "swap_free_mb" in got
