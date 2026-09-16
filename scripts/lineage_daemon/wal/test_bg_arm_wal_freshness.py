"""WAL-at-arm gate freshness (rab root cause follow-through, 2026-09-15): an events>0 WAL
is NOT proof the seat is hydratable when those events came from a PRIOR capture under an
old sid (pm-acme had 2181 stale events and no current-cwd transcript). The gate must
count only events keyed to the LIVE blue sid the beat resolved (obs['blue_sid_used'])."""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

ROOT = "fresh-seat"


def _seed(tmp_path, sid, n):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    for i in range(n):
        store.append(ts=1.0 + i, lineage_root=ROOT, generation=1, sid=sid, runtime="claude",
                     kind="prompt", summary="x", source_path="/t.jsonl")
    store.close()


def _arm(tmp_path):
    return BgArm(str(tmp_path), ROOT, seams=object(), cutover_active=lambda: True,
                 blue_pane_pid_fn=lambda root: 1)


def test_stale_sid_events_do_not_count_as_blue_wal(tmp_path):
    _seed(tmp_path, "old-sid", 5)
    assert _arm(tmp_path)._blue_wal_event_count(blue_sid="live-sid") == 0


def test_live_sid_events_count(tmp_path):
    _seed(tmp_path, "old-sid", 5)
    _seed(tmp_path, "live-sid", 2)
    assert _arm(tmp_path)._blue_wal_event_count(blue_sid="live-sid") == 2


def test_no_blue_sid_falls_back_to_unfiltered_count(tmp_path):
    """The beat could not resolve a live sid -> keep today's behaviour (no regression)."""
    _seed(tmp_path, "old-sid", 5)
    assert _arm(tmp_path)._blue_wal_event_count(blue_sid=None) == 5
