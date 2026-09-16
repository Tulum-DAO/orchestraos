"""RED-first tests for wal_tailer.py — the capture-only WAL daemon (spec §7.1).

Stage-1 scope: one seat, WAL on / swaps off. Wires the claude adapter + git +
proc enrichers to the store. Enforces the CPU discipline (poll floor >= 5s,
single process per seat) and stays INERT (writes only the WAL db; imports no
beat/execute/fleet/decide/registry code — asserted by effect).
"""
import json
import subprocess

import pytest

from lineage_daemon.wal.store import WalStore
from lineage_daemon.wal import wal_tailer as WT


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def _seat(tmp_path):
    src = tmp_path / "s.jsonl"
    src.write_text(json.dumps({
        "type": "assistant", "sessionId": "sid-A",
        "timestamp": "2026-09-02T03:00:00.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Bash",
             "input": {"command": "ls"}}]}}) + "\n")
    repo = tmp_path / "cwd"
    repo.mkdir()
    _git(repo, "init")
    (repo / "new.txt").write_text("x")
    panes = tmp_path / "panes"
    panes.mkdir()
    (panes / "61.json").write_text(json.dumps({
        "pane": "%61", "session_id": "sid-A", "cwd": str(repo),
        "event": "PreToolUse", "tool": "Bash", "ts": 1000.0,
        "state": "working"}))
    store = WalStore(str(tmp_path / "ios-watch-dev.db"))
    tailer = WT.WalTailer(store, str(src), str(repo), str(panes),
                          lineage_root="ios-watch-dev", sid="sid-A",
                          generation=6)
    return store, tailer


def test_tick_captures_all_three_sources(tmp_path):
    store, tailer = _seat(tmp_path)
    n = tailer.tick()
    kinds = {r["kind"] for r in store.events()}
    assert n >= 3
    assert {"tool_call", "file_mod", "proc"} <= kinds


def test_second_tick_is_quiet_when_nothing_changed(tmp_path):
    store, tailer = _seat(tmp_path)
    tailer.tick()
    assert tailer.tick() == 0  # offset cursor + enricher dedupe = no re-capture


def test_run_rejects_subfloor_interval(tmp_path):
    _, tailer = _seat(tmp_path)
    with pytest.raises(ValueError):
        tailer.run(interval=1.0, should_stop=lambda: True)


def test_run_ticks_then_stops(tmp_path):
    store, tailer = _seat(tmp_path)
    calls = {"stop": 0, "sleep": 0}

    def should_stop():
        calls["stop"] += 1
        return calls["stop"] > 2  # allow 2 ticks

    tailer.run(interval=5.0, should_stop=should_stop,
               sleep=lambda s: calls.__setitem__("sleep", calls["sleep"] + 1))
    assert calls["sleep"] == 2  # slept after each of the 2 ticks


def test_seat_lock_is_single_process(tmp_path):
    a = WT.SeatLock("ios-watch-dev", str(tmp_path))
    b = WT.SeatLock("ios-watch-dev", str(tmp_path))
    assert a.acquire() is True
    assert b.acquire() is False   # second process cannot double-tail the seat
    a.release()
    assert b.acquire() is True    # freed after release
    b.release()


def test_tailer_imports_no_live_daemon_modules():
    """INERT by construction: the capture path must not pull in the rotation
    daemon's mutating modules (beat/execute/fleet/decide/registry/cron_beat)."""
    import inspect
    src = inspect.getsource(WT)
    forbidden = ["import beat", "import execute", "import fleet",
                 "import decide", "import cron_beat", "registry",
                 "promote_successor", "rotate_agent"]
    hits = [tok for tok in forbidden if tok in src]
    assert hits == [], f"wal_tailer must stay INERT; found live-path refs: {hits}"
