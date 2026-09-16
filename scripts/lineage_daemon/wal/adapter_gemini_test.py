"""RED-first tests for the gemini WAL adapter (Build A, per-provider adapter).

gemini/agy live store (A0 D1, verified by effect on 491 live dbs):
  ~/.gemini/antigravity-cli/conversations/<uuid>.db, journal_mode=wal.
  POLL cursor = MAX(idx) on `steps` (idx = INTEGER PRIMARY KEY / rowid alias).
  Bodies = PROTOBUF-wire blobs in steps.step_payload (magic 08..), NOT jsonl.

This adapter is a NET-NEW sqlite-WAL POLL tailer (not an adapter_claude clone):
  * cursor = MAX(idx), poll new rows with idx > cursor;
  * RO-probe HARD rules: `file:...?mode=ro` URI, NEVER immutable=1, busy_timeout,
    read the LIVE db (never copy a bare .db mid-checkpoint);
  * protobuf bodies stay BY-REFERENCE at index time (the court-contagion vector
    never enters the index) — protobuf-decode happens only in the normalize path
    (normalize.py) BEFORE any scrub, never here.

The synthetic db below emulates the antigravity `steps` shape with SYNTHETIC
protobuf-ish blobs (no real model voice) per the A0 opaque-fixture protocol.
"""
import os
import sqlite3
import tempfile

import pytest

from lineage_daemon.wal.adapter_gemini import GeminiWalAdapter, ro_connect
from lineage_daemon.wal.store import WalStore


def _make_gemini_db(path, steps):
    """steps: list of (idx, step_type, status, permissions_blob, payload_blob)."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer NOT NULL"
        " DEFAULT 0, status integer NOT NULL DEFAULT 0, permissions blob,"
        " step_payload blob, step_format integer NOT NULL DEFAULT 0)")
    conn.executemany(
        "INSERT INTO steps (idx, step_type, status, permissions, step_payload)"
        " VALUES (?,?,?,?,?)", steps)
    conn.commit()
    conn.close()


_SYN = bytes.fromhex("080f20032adf020a0b")  # synthetic protobuf-ish header, no voice


def test_runtime_is_gemini():
    assert GeminiWalAdapter.runtime == "gemini"


def test_poll_captures_all_steps_in_order():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "conv-uuid.db")
        _make_gemini_db(db, [
            (1, 15, 3, None, _SYN),
            (2, 14, 3, None, _SYN + b"\x01"),
            (3, 132, 3, b"\x0a\x02perm", _SYN + b"\x02"),
        ])
        st = WalStore(os.path.join(tmp, "wal.db"))
        n = GeminiWalAdapter(st, "gem-seat", 7).tail(db)
        assert n == 3
        evs = st.events()
        assert [e["runtime"] for e in evs] == ["gemini", "gemini", "gemini"]
        assert all(e["generation"] == 7 for e in evs)
        # total order preserved: body_ref carries the idx
        refs = [e["body_ref"] for e in evs]
        assert refs == ["gemini:%s#idx=1" % db, "gemini:%s#idx=2" % db,
                        "gemini:%s#idx=3" % db]


def test_cursor_is_max_idx_incremental():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "conv-uuid.db")
        _make_gemini_db(db, [(1, 15, 3, None, _SYN), (2, 15, 3, None, _SYN)])
        st = WalStore(os.path.join(tmp, "wal.db"))
        a = GeminiWalAdapter(st, "gem-seat", 0)
        assert a.tail(db) == 2
        # cursor persisted as MAX(idx)
        cur = st.get_cursor(db)
        assert cur["last_off"] == 2
        # no new rows -> nothing re-read
        assert a.tail(db) == 0
        # append a new higher-idx row -> only it is consumed
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO steps (idx, step_type, status, step_payload)"
                     " VALUES (3, 15, 3, ?)", (_SYN,))
        conn.commit(); conn.close()
        assert a.tail(db) == 1
        assert st.get_cursor(db)["last_off"] == 3


def test_structural_summary_carries_discriminator_not_body():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "conv-uuid.db")
        _make_gemini_db(db, [(1, 132, 3, b"perm", _SYN)])
        st = WalStore(os.path.join(tmp, "wal.db"))
        GeminiWalAdapter(st, "gem-seat", 0).tail(db)
        e = st.events()[0]
        # discriminator present, body NOT rendered into the index
        assert "type=132" in e["summary"]
        assert "status=3" in e["summary"]
        assert "perm" in e["summary"]  # permission signal noted structurally
        # the raw protobuf never appears in the summary
        assert _SYN.hex() not in (e["summary"] or "")
        assert e["integrity"]  # hash of the payload is recorded


def test_sid_from_db_filename():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "abcd-1234.db")
        _make_gemini_db(db, [(1, 15, 3, None, _SYN)])
        st = WalStore(os.path.join(tmp, "wal.db"))
        GeminiWalAdapter(st, "gem-seat", 0).tail(db)
        assert st.events()[0]["sid"] == "abcd-1234"


def test_ro_connect_is_read_only_and_no_immutable():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "conv.db")
        _make_gemini_db(db, [(1, 15, 3, None, _SYN)])
        conn = ro_connect(db)
        try:
            # RO connection MUST refuse writes (proves mode=ro, not a rw open)
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO steps (idx, step_type) VALUES (99, 1)")
                conn.commit()
        finally:
            conn.close()


def test_ro_open_does_not_create_missing_db():
    """mode=ro never creates the file — a missing source is 0 events, not a new
    empty db written next to the agent (RO-probe safety)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "does-not-exist.db")
        st = WalStore(os.path.join(tmp, "wal.db"))
        assert GeminiWalAdapter(st, "gem-seat", 0).tail(db) == 0
        assert not os.path.exists(db)  # reader never materialized the file


def test_reads_live_wal_db_without_copy():
    """A WAL-mode reader (mode=ro) reads a live db with an open -wal segment; the
    adapter never copies the bare .db (torn-read hazard). Simulate a live writer
    holding uncheckpointed rows in the -wal, then poll."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "live.db")
        _make_gemini_db(db, [(1, 15, 3, None, _SYN)])
        # a live writer connection keeps rows in the -wal (no checkpoint)
        writer = sqlite3.connect(db)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO steps (idx, step_type, status, step_payload)"
                       " VALUES (2, 15, 3, ?)", (_SYN,))
        writer.commit()
        try:
            st = WalStore(os.path.join(tmp, "wal.db"))
            n = GeminiWalAdapter(st, "gem-seat", 0).tail(db)
            assert n == 2  # the reader sees the uncheckpointed -wal row
        finally:
            writer.close()
