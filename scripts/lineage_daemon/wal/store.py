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
_RUNTIMES = ("claude", "codex", "gemini", "grok")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wal_events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           REAL    NOT NULL,
  lineage_root TEXT    NOT NULL,
  generation   INTEGER NOT NULL,
  sid          TEXT    NOT NULL,
  runtime      TEXT    NOT NULL CHECK(runtime IN ('claude','codex','gemini','grok')),
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
  source_dev   INTEGER,
  source_ino   INTEGER,
  updated      REAL
);

-- Append-only enforcement: the event log is immutable once written.
CREATE TRIGGER IF NOT EXISTS wal_events_no_update
  BEFORE UPDATE ON wal_events
  BEGIN SELECT RAISE(ABORT, 'wal_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS wal_events_no_delete
  BEFORE DELETE ON wal_events
  BEGIN SELECT RAISE(ABORT, 'wal_events is append-only'); END;

-- SUBAGENT SIDECAR (gm msg_d0b7b58b item 2): the seat's Claude subagents at every depth
-- (<sid>/subagents/agent-<id>.jsonl), same kinds, plus who wrote them. A separate table so no
-- existing wal_events reader (hydrate, census, ctx, watermark, drain) sees a single new row: the
-- blue's own stream and its ctx count stay main-transcript-only. Same append-only triggers.
CREATE TABLE IF NOT EXISTS wal_subagent_events (
  seq             INTEGER PRIMARY KEY AUTOINCREMENT,
  ts              REAL    NOT NULL,
  lineage_root    TEXT    NOT NULL,
  generation      INTEGER NOT NULL,
  sid             TEXT    NOT NULL,
  agent_id        TEXT    NOT NULL,
  parent_agent_id TEXT,
  depth           INTEGER,
  agent_type      TEXT,
  runtime         TEXT    NOT NULL CHECK(runtime IN ('claude','codex','gemini','grok')),
  kind            TEXT    NOT NULL CHECK(kind IN (
                    'prompt','response','tool_call','tool_result',
                    'file_mod','git','proc','ctx','marker')),
  summary         TEXT,
  body_ref        TEXT,
  source_path     TEXT    NOT NULL,
  source_off      INTEGER,
  integrity       TEXT
);
CREATE INDEX IF NOT EXISTS idx_wal_sub_lineage_seq ON wal_subagent_events(lineage_root, seq);
CREATE INDEX IF NOT EXISTS idx_wal_sub_agent_seq ON wal_subagent_events(agent_id, seq);

CREATE TABLE IF NOT EXISTS wal_subagent_cursors (
  source_path  TEXT PRIMARY KEY,
  lineage_root TEXT,
  agent_id     TEXT,
  last_off     INTEGER,
  source_dev   INTEGER,
  source_ino   INTEGER,
  updated      REAL
);

CREATE TRIGGER IF NOT EXISTS wal_subagent_events_no_update
  BEFORE UPDATE ON wal_subagent_events
  BEGIN SELECT RAISE(ABORT, 'wal_subagent_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS wal_subagent_events_no_delete
  BEFORE DELETE ON wal_subagent_events
  BEGIN SELECT RAISE(ABORT, 'wal_subagent_events is append-only'); END;
"""


class WalStore:
    def __init__(self, path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        # Existing fleet DBs predate source identity.  SQLite cannot amend a
        # CHECK constraint in place, but Grok rows only enter newly-created DBs
        # while this migration adds the cursor identity columns everywhere.
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(wal_cursors)")}
        if "source_dev" not in cols:
            self._conn.execute("ALTER TABLE wal_cursors ADD COLUMN source_dev INTEGER")
        if "source_ino" not in cols:
            self._conn.execute("ALTER TABLE wal_cursors ADD COLUMN source_ino INTEGER")
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

    def set_cursor(self, source_path, lineage_root, last_off, last_seq,
                   source_dev=None, source_ino=None):
        import time
        self._conn.execute(
            "INSERT INTO wal_cursors (source_path, lineage_root, last_off,"
            " last_seq, source_dev, source_ino, updated) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(source_path) DO UPDATE SET"
            " lineage_root=excluded.lineage_root, last_off=excluded.last_off,"
            " last_seq=excluded.last_seq,"
            " source_dev=COALESCE(excluded.source_dev, wal_cursors.source_dev),"
            " source_ino=COALESCE(excluded.source_ino, wal_cursors.source_ino),"
            " updated=excluded.updated",
            (source_path, lineage_root, last_off, last_seq, source_dev,
             source_ino, time.time()),
        )
        self._conn.commit()

    def touch_cursor(self, source_path):
        """Stamp `updated` on an EXISTING cursor row; return True if one was there.

        This is the LOOKED-AT witness for a pass that reached no consumable bytes
        (gm's ranked item 3). It deliberately does NOT insert: a source that is
        absent must never mint a cursor row, because bg_wal_census reads a cursor
        as evidence the source is real and treats "no cursor for the live sid" as
        its fail-closed branch. It also never moves last_off/last_seq — a look is
        not a consumption.
        """
        import time
        cur = self._conn.execute(
            "UPDATE wal_cursors SET updated=? WHERE source_path=?",
            (time.time(), source_path))
        self._conn.commit()
        return cur.rowcount > 0

    # ---- subagent sidecar (gm msg_d0b7b58b item 2) -------------------------------------------
    def append_subagent(self, *, ts, lineage_root, generation, sid, agent_id, runtime, kind,
                        source_path, parent_agent_id=None, depth=None, agent_type=None,
                        summary=None, body_ref=None, source_off=None, integrity=None):
        cur = self._conn.execute(
            "INSERT INTO wal_subagent_events (ts, lineage_root, generation, sid, agent_id,"
            " parent_agent_id, depth, agent_type, runtime, kind, summary, body_ref, source_path,"
            " source_off, integrity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, lineage_root, generation, sid, agent_id, parent_agent_id, depth, agent_type,
             runtime, kind, summary, body_ref, source_path, source_off, integrity))
        if not self._in_txn:
            self._conn.commit()
        return cur.lastrowid

    def subagent_events(self, lineage_root=None, agent_id=None):
        q, args = "SELECT * FROM wal_subagent_events", []
        conds = []
        if lineage_root is not None:
            conds.append("lineage_root=?")
            args.append(lineage_root)
        if agent_id is not None:
            conds.append("agent_id=?")
            args.append(agent_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return list(self._conn.execute(q + " ORDER BY seq", args))

    def get_subagent_cursor(self, source_path):
        row = self._conn.execute("SELECT * FROM wal_subagent_cursors WHERE source_path=?",
                                 (source_path,)).fetchone()
        return dict(row) if row else None

    def set_subagent_cursor(self, source_path, lineage_root, agent_id, last_off,
                            source_dev=None, source_ino=None):
        """Inside transaction() it commits WITH the line's events (one unit: no re-append after a
        crash between the two, codex r1)."""
        import time
        self._conn.execute(
            "INSERT INTO wal_subagent_cursors (source_path, lineage_root, agent_id, last_off,"
            " source_dev, source_ino, updated) VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET"
            " lineage_root=excluded.lineage_root, agent_id=excluded.agent_id,"
            " last_off=excluded.last_off, source_dev=excluded.source_dev,"
            " source_ino=excluded.source_ino, updated=excluded.updated",
            (source_path, lineage_root, agent_id, last_off, source_dev, source_ino, time.time()))
        if not self._in_txn:
            self._conn.commit()

    def close(self):
        self._conn.close()
