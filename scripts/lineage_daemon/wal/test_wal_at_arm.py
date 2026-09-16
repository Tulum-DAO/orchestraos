"""GOAL-FINDING #5 (WAL-capture-at-arm), v2.1 addendum DEC-1789425184694395.

An armed seat whose WAL (state/wal/<root>.db) has ZERO events cannot be hydrated, so a
spawned green could never ingest and READY is unreachable. The beat must REFUSE to spawn
such a green: grace one beat for capture to produce events, then refuse + alarm LOUD.
Provider-agnostic (no runtime-name conditionals in the gate).
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_arm import BgArm, WalCaptureNotStarted  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

ROOT = "wal-arm-seat"


class _Seams:
    def __init__(self):
        self.spawned = []

    def register_provisional(self, *a, **k): pass
    def project_now(self, root): return True
    def spawn(self, root, green_alias): self.spawned.append(green_alias)
    def verify(self, root, green_alias): return False
    def hydrate(self, root, green_alias, since_seq): pass
    def reap(self, root, blue): pass


def _obs():
    return {"root": ROOT, "runtime": "claude", "ctx_pct": 0.72, "death": "",
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "m"}}


def _arm(tmp_path, seams):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), ROOT, seams=seams, cutover_active=lambda: True,
                 blue_pane_pid_fn=lambda root: None)   # real WAL reader (no injected count)


def _seed_wal(tmp_path):
    ws = WalStore(str(tmp_path / f"{ROOT}.db"))
    ws.append(ts=1000.0, lineage_root=ROOT, generation=6, sid="sid-b", runtime="claude",
              kind="response", summary="seed", source_path="s")
    ws.close()


def test_empty_wal_defers_first_beat_no_spawn_no_alarm(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs())
    assert seams.spawned == [], "must NOT spawn a green when blue WAL is empty"
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "SOLO", "stays SOLO (deferred)"
    # grace beat: not yet an alarm
    assert BgStateStore(str(tmp_path), ROOT).read_meta("wal_empty_at_arm_alarm") in (None, False)


def test_empty_wal_refuses_after_grace_loud(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs())                    # beat 1: defer
    with pytest.raises(WalCaptureNotStarted):
        arm.beat(_obs())               # beat 2: still empty -> refuse LOUD
    assert seams.spawned == []
    assert BgStateStore(str(tmp_path), ROOT).read_meta("wal_empty_at_arm_alarm") is True


def test_wal_with_events_allows_prewarm_spawn(tmp_path):
    seams = _Seams()
    _seed_wal(tmp_path)
    arm = _arm(tmp_path, seams)
    arm.beat(_obs())
    assert seams.spawned, "a seat with a non-empty WAL must prewarm+spawn"
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "PREWARMING"


def test_wal_events_clear_the_empty_counter(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs())                    # beat 1: empty -> defer, counter=1
    _seed_wal(tmp_path)                 # capture produced events
    arm.beat(_obs())                    # beat 2: has events -> spawn, counter reset
    assert seams.spawned
    assert BgStateStore(str(tmp_path), ROOT).read_meta("wal_empty_beats") == 0
