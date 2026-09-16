"""RED-first tests for the WAL store (spec wal-bluegreen-rotation-spec.md §2.2).

The store is the append-only index/order/integrity layer for one lineage.
Invariants under test:
  - SQLite in WAL journal mode with wal_events + wal_cursors tables.
  - wal_events is APPEND-ONLY: total-ordered seq, no UPDATE/DELETE by any writer.
  - schema CHECK guards reject unknown kind/runtime (capture cannot smuggle a
    non-canonical event past the store).
  - wal_cursors is MUTABLE resume state (crash-safe tail cursor), separate from
    the append-only events.
"""
import sqlite3

import pytest

from lineage_daemon.wal.store import WalStore


def _ev(**over):
    base = dict(
        ts=1000.0, lineage_root="ios-watch-dev", generation=6,
        sid="757ef800", runtime="claude", kind="tool_call",
        summary="Bash(ls)", body_ref="p.jsonl:42", source_path="p.jsonl",
        source_off=128, integrity="deadbeef",
    )
    base.update(over)
    return base


def test_open_creates_schema_in_wal_mode(tmp_path):
    db = tmp_path / "ios-watch-dev.db"
    store = WalStore(str(db))
    tables = {r[0] for r in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"wal_events", "wal_cursors"} <= tables
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_append_returns_monotonic_total_order(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    s1 = store.append(**_ev(kind="prompt"))
    s2 = store.append(**_ev(kind="response"))
    s3 = store.append(**_ev(kind="tool_call"))
    assert [s1, s2, s3] == [1, 2, 3]
    kinds = [r["kind"] for r in store.events("ios-watch-dev")]
    assert kinds == ["prompt", "response", "tool_call"]


def test_wal_events_reject_update(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    store.append(**_ev())
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("UPDATE wal_events SET summary='x' WHERE seq=1")


def test_wal_events_reject_delete(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    store.append(**_ev())
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute("DELETE FROM wal_events WHERE seq=1")


def test_invalid_kind_rejected(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    with pytest.raises(sqlite3.IntegrityError):
        store.append(**_ev(kind="freeform_essay"))


def test_invalid_runtime_rejected(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    with pytest.raises(sqlite3.IntegrityError):
        store.append(**_ev(runtime="gpt"))


def test_cursor_is_mutable_resume_state(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    assert store.get_cursor("p.jsonl") is None
    store.set_cursor("p.jsonl", "ios-watch-dev", last_off=128, last_seq=1)
    store.set_cursor("p.jsonl", "ios-watch-dev", last_off=512, last_seq=4)
    cur = store.get_cursor("p.jsonl")
    assert cur["last_off"] == 512 and cur["last_seq"] == 4


def test_max_seq_reflects_last_append(tmp_path):
    store = WalStore(str(tmp_path / "l.db"))
    assert store.max_seq() == 0
    store.append(**_ev())
    store.append(**_ev())
    assert store.max_seq() == 2


def test_count_events_filters_by_live_sid(tmp_path):
    """Freshness (pm-acme class): a WAL can hold events from a PRIOR capture under an
    old sid; the WAL-at-arm gate must count only events keyed to the LIVE sid."""
    store = WalStore(str(tmp_path / "w.db"))
    store.append(**_ev(kind="prompt", sid="old-sid"))
    store.append(**_ev(kind="response", sid="old-sid"))
    store.append(**_ev(kind="prompt", sid="live-sid"))
    assert store.count_events("ios-watch-dev") == 3
    assert store.count_events("ios-watch-dev", sid="live-sid") == 1
    assert store.count_events("ios-watch-dev", sid="never") == 0
    store.close()
