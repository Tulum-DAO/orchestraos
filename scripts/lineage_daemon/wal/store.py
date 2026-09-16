"""WAL store — the append-only index/order/integrity layer for one lineage.

Spec: .workspace/proposals/wal-bluegreen-rotation-spec.md §2.2 (DEC-1788316931).

Bodies stay in the source stream (jsonl/rollout/db); this store holds total
order (seq), normalized kind, a one-line summary, an integrity hash, and a
`body_ref` (path+offset) resolved by hydrate later. STAGE 1 = capture-only:
this module only ever writes to a NEW `state/wal/<lineage_root>.db` — it never
touches registry/agent-sessions/state-agents/self_retire_armed/the beat path.

Append-only discipline is ENFORCED (not just documented): BEFORE UPDATE/DELETE
triggers on wal_events RAISE ABORT, so no writer — not even a buggy one — can
rewrite history. `wal_cursors` is deliberately mutable: it is crash-safe tail
resume state, not part of the event log.

INTENTIONALLY LIGHTWEIGHT: imports only sqlite3/os — no beat/execute/daemon
chain — so a capture-only tailer stays cheap and free of circular imports
(mirrors baseline_store.py's discipline).
"""
import contextlib
import os
import sqlite3

_KINDS = (
    "prompt", "response", "tool_call", "tool_result",
    "file_mod", "git", "proc", "ctx", "marker",
)
_RUNTIMES = ("claude", "codex", "gemini")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wal_events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           REAL    NOT NULL,
  lineage_root TEXT    NOT NULL,
  generation   INTEGER NOT NULL,
  sid          TEXT    NOT NULL,
  runtime      TEXT    NOT NULL CHECK(runtime IN ('claude','codex','gemini')),
  kind         TEXT    NOT NULL CHECK(kind IN (
                 'prompt','response','tool_call','tool_result',
                 'file_mod','git','proc','ctx','marker')),
  summary      TEXT,
  body_ref     TEXT,
  source_path  TEXT    NOT NULL,
  source_off   INTEGER,
  integrity    TEXT
);
CREATE INDEX IF NOT EXISTS idx_wal_lineage_seq ON wal_events(lineage_root, seq);
CREATE INDEX IF NOT EXISTS idx_wal_kind ON wal_events(lineage_root, kind, seq);

CREATE TABLE IF NOT EXISTS wal_cursors (
  source_path  TEXT PRIMARY KEY,
  lineage_root TEXT,
  last_off     INTEGER,
  last_seq     INTEGER,
  updated      REAL
);

-- Append-only enforcement: the event log is immutable once written.
CREATE TRIGGER IF NOT EXISTS wal_events_no_update
  BEFORE UPDATE ON wal_events
  BEGIN SELECT RAISE(ABORT, 'wal_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS wal_events_no_delete
  BEFORE DELETE ON wal_events
  BEGIN SELECT RAISE(ABORT, 'wal_events is append-only'); END;
"""


class WalStore:
    def __init__(self, path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._in_txn = False

    @contextlib.contextmanager
    def transaction(self):
        """Group a unit of appends into ONE atomic commit.

        Blocker-#3 condition B: an adapter that appends several events for a
        single source line must commit them together or not at all. While a
        transaction is active, append() defers its per-row commit; on normal
        exit the whole unit commits, on any exception it rolls back — so a
        mid-line raise never leaves a half-committed-line residual that the next
        tick would re-read and duplicate. Not reentrant (one line at a time).
        """
        self._in_txn = True
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        finally:
            self._in_txn = False

    def append(self, *, ts, lineage_root, generation, sid, runtime, kind,
               source_path, summary=None, body_ref=None, source_off=None,
               integrity=None):
        """INSERT one event, return its assigned total-order seq.

        Invalid runtime/kind are rejected by the schema CHECK constraints
        (raised as sqlite3.IntegrityError) — capture cannot smuggle a
        non-canonical event past the store.
        """
        cur = self._conn.execute(
            "INSERT INTO wal_events (ts, lineage_root, generation, sid, runtime,"
            " kind, summary, body_ref, source_path, source_off, integrity)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, lineage_root, generation, sid, runtime, kind, summary,
             body_ref, source_path, source_off, integrity),
        )
        if not self._in_txn:
            self._conn.commit()
        return cur.lastrowid

    def events(self, lineage_root=None):
        if lineage_root is None:
            return list(self._conn.execute(
                "SELECT * FROM wal_events ORDER BY seq"))
        return list(self._conn.execute(
            "SELECT * FROM wal_events WHERE lineage_root=? ORDER BY seq",
            (lineage_root,)))

    def count_events(self, lineage_root, sid=None):
        """Event count for a lineage; with `sid`, only events keyed to that sid (WAL-at-arm
        freshness: stale prior-capture events under an old sid must not count as live)."""
        if sid is None:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM wal_events WHERE lineage_root=?", (lineage_root,))
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM wal_events WHERE lineage_root=? AND sid=?",
                (lineage_root, sid))
        return row.fetchone()[0]

    def max_seq(self):
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM wal_events").fetchone()
        return row[0]

    def last_event(self):
        return self._conn.execute(
            "SELECT * FROM wal_events ORDER BY seq DESC LIMIT 1").fetchone()

    def get_cursor(self, source_path):
        row = self._conn.execute(
            "SELECT * FROM wal_cursors WHERE source_path=?",
            (source_path,)).fetchone()
        return dict(row) if row else None

    def set_cursor(self, source_path, lineage_root, last_off, last_seq):
        import time
        self._conn.execute(
            "INSERT INTO wal_cursors (source_path, lineage_root, last_off,"
            " last_seq, updated) VALUES (?,?,?,?,?)"
            " ON CONFLICT(source_path) DO UPDATE SET"
            " lineage_root=excluded.lineage_root, last_off=excluded.last_off,"
            " last_seq=excluded.last_seq, updated=excluded.updated",
            (source_path, lineage_root, last_off, last_seq, time.time()),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()
