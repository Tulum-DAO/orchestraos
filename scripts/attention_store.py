"""attention_store.py — the Updates record and per-person seen state (attention part 1).

Five additive tables in the ledger DB (state/tasks.db), created ONLY when the migration
`m20261104_attention` is armed (approval_schema.armed: env APPROVAL_DDL_ARMED or the sentinel
~/runtime/APPROVAL_DDL_ARMED_m20261104_attention). Unarmed, every read is empty (rev 0) and every
write raises AttentionNotArmed; nothing else changes. Removing the sentinel later makes the code
read the store as unarmed again, leaving the tables inert (that is rollback step 1).

  attention_events      one row per condition, EVER (unique dedup_key). Kinds: done | failed | stuck |
                        missed_call | on_behalf. A condition that clears and comes back re-opens the
                        same row (occurrences + 1, reopened_at) and reads as unread again.
  attention_seen        (person, item_id) -> first seen_at + surface. First write wins.
  attention_meta        'rev' (bumped on every VISIBLE change, read by the /events digest),
                        'rules_epoch' (the timeout-rule clock never starts before it), seed markers.
  attention_rules       person-owned timeout rules (attention_rules.py)
  attention_rule_fires  one row per (rule, target, action) that fired

Writes: one short BEGIN IMMEDIATE transaction each, the rev bump inside it, upserts rather than
check-then-insert. busy_timeout 5 s plus 3 bounded retries, then AttentionBusy. Three writers share
the file: the gateway's detector thread, gateway request threads, and the `orchestra update` CLI.

rev counts VISIBLE changes (a new row, a re-open, a resolve, a content change, a seen mark). The
detector refreshing observed_at / clear_streak on an open row every tick is bookkeeping and does not
bump it, or every phone would refetch once a minute for nothing.

CLI:  attention_store.py migrate
      attention_store.py mirror-completions [--dry-run]
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import approval_config  # noqa: E402
import approval_schema  # noqa: E402

MIGRATION_ID = "m20261104_attention"
_BUSY_MS = 5000
_BACKOFF_S = (0.1, 0.3, 0.9)
_pending_said = False

EVENT_KINDS = ("done", "failed", "stuck", "missed_call", "on_behalf")

DDL = """
CREATE TABLE IF NOT EXISTS attention_events (
  id              TEXT PRIMARY KEY,
  kind            TEXT NOT NULL,
  agent           TEXT NOT NULL,
  reason          TEXT,
  dedup_key       TEXT NOT NULL,
  title           TEXT NOT NULL,
  body            TEXT,
  evidence        TEXT,
  source_id       TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  observed_at     TEXT,
  clear_streak    INTEGER NOT NULL DEFAULT 0,
  occurrences     INTEGER NOT NULL DEFAULT 1,
  reopened_at     TEXT,
  resolved_at     TEXT,
  resolved_reason TEXT,
  rule_id         TEXT,
  push_override   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attn_key ON attention_events(dedup_key);
CREATE INDEX IF NOT EXISTS idx_attn_created ON attention_events(created_at);
CREATE INDEX IF NOT EXISTS idx_attn_source  ON attention_events(source_id);

CREATE TABLE IF NOT EXISTS attention_seen (
  person   TEXT NOT NULL,
  item_id  TEXT NOT NULL,
  seen_at  TEXT NOT NULL,
  surface  TEXT,
  PRIMARY KEY (person, item_id)
);

CREATE TABLE IF NOT EXISTS attention_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS attention_rules (
  id          TEXT PRIMARY KEY,
  person      TEXT NOT NULL,
  name        TEXT,
  trigger     TEXT NOT NULL,
  after_s     INTEGER NOT NULL,
  scope_agent TEXT,
  scope_kind  TEXT,
  scope_risk  TEXT,
  actions     TEXT NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  is_default  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attn_rules_person ON attention_rules(person);

CREATE TABLE IF NOT EXISTS attention_rule_fires (
  rule_id   TEXT NOT NULL,
  target    TEXT NOT NULL,
  action    TEXT NOT NULL,
  fired_at  TEXT NOT NULL,
  last_at   TEXT NOT NULL,
  count     INTEGER NOT NULL DEFAULT 1,
  next_at   TEXT,
  result    TEXT,
  PRIMARY KEY (rule_id, target, action)
);
"""


class AttentionNotArmed(RuntimeError):
    """A write while m20261104_attention is unarmed (the CLI exits 3, the gateway serves no updates)."""


class AttentionBusy(RuntimeError):
    """The ledger stayed locked through every retry (the CLI exits 1: run it again with the same --key)."""


def _iso(now=None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.isoformat()


def _gen_id(prefix="evt") -> str:
    """Same shape as approval_schema._gen_id: <prefix>_<8 hex>_<ms mod 1e8>."""
    return f"{prefix}_{random.randrange(16**8):08x}_{int(time.time() * 1000) % 10**8}"


def _is_busy(e: Exception) -> bool:
    s = str(e).lower()
    return isinstance(e, sqlite3.OperationalError) and ("locked" in s or "busy" in s)


class AttentionStore:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or approval_config.db_path())
        approval_config.refuse_live_db_under_pytest(self.db_path)   # the same fence as ApprovalStore
        self.armed = None

    # ---- plumbing ----
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=_BUSY_MS / 1000, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute(f"PRAGMA busy_timeout={int(_BUSY_MS)}")
        return c

    def _is_armed(self) -> bool:
        if self.armed is None:
            self.migrate()
        return bool(self.armed)

    def _write(self, fn):
        """Run fn(c) -> (result, changed) in one BEGIN IMMEDIATE transaction; bump rev when changed."""
        if not self._is_armed():
            raise AttentionNotArmed(
                f"attention store not armed ({MIGRATION_ID}); arm via APPROVAL_DDL_ARMED={MIGRATION_ID} "
                f"or the sentinel ~/runtime/APPROVAL_DDL_ARMED_{MIGRATION_ID}")
        last = None
        for attempt in range(len(_BACKOFF_S) + 1):
            c = self._conn()
            try:
                c.execute("BEGIN IMMEDIATE")
                result, changed = fn(c)
                if changed:
                    self._bump(c)
                c.execute("COMMIT")
                return result
            except sqlite3.OperationalError as e:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if not _is_busy(e):
                    raise
                last = e
            except BaseException:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            finally:
                c.close()
            if attempt < len(_BACKOFF_S):
                time.sleep(_BACKOFF_S[attempt])
        raise AttentionBusy(f"the ledger is busy, run it again ({last})")

    def _read(self, fn, empty):
        if not self._is_armed():
            return empty
        c = self._conn()
        try:
            return fn(c)
        finally:
            c.close()

    @staticmethod
    def _bump(c):
        c.execute("INSERT INTO attention_meta(key, value) VALUES('rev', '1') "
                  "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")

    # ---- schema ----
    def migrate(self):
        """Create the tables when armed; otherwise say PENDING once per process and stay inert."""
        global _pending_said
        if not approval_schema.armed(MIGRATION_ID):
            self.armed = False
            if not _pending_said:
                _pending_said = True
                print(f"[attention_store] DDL PENDING (unarmed): {MIGRATION_ID} "
                      f"tables=attention_* — arm via APPROVAL_DDL_ARMED={MIGRATION_ID} "
                      f"or sentinel ~/runtime/APPROVAL_DDL_ARMED_{MIGRATION_ID}", file=sys.stderr)
            return []
        c = self._conn()
        try:
            before = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            c.executescript(DDL)
            # The rule clock's floor: written once, when the tables first appear (the release).
            c.execute("INSERT OR IGNORE INTO attention_meta(key, value) VALUES('rules_epoch', ?)", [_iso()])
            after = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            c.close()
        self.armed = True
        return sorted(t for t in after - before if t.startswith("attention_"))

    # ---- meta ----
    def rev(self) -> int:
        def f(c):
            r = c.execute("SELECT value FROM attention_meta WHERE key='rev'").fetchone()
            return int(r["value"]) if r else 0
        try:
            return self._read(f, 0)
        except sqlite3.Error:
            return 0

    def meta(self, key):
        return self._read(lambda c: (c.execute("SELECT value FROM attention_meta WHERE key=?", [key])
                                     .fetchone() or {"value": None})["value"], None)

    def set_meta(self, key, value, bump=False):
        def f(c):
            c.execute("INSERT INTO attention_meta(key, value) VALUES(?, ?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, str(value)])
            return None, bump
        return self._write(f)

    # ---- events ----
    @staticmethod
    def _id_for(c, key):
        r = c.execute("SELECT id FROM attention_events WHERE dedup_key=?", [key]).fetchone()
        return r["id"] if r else None

    def add_done(self, agent, title, key=None, body=None, evidence=None, source_id=None, now=None):
        """A done note, resolved at birth. With source_id (its linked completion card) the key is
        card:<source_id> and this UPSERTS (the CLI's text wins over the mirror's); otherwise the key is
        done:<agent>:<key> (a fresh uuid when none is given) and a retry with the same key is a no-op."""
        ts = _iso(now)
        ev = json.dumps(evidence) if isinstance(evidence, (dict, list)) else evidence
        if source_id:
            dkey = f"card:{source_id}"
            sql = ("INSERT INTO attention_events (id, kind, agent, dedup_key, title, body, evidence, source_id, "
                   "created_at, updated_at, resolved_at, resolved_reason) "
                   "VALUES (?, 'done', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done_note') "
                   "ON CONFLICT(dedup_key) DO UPDATE SET title=excluded.title, body=excluded.body, "
                   "evidence=COALESCE(excluded.evidence, evidence), updated_at=excluded.updated_at")
        else:
            dkey = f"done:{agent}:{key or uuid.uuid4().hex}"
            sql = ("INSERT INTO attention_events (id, kind, agent, dedup_key, title, body, evidence, source_id, "
                   "created_at, updated_at, resolved_at, resolved_reason) "
                   "VALUES (?, 'done', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done_note') "
                   "ON CONFLICT(dedup_key) DO NOTHING")

        def f(c):
            cur = c.execute(sql, [_gen_id(), agent, dkey, title, body, ev, source_id, ts, ts, ts])
            return self._id_for(c, dkey), cur.rowcount > 0
        return self._write(f)

    def _insert_resolved(self, kind, agent, reason, dkey, title, body=None, evidence=None, source_id=None,
                         rule_id=None, now=None):
        ts = _iso(now)
        ev = json.dumps(evidence) if isinstance(evidence, (dict, list)) else evidence

        def f(c):
            cur = c.execute(
                "INSERT INTO attention_events (id, kind, agent, reason, dedup_key, title, body, evidence, "
                "source_id, created_at, updated_at, resolved_at, resolved_reason, rule_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dedup_key) DO NOTHING",
                [_gen_id(), kind, agent, reason, dkey, title, body, ev, source_id, ts, ts, ts, "recorded", rule_id])
            return self._id_for(c, dkey), cur.rowcount > 0
        return self._write(f)

    def on_behalf_for(self, item_id):
        """The on_behalf record (the Updates row's evidence) for a card a rule or a handed-to agent answered, or
        None."""
        def f(c):
            r = c.execute("SELECT evidence FROM attention_events WHERE dedup_key=?", [f"on_behalf:{item_id}"]).fetchone()
            if r is None or not r["evidence"]:
                return None
            try:
                return json.loads(r["evidence"])
            except ValueError:
                return None
        return self._read(f, None)

    def record_missed_call(self, call_id, agent, title, now=None):
        """The missed_call type (its producer, an agent calling the person, arrives with APNs in part 5)."""
        return self._insert_resolved("missed_call", agent, None, f"missed_call:{call_id}", title,
                                     source_id=call_id, now=now)

    def record_on_behalf(self, item_id, agent, title, body=None, on_behalf=None, rule_id=None, now=None):
        """A card a timeout rule closed for the person: one quiet Updates row per item, ever."""
        return self._insert_resolved("on_behalf", agent, (on_behalf or {}).get("action"), f"on_behalf:{item_id}",
                                     title, body=body, evidence=on_behalf, source_id=item_id, rule_id=rule_id,
                                     now=now)

    def open_condition(self, kind, agent, reason, dedup_key, title, body=None, source_id=None, now=None,
                       rule_id=None, push_override=None):
        """Open (or keep open) the one row for this condition. A resolved row RE-OPENS: occurrences + 1,
        reopened_at = now, and its seen marks are deleted so it reads as unread again."""
        ts = _iso(now)
        po = json.dumps(push_override) if isinstance(push_override, dict) else push_override

        def f(c):
            prev = c.execute("SELECT id, resolved_at, push_override FROM attention_events WHERE dedup_key=?",
                             [dedup_key]).fetchone()
            if prev is None:
                c.execute(
                    "INSERT INTO attention_events (id, kind, agent, reason, dedup_key, title, body, source_id, "
                    "created_at, updated_at, observed_at, rule_id, push_override) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [_gen_id(), kind, agent, reason, dedup_key, title, body, source_id, ts, ts, ts, rule_id, po])
                return self._id_for(c, dedup_key), True
            eid = prev["id"]
            if prev["resolved_at"] is not None:
                c.execute(
                    "UPDATE attention_events SET resolved_at=NULL, resolved_reason=NULL, occurrences=occurrences+1, "
                    "reopened_at=?, updated_at=?, observed_at=?, clear_streak=0, title=?, body=COALESCE(?, body), "
                    "rule_id=COALESCE(?, rule_id), push_override=? WHERE id=?",
                    [ts, ts, ts, title, body, rule_id, po, eid])
                c.execute("DELETE FROM attention_seen WHERE item_id=?", [eid])
                return eid, True
            changed = po is not None and po != prev["push_override"]
            c.execute("UPDATE attention_events SET observed_at=?, clear_streak=0, "
                      "push_override=COALESCE(?, push_override), rule_id=COALESCE(?, rule_id) WHERE id=?",
                      [ts, po, rule_id, eid])
            if changed:
                c.execute("UPDATE attention_events SET updated_at=? WHERE id=?", [ts, eid])
            return eid, changed
        return self._write(f)

    def set_push_override(self, event_id, push_override, now=None):
        po = json.dumps(push_override) if isinstance(push_override, dict) else push_override

        def f(c):
            cur = c.execute("UPDATE attention_events SET push_override=?, updated_at=? WHERE id=? "
                            "AND COALESCE(push_override,'') <> COALESCE(?, '')", [po, _iso(now), event_id, po])
            return cur.rowcount > 0, cur.rowcount > 0
        return self._write(f)

    def note_absent(self, event_id) -> int:
        """One more successful scan without the condition. Bookkeeping only (no rev bump)."""
        def f(c):
            c.execute("UPDATE attention_events SET clear_streak=clear_streak+1 WHERE id=? AND resolved_at IS NULL",
                      [event_id])
            r = c.execute("SELECT clear_streak FROM attention_events WHERE id=?", [event_id]).fetchone()
            return (r["clear_streak"] if r else 0), False
        return self._write(f)

    def resolve(self, event_id, reason, now=None) -> bool:
        ts = _iso(now)

        def f(c):
            cur = c.execute("UPDATE attention_events SET resolved_at=?, resolved_reason=?, updated_at=? "
                            "WHERE id=? AND resolved_at IS NULL", [ts, reason, ts, event_id])
            return cur.rowcount > 0, cur.rowcount > 0
        return self._write(f)

    # ---- reads ----
    @staticmethod
    def _agents_sql(agents, col="agent"):
        if agents is None:
            return "", []
        agents = list(agents)
        if not agents:
            return " AND 0", []
        return f" AND {col} IN ({','.join('?' for _ in agents)})", agents

    def get(self, event_id):
        return self._read(lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM attention_events WHERE id=?", [event_id]).fetchone()), None)

    def get_by_key_any(self, dedup_key):
        return self._read(lambda c: (lambda r: dict(r) if r else None)(
            c.execute("SELECT * FROM attention_events WHERE dedup_key=?", [dedup_key]).fetchone()), None)

    def exists(self, ids):
        ids = [i for i in ids if isinstance(i, str)]
        if not ids:
            return set()
        return self._read(lambda c: {r["id"] for r in c.execute(
            f"SELECT id FROM attention_events WHERE id IN ({','.join('?' for _ in ids)})", ids)}, set())

    def list_events(self, since=None, limit=100, agents=None):
        """Updates order: open rows first, then newest (re-opened rows count from their re-open)."""
        where, args = "WHERE 1", []
        if since:
            where += " AND updated_at >= ?"
            args.append(since)
        a_sql, a_args = self._agents_sql(agents)
        where += a_sql
        args += a_args
        return self._read(lambda c: [dict(r) for r in c.execute(
            f"SELECT * FROM attention_events {where} "
            "ORDER BY (resolved_at IS NULL) DESC, COALESCE(reopened_at, created_at) DESC, id DESC LIMIT ?",
            args + [int(limit)])], [])

    def open_events(self, kinds=None):
        k = list(kinds) if kinds else None
        sql = "SELECT * FROM attention_events WHERE resolved_at IS NULL"
        if k:
            sql += f" AND kind IN ({','.join('?' for _ in k)})"
        return self._read(lambda c: [dict(r) for r in c.execute(sql + " ORDER BY created_at", k or [])], [])

    def unread_count(self, person, agents=None, since=None) -> int:
        """True total of unread Updates (never the size of a page). A done note whose linked completion
        card was answered reads as seen, so the join mirrors attention_items' rule."""
        def f(c):
            has_ledger = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                   "AND name='approval_requests'").fetchone() is not None
            join = ("LEFT JOIN approval_requests a ON e.kind='done' AND a.id=e.source_id" if has_ledger else "")
            linked = " AND NOT (a.id IS NOT NULL AND a.status <> 'pending')" if has_ledger else ""
            a_sql, a_args = self._agents_sql(agents, "e.agent")
            s_sql, s_args = (" AND e.updated_at >= ?", [since]) if since else ("", [])
            r = c.execute(
                "SELECT COUNT(*) AS n FROM attention_events e "
                "LEFT JOIN attention_seen s ON s.person=? AND s.item_id=e.id "
                f"{join} WHERE s.item_id IS NULL{linked}{a_sql}{s_sql}",
                [person] + a_args + s_args).fetchone()
            return int(r["n"])
        return self._read(f, 0)

    def count_events(self, agents=None, since=None) -> int:
        a_sql, a_args = self._agents_sql(agents)
        s_sql, s_args = (" AND updated_at >= ?", [since]) if since else ("", [])
        return self._read(lambda c: int(c.execute(
            f"SELECT COUNT(*) FROM attention_events WHERE 1{a_sql}{s_sql}", a_args + s_args).fetchone()[0]), 0)

    # ---- seen ----
    def mark_seen(self, person, ids, surface, now=None) -> int:
        """First write wins. Returns how many items became seen now; rev bumps once if any did."""
        ts = _iso(now)
        ids = [i for i in dict.fromkeys(ids) if isinstance(i, str) and i]

        def f(c):
            n = 0
            for i in ids:
                n += c.execute("INSERT OR IGNORE INTO attention_seen(person, item_id, seen_at, surface) "
                               "VALUES (?,?,?,?)", [person, i, ts, surface]).rowcount
            return n, n > 0
        return self._write(f)

    def seen_map(self, person, ids=None):
        def f(c):
            if ids is None:
                rows = c.execute("SELECT item_id, seen_at, surface FROM attention_seen WHERE person=?", [person])
            else:
                lst = list(ids)
                if not lst:
                    return {}
                rows = c.execute("SELECT item_id, seen_at, surface FROM attention_seen WHERE person=? AND "
                                 f"item_id IN ({','.join('?' for _ in lst)})", [person] + lst)
            return {r["item_id"]: (r["seen_at"], r["surface"]) for r in rows}
        return self._read(f, {})


# ---- completion cards -> Updates (mirror, never move) ------------------------------------------

def mirror_completions(store: AttentionStore, ledger, dry_run=False):
    """Every PENDING completion card gets one done event (key card:<id>, the card's own created_at).
    The card is never touched: it stays pending, so /pending-approvals, the phone and the watch show it
    exactly as before. Idempotent (ON CONFLICT DO NOTHING). Returns [{id, source_id, agent, title}]
    planned (dry run) or newly inserted."""
    if not store._is_armed():
        return []
    cards = [r for r in ledger.pending_to_notify() if r.get("kind") == "completion"]
    out = []
    for r in cards:
        dkey = f"card:{r['id']}"
        if dry_run:
            if store.get_by_key_any(dkey) is None:
                out.append({"id": None, "source_id": r["id"], "agent": r["from_agent"], "title": r["question"]})
            continue
        ts = r.get("created_at") or _iso()

        def f(c, r=r, dkey=dkey, ts=ts):
            cur = c.execute(
                "INSERT INTO attention_events (id, kind, agent, dedup_key, title, body, evidence, source_id, "
                "created_at, updated_at, resolved_at, resolved_reason) "
                "VALUES (?, 'done', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'done_note') ON CONFLICT(dedup_key) DO NOTHING",
                [_gen_id(), r["from_agent"], dkey, r["question"], r.get("summary"), r.get("evidence"), r["id"],
                 ts, ts, ts])
            return (AttentionStore._id_for(c, dkey) if cur.rowcount else None), cur.rowcount > 0
        eid = store._write(f)
        if eid:
            out.append({"id": eid, "source_id": r["id"], "agent": r["from_agent"], "title": r["question"]})
    return out


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="attention_store.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    pm = sub.add_parser("mirror-completions")
    pm.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    st = AttentionStore()
    made = st.migrate()
    if args.cmd == "migrate":
        if not st.armed:
            print(f"not armed: {MIGRATION_ID} (nothing created)")
            return 3
        print("tables created: " + (", ".join(made) if made else "none (already present)"))
        try:
            import notify_policy
            from attention_rules import RuleStore
            person, _ = notify_policy.install_user_and_zone()
            if RuleStore(st).seed_defaults(person):
                print(f"default rules seeded for {person} (remind-only; the no-progress rule is off)")
        except Exception as e:  # noqa: BLE001 — the gateway seeds them on its first tick anyway
            print(f"default rules not seeded now ({e}); the gateway seeds them on its first tick")
        return 0
    if not st.armed:
        print(f"not armed: {MIGRATION_ID}")
        return 3
    ledger = approval_schema.ApprovalStore()
    ledger.migrate()
    rows = mirror_completions(st, ledger, dry_run=args.dry_run)
    for r in rows:
        print(f"{'would mirror' if args.dry_run else 'mirrored'} {r['source_id']} ({r['agent']}): {r['title']}")
    print(f"{len(rows)} cards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
