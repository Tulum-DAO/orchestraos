"""questionnaires + questionnaire_questions + questionnaire_answers tables and
QuestionnaireStore — the CONTAINER of N grouped decisions (native questionnaire
system spec 2026-08-13 REV-2, phase a).

Lives in the SAME tasks.db file as ApprovalStore but in DEDICATED tables (spec
§2.1, Option B): the questionnaire has its own lifecycle — mutable draft ->
atomic submit-once -> R3 delivery — so `approval_requests`' immutable
answer-once semantics stay untouched. No `expires_at` (R8): a questionnaire ends
submitted/resumed, cancelled (emitter), or discarded (the operator) — never auto-expires.
"""
import sqlite3, json, os, random
from datetime import datetime, timezone
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from approval_config import DB_PATH


def _now():
    return datetime.now(tz=timezone.utc).isoformat()


def _gen_id():
    return (f"qnr_{random.randrange(16**8):08x}_"
            f"{int(datetime.now(tz=timezone.utc).timestamp()*1000)%10**8}")


def _answered(a):
    """A question is answered iff it holds a chosen option OR non-blank text
    (R7: text alone is a valid free-form answer)."""
    if a is None:
        return False
    opt = a["option_n"] if isinstance(a, sqlite3.Row) else a.get("option_n")
    txt = a["answer_text"] if isinstance(a, sqlite3.Row) else a.get("answer_text")
    return bool((opt is not None and str(opt) != "")
                or (txt is not None and str(txt).strip() != ""))


DDL = """
CREATE TABLE IF NOT EXISTS questionnaires (
  id               TEXT PRIMARY KEY,
  from_agent       TEXT NOT NULL,
  title            TEXT NOT NULL,
  summary          TEXT,
  status           TEXT NOT NULL DEFAULT 'pending',
  worker_kind      TEXT NOT NULL DEFAULT 'pane',
  thread_key       TEXT,
  op_key           TEXT,
  feature          TEXT,
  question_count   INTEGER NOT NULL,
  draft_rev        INTEGER NOT NULL DEFAULT 0,
  draft_updated_at TEXT,
  draft_surface    TEXT,
  created_at       TEXT NOT NULL,
  submitted_at     TEXT,
  resumed_at       TEXT,
  resume_acked_at  TEXT,
  resume_attempts  INTEGER NOT NULL DEFAULT 0,
  resume_error     TEXT,
  urgency          INTEGER NOT NULL DEFAULT 0,
  discarded_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_qnr_status ON questionnaires(status);
CREATE INDEX IF NOT EXISTS idx_qnr_dedup  ON questionnaires(from_agent, op_key, status);

CREATE TABLE IF NOT EXISTS questionnaire_questions (
  qnr_id   TEXT NOT NULL,
  n        INTEGER NOT NULL,
  prompt   TEXT NOT NULL,
  kind     TEXT NOT NULL,          -- 'menu' | 'free_text'
  menu     TEXT,                   -- kind='menu': {options:[{n,label,detail?,input_kind}], selected_n?}
  required INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (qnr_id, n)
);

CREATE TABLE IF NOT EXISTS questionnaire_answers (
  qnr_id      TEXT NOT NULL,
  n           INTEGER NOT NULL,
  option_n    TEXT,
  answer_text TEXT,
  state       TEXT NOT NULL DEFAULT 'draft',   -- 'draft' (mutable) | 'final' (frozen at submit)
  updated_at  TEXT NOT NULL,
  surface     TEXT,
  PRIMARY KEY (qnr_id, n)
);
"""


