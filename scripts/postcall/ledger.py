"""Post-call ledger (design §1). One sqlite file per mode."""
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

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
CREATE TABLE IF NOT EXISTS due_changes (
  cmt TEXT NOT NULL, seq INTEGER NOT NULL, due_ts TEXT NOT NULL, due_source TEXT, task_due_ts TEXT,
  from_call TEXT, created_at TEXT NOT NULL, PRIMARY KEY (cmt, seq));
CREATE TABLE IF NOT EXISTS results (cmt TEXT PRIMARY KEY, text TEXT, recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS card_items (
  card_id TEXT NOT NULL, q_n INTEGER NOT NULL, key TEXT NOT NULL, options_json TEXT NOT NULL,
  suggested_n INTEGER NOT NULL, flagged INTEGER NOT NULL, item_json TEXT, PRIMARY KEY (card_id, q_n));
"""
# Identity of a commitment for cross-call linking (design §5), added to rows from before it existed.
_CMT_COLUMNS = (("owner", "TEXT"), ("counterpart", "TEXT"), ("action_type", "TEXT"), ("operation", "TEXT"),
                ("deliverable", "TEXT"), ("recurring", "INTEGER NOT NULL DEFAULT 0"), ("trust", "TEXT"),
                ("text", "TEXT"), ("due_ts", "TEXT"), ("due_source", "TEXT"), ("task_due_ts", "TEXT"),
                ("first_call", "TEXT"), ("first_call_date", "TEXT"), ("linked_cmt", "TEXT"),
                ("closed_at", "TEXT"), ("due_pending_patch", "INTEGER"), ("quote", "TEXT"))
_META = ("owner", "counterpart", "action_type", "operation", "deliverable", "recurring", "trust", "text",
         "due_ts", "due_source", "task_due_ts", "first_call", "first_call_date", "quote")
# The content hash a call's current-revision card was planned at (card_revision compares against it).
_CALL_COLUMNS = (("card_hash", "TEXT"),)
# Per-effect failure bookkeeping (run_pending), added to rows from before it existed.
_EFFECT_COLUMNS = (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("last_error", "TEXT"), ("rerouted", "TEXT"))
MAX_EFFECT_ATTEMPTS = 5
MAX_CALL_ATTEMPTS = 3   # failed_retry(n) -> failed_final (design §1 states); the sweep skips it after
CLOSED_WINDOW = timedelta(days=14)
_APPLIABLE = ("submitted", "resumed")
_OPEN = ("planned", "sent")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, path):
        self.path = path
        self._failed_this_run = set()   # one Ledger per sweep: a failed effect is retried next sweep
        with self._conn() as c:
            c.executescript(_SCHEMA)
            for table, columns in (("commitments", _CMT_COLUMNS), ("effects", _EFFECT_COLUMNS),
                                   ("calls", _CALL_COLUMNS)):
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns:
                    if name not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def set_call_status(self, call_id, status, *, content_hash=None, error=None):
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO calls(call_id, content_hash, status, attempts, last_error, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET "
                # attempts count per transcript: a new content hash starts a fresh budget
                "status=CASE WHEN excluded.status='failed_retry' AND (CASE WHEN excluded.content_hash IS NOT "
                "calls.content_hash AND excluded.content_hash IS NOT NULL THEN 0 ELSE calls.attempts END) + 1 >= ? "
                "THEN 'failed_final' ELSE excluded.status END, "
                "attempts=(CASE WHEN excluded.content_hash IS NOT calls.content_hash AND excluded.content_hash "
                "IS NOT NULL THEN 0 ELSE calls.attempts END) + (excluded.status='failed_retry'), "
                "content_hash=COALESCE(excluded.content_hash, calls.content_hash), "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (call_id, content_hash, status, int(status == "failed_retry"), error, now, now, MAX_CALL_ATTEMPTS))

    def calls_with_status(self, status):
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT call_id FROM calls WHERE status=? ORDER BY rowid", (status,))]

    def card_revision(self, call_id, content_hash):
        """The revision a card for this content goes under (design §1 "Revisions"): the call's current
        one, bumped once when the content changed after a card was planned at it. It compares with the
        hash the card was planned at, never the call's last-seen hash: a failed attempt stores that one
        first, and comparing with it would re-use the old revision's card id (review round 2, B1)."""
        with self._conn() as c:
            row = c.execute("SELECT revision, COALESCE(card_hash, content_hash) FROM calls WHERE call_id=?",
                            (call_id,)).fetchone()
            if row is None:
                return 0
            rev, old_hash = row
            carded = c.execute("SELECT 1 FROM effects WHERE kind='card' AND effect_id LIKE ? LIMIT 1",
                               (f"card:{call_id}:r{rev}:p%",)).fetchone()
            if carded and old_hash != content_hash:
                rev += 1
                c.execute("UPDATE calls SET revision=? WHERE call_id=?", (rev, call_id))
            return rev

    def known(self, key):
        """True when this source identity already has a commitments row (asked, linked or decided)."""
        with self._conn() as c:
            return c.execute("SELECT 1 FROM commitments WHERE key=?", (key,)).fetchone() is not None

    def has_card(self, call_id):
        with self._conn() as c:
            return c.execute("SELECT 1 FROM effects WHERE kind='card' AND call_id=? LIMIT 1",
                             (call_id,)).fetchone() is not None

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

    def plan_card(self, effect, *, call_id, frozen, meta=None):
        self.plan_cards([(effect, frozen)], call_id=call_id, meta=meta)

    def plan_cards(self, pages, *, call_id, meta=None, content_hash=None):
        """Every page of one card revision: its intent, frozen options and a commitment row per item,
        all in one transaction, so a crash between pages never leaves a half-planned card. meta[key]
        is the item's linking identity (design §5), stored on its commitment row."""
        meta = meta or {}
        with self._conn() as c:
            for effect, frozen in pages:
                self._plan_page(c, effect, call_id, frozen, meta)
            if content_hash is not None:   # the call row may not exist yet on a first ingest
                now = _now()
                c.execute("INSERT INTO calls(call_id, content_hash, card_hash, status, created_at, updated_at) "
                          "VALUES(?,?,?, 'carding', ?,?) ON CONFLICT(call_id) DO UPDATE SET "
                          "card_hash=excluded.card_hash", (call_id, content_hash, content_hash, now, now))

    def due_of(self, cmt):
        with self._conn() as c:
            row = c.execute("SELECT due_ts FROM commitments WHERE cmt=?", (cmt,)).fetchone()
        return row[0] if row else None

    def occurrences(self, call_id):
        """This call's earlier occurrences (asked or linked), for re-extraction (design §5)."""
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(
                "SELECT key, owner, action_type, operation, deliverable, quote, text, first_call_date, "
                "COALESCE(linked_cmt, cmt) AS cmt FROM commitments WHERE first_call=? ORDER BY rowid", (call_id,))]

    def _plan_page(self, c, effect, call_id, frozen, meta):
        if c.execute("SELECT 1 FROM card_items WHERE card_id=? LIMIT 1", (effect.effect_id,)).fetchone():
            raise ValueError(f"{effect.effect_id} already has items; a card's frozen items never change")
        self.plan(effect, call_id=call_id, conn=c)
        for f in frozen:
            c.execute("INSERT OR IGNORE INTO card_items VALUES (?,?,?,?,?,?,?)",
                      (effect.effect_id, f["q_n"], f["key"], json.dumps(f["options"]), f["suggested_n"],
                       int(bool(f.get("flagged"))), json.dumps(f.get("item"))))
            m = meta.get(f["key"]) or {}
            c.execute(f"INSERT OR IGNORE INTO commitments(key, cmt, state, {', '.join(_META)}) "
                      f"VALUES (?,?, 'asked', {', '.join('?' * len(_META))})",
                      (f["key"], "cmt_" + secrets.token_hex(6), *[_meta_value(m, k) for k in _META]))

    # --- cross-call linking (design §5) ------------------------------------------------------------

    def link_candidates(self, *, counterpart, deliverable, exclude_call, at):
        """Commitments from other calls with this normalized counterpart + deliverable that are open,
        or closed (done / dropped) less than 14 days before `at`. Oldest first."""
        cutoff = _parse(at) - CLOSED_WINDOW
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT * FROM commitments WHERE counterpart=? AND deliverable=? AND first_call IS NOT ? "
                "AND decision IS NOT 'same_as' ORDER BY rowid", (counterpart, deliverable, exclude_call)).fetchall()
        return [dict(r) for r in rows if not r["closed_at"] or _parse(r["closed_at"]) > cutoff]

    def link(self, key, cmt, *, call_id, meta):
        """An occurrence auto-linked to an existing commitment: recorded, never asked (design §5)."""
        with self._conn() as c:
            c.execute(f"INSERT OR IGNORE INTO commitments(key, cmt, state, decision, decision_src, decided_at, "
                      f"linked_cmt, {', '.join(_META)}) VALUES (?,?, 'linked', 'same_as', ?, ?, ?, "
                      f"{', '.join('?' * len(_META))})",
                      (key, "cmt_" + secrets.token_hex(6), f"auto:{call_id}", _now(), cmt,
                       *[_meta_value(meta, k) for k in _META]))

    def due_changes(self, cmt):
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM due_changes WHERE cmt=? ORDER BY seq", (cmt,))]

    def due_pending_patch(self, cmt):
        with self._conn() as c:
            row = c.execute("SELECT due_pending_patch FROM commitments WHERE cmt=?", (cmt,)).fetchone()
        return row[0] if row else None

    def record_due_change(self, cmt, due_ts, due_source, from_call, task_due_ts=None, *, conn=None):
        """An immutable due_changes row with the next seq, plus what the commitment's state calls for
        (design §5 table). Older open patch/duenote effects for the commitment become obsolete, so a
        stale retry can never overwrite a newer date. Returns the seq."""
        if conn is None:
            with self._conn() as c:
                return self.record_due_change(cmt, due_ts, due_source, from_call, task_due_ts, conn=c)
        from .effects import Effect
        c = conn
        row = c.execute("SELECT key, decision, decision_src, owner FROM commitments WHERE cmt=?", (cmt,)).fetchone()
        key, decision, decision_src, owner = row
        task_due_ts = task_due_ts or due_ts
        seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM due_changes WHERE cmt=?", (cmt,)).fetchone()[0]
        c.execute("INSERT INTO due_changes VALUES (?,?,?,?,?,?,?)",
                  (cmt, seq, due_ts, due_source, task_due_ts, from_call, _now()))
        c.execute("UPDATE commitments SET due_ts=?, due_source=?, task_due_ts=? WHERE cmt=?",
                  (due_ts, due_source, task_due_ts, cmt))
        c.execute("UPDATE effects SET state='obsolete' WHERE state IN ('planned', 'sent') AND kind IN "
                  "('patch', 'duenote') AND (effect_id LIKE ? OR effect_id LIKE ?)",
                  (f"patch:{cmt}:s%", f"duenote:{cmt}:s%"))
        base = {"cmt": cmt, "key": key, "seq": seq, "due_ts": task_due_ts, "due_source": due_source,
                "decision_src": decision_src}
        if decision == "assign":
            d = c.execute("SELECT payload_json, rerouted FROM effects WHERE effect_id=?", (f"dispatch:{cmt}",)).fetchone()
            seat = "gm" if not d or d[1] else json.loads(d[0])["seat"]   # follows the work if it went to gm
            self.plan(Effect(f"duenote:{cmt}:s{seq}", "duenote",
                             {**base, "to": seat, "dispatch": f"dispatch:{cmt}"}), call_id=from_call, conn=c)
        elif decision in ("take", "track"):
            if _patch_enabled():
                self.plan(Effect(f"patch:{cmt}:s{seq}", "patch", base), call_id=from_call, conn=c)
                c.execute("UPDATE commitments SET due_pending_patch=NULL WHERE cmt=?", (cmt,))
            else:
                c.execute("UPDATE commitments SET due_pending_patch=? WHERE cmt=?", (seq, cmt))
        return seq

    def record_result(self, cmt, text, *, now=None):
        """A seat's result for a dispatched (or otherwise finished) commitment: it is done (design §6)."""
        at = now or _now()
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO results VALUES (?,?,?)", (cmt, text, at))
            c.execute("UPDATE commitments SET state='done', closed_at=COALESCE(closed_at, ?) WHERE cmt=?", (at, cmt))

    def plan_retire(self, card_id, effect, *, call_id):
        """The retire intent and its undecided items marked `unreviewed`: one transaction."""
        with self._conn() as c:
            self.plan(effect, call_id=call_id, conn=c)
            c.execute("UPDATE commitments SET state='unreviewed' WHERE decision IS NULL AND state='asked' "
                      "AND key IN (SELECT key FROM card_items WHERE card_id=?)", (card_id,))

    def dispatched_without_result(self):
        """Commitment ids with a delivered dispatch and no recorded result yet."""
        with self._conn() as c:
            done = {r[0] for r in c.execute("SELECT cmt FROM results")}
            rows = c.execute("SELECT payload_json FROM effects WHERE kind='dispatch' AND state='confirmed' "
                             "ORDER BY rowid").fetchall()
        return [cmt for cmt in (json.loads(r[0]).get("cmt") for r in rows) if cmt and cmt not in done]

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
        row = self._commitment(key) or {}
        return row.get("linked_cmt") or row.get("cmt")

    def apply_answers(self, card_id, *, status, answers):
        """Resolve each frozen item (explicit final answer > Q1 rule), then decide it: first applied
        answer wins per commitment, and its effect is planned in the same transaction."""
        if status not in _APPLIABLE:
            return "not_submitted"
        q1 = answers.get(1) or {}
        bulk = q1.get("option_n") if q1.get("final") else None
        with self._conn() as c:
            rows = c.execute("SELECT q_n, key, options_json, suggested_n, flagged, item_json FROM card_items "
                             "WHERE card_id=? ORDER BY q_n", (card_id,)).fetchall()
        for q_n, key, options_json, suggested_n, flagged, item_json in rows:
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
            self._decide(key, action, f"{card_id}#{q_n}", json.loads(item_json) if item_json else None)
        return "applied"

    def _decide(self, key, action, src, item=None):
        from .effects import Effect
        kind = action["action"]
        now = _now()
        with self._conn() as c:
            cur = c.execute("UPDATE commitments SET decision=?, decision_src=?, decided_at=?, "
                            "state=CASE ? WHEN 'drop' THEN 'dropped' ELSE 'decided' END, "
                            "closed_at=CASE ? WHEN 'drop' THEN ? ELSE closed_at END "
                            "WHERE key=? AND decision IS NULL", (kind, src, now, kind, kind, now, key))
            if cur.rowcount != 1:
                return False
            cmt, due_ts, due_source, task_due_ts, first_call = c.execute(
                "SELECT cmt, due_ts, due_source, task_due_ts, first_call FROM commitments WHERE key=?",
                (key,)).fetchone()
            if kind == "same_as":
                # Shaw said this item IS that commitment: link it and carry its due over as a change.
                c.execute("UPDATE commitments SET linked_cmt=? WHERE key=?", (action["cmt"], key))
                if due_ts:
                    old = c.execute("SELECT due_ts FROM commitments WHERE cmt=?", (action["cmt"],)).fetchone()
                    if old and old[0] != due_ts:
                        self.record_due_change(action["cmt"], due_ts, due_source, first_call, task_due_ts, conn=c)
                return True
            latest = c.execute("SELECT due_ts, due_source, task_due_ts FROM due_changes WHERE cmt=? "
                               "ORDER BY seq DESC LIMIT 1", (cmt,)).fetchone()
            if latest:  # a later call moved the date before Shaw decided: the latest seq is the date
                item = {**(item or {}), "due_ts": latest[0], "due_source": latest[1]}
                if kind == "track":
                    action = {**action, "chase_date": latest[2]}
            base = {"cmt": cmt, "key": key, "decision_src": src, "item": item}
            if action["action"] in ("take", "track"):
                self.plan(Effect(f"task:{cmt}", "task", {**base, **action}), call_id=None, conn=c)
            elif action["action"] == "assign":
                self.plan(Effect(f"dispatch:{cmt}", "dispatch", {**base, "seat": action["seat"],
                                                                  "agent_work": action["agent_work"],
                                                                  **({"route": True} if action.get("route") else {})}),
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
            return [dict(r) for r in c.execute("SELECT * FROM effects ORDER BY rowid")]

    def _set_effect(self, effect_id, state, external_id=None):
        with self._conn() as c:
            c.execute("UPDATE effects SET state=?, external_id=COALESCE(?, external_id), "
                      "confirmed_at=CASE WHEN ?='confirmed' THEN ? ELSE confirmed_at END WHERE effect_id=?",
                      (state, external_id, state, _now(), effect_id))

    def run_pending(self, adapter):
        """Reconcile, then send. An effect already visible outside is confirmed, never re-sent.
        Each effect is tried on its own: a failure is recorded on its row and never blocks the rest.
        It is retried on the next sweep (not again in this one); after MAX_EFFECT_ATTEMPTS it is
        `failed` and skipped. A crash (SystemExit, KeyboardInterrupt) still propagates."""
        from .effects import Effect
        for row in [r for r in self.effects() if r["state"] in _OPEN]:
            if row["effect_id"] in self._failed_this_run:
                continue
            eff = Effect(row["effect_id"], row["kind"], json.loads(row["payload_json"]))
            try:
                self._run_one(eff, adapter)
            except Exception as e:
                self._effect_failed(eff.effect_id, e)

    def _run_one(self, eff, adapter):
        from .effects import Effect
        if eff.kind == "dispatch" and not self._dispatch_authorized(eff):
            self._set_effect(eff.effect_id, "refused")
            return
        if eff.kind == "dispatch":
            # The seat must be live right now (design §6 check 4). If not, no silent reroute: the same
            # dispatch goes to gm, asking gm to route it, and the effect records `rerouted`.
            from . import seats
            seat = eff.payload.get("seat")
            rerouted = seat if seat != "gm" and not seats.is_live(seat) else None
            with self._conn() as c:
                c.execute("UPDATE effects SET rerouted=? WHERE effect_id=?", (rerouted, eff.effect_id))
            if rerouted:
                eff = Effect(eff.effect_id, eff.kind, {**eff.payload, "seat": "gm", "rerouted_from": rerouted})
        found = adapter.find(eff)
        if found:
            self._set_effect(eff.effect_id, "confirmed", found)
            return
        self._set_effect(eff.effect_id, "sent")
        self._set_effect(eff.effect_id, "confirmed", adapter.send(eff))

    def _effect_failed(self, effect_id, error):
        self._failed_this_run.add(effect_id)
        with self._conn() as c:
            c.execute("UPDATE effects SET attempts=attempts+1, last_error=?, "
                      "state=CASE WHEN attempts+1 >= ? THEN 'failed' ELSE state END WHERE effect_id=?",
                      (repr(error)[-500:], MAX_EFFECT_ATTEMPTS, effect_id))

    def failed_effects(self):
        return [{"effect_id": r["effect_id"], "kind": r["kind"], "attempts": r["attempts"], "error": r["last_error"]}
                for r in self.effects() if r["state"] == "failed"]

    # --- results notice (design §6) -------------------------------------------------------------------

    def results_due(self, now):
        """Plan one `results:<card>` notice to gm per card whose dispatches are all confirmed with a
        result each, or whose first dispatch is 48 h old (partial); a card with nothing dispatched but
        unreviewed items gets one too. Returns the newly planned ids."""
        from .effects import Effect
        now = _parse(now)
        planned = []
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            done = {r[0] for r in c.execute("SELECT effect_id FROM effects WHERE kind='results'")}
            results = {r[0] for r in c.execute("SELECT cmt FROM results")}
            dispatches = [dict(r) for r in c.execute("SELECT * FROM effects WHERE kind='dispatch'")]
            cards = [dict(r) for r in c.execute("SELECT * FROM effects WHERE kind='card'")]
            for card in cards:
                eid = f"results:{card['effect_id']}"
                if eid in done:
                    continue
                mine = [d for d in dispatches
                        if (json.loads(d["payload_json"]).get("decision_src") or "").startswith(card["effect_id"] + "#")]
                u = c.execute("SELECT COUNT(*) FROM card_items ci JOIN commitments cm ON cm.key=ci.key "
                              "WHERE ci.card_id=? AND cm.state='unreviewed'", (card["effect_id"],)).fetchone()[0]
                if not mine and not u:
                    continue
                cmts = [json.loads(d["payload_json"])["cmt"] for d in mine]
                k = sum(cm in results for cm in cmts)
                complete = k == len(mine) and all(d["state"] == "confirmed" for d in mine)
                if mine and not complete:
                    first = min(_parse(d["confirmed_at"] or d["created_at"]) for d in mine)
                    if now - first < timedelta(hours=48):
                        continue
                # Due changes waiting on the gated patch (design §5 table), for this card's commitments.
                p = c.execute("SELECT COUNT(DISTINCT cm.cmt) FROM card_items ci JOIN commitments cm "
                              "ON cm.key=ci.key OR cm.cmt=(SELECT linked_cmt FROM commitments WHERE key=ci.key) "
                              "WHERE ci.card_id=? AND cm.due_pending_patch IS NOT NULL",
                              (card["effect_id"],)).fetchone()[0]
                title = _call_title(c, card)
                body = (f"{title}: {k}/{len(mine)} done, unreviewed {u}, due_pending_patch {p}"
                        + ("" if complete else " (partial, 48 h)"))
                self.plan(Effect(eid, "results", {"to": "gm", "subject": f"Post-call results ready: {title}",
                                                  "body": body, "card": card["effect_id"]}),
                          call_id=card["call_id"], conn=c)
                planned.append(eid)
        return planned

    def call_status(self, call_id):
        with self._conn() as c:
            row = c.execute("SELECT status FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return row[0] if row else None


def _parse(at):
    if isinstance(at, datetime):
        t = at
    else:
        t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _meta_value(meta, k):
    v = meta.get(k)
    return int(bool(v)) if k == "recurring" else v


def _patch_enabled():
    """Live reschedule patching waits on the owner's guarded-UPDATE fix (design §5, §7 step 6)."""
    return (os.environ.get("POSTCALL_RESCHEDULE_PATCH") or "").strip().lower() in ("1", "true", "on", "yes")


def _call_title(c, card):
    for (item_json,) in c.execute("SELECT item_json FROM card_items WHERE card_id=? ORDER BY q_n", (card["effect_id"],)):
        title = (json.loads(item_json) or {}).get("call_title") if item_json else None
        if title:
            return title
    return json.loads(card["payload_json"]).get("title") or card["effect_id"]
