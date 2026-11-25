"""Post-call ledger (design §1). One sqlite file per mode."""
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  call_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0, content_hash TEXT,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path):
        self.path = path
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def set_call_status(self, call_id, status, *, content_hash=None, error=None):
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO calls(call_id, content_hash, status, attempts, last_error, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET status=excluded.status, "
                "content_hash=COALESCE(excluded.content_hash, calls.content_hash), "
                "attempts=calls.attempts + (excluded.status='failed_retry'), "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (call_id, content_hash, status, int(status == "failed_retry"), error, now, now))

    def call_status(self, call_id):
        with self._conn() as c:
            row = c.execute("SELECT status FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return row[0] if row else None