def _coerce_menu_n_str(menu):
    """Serve/store menu option `n` (and selected_n) as STRINGS — the client
    contract (MenuData.Option.n:String) and the decision-card behavior
    (approval.py:293, watch_gateway.py:115/1734). The questionnaire path was the
    outlier: approval.py questionnaire stored int n -> Swift JSONDecoder threw ->
    "Could not load this questionnaire" on phone+watch, AND _validate_qnr_answer
    (str == int) refused every draft/submit (incident 2026-09-10, qnr_85a63e43 +
    qnr_132ea298). Coercing at BOTH boundaries heals live int rows with no
    migration. Non-dict shapes pass through untouched."""
    if not isinstance(menu, dict):
        return menu
    opts = menu.get("options")
    if isinstance(opts, list):
        for idx, o in enumerate(opts):
            if isinstance(o, dict):
                if o.get("n") is None:
                    o["n"] = str(idx + 1)
                elif not isinstance(o["n"], str):
                    o["n"] = str(o["n"])
    if menu.get("selected_n") is not None and not isinstance(menu["selected_n"], str):
        menu["selected_n"] = str(menu["selected_n"])
    return menu


class QuestionnaireStore:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or DB_PATH)

    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def migrate(self):
        c = self._conn()
        try:
            c.executescript(DDL)
            # Additive columns (same discipline as ApprovalStore.migrate).
            existing = {r[1] for r in c.execute("PRAGMA table_info(questionnaires)").fetchall()}
            if "notified_at" not in existing:   # ntfy push stamp (spec §5)
                c.execute("ALTER TABLE questionnaires ADD COLUMN notified_at TEXT")
            for col in ("last_attempt_at", "escalated_at"):   # SLA spec 2026-08-14
                if col not in existing:
                    c.execute(f"ALTER TABLE questionnaires ADD COLUMN {col} TEXT")
            if "real_attempts" not in existing:   # G3 answer-resume spec 2026-08-18:
                # resume_attempts becomes the EVERY-attempt counter (busy/no-head
                # refusals included — zero-attempts-while-pending impossible by
                # construction); real_attempts carries the old semantics so
                # WATCHDOG_MAX_ATTEMPTS keeps gating on REAL failed injects,
                # never on busy minutes (3 busy beats must not read as
                # "delivered 3x with no ack").
                c.execute("ALTER TABLE questionnaires ADD COLUMN real_attempts "
                          "INTEGER NOT NULL DEFAULT 0")
                # Backfill: historically every counted attempt WAS a real one.
                c.execute("UPDATE questionnaires SET real_attempts=resume_attempts")
            c.commit()
        finally:
            c.close()

    def set_notified(self, qid):
        c = self._conn()
        try:
            c.execute("UPDATE questionnaires SET notified_at=? WHERE id=?", [_now(), qid])
            c.commit()
        finally:
            c.close()

    # ---- emission ----
    def create(self, from_agent, title, questions, summary=None, thread_key=None,
               op_key=None, feature=None, worker_kind="pane", urgency=0):
        """`questions` = ordered list of dicts:
        {prompt, kind:'menu'|'free_text', menu?:{options:[...],selected_n?},
        required?|optional?} — `optional: true` == `required: 0` (synonym; the operator
        field bug 2026-08-16: emitters wrote "(optional)" in prompt text only).
        Questions are IMMUTABLE after emission (re-emit a new questionnaire to change).
        Dedups on (from_agent, op_key, pending), same contract as approvals."""
        c = self._conn()
        try:
            if op_key:
                row = c.execute(
                    "SELECT id FROM questionnaires WHERE from_agent=? AND op_key=? "
                    "AND status='pending' LIMIT 1", [from_agent, op_key]).fetchone()
                if row:
                    return row["id"]
            qid = _gen_id(); now = _now()
            c.execute(
                """INSERT INTO questionnaires
                   (id, from_agent, title, summary, status, worker_kind, thread_key,
                    op_key, feature, question_count, created_at, urgency)
                   VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?)""",
                [qid, from_agent, title, summary, worker_kind, thread_key, op_key,
                 feature, len(questions), now, int(urgency or 0)])
            for i, q in enumerate(questions, start=1):
                menu = q.get("menu")
                if isinstance(menu, dict):
                    menu = _coerce_menu_n_str(menu)   # no new int-n rows
                if isinstance(menu, (dict, list)):
                    menu = json.dumps(menu)
                c.execute(
                    "INSERT INTO questionnaire_questions (qnr_id, n, prompt, kind, menu, required) "
                    "VALUES (?,?,?,?,?,?)",
                    [qid, i, q.get("prompt", ""), q.get("kind", "free_text"), menu,
                     int(q.get("required", 0 if q.get("optional") else 1))])
            c.commit()
            return qid
        finally:
            c.close()

    # ---- read ----
    def get(self, qid):
        """Full container: questionnaire fields + ordered questions (menus parsed) +
        each question's current draft answer + answered_count. THE resume payload."""
        c = self._conn()
        try:
            q = c.execute("SELECT * FROM questionnaires WHERE id=?", [qid]).fetchone()
            if not q:
                return None
            d = dict(q)
            qrows = c.execute(
                "SELECT * FROM questionnaire_questions WHERE qnr_id=? ORDER BY n", [qid]).fetchall()
            arows = c.execute(
                "SELECT * FROM questionnaire_answers WHERE qnr_id=?", [qid]).fetchall()
            ans = {a["n"]: a for a in arows}
            d["questions"] = []
            for row in qrows:
                qd = dict(row)
                if qd.get("menu"):
                    try:
                        qd["menu"] = _coerce_menu_n_str(json.loads(qd["menu"]))
                    except (ValueError, TypeError):
                        pass
                a = ans.get(qd["n"])
                qd["answer"] = ({"option_n": a["option_n"], "answer_text": a["answer_text"],
                                 "state": a["state"]} if a else None)
                d["questions"].append(qd)
            d["answered_count"] = sum(1 for a in ans.values() if _answered(a))
            return d
        finally:
            c.close()

    def list_pending(self):
        """Pending containers with answered_count (draft coverage). Priority sort is
        applied at the gateway (shared with approvals); here just created_at asc."""
        c = self._conn()
        try:
            out = []
            for q in c.execute(
                    "SELECT * FROM questionnaires WHERE status='pending' "
                    "ORDER BY created_at ASC").fetchall():
                d = dict(q)
                arows = c.execute(
                    "SELECT option_n, answer_text FROM questionnaire_answers WHERE qnr_id=?",
                    [q["id"]]).fetchall()
                d["answered_count"] = sum(1 for a in arows if _answered(a))
                out.append(d)
            return out
        finally:
            c.close()

    # ---- draft (mutable, per-question merge) ----
    def save_draft(self, qid, answers, base_rev=None, surface=None):
        """`answers` = {n: {option_n?, answer_text?}} — per-question UPSERT MERGE:
        only the keys sent for a question are written (two surfaces editing
        different questions never clobber each other). Returns
        {draft_rev, conflicts:[n]} or {error} / None. base_rev stale -> flags the
        written questions as conflicts (informational; last-writer-wins)."""
        c = self._conn()
        try:
            q = c.execute("SELECT status, draft_rev FROM questionnaires WHERE id=?",
                          [qid]).fetchone()
            if not q:
                return None
            if q["status"] != "pending":
                return {"error": "questionnaire not pending", "status": q["status"]}
            stale = base_rev is not None and int(base_rev) < q["draft_rev"]
            conflicts, now = [], _now()
            for n, a in (answers or {}).items():
                n = int(n)
                cur = c.execute(
                    "SELECT option_n, answer_text FROM questionnaire_answers WHERE qnr_id=? AND n=?",
                    [qid, n]).fetchone()
                opt = a["option_n"] if "option_n" in a else (cur["option_n"] if cur else None)
                txt = a["answer_text"] if "answer_text" in a else (cur["answer_text"] if cur else None)
                if cur:
                    c.execute(
                        "UPDATE questionnaire_answers SET option_n=?, answer_text=?, "
                        "state='draft', updated_at=?, surface=? WHERE qnr_id=? AND n=?",
                        [opt, txt, now, surface, qid, n])
                else:
                    c.execute(
                        "INSERT INTO questionnaire_answers (qnr_id, n, option_n, answer_text, "
                        "state, updated_at, surface) VALUES (?,?,?,?, 'draft', ?, ?)",
                        [qid, n, opt, txt, now, surface])
                if stale:
                    conflicts.append(n)
            new_rev = q["draft_rev"] + 1
            c.execute(
                "UPDATE questionnaires SET draft_rev=?, draft_updated_at=?, draft_surface=? WHERE id=?",
                [new_rev, now, surface, qid])
            c.commit()
            return {"draft_rev": new_rev, "conflicts": conflicts}
        finally:
            c.close()

    # ---- submit (all-or-nothing, once) ----
    def submit(self, qid, answers=None, surface=None):
        """Merge the final delta into the draft, then require EVERY question to hold
        a valid answer -> atomic freeze (answers->final) + guarded flip
        (pending->submitted, once). Returns {applied:True} | {applied:False, missing}
        | {applied:False, status} | None."""
        c = self._conn()
        try:
            q = c.execute("SELECT status, question_count FROM questionnaires WHERE id=?",
                          [qid]).fetchone()
            if not q:
                return None
            if q["status"] != "pending":
                return {"applied": False, "status": q["status"]}
            now = _now()
            for n, a in (answers or {}).items():
                n = int(n)
                cur = c.execute(
                    "SELECT option_n, answer_text FROM questionnaire_answers WHERE qnr_id=? AND n=?",
                    [qid, n]).fetchone()
                opt = a["option_n"] if "option_n" in a else (cur["option_n"] if cur else None)
                txt = a["answer_text"] if "answer_text" in a else (cur["answer_text"] if cur else None)
                if cur:
                    c.execute(
                        "UPDATE questionnaire_answers SET option_n=?, answer_text=?, "
                        "state='draft', updated_at=?, surface=? WHERE qnr_id=? AND n=?",
                        [opt, txt, now, surface, qid, n])
                else:
                    c.execute(
                        "INSERT INTO questionnaire_answers (qnr_id, n, option_n, answer_text, "
                        "state, updated_at, surface) VALUES (?,?,?,?, 'draft', ?, ?)",
                        [qid, n, opt, txt, now, surface])
            have = {r["n"]: r for r in c.execute(
                "SELECT n, option_n, answer_text FROM questionnaire_answers WHERE qnr_id=?",
                [qid]).fetchall()}
            # Optional questions (required=0 — stored by create() since day one
            # but previously IGNORED here) never block submission. the operator field
            # bug 2026-08-16: an optional free-text question wedged the watch
            # submit. Absent row -> required (fail-safe = old behavior).
            req = {r["n"]: r["required"] for r in c.execute(
                "SELECT n, required FROM questionnaire_questions WHERE qnr_id=?",
                [qid]).fetchall()}
            missing = [n for n in range(1, q["question_count"] + 1)
                       if req.get(n, 1) and not _answered(have.get(n))]
            if missing:
                c.commit()   # the delta persists as draft; caller jumps to first missing
                return {"applied": False, "missing": missing}
            res = c.execute(
                "UPDATE questionnaires SET status='submitted', submitted_at=? "
                "WHERE id=? AND status='pending'", [now, qid])
            if res.rowcount == 0:      # lost the submit race
                c.commit()
                return {"applied": False}
            c.execute("UPDATE questionnaire_answers SET state='final' WHERE qnr_id=?", [qid])
            c.commit()
            return {"applied": True}
        finally:
            c.close()

    # ---- terminal transitions ----
    def discard(self, qid):
        """R8: the operator dismisses without answering. Guarded pending->discarded (once)."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE questionnaires SET status='discarded', discarded_at=? "
                "WHERE id=? AND status='pending'", [_now(), qid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()

    def cancel(self, qid):
        """Emitter cancels / dies. Guarded pending->cancelled (once)."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE questionnaires SET status='cancelled' "
                "WHERE id=? AND status='pending'", [qid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()

    # ---- R3 delivery bookkeeping (mirrors approvals) ----
    def mark_resumed_fired(self, qid, error=None):
        """REAL delivery attempt (SLA spec 2026-08-14): counts toward escalation."""
        c = self._conn()
        try:
            c.execute(
                "UPDATE questionnaires SET resumed_at=?, last_attempt_at=?, "
                "resume_attempts=resume_attempts+1, real_attempts=real_attempts+1, "
                "resume_error=? WHERE id=?",
                [_now(), _now(), error, qid])
            c.commit()
        finally:
            c.close()

    def stamp_retry(self, qid, reason=None):
        """CHEAP refusal (busy / no-live-head). G3 spec 2026-08-18: the attempt is
        COUNTED (resume_attempts+1) so a pending row is never indistinguishable
        from a dead one — qnr_3c914a10 sat 4m19s at attempts=0 while gm was
        mid-turn and gm learned of the operator's answers only by store-polling. It still
        does NOT count toward real_attempts (escalation gates on real failures).
        `reason` (incident qnr_d49f5401): the refusal detail persists in
        resume_error so a store read alone explains a stalled delivery."""
        c = self._conn()
        try:
            if reason is not None:
                c.execute("UPDATE questionnaires SET last_attempt_at=?, "
                          "resume_attempts=resume_attempts+1, resume_error=? "
                          "WHERE id=?", [_now(), reason, qid])
            else:
                c.execute("UPDATE questionnaires SET last_attempt_at=?, "
                          "resume_attempts=resume_attempts+1 WHERE id=?",
                          [_now(), qid])
            c.commit()
        finally:
            c.close()

    def claim_delivery(self, qid):
        """Once-only CAS (G3 spec): a delivery claim is atomic with the row still
        being submitted-unacked. A manual qack that consumed the answers first
        makes this return False — the late inject NEVER fires (tonight's
        triple-path double-delivery fixture)."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE questionnaires SET last_attempt_at=last_attempt_at "
                "WHERE id=? AND status='submitted' AND resume_acked_at IS NULL",
                [qid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()

    def mark_escalated(self, qid):
        """Escalation fired: throttle re-escalation (no attempt increment)."""
        c = self._conn()
        try:
            c.execute("UPDATE questionnaires SET escalated_at=?, last_attempt_at=? WHERE id=?",
                      [_now(), _now(), qid])
            c.commit()
        finally:
            c.close()

    def ack(self, qid):
        """Emitter ran `qack` — flip submitted->resumed (delivery confirmed)."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE questionnaires SET resume_acked_at=?, status='resumed' "
                "WHERE id=? AND status='submitted'", [_now(), qid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()

    def history(self, limit=50):
        """R8 everything-to-history: containers that reached a the operator-visible end
        (submitted/resumed AND discarded), newest-ended-first."""
        c = self._conn()
        try:
            out = []
            for q in c.execute(
                    "SELECT * FROM questionnaires "
                    "WHERE status IN ('submitted','resumed','discarded') "
                    "ORDER BY COALESCE(submitted_at, discarded_at) DESC LIMIT ?",
                    [int(limit)]).fetchall():
                d = dict(q)
                arows = c.execute(
                    "SELECT option_n, answer_text FROM questionnaire_answers WHERE qnr_id=?",
                    [q["id"]]).fetchall()
                d["answered_count"] = sum(1 for a in arows if _answered(a))
                out.append(d)
            return out
        finally:
            c.close()

    def submitted_unacked(self):
        c = self._conn()
        try:
            return [dict(r) for r in c.execute(
                "SELECT * FROM questionnaires WHERE status='submitted' "
                "AND resume_acked_at IS NULL").fetchall()]
        finally:
            c.close()
