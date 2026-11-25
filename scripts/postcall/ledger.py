"""Post-call ledger (design §1). One sqlite file per mode."""
import json
import secrets
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
CREATE TABLE IF NOT EXISTS commitments (
  key TEXT PRIMARY KEY, cmt TEXT UNIQUE NOT NULL, state TEXT NOT NULL,
  decision TEXT, decision_src TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS card_items (
  card_id TEXT NOT NULL, q_n INTEGER NOT NULL, key TEXT NOT NULL, options_json TEXT NOT NULL,
  suggested_n INTEGER NOT NULL, flagged INTEGER NOT NULL, PRIMARY KEY (card_id, q_n));
"""
_APPLIABLE = ("submitted", "resumed")
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

    def plan_card(self, effect, *, call_id, frozen):
        """The card intent, its frozen options and a commitment row per item: one transaction."""
        with self._conn() as c:
            self.plan(effect, call_id=call_id, conn=c)
            for f in frozen:
                c.execute("INSERT OR IGNORE INTO card_items VALUES (?,?,?,?,?,?)",
                          (effect.effect_id, f["q_n"], f["key"], json.dumps(f["options"]), f["suggested_n"],
                           int(bool(f.get("flagged")))))
                c.execute("INSERT OR IGNORE INTO commitments(key, cmt, state) VALUES (?,?, 'asked')",
                          (f["key"], "cmt_" + secrets.token_hex(6)))

    def _commitment(self, key):
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM commitments WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def decision(self, key):
        return (self._commitment(key) or {}).get("decision")

    def item_state(self, key):
        return (self._commitment(key) or {}).get("state")

    def commitment_of(self, key):
        return (self._commitment(key) or {}).get("cmt")

    def apply_answers(self, card_id, *, status, answers):
        """Resolve each frozen item (explicit final answer > Q1 rule), then decide it: first applied
        answer wins per commitment, and its effect is planned in the same transaction."""
        if status not in _APPLIABLE:
            return "not_submitted"
        q1 = answers.get(1) or {}
        bulk = q1.get("option_n") if q1.get("final") else None
        with self._conn() as c:
            rows = c.execute("SELECT q_n, key, options_json, suggested_n, flagged FROM card_items "
                             "WHERE card_id=? ORDER BY q_n", (card_id,)).fetchall()
        for q_n, key, options_json, suggested_n, flagged in rows:
            options = json.loads(options_json)
            a = answers.get(q_n) or {}
            if a.get("final") and a.get("option_n"):
                action = options[int(a["option_n"]) - 1]
            elif bulk == 1 and not flagged:
                action = options[suggested_n - 1]
            elif bulk == 3:
                action = {"action": "drop"}
            else:
                with self._conn() as c:
                    c.execute("UPDATE commitments SET state='unreviewed' WHERE key=? AND decision IS NULL", (key,))
                continue
            self._decide(key, action, f"{card_id}#{q_n}")
        return "applied"

    def _decide(self, key, action, src):
        from .effects import Effect
        with self._conn() as c:
            cur = c.execute("UPDATE commitments SET decision=?, decision_src=?, state='decided', decided_at=? "
                            "WHERE key=? AND decision IS NULL", (action["action"], src, _now(), key))
            if cur.rowcount != 1:
                return False
            cmt = c.execute("SELECT cmt FROM commitments WHERE key=?", (key,)).fetchone()[0]
            base = {"cmt": cmt, "key": key, "decision_src": src}
            if action["action"] in ("take", "track"):
                self.plan(Effect(f"task:{cmt}", "task", {**base, **action}), call_id=None, conn=c)
            elif action["action"] == "assign":
                self.plan(Effect(f"dispatch:{cmt}", "dispatch", {**base, "seat": action["seat"],
                                                                  "agent_work": action["agent_work"]}),
                          call_id=None, conn=c)
        return True

    def _dispatch_authorized(self, effect):
        p = effect.payload
        row = self._commitment(p.get("key")) if p.get("key") else None
        if row is None:
            with self._conn() as c:
                c.row_factory = sqlite3.Row
                r = c.execute("SELECT * FROM commitments WHERE cmt=?", (p.get("cmt"),)).fetchone()
            row = dict(r) if r else None
        return bool(row) and row["decision"] == "assign" and row["decision_src"] == p.get("decision_src") \
            and effect.effect_id == f"dispatch:{row['cmt']}"

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
            if eff.kind == "dispatch" and not self._dispatch_authorized(eff):
                self._set_effect(eff.effect_id, "refused")
                continue
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
