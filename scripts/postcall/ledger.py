"""Post-call ledger (design §1). One sqlite file per mode."""
import json
import sqlite3
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  call_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0, content_hash TEXT,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS effects (
  effect_id TEXT PRIMARY KEY, call_id TEXT, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL, external_id TEXT, created_at TEXT NOT NULL, confirmed_at TEXT);
"""
_OPEN = ("planned", "sent")


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

    def call_row(self, call_id):
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return dict(row) if row else None

    def plan(self, effect, *, call_id, conn=None):
        """Durable intent, committed before any external write. Same effect_id twice = one row."""
        sql = ("INSERT OR IGNORE INTO effects(effect_id, call_id, kind, payload_json, state, created_at) "
               "VALUES(?,?,?,?,'planned',?)")
        args = (effect.effect_id, call_id, effect.kind, json.dumps(effect.payload, sort_keys=True), _now())
        if conn is not None:
            conn.execute(sql, args)
            return
        with self._conn() as c:
            c.execute(sql, args)

    def effects(self):
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM effects ORDER BY created_at, effect_id")]

    def _set_effect(self, effect_id, state, external_id=None):
        with self._conn() as c:
            c.execute("UPDATE effects SET state=?, external_id=COALESCE(?, external_id), "
                      "confirmed_at=CASE WHEN ?='confirmed' THEN ? ELSE confirmed_at END WHERE effect_id=?",
                      (state, external_id, state, _now(), effect_id))

    def run_pending(self, adapter):
        """Reconcile, then send. An effect already visible outside is confirmed, never re-sent."""
        from .effects import Effect
        for row in [r for r in self.effects() if r["state"] in _OPEN]:
            eff = Effect(row["effect_id"], row["kind"], json.loads(row["payload_json"]))
            found = adapter.find(eff)
            if found:
                self._set_effect(eff.effect_id, "confirmed", found)
                continue
            self._set_effect(eff.effect_id, "sent")
            self._set_effect(eff.effect_id, "confirmed", adapter.send(eff))

    def call_status(self, call_id):
        with self._conn() as c:
            row = c.execute("SELECT status FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return row[0] if row else None
