"""approval_requests table + ApprovalStore. The durable, transport-agnostic ledger."""
import sqlite3, json, os, random
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# NOT `from approval_config import DB_PATH`: that binds once at THIS module's import and
# re-freezes the very path we just made lazy. The module is imported and the value read
# per call instead.
import approval_config
from approval_config import DEFAULT_OPTIONS, EXPIRY_HOURS

def _now(): return datetime.now(tz=timezone.utc).isoformat()


def _expire_pending_enabled():
    """the operator's Q4 gate, read at CALL time (env-overridable, test-injectable)."""
    from approval_config import EXPIRE_PENDING
    return bool(EXPIRE_PENDING)

# Re-card guard window (P0, v3 msg_667fb1b8): a same-op_key row created within
# this window blocks a NEW card regardless of status — a persisting pane menu
# is the same ask even after its row went terminal (the false
# resolved_elsewhere cascade re-carded the operator's phone 60s after his tap).
RECARD_GUARD_S = 900
def _gen_id(): return f"apr_{random.randrange(16**8):08x}_{int(datetime.now(tz=timezone.utc).timestamp()*1000)%10**8}"

DDL = """
CREATE TABLE IF NOT EXISTS approval_requests (
  id              TEXT PRIMARY KEY,
  from_agent      TEXT NOT NULL,
  question        TEXT NOT NULL,
  op_key          TEXT,
  options         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  answer          TEXT,
  answer_text     TEXT,
  thread_key      TEXT,
  worker_kind     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  answered_at     TEXT,
  resumed_at      TEXT,
  resume_acked_at TEXT,
  resume_attempts INTEGER NOT NULL DEFAULT 0,
  expires_at      TEXT NOT NULL,
  notified_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_appr_status ON approval_requests(status);
CREATE INDEX IF NOT EXISTS idx_appr_dedup  ON approval_requests(from_agent, op_key, status);
"""

# --- Default-OFF arming gate for FUTURE migrate() DDL (DESIGN_ddl-arming-gate.md) ---
# The R2 lesson: migrate() runs unconditionally on init, so ANY schema DDL that
# reaches the live-checkout branch auto-fires ALTER TABLE on live tasks.db on the
# next consumer restart. The legacy batches above are ALREADY LIVE and stay ungated
# forever (gating them adds noise, zero safety). Every FUTURE column batch is
# appended HERE with a unique migration id — never inline in the legacy DDL — and
# does NOT touch the live schema until the operator arms it. EMPTY at install => the guard's
# own merge is a DDL no-op (byte-equivalent live behavior).
#   {"id": "m20260901_example", "columns": [("new_col", "TEXT"), ...]}
GATED_MIGRATIONS: list = [
    # R7d answer-surface attribution (SPEC_r7-answer-path-package.md §R7d +
    # proposal answer-surface-attribution-spec.md, delta 1: rides the arming
    # gate, NOT migrate-on-restart). Two additive-nullable columns; legacy rows
    # carry NULL after the ALTER. Stamped at the ONE core (record_answer);
    # provenance-only, NEVER branched on for authorization.
    #   answer_surface  web|phone|watch|gateway|agent_cli|unknown — where answered
    #   answered_by     the principal: 'operator' | '<agent_id>' (self-authored gates)
    {"id": "m20260825_answer_attribution",
     "columns": [("answer_surface", "TEXT"), ("answered_by", "TEXT")]},
    # "Waiting on you" human-blocker surface (SERVER spec 2026-08-25 §1.1/§5).
    # Three additive-nullable columns for kind='human_task' rows. UNARMED at
    # merge => byte-identical live schema (the R2 lesson); arming is a SEPARATE
    # live the operator DDL gate (APPROVAL_DDL_ARMED=m20260825_human_task). A legacy row
    # (all NULL) behaves exactly as today.
    #   block_task     what the operator must do (agent-authored markdown)
    #   blocks_what    optional label OR a single apr_/qnr_ id to deep-link/couple
    #   snoozed_until  ISO snooze marker + 2h re-notify cadence field (§2)
    {"id": "m20260825_human_task",
     "columns": [("block_task", "TEXT"), ("blocks_what", "TEXT"),
                 ("snoozed_until", "TEXT")]},
    # Device provenance for device-scoped gateway tokens (gm ruling 2026-10-05 (d)).
    # Pairing now mints a PER-DEVICE bearer instead of handing over the fleet token, so
    # "the operator answered this" is no longer the whole truth: WHICH of his devices
    # answered is now a real question, and a revoked device's past answers must stay
    # attributable. One additive-nullable column, same shape and same reasoning as the
    # R7d pair above, and like them it is provenance-only and NEVER branched on for
    # authorization. UNARMED at merge => byte-identical live schema (the R2 lesson).
    #   answer_device   "<device_id>:<label>" for a device token; NULL for the fleet
    #                   bearer and for every legacy row
    {"id": "m20261005_answer_device",
     "columns": [("answer_device", "TEXT")]},
    # Card RETIRE verb (operator ruling 2026-10-10; gm msg_3f3a4d05). Additive, nullable, empty
    # for every existing card. The SAME arm also adds them to questionnaires
    # (QuestionnaireStore.migrate). Unarmed, retire() refuses rather than half-writing.
    #   retired_at     when an author/gm marked the pending card no longer needed
    #   retired_by     who (the author, its current generation, or gm)
    #   retire_reason  why, in plain words (required)
    #   superseded_by  optional apr_/qnr_ id of the card that replaced it
    {"id": "m20261010_card_retire",
     "columns": [("retired_at", "TEXT"), ("retired_by", "TEXT"),
                 ("retire_reason", "TEXT"), ("superseded_by", "TEXT")]},
    # Attention part 1 (2026-11-04). The SAME arm also creates the attention_* tables
    # (attention_store.AttentionStore.migrate). Additive, nullable; a legacy row is NULL.
    #   urgency         0-3, an agent's own priority input (approval.py --urgency); _priority_score
    #                   already reads it, so a row that sets it sorts higher. No row sets it today.
    #   default_option  the asking agent's suggested default (approval.py --default): an option n for
    #                   a menu card, approve|deny for a plain approval. A timeout rule's "take the
    #                   default" uses it; a card without one expires as "no answer".
    {"id": "m20261104_attention",
     "columns": [("urgency", "INTEGER"), ("default_option", "TEXT")]},
]

RETIRE_MIGRATION = "m20261010_card_retire"
RETIRE_COLS = ("retired_at", "retired_by", "retire_reason", "superseded_by")


def _card_provenance():
    """card_provenance, imported on first use (it lives beside this file; a caller that imported this
    module by path may not have the directory on sys.path)."""
    try:
        import card_provenance
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import card_provenance
    return card_provenance


def _cols(c, table):
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def retire_card(c, table, rid, caller, reason, superseded_by, reg, now_iso=None, read_pane=None,
                has_session=None):
    """THE one RETIRE transition, shared by ApprovalStore and QuestionnaireStore (same tasks.db).
    pending -> 'retired' with who/why/what-replaced-it; the row stays and goes to history. A
    kind='menu' row only when its menu is GONE (see menu_gone): a live menu is menu-bridge's to
    resolve from the pane. A safety-gate card (permission card / grant proposal) is never retired:
    only a person answers it. Sends nothing and resumes nothing: it is not an answer.
    Returns (ok, reason); a refusal always says why."""
    reason = (reason or "").strip()
    if not reason:
        return False, "a reason is required: say in plain words why the card is no longer needed"
    if not set(RETIRE_COLS) <= _cols(c, table):
        return False, (f"DDL not armed: {RETIRE_MIGRATION} (the retire columns do not exist yet; "
                       f"the operator arms via ~/runtime/APPROVAL_DDL_ARMED_{RETIRE_MIGRATION})")
    text_col = "question" if table == "approval_requests" else "title"
    kind_sql = "COALESCE(kind,'')" if table == "approval_requests" else "''"
    extra = ", origin, op_key, created_at, menu" if table == "approval_requests" else ""
    row = c.execute(f"SELECT from_agent, status, {kind_sql} AS kind, summary, {text_col} AS text{extra} "
                    f"FROM {table} WHERE id=?", [rid]).fetchone()
    if not row:
        return False, f"no card {rid}"
    if row["status"] != "pending":
        return False, f"{rid} is {row['status']}, not pending: only a pending card can be retired"
    import seat_identity
    ok, why = seat_identity.may_retire(reg, caller, row["from_agent"])
    if not ok:
        return False, why
    menu_note = ""
    if row["kind"] == "menu":
        if is_gate_row(dict(row)):
            return False, f"{rid} is a safety-gate card: only a person answers it, it is never retired"
        gone, basis = menu_gone(row, reg, read_pane, has_session)
        if not gone:
            return False, (f"{rid} is kind='menu' and its menu is still live ({basis}): menu-bridge "
                           f"owns it and resolves it from the pane")
        menu_note = f" [menu gone, pane retired: {basis}]"
    if superseded_by:
        if superseded_by == rid:
            return False, "a card cannot supersede itself"
        found = c.execute("SELECT 1 FROM approval_requests WHERE id=?", [superseded_by]).fetchone()
        if not found and c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                   "AND name='questionnaires'").fetchone():
            found = c.execute("SELECT 1 FROM questionnaires WHERE id=?", [superseded_by]).fetchone()
        if not found:
            return False, f"--superseded-by {superseded_by}: no such card"
    stamp = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"\n\n— RETIRED {stamp} by {caller}: {reason}" + (
        f" (superseded by {superseded_by})" if superseded_by else "") + menu_note
    res = c.execute(
        f"UPDATE {table} SET status='retired', retired_at=?, retired_by=?, retire_reason=?, "
        f"superseded_by=?, summary=COALESCE(summary, {text_col}, '') || ? "
        f"WHERE id=? AND status='pending' AND {kind_sql}=?",
        [stamp, caller, reason, superseded_by or None, line, rid, row["kind"]])
    c.commit()
    if res.rowcount != 1:
        now = c.execute(f"SELECT status FROM {table} WHERE id=?", [rid]).fetchone()
        return False, f"{rid} changed under us (now {now['status'] if now else 'gone'}): not retired"
    return True, None


def reopen_card(c, rid, caller, reason, reg, now_iso=None):
    """THE one RE-OPEN transition: undo a retire done IN ERROR. retired -> pending on
    approval_requests, the retire columns cleared, one history line appended
    ("— RE-OPENED <stamp> by <caller>: <reason>"); the RETIRED line above it stays, so the history
    shows both. gm only (bare or current generation). Sends nothing and resumes nothing: notified_at
    is untouched, so no notifier fires again. Returns (ok, reason)."""
    reason = (reason or "").strip()
    if not reason:
        return False, "a reason is required: say why the retire was wrong"
    import seat_identity
    try:
        ok = seat_identity._is_current(reg, caller, seat_identity.STEWARD)
    except sqlite3.Error:
        ok = False
    if not ok:
        return False, f"only gm may re-open a retired card ({caller} is not gm)"
    if not set(RETIRE_COLS) <= _cols(c, "approval_requests"):
        return False, f"DDL not armed: {RETIRE_MIGRATION}"
    row = c.execute("SELECT status FROM approval_requests WHERE id=?", [rid]).fetchone()
    if not row:
        return False, f"no card {rid}"
    if row["status"] != "retired":
        return False, f"{rid} is {row['status']}, not retired: only a retired card can be re-opened"
    stamp = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"\n\n— RE-OPENED {stamp} by {caller}: {reason}"
    res = c.execute(
        "UPDATE approval_requests SET status='pending', retired_at=NULL, retired_by=NULL, retire_reason=NULL, "
        "superseded_by=NULL, summary=COALESCE(summary, question, '') || ? WHERE id=? AND status='retired'",
        [line, rid])
    c.commit()
    if res.rowcount != 1:
        return False, f"{rid} changed under us: not re-opened"
    return True, None


def _iso(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def menu_gone(row, reg, read_pane=None, has_session=None):
    """(gone, basis) for a PENDING kind='menu' card. Gone only on POSITIVE evidence; anything
    unreadable counts as live.
      - DEAD SEAT, either row type, when the generation check cannot prove it: no canonical row for
        the root (or canonical status 'retired' or 'parked') AND has_session says no tmux session
        exists under the author, the root, any `<root>-gN` or the canonical session name. A menu is
        pane state of a process: with no pane anywhere it is gone, even for a parked seat (a resumed
        seat boots without it). An online or provisional seat still refuses. Without has_session
        this rule is off.
      - BRIDGE row (origin='menu_bridge', op_key 'menu:<session>:...'): the owner pane is READ
        (capture only). Session not in tmux -> gone. A menu on screen, or a failed read -> live.
        Pane up with no menu -> gone only if the generation that posed it has retired.
      - CLI-minted row: there is no pane menu, so the question is the posing generation alone:
        a named author generation (`<root>-gN`) with retired_at set, or a bare-root author whose
        CURRENT canonical generation was promoted after the card was created."""
    op_key = row["op_key"] or ""
    if row["origin"] == "menu_bridge" and op_key.startswith("menu:") and op_key.count(":") >= 2:
        session = op_key.split(":", 2)[1]
        seen = read_pane(session) if read_pane else "error:no pane reader"
        if seen == "no-session":
            return True, f"{session} not in tmux"
        if seen != "none":
            return False, ("menu still on screen in " + session) if seen == "menu" else \
                f"could not read {session}'s pane ({seen})"
        gone, basis = _posing_generation_retired(reg, row["from_agent"], row["created_at"], has_session)
        return gone, (f"no menu on screen in {session}; {basis}" if gone else basis)
    return _posing_generation_retired(reg, row["from_agent"], row["created_at"], has_session)


def _posing_generation_retired(reg, author, created_at, has_session=None, menu=True):
    """(gone, basis) from the registry alone. Called for kind='menu' cards only: a rotation ends a
    menu (pane state), but it never answers an approval or a human task."""
    import seat_identity
    c = seat_identity._conn(reg)
    if c is None:
        return False, "no registry to check the posing generation"
    try:
        root, gen = seat_identity.resolve(reg, author or "")
        if gen is not None:
            r = c.execute("SELECT retired_at FROM generations WHERE root=? AND generation=?",
                          (root, gen)).fetchone()
            if r and r[0]:
                return True, f"{author} retired {r[0]}"
            return False, f"{author} is not retired"
        r = c.execute("SELECT g.promoted_at FROM canonical k JOIN generations g ON g.id = k.generation_id "
                      "WHERE k.root=?", (root,)).fetchone()
        m = seat_identity._GEN.match(author or "")     # an unregistered `<root>-gN` still names its root
        dead_root = m["root"] if m else root
        canon = c.execute("SELECT tmux_session, status FROM canonical WHERE root=?", (dead_root,)).fetchone()
        gens = [n for (n,) in c.execute("SELECT generation FROM generations WHERE root=?", (dead_root,))]
    except sqlite3.Error as e:
        return False, f"registry unreadable ({type(e).__name__})"
    finally:
        c.close()
    promoted, made = _iso(r[0]) if r else None, _iso(created_at)
    if promoted and made and made < promoted:
        return True, f"posed {created_at} by an earlier {root} generation (current promoted {r[0]})"
    dead, why = _seat_is_dead(author, dead_root, canon, gens, has_session,
                              gone_states=("retired", "parked") if menu else ("retired",))
    if dead:
        return True, why
    return False, f"posed by {root}'s current generation (or unprovable)"


def _seat_is_dead(author, root, canon, gens, has_session, gone_states=("retired",)):
    """(dead, basis): no canonical row (or canonical status in gone_states) AND no tmux session under
    any name the seat could have had. has_session(name) -> True | False | None (None = could not
    tell = live)."""
    if has_session is None:
        return False, ""
    if canon and canon[1] not in gone_states:
        return False, ""
    names = {author, root, *(f"{root}-g{n}" for n in gens)}
    if canon and canon[0]:
        names.add(canon[0])
    for name in sorted(n for n in names if n):
        if has_session(name) is not False:
            return False, ""
    state = "no canonical row" if not canon else f"canonical {canon[1]}"
    return True, f"posing seat {root} is gone: {state}, no tmux session ({', '.join(sorted(n for n in names if n))})"


def close_menu_card(c, rid, caller, reason, reg, read_pane, now_iso=None):
    """THE one CLOSE-MENU transition. A PENDING kind='menu' row whose question was answered another
    way -> status 'resolved_elsewhere' (the status the menu reconciler already writes; every client
    tolerates it) with who/why appended to the summary. Only the author, its current generation,
    or gm (seat_identity.may_retire). A safety-gate card is refused: only a person answers it.
    Two kinds of evidence, never mixed:
      - BRIDGE row (origin='menu_bridge', op_key 'menu:<session>:...'): the owner pane is READ
        (read_pane(session) -> 'menu' | 'none' | 'no-session' | 'error'); a menu still on
        screen, or a failed read, refuses. Note: "answered in pane".
      - CLI-minted row (no bridge origin): there is no pane menu, so a blank screen proves
        nothing; the pane is NOT read and a --reason stating the actual basis is required.
    Never sends keys or injects: read_pane only captures. Sends nothing, resumes nothing.
    Returns (ok, reason, note)."""
    reason = (reason or "").strip()
    row = c.execute("SELECT from_agent, status, COALESCE(kind,'') AS kind, origin, op_key, menu "
                    "FROM approval_requests WHERE id=?", [rid]).fetchone()
    if not row:
        return False, f"no card {rid}", None
    if row["kind"] != "menu":
        return False, f"{rid} is kind='{row['kind']}', not a menu card: use retire", None
    if row["status"] != "pending":
        return False, f"{rid} is {row['status']}, not pending", None
    if is_gate_row(dict(row)):
        return False, f"{rid} is a safety-gate card: only a person answers it, nothing closed", None
    import seat_identity
    ok, why = seat_identity.may_retire(reg, caller, row["from_agent"])
    if not ok:
        return False, why, None
    stamp = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    op_key = row["op_key"] or ""
    bridge = row["origin"] == "menu_bridge" and op_key.startswith("menu:") and op_key.count(":") >= 2
    if bridge:
        session = op_key.split(":", 2)[1]
        seen = read_pane(session)
        if seen == "menu":
            return False, f"a menu is still on screen in {session}: answer it there, nothing closed", None
        if seen not in ("none", "no-session"):
            return False, f"could not read {session}'s pane ({seen}): nothing closed", None
        basis = ("no menu on screen" if seen == "none" else f"{session} not in tmux")
        note = f"answered in pane ({basis}, checked {stamp})" + (f": {reason}" if reason else "")
    else:
        if not reason:
            return False, ("a CLI-minted menu card has no pane menu to check: --reason must state "
                           "the actual basis (e.g. 'the operator ruled X in session')"), None
        note = f"closed: {reason} (CLI-minted menu card; no pane read)"
    line = f"\n\n— CLOSED {stamp} by {caller}: {note}"
    res = c.execute(
        "UPDATE approval_requests SET status='resolved_elsewhere', "
        "summary=COALESCE(summary, question, '') || ? "
        "WHERE id=? AND status='pending' AND kind='menu'", [line, rid])
    if res.rowcount != 1:
        c.rollback()
        return False, f"{rid} changed under us: not closed", None
    for t in ("menu_bridge_absence", "menu_bridge_capheld"):
        if op_key and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [t]).fetchone():
            c.execute(f"DELETE FROM {t} WHERE op_key=?", [op_key])
    c.commit()
    return True, None, note


def export_retired(row):
    """Client export of a retired card: the REAL status 'retired' plus a 'retired' object with
    exactly {by, reason, superseded_by, at}; answered_at reads as when it ended. All three clients
    (iOS/watch, Mac, web) confirmed they tolerate an unknown status, so gm ruled the flip from the
    interim 'discarded' (msg_6003469a). Every other row is untouched."""
    if row.get("status") != "retired":
        return row
    row["retired"] = {"by": row.get("retired_by"), "reason": row.get("retire_reason"),
                      "superseded_by": row.get("superseded_by"), "at": row.get("retired_at")}
    row["answered_at"] = row.get("answered_at") or row.get("retired_at")
    return row


def armed(migration_id):
    """True iff this migration is armed to apply (default OFF — the operator's gate).
      env  APPROVAL_DDL_ARMED == 'all' or contains migration_id (comma-list)
      OR   sentinel ~/runtime/APPROVAL_DDL_ARMED_<migration_id> exists
    Fail-safe: ANY read failure (env/HOME/sentinel IO) => False. A broken gate
    degrades to "schema change waits," never to "approval store down" — the
    caller (migrate) treats False as unarmed and never raises into init."""
    try:
        env = os.environ.get("APPROVAL_DDL_ARMED", "") or ""
        tokens = {t.strip() for t in env.split(",") if t.strip()}
        if "all" in tokens or migration_id in tokens:
            return True
        sentinel = Path.home() / "runtime" / f"APPROVAL_DDL_ARMED_{migration_id}"
        return sentinel.exists()
    except Exception:
        return False

GATE_BRIDGE_KINDS = ("gate", "gate_grant")


def is_gate_row(row) -> bool:
    """A safety-gate card: origin 'gate', or a menu whose bridge kind is 'gate' / 'gate_grant' (either is enough,
    so a card that only looks like one is treated as one: the safe side)."""
    if not isinstance(row, dict):
        return False
    if row.get("origin") == "gate":
        return True
    m = row.get("menu")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            return False
    b = m.get("bridge") if isinstance(m, dict) else None
    return isinstance(b, dict) and b.get("kind") in GATE_BRIDGE_KINDS


def _gate_row_locked(c, rid, cols) -> bool:
    """True when `rid` is a safety-gate card (read inside the writer's own connection)."""
    want = [x for x in ("origin", "menu") if x in cols]
    if not want:
        return False
    r = c.execute(f"SELECT {', '.join(want)} FROM approval_requests WHERE id=?", [rid]).fetchone()
    if r is None:
        return False
    return is_gate_row(dict(zip(want, tuple(r))))


def _ledger_answer_hook(rid, answer, option_n=None, answer_text=None):
    """Outward-action ledger fast path (V3): tell the ledger a card was answered, AFTER the
    answer-once UPDATE committed. Never raises to the answerer: the answer is already durable,
    and outward_ledger.sweep() reconciles the same thing from the card row later."""
    try:
        import outward_ledger
        outward_ledger.on_card_answer(rid, answer, option_n=option_n, answer_text=answer_text)
    except Exception as e:  # noqa: BLE001 — the ledger is a view of the answer, never a gate on it
        print(f"[approval_schema] outward ledger hook for {rid} failed ({type(e).__name__}): {e}",
              file=sys.stderr)


class ApprovalStore:
    def __init__(self, db_path=None):
        # Resolved per construction, so a monkeypatched ORCHESTRA_DIR actually isolates.
        self.db_path = str(db_path or approval_config.db_path())
        # THE CONSTRUCTOR FENCE (gm ruling 2026-10-05). A conftest guard only protects suites
        # that load that conftest; this one travels with the code, so it also covers a bare
        # `pytest some_file.py` from another root, a doctest, or a script a test shells out to.
        # Two independent fences, because this class has already recurred twice: a test wrote
        # to the live ledger on 2026-09-25 and again on 2026-10-05.
        approval_config.refuse_live_db_under_pytest(self.db_path)
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        return c
    def migrate(self):
        c = self._conn()
        try:
            c.executescript(DDL); c.commit()
            # Additive DecisionCard fields (nullable; old rows -> NULL, existing flow unchanged).
            existing = {r[1] for r in c.execute("PRAGMA table_info(approval_requests)").fetchall()}
            # kind: NULL/'approval' = gate request; 'completion' = evidence-attached
            # done-brief (spec §3.4) — same ledger, additive, old flow untouched.
            # evidence: JSON, dual-use. On completion rows: {"tests": str?,
            # "urls": [{"label","url"}], "verified": bool} (§3.4 done-brief). On
            # commitment human_task rows: {"due_ts": ISO8601-with-offset,
            # "due_source": str} for the countdown chip (DEC-1789352893701528).
            # menu: JSON capture of a bridged native pane-menu (kind='menu' rows;
            # app-watch-decision-surface §1). option_n: the answered option index
            # (answer=='option'), the digit fire_resume injects. Both nullable.
            for col in ("summary", "risk_level", "reversibility", "feature", "kind", "evidence",
                        "menu", "option_n", "resume_error", "discarded_at",
                        "last_attempt_at", "escalated_at", "notify_skipped"):
                if col not in existing:
                    c.execute(f"ALTER TABLE approval_requests ADD COLUMN {col} TEXT")
            # Additive-nullable §1.1 provider-neutral IDENTITY columns (SPEC
            # all-model-parity R2). All nullable: a legacy row (all NULL) behaves
            # exactly as today — enforcement of these fields is a separate, later,
            # per-writer arming gate (mirrors cv4 shadow discipline). NOTHING
            # branches on `provider` — it is provenance only. No cv4 authority
            # CHECK lands here (cv4_seat_epochs/cv4_leases do not exist on live).
            #   seat_id  stable lineage address (gm, gemini-dev)
            #   run_id   immutable logical run/generation identity
            #   generation  lineage sequence number (INTEGER)
            #   seat_epoch  authority epoch at creation, from cv4_seat_epochs — never self-declared (INTEGER)
            #   provider  claude|gemini|codex — provenance ONLY, no state-machine branch
            #   provider_session_id  Claude sid / agy brain id
            #   process_instance_id  one OS process incarnation
            #   process_lease_id  cv4 lease token (nullable until enforcement arms)
            #   origin  canonical|backfill_fs|shadow_proposal — migration provenance
            for col, coltype in (("seat_id", "TEXT"), ("run_id", "TEXT"),
                                 ("generation", "INTEGER"), ("seat_epoch", "INTEGER"),
                                 ("provider", "TEXT"), ("provider_session_id", "TEXT"),
                                 ("process_instance_id", "TEXT"), ("process_lease_id", "TEXT"),
                                 ("origin", "TEXT")):
                if col not in existing:
                    c.execute(f"ALTER TABLE approval_requests ADD COLUMN {col} {coltype}")
            # Additive-nullable §3.1 STALE_TARGET + rebind columns (SPEC
            # all-model-parity R3). A resume whose target fails the lease/run
            # verification transitions the answered row to a durable
            # 'stale_target' — never a silent consume/blind inject into a
            # successor (the g14/g15 promotion defect class). The row is then
            # re-bindable back to 'answered' via the deterministic three-leg
            # rebind (§3). All nullable; a legacy row is untouched.
            #   stale_reason   why the resume target was rejected (durable)
            #   stale_at       when the row went stale_target
            #   rebound_at     when a rebind last succeeded
            #   rebind_event   JSON list of every rebind attempt (durable audit)
            for col in ("stale_reason", "stale_at", "rebound_at", "rebind_event"):
                if col not in existing:
                    c.execute(f"ALTER TABLE approval_requests ADD COLUMN {col} TEXT")
            c.commit()
            # --- GATED future migrations (DESIGN_ddl-arming-gate.md) ---
            # Default-OFF: a future batch lives in CODE (GATED_MIGRATIONS) but does
            # NOT fire on the live schema until the operator arms it. EMPTY at install => this
            # loop is a no-op (byte-equivalent live behavior). Armed => idempotent
            # ADD COLUMN via the same PRAGMA existence check as the legacy batches.
            # Unarmed => skip + loud PENDING log; init NEVER crashes.
            live = {r[1] for r in c.execute("PRAGMA table_info(approval_requests)").fetchall()}
            for mig in GATED_MIGRATIONS:
                mid = mig["id"]
                cols = mig.get("columns", [])
                if not armed(mid):
                    pending = [name for name, _ in cols if name not in live]
                    if not pending:
                        continue          # already applied (e.g. armed at init): nothing to say
                    print(f"[approval_schema] DDL PENDING (unarmed): {mid} "
                          f"cols={pending} — arm via APPROVAL_DDL_ARMED={mid} "
                          f"or sentinel ~/runtime/APPROVAL_DDL_ARMED_{mid}",
                          file=sys.stderr)
                    continue
                for name, coltype in cols:
                    if name not in live:
                        # Once armed, every beat runs migrate(): two processes can both read the column
                        # as absent, and the loser's ALTER then fails. The column is there either way.
                        try:
                            c.execute(f"ALTER TABLE approval_requests ADD COLUMN {name} {coltype}")
                        except sqlite3.OperationalError as e:
                            if "duplicate column" not in str(e):
                                raise
                        live.add(name)
            c.commit()
        finally:
            c.close()
    def create(self, from_agent, question, worker_kind, op_key=None,
               thread_key=None, options=None,
               summary=None, risk_level=None, reversibility=None, feature=None,
               kind=None, evidence=None, menu=None,
               seat_id=None, run_id=None, generation=None, seat_epoch=None,
               provider=None, provider_session_id=None, process_instance_id=None,
               process_lease_id=None, origin=None,
               block_task=None, blocks_what=None, urgency=None, default_option=None,
               report=None):
        # `report` (optional dict): filled with {"inserted": bool} so a caller can tell a card this call
        # made from one the op_key dedupe or the re-card guard handed back. The return value is
        # unchanged (the id either way).
        if report is not None:
            report["inserted"] = False
        # §1.1/§4 identity kwargs are ALL optional + provenance-only. `provider`
        # is recorded, NEVER branched on. A value the caller passes (an adapter
        # that captured it from its runtime) always wins. What it leaves empty of
        # the source agent's identity (seat_id, provider, provider_session_id,
        # generation) is filled HERE, before the row is written, from the
        # registry entry of the agent named by from_agent (card_provenance.py):
        # this is the one insert every card type passes through (approval.py's
        # request/complete/human-task, the menu bridge, the backfill, the lineage
        # daemon), so no writer can skip it. Never the author's own claim: an
        # agent cannot write another seat's identity onto a card it files.
        ident = _card_provenance().fill(from_agent, {
            "seat_id": seat_id, "provider": provider,
            "provider_session_id": provider_session_id, "generation": generation})
        seat_id, provider = ident["seat_id"], ident["provider"]
        provider_session_id, generation = ident["provider_session_id"], ident["generation"]
        options = options or DEFAULT_OPTIONS
        if isinstance(evidence, (dict, list)):
            evidence = json.dumps(evidence)
        if isinstance(menu, (dict, list)):
            menu = json.dumps(menu)
        c = self._conn()
        try:
            if op_key:
                # The dedup check and the insert run under one write lock: two writers (the gateway making a
                # card on read, the bridge's cron tick) could otherwise both find no card and both insert one.
                c.execute("BEGIN IMMEDIATE")
                row = c.execute(
                    "SELECT id FROM approval_requests WHERE from_agent=? AND op_key=? AND status='pending' LIMIT 1",
                    [from_agent, op_key]).fetchone()
                if row:
                    return row["id"]
                # Re-card guard (P0, v3 msg_667fb1b8): a RECENT same-op_key row
                # blocks a new card REGARDLESS of status — the field cascade was
                # answered -> (false) resolved_elsewhere, after which the pending
                # dedup stopped applying and the still-parked pane menu re-carded
                # 60s later as the operator's ghost phone duplicate. Window-bounded so a
                # genuinely repeated ask re-cards once the window passes.
                # NOTE: created_at is ISO-with-'T'; sqlite datetime() is
                # space-format — normalize or the comparison is same-day-true.
                row = c.execute(
                    "SELECT id FROM approval_requests WHERE from_agent=? AND op_key=? "
                    "AND replace(created_at,'T',' ') > datetime('now', ?) "
                    "ORDER BY created_at DESC LIMIT 1",
                    [from_agent, op_key, f"-{RECARD_GUARD_S} seconds"]).fetchone()
                if row:
                    return row["id"]
            rid = _gen_id()
            if report is not None:
                report["inserted"] = True
            now = _now()
            exp = (datetime.now(timezone.utc) + timedelta(hours=EXPIRY_HOURS)).isoformat()
            c.execute(
                """INSERT INTO approval_requests
                   (id, from_agent, question, op_key, options, status, thread_key,
                    worker_kind, created_at, expires_at, summary, risk_level, reversibility, feature,
                    kind, evidence, menu,
                    seat_id, run_id, generation, seat_epoch, provider,
                    provider_session_id, process_instance_id, process_lease_id, origin)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [rid, from_agent, question, op_key, json.dumps(options), "pending",
                 thread_key, worker_kind, now, exp, summary, risk_level, reversibility, feature,
                 kind, evidence, menu,
                 seat_id, run_id, generation, seat_epoch, provider,
                 provider_session_id, process_instance_id, process_lease_id, origin])
            # Gated columns go in with the card (same transaction), only when they exist (armed);
            # unarmed, the row is exactly as before.
            gated = {k: v for k, v in (("urgency", urgency), ("default_option", default_option))
                     if v is not None}
            if gated:
                have = {r[1] for r in c.execute("PRAGMA table_info(approval_requests)").fetchall()}
                sets = {k: v for k, v in gated.items() if k in have}
                if sets:
                    c.execute(f"UPDATE approval_requests SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                              list(sets.values()) + [rid])
            c.commit()
            # §1.1/§6 human_task columns (AGY item 1): the base INSERT above is a
            # STATIC column list, so writing block_task/blocks_what unconditionally
            # would OperationalError-crash EVERY create while the migration is
            # UNARMED (columns absent). PRAGMA-guard the write (the same have_attr
            # pattern record_answer uses): armed => stamp them; unarmed => the base
            # insert stands, byte-identical to today.
            if block_task is not None or blocks_what is not None:
                have = {r[1] for r in
                        c.execute("PRAGMA table_info(approval_requests)").fetchall()}
                if "block_task" in have and "blocks_what" in have:
                    c.execute(
                        "UPDATE approval_requests SET block_task=?, blocks_what=? WHERE id=?",
                        [block_task, blocks_what, rid])
                    c.commit()
            return rid
        finally:
            c.close()

    # allow-list of columns the patch/update path may mutate (never status/op_key/id).
    # `evidence` is dual-use (DEC-1789352893701528): on commitment human_task rows it
    # carries {"due_ts","due_source"} for the countdown chip; on completion rows it is
    # the done-brief JSON (§3.4). Coerced dict/list -> JSON exactly like create() (:190).
    _PATCHABLE = ("feature", "block_task", "blocks_what", "summary", "evidence")

    def update_fields(self, rid, **fields):
        """General in-place patch (contract DEC-1789349623024244 / msg_410c3454):
        update ONLY the given columns on a PENDING kind='human_task' row, and set
        question := block_task whenever block_task is given. Refuse (return False)
        a missing id, a non-pending row, or a non-human_task row. Only allow-listed
        columns are writable. `create()` returns-existing on a repeat op_key so it
        cannot backfill and there was no update verb — this is that verb."""
        cols = {k: v for k, v in fields.items() if k in self._PATCHABLE}
        if not cols:
            return False
        # coerce a dict/list evidence to JSON, same as create() at :190 (a caller
        # may pass a prebuilt JSON string, which is stored verbatim).
        if isinstance(cols.get("evidence"), (dict, list)):
            cols["evidence"] = json.dumps(cols["evidence"])
        c = self._conn()
        try:
            row = c.execute(
                "SELECT status, kind FROM approval_requests WHERE id=?", [rid]).fetchone()
            if not row or row["status"] != "pending" or row["kind"] != "human_task":
                return False
            have = {r[1] for r in
                    c.execute("PRAGMA table_info(approval_requests)").fetchall()}
            sets, vals = [], []
            for k, v in cols.items():
                if k in have:
                    sets.append(f"{k}=?"); vals.append(v)
            if "block_task" in cols:   # question mirrors block_task
                sets.append("question=?"); vals.append(cols["block_task"])
            if not sets:
                return False
            vals.append(rid)
            c.execute(f"UPDATE approval_requests SET {', '.join(sets)} WHERE id=?", vals)
            c.commit()
            return True
        finally:
            c.close()

    def merge_evidence(self, rid, key, value):
        """Read-merge-write of ONE top-level `evidence` key, on ANY status (outward-action ledger
        V2; update_fields stays pending+human_task-only). Under BEGIN IMMEDIATE so two writers
        cannot lose each other's key. Every other key is written back exactly as read: the ledger
        writes only `action`; `assist`, `due_ts`, `due_source` and the done-brief keys are never
        touched (contract E1: evidence is additive, never a new column). Returns True iff a row
        was written; False for a missing id or an evidence value that is not a JSON object."""
        if not isinstance(key, str) or not key:
            return False
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT evidence FROM approval_requests WHERE id=?", [rid]).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return False
            raw = row["evidence"]
            if raw in (None, ""):
                ev = {}
            else:
                try:
                    ev = json.loads(raw)
                except (TypeError, ValueError):
                    c.execute("ROLLBACK")
                    return False
                if not isinstance(ev, dict):
                    c.execute("ROLLBACK")
                    return False
            ev[key] = value
            c.execute("UPDATE approval_requests SET evidence=? WHERE id=?", [json.dumps(ev), rid])
            c.execute("COMMIT")
            return True
        finally:
            c.close()

    def reshape_commitment(self, from_agent, op_key, *, feature, block_task,
                           summary, blocks_what):
        """Backfill convenience: resolve the PENDING row by (from_agent, op_key)
        and update_fields() it to the task-first commitment shape. Returns the row
        id or None if no pending row matches."""
        c = self._conn()
        try:
            row = c.execute(
                "SELECT id FROM approval_requests WHERE from_agent=? AND op_key=? "
                "AND status='pending' ORDER BY created_at DESC LIMIT 1",
                [from_agent, op_key]).fetchone()
        finally:
            c.close()
        if not row:
            return None
        rid = row["id"]
        ok = self.update_fields(rid, feature=feature, block_task=block_task,
                                summary=summary, blocks_what=blocks_what)
        return rid if ok else None
    def append_note(self, rid, author, text, now_iso=None):
        """Append an author's timestamped NOTE to a row's summary. Never rewrites the
        original text and never touches status/answer/notified_at. Works on EVERY kind
        (menu cards, requests and human_tasks), unlike update_fields, which refuses anything
        but human_task. A note is the honest CORRECTION path: the original and the correction
        both stay visible. A card that is no longer NEEDED is retired instead (retire()).
        Returns (ok, reason). ok=False always carries a reason: a refusal that prints an
        empty string is the failure-that-reads-as-success class."""
        text = (text or "").strip()
        if not text:
            return False, "empty note: nothing to append"
        c = self._conn()
        try:
            row = c.execute(
                "SELECT from_agent, summary, question FROM approval_requests WHERE id=?",
                [rid]).fetchone()
            if not row:
                return False, f"no approval row {rid}"
            # The author, its own seat across rotations (a successor annotating a predecessor's
            # card, by POSITIVE registry evidence; seat_identity), or the operator, who owns every
            # surface.
            why = None
            if author != "operator":
                import seat_identity
                same, why = seat_identity.may_annotate(
                    seat_identity.registry_beside(self.db_path), author, row["from_agent"])
                if not same:
                    return False, why or (f"{author} is not the author of {rid} "
                                          f"(authored by {row['from_agent']}); only the author, "
                                          f"its current successor, or the operator may annotate it")
            stamp = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            base = row["summary"] or row["question"] or ""
            who = f"{author} (orphan steward)" if why == "orphan-steward" else author
            note = f"\n\n— NOTE {stamp} by {who}: {text}"
            c.execute("UPDATE approval_requests SET summary=? WHERE id=?", [base + note, rid])
            c.commit()
            return True, None
        finally:
            c.close()

    def retire(self, rid, caller, reason, superseded_by=None, now_iso=None, read_pane=None, has_session=None):
        """Card RETIRE verb: the author, its current generation, or gm marks a PENDING card no
        longer needed (operator ruling 2026-10-10). See retire_card. read_pane (close-menu's
        read-only pane reader) is used only for a bridge menu card; has_session only for the
        dead-seat rule of a menu card. Returns (ok, reason)."""
        import seat_identity
        c = self._conn()
        try:
            return retire_card(c, "approval_requests", rid, caller, reason, superseded_by,
                               seat_identity.registry_beside(self.db_path), now_iso, read_pane, has_session)
        finally:
            c.close()

    def reopen(self, rid, caller, reason, now_iso=None):
        """See reopen_card. Returns (ok, reason)."""
        import seat_identity
        c = self._conn()
        try:
            return reopen_card(c, rid, caller, reason, seat_identity.registry_beside(self.db_path), now_iso)
        finally:
            c.close()

    def close_menu(self, rid, caller, reason, read_pane, now_iso=None):
        """See close_menu_card. Returns (ok, reason, note)."""
        import seat_identity
        c = self._conn()
        try:
            return close_menu_card(c, rid, caller, reason,
                                   seat_identity.registry_beside(self.db_path), read_pane, now_iso)
        finally:
            c.close()

    def get(self, rid):
        c = self._conn()
        try:
            r = c.execute("SELECT * FROM approval_requests WHERE id=?", [rid]).fetchone()
            return dict(r) if r else None
        finally:
            c.close()
    def get_many(self, ids):
        """{id: row} for the ids that exist, in one read (the attention list links many done notes to
        their completion cards at once). Read-only."""
        ids = [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and i]
        if not ids:
            return {}
        c = self._conn()
        try:
            out = {}
            for k in range(0, len(ids), 500):
                chunk = ids[k:k + 500]
                for r in c.execute(f"SELECT * FROM approval_requests WHERE id IN "
                                   f"({','.join('?' for _ in chunk)})", chunk):
                    out[r["id"]] = dict(r)
            return out
        finally:
            c.close()
    def find_by_op_key(self, op_key, origin=None):
        """Rows carrying this op_key, optionally filtered by migration origin —
        newest-first. The R7a migrators (§2.1 FS backfill, §2.2 proposal shadow)
        use this as their IDEMPOTENCY gate: a re-run must not re-import a row it
        already created, INDEPENDENT of the row's later status (create()'s own
        dedup only blocks a still-'pending' twin + a 900s re-card window, so a
        backfilled row that has SINCE been answered would slip past it). Keying
        on op_key+origin closes that. Read-only; never mutates."""
        if not op_key:
            return []
        c = self._conn()
        try:
            if origin is None:
                q = "SELECT * FROM approval_requests WHERE op_key=? ORDER BY created_at DESC"
                params = [op_key]
            else:
                q = ("SELECT * FROM approval_requests WHERE op_key=? AND origin=? "
                     "ORDER BY created_at DESC")
                params = [op_key, origin]
            return [dict(r) for r in c.execute(q, params).fetchall()]
        finally:
            c.close()
    def record_answer(self, rid, answer, answer_text, option_n=None,
                      surface=None, answered_by=None, device=None, gate_checked=False):
        """Id-bound + answer-once: only a still-'pending' row accepts an answer.
        option_n (menu rows, answer=='option'): the captured option index the
        resume path will inject as a keypress.

        R7d attribution (SPEC §R7d): `surface`/`answered_by` are provenance-only
        (audit/forensics/telemetry) — NEVER read back for an authz decision. The
        EDGE owns its tag; this core just persists what it is handed. `surface`
        defaults to 'unknown' (an edge that passes no tag), `answered_by` stays
        NULL. The two columns ride the arming gate (delta 1), so they may not
        exist yet: when unarmed we fall back to the legacy UPDATE and the answer
        still lands (fail-open — a missing tag never blocks an answer).

        A safety-gate card (a permission card or a grant proposal) is refused here unless
        `gate_checked` is True, which only watch_gateway.apply_answer passes, after it has checked
        that a person is answering (plan Task 7a). Every other writer (the ntfy listener, a script
        that imports the store) is refused: fail closed."""
        c = self._conn()
        try:
            cols = {r[1] for r in
                    c.execute("PRAGMA table_info(approval_requests)").fetchall()}
            if not gate_checked and _gate_row_locked(c, rid, cols):
                return False
            # Each provenance column rides its own arming gate, so compose the SET clause
            # from what actually exists rather than branching on every combination. Same
            # fail-open rule as before: a column that is not armed yet never blocks an
            # answer, it just records less.
            sets = ["answer=?", "answer_text=?", "option_n=?",
                    "status='answered'", "answered_at=?"]
            vals = [answer, answer_text, option_n, _now()]
            if "answer_surface" in cols and "answered_by" in cols:
                sets += ["answer_surface=?", "answered_by=?"]
                vals += [surface or "unknown", answered_by]
            if "answer_device" in cols:
                sets.append("answer_device=?")
                vals.append(device)
            res = c.execute(
                f"UPDATE approval_requests SET {', '.join(sets)} "
                "WHERE id=? AND status='pending'", vals + [rid])
            c.commit()
        finally:
            c.close()
        if res.rowcount > 0:
            _ledger_answer_hook(rid, answer, option_n, answer_text)
        return res.rowcount > 0
    def record_batch_answer(self, rid, answers, surface=None, answered_by=None,
                            device=None):
        """§1.1(b) condition-6 (DEC-1787700374): DURABLE-FIRST multi-part SUBMIT.
        Persist a whole multi-part answer BATCH onto the pending menu row and
        transition it to 'answered' — BEFORE any live-pane replay — so a
        `menu_gone` on the ephemeral pane can never evaporate the operator's answers (the
        Bug-3 answer-loss class). The batch (answers=[{part, ns:[...], text?}, …])
        is stored as JSON in answer_text with answer='batch'; the caller
        (durable_first_batch_submit) validates it against the condition-1 hydrated
        parts[] BEFORE calling, so this core just persists what it is handed —
        mirroring record_answer's single-writer discipline.

        Id-bound + answer-once: only a still-'pending' row accepts the batch (a
        racing double-submit / watchdog re-fire no-ops -> returns False, never a
        second write). surface/answered_by are provenance-only (R7d), fail-open on
        the un-armed attribution columns exactly like record_answer."""
        answer_text = json.dumps(answers)
        c = self._conn()
        try:
            cols = {r[1] for r in
                    c.execute("PRAGMA table_info(approval_requests)").fetchall()}
            if _gate_row_locked(c, rid, cols):
                return False                 # a safety-gate card is never a multi-part menu (plan Task 7a)
            have_attr = "answer_surface" in cols and "answered_by" in cols
            if have_attr:
                res = c.execute(
                    "UPDATE approval_requests SET answer='batch', answer_text=?, "
                    "status='answered', answered_at=?, answer_surface=?, answered_by=? "
                    "WHERE id=? AND status='pending'",
                    [answer_text, _now(), surface or "unknown", answered_by, rid])
            else:
                res = c.execute(
                    "UPDATE approval_requests SET answer='batch', answer_text=?, "
                    "status='answered', answered_at=? "
                    "WHERE id=? AND status='pending'",
                    [answer_text, _now(), rid])
            c.commit()
        finally:
            c.close()
        if res.rowcount > 0:
            _ledger_answer_hook(rid, "batch", None, answer_text)
        return res.rowcount > 0
    def pending_menu_row_for_session(self, session):
        """§1.1(b) condition-6: the newest still-PENDING kind='menu' ledger row
        for `session` — the durable anchor a multi-part submit persists onto.
        Matched by op_key prefix (menu:{session}:...) exactly like
        cached_hydration / resolve_pending_menus_for_session (one live pane, one
        live menu; any other pending menu row for the session is a stale dupe).
        Returns the full row dict or None. Read-only."""
        like = session.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        c = self._conn()
        try:
            r = c.execute(
                "SELECT * FROM approval_requests "
                "WHERE kind='menu' AND status='pending' AND op_key LIKE ? ESCAPE '\\' "
                "ORDER BY created_at DESC LIMIT 1",
                [f"menu:{like}:%"]).fetchone()
            return dict(r) if r else None
        finally:
            c.close()
    def latest_menu_row_for_session(self, session):
        """§1.1(b) condition-6: the newest kind='menu' ledger row for `session`
        REGARDLESS of status — used to detect an already-answered batch (a client
        retry after a durable persist) so a second submit is idempotent, never a
        'no_durable_row' false-failure. Read-only; returns the row dict or None."""
        like = session.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        c = self._conn()
        try:
            r = c.execute(
                "SELECT * FROM approval_requests "
                "WHERE kind='menu' AND op_key LIKE ? ESCAPE '\\' "
                "ORDER BY created_at DESC LIMIT 1",
                [f"menu:{like}:%"]).fetchone()
            return dict(r) if r else None
        finally:
            c.close()
    def mark_resumed_fired(self, rid):
        """REAL delivery attempt (SLA spec 2026-08-14): counts toward escalation."""
        c = self._conn()
        try:
            c.execute("UPDATE approval_requests SET resumed_at=?, last_attempt_at=?, "
                      "resume_attempts=resume_attempts+1 WHERE id=?",
                      [_now(), _now(), rid]); c.commit()
        finally:
            c.close()
    def stamp_retry(self, rid):
        """CHEAP refusal (busy / no-live-head — nothing entered the pane): throttle
        stamp only, never counts toward escalation (SLA spec 2026-08-14)."""
        c = self._conn()
        try:
            c.execute("UPDATE approval_requests SET last_attempt_at=? WHERE id=?",
                      [_now(), rid]); c.commit()
        finally:
            c.close()
    def mark_escalated(self, rid):
        """Escalation fired: throttle re-escalation (no attempt increment)."""
        c = self._conn()
        try:
            c.execute("UPDATE approval_requests SET escalated_at=?, last_attempt_at=? WHERE id=?",
                      [_now(), _now(), rid]); c.commit()
        finally:
            c.close()
    def ack(self, rid):
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET resume_acked_at=?, status='resumed' "
                "WHERE id=? AND status='answered'", [_now(), rid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()
    def mark_resolved_elsewhere(self, rid):
        """Menu rows (§1a): the pane menu vanished before the keypress landed —
        the decision was taken elsewhere. Terminal; watchdog never re-fires."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET status='resolved_elsewhere' "
                "WHERE id=? AND status='answered'", [rid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()
    def resolve_pending_menus_for_session(self, session):
        """F6-polish-1 (2026-08-25): a verified multipart submit answered the live
        pane menu for `session`, but the submit is pane-bound and never touched the
        mirror ledger row — it lingers pending until the ~60s reconcile_orphans
        cron. Resolve the still-PENDING bridged menu row(s) for this session NOW so
        the card clears on the next poll (phone AND watch), no app build.

        Matches reconcile_orphans' guard (status='pending', kind='menu') EXACTLY,
        so the two are idempotent: whichever runs first transitions the row, the
        other no-ops. Matched by op_key prefix (menu:{session}:...) since the exact
        question isn't available here — one live pane has one live menu; any other
        pending menu row for the same session is a stale dupe and correctly clears
        too. LIKE metacharacters in the session are escaped (ESCAPE '\\').
        Returns the number of rows transitioned.

        PROVENANCE GATE (DEC-1788138920 sibling fix): same discipline as
        menu_bridge.reconcile_orphans — only rows the bridge itself created
        (origin='menu_bridge') are eligible for auto-resolve. A kind=menu row
        minted elsewhere (CLI --options auto-synthesize, or any writer whose
        op_key merely collides with the menu:{session}: prefix) must never be
        resolved out from under the operator; it stays pending + visible."""
        like = session.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET status='resolved_elsewhere' "
                "WHERE kind='menu' AND status='pending' "
                "AND origin='menu_bridge' "
                "AND op_key LIKE ? ESCAPE '\\'",
                [f"menu:{like}:%"])
            c.commit()
            return res.rowcount
        finally:
            c.close()
    def hydrate_menu(self, session, walked, op_key=None):
        """§1.1(b) condition 1 (AGY-caught) + condition 4: LEDGER WRITE-BACK on a
        SUCCESSFUL /agent-menu-capture walk. Merge the fully-walked parts[] into
        the pending needs_hydration menu row(s) for `session` and mark them
        walk_complete (clearing needs_hydration). This makes a later answer
        validate against the full parts[] AND turns the ledger into the hydration
        CACHE (subsequent /agent-menu-capture reads the stored payload without
        actuating a live pane — condition 4).

        `walked` is menu_capture_walk's return ({parts, part_count, walk_complete}).
        STRICT gate: the caller MUST only call this with walk_complete:true; a
        defensive guard here also refuses a non-complete walk (never poison the
        cache with partial parts). The merge PRESERVES the passive capture's flat
        fields (kind/question/chrome/…) and only overwrites parts/part_count/
        walk_complete + drops needs_hydration; flat `question`/`options` are
        re-mirrored to part-0 (legacy-flat-fields-mirror-part-one) since the walk
        leaves the pane on part 0.

        Guarded to status='pending' AND kind='menu' AND op_key LIKE menu:{session}:%
        (mirrors resolve_pending_menus_for_session — one live pane, one live menu).
        `op_key` (the card identity of the menu that was walked): only that card is written, so a walk of the
        menu now on screen never lands on the card of one it replaced. Absent: every pending menu row of the session,
        as before.

        Returns the number of rows written. Sanctioned single-writer: the gateway
        NEVER raw-writes tasks.db."""
        if not isinstance(walked, dict) or not walked.get("walk_complete") \
                or not walked.get("parts"):
            return 0                                       # STRICT: complete-only
        parts = walked["parts"]
        part_count = walked.get("part_count", len(parts))
        like = session.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        c = self._conn()
        try:
            rows = c.execute(
                "SELECT id, menu, options FROM approval_requests "
                "WHERE kind='menu' AND status='pending' AND op_key LIKE ? ESCAPE '\\'"
                + (" AND op_key = ?" if op_key else ""),
                [f"menu:{like}:%"] + ([op_key] if op_key else [])).fetchall()
            n = 0
            for r in rows:
                try:
                    menu = json.loads(r["menu"]) if isinstance(r["menu"], str) else (r["menu"] or {})
                except (TypeError, ValueError):
                    menu = {}
                if not isinstance(menu, dict):
                    menu = {}
                menu["parts"] = parts
                menu["part_count"] = part_count
                menu["walk_complete"] = True
                menu.pop("needs_hydration", None)
                # legacy flat mirror = part-0 (the walk restores the pane to part 0)
                p0 = parts[0] if isinstance(parts[0], dict) else {}
                if p0.get("question"):
                    menu["question"] = p0["question"]
                if p0.get("options") is not None:
                    menu["options"] = p0["options"]
                    opt_col = json.dumps([o.get("label", "") for o in p0["options"]
                                          if isinstance(o, dict)])
                else:
                    opt_col = r["options"]
                c.execute(
                    "UPDATE approval_requests SET menu=?, options=? "
                    "WHERE id=? AND status='pending'",
                    [json.dumps(menu), opt_col, r["id"]])
                n += 1
            c.commit()
            return n
        finally:
            c.close()
    def cached_hydration(self, session, op_key=None):
        """§1.1(b) condition 4 read-side: the stored menu dict for `session` iff a
        pending menu row already carries a fully-walked (walk_complete:true) payload
        — the hydration cache. Returns that menu (so /agent-menu-capture can serve
        it WITHOUT re-walking the live pane), else None. Read-only. `op_key`: only the card
        of the menu now on screen, so a stored walk of a menu that has since changed is never served."""
        like = session.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        c = self._conn()
        try:
            rows = c.execute(
                "SELECT menu FROM approval_requests "
                "WHERE kind='menu' AND status='pending' AND op_key LIKE ? ESCAPE '\\' "
                + ("AND op_key = ? " if op_key else "") +
                "ORDER BY created_at DESC",
                [f"menu:{like}:%"] + ([op_key] if op_key else [])).fetchall()
        finally:
            c.close()
        for r in rows:
            try:
                menu = json.loads(r["menu"]) if isinstance(r["menu"], str) else (r["menu"] or {})
            except (TypeError, ValueError):
                continue
            if isinstance(menu, dict) and menu.get("walk_complete") and menu.get("parts"):
                return menu
        return None
    def mark_resume_failed(self, rid, error):
        """Menu rows (§1a): a resume phase failed — record the honest error,
        never a silent half-submit. Terminal; watchdog never re-fires."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET status='resume_failed', resume_error=? "
                "WHERE id=? AND status='answered'", [str(error)[:500], rid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()
    def mark_stale_target(self, rid, reason):
        """§3.1: a resume whose target fails the lease/run verification
        transitions the ANSWERED row to a durable 'stale_target' state — never
        a silent consume, never a blind inject into a successor (the verified
        g14/g15 promotion defect class). Only an 'answered' row can go
        stale_target (the mismatch is detected at fire_resume time, after the
        answer landed). The answer is preserved; the reason is recorded. The
        row is re-bindable back to 'answered' via rebind(). Idempotent:
        returns False if the row is not currently 'answered'."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET status='stale_target', "
                "stale_reason=?, stale_at=? WHERE id=? AND status='answered'",
                [str(reason)[:500], _now(), rid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()

    def rebind(self, rid, *, run_id, seat_epoch, recovery_authorized,
               current_epoch_for=None):
        """DETERMINISTIC REBIND (the operator ruling 08-23, spec §3): a 'stale_target'
        row returns to 'answered' ONLY when ALL THREE legs hold —

          (a) run_id      == the row's run_id (SAME logical run)
          (b) seat_epoch  == the row's seat_epoch == the seat's CURRENT minted
                             epoch (no rotation happened since; a NEW epoch is
                             a rotation, whose disposition is transfer /
                             supersede / invalidate at promotion, NOT rebind)
          (c) recovery_authorized is True — a fresh cv4 lease acquired through
                             the AUTHORIZED recovery path (recover_in_repair_
                             seat semantics: stranded lease expired, re-read
                             under lock)

        Any leg failing -> the row STAYS 'stale_target', returns False, and a
        refused-rebind attempt is recorded durably (audit, never silent). A new
        process NEVER inherits a pending approval by lease acquisition alone.

        current_epoch_for: an INJECTABLE callable seat_id -> current minted
        epoch (cv4_seat_epochs MAX on live). Default None => the epoch leg
        CANNOT be verified and rebind REFUSES — no approval-side check may
        front-run the cv4 authority layer, whose tables are absent on live
        today (spec §1). Tests inject a stub; the live wiring waits for cv4.

        The authoritative transition is guarded by the UPDATE's
        `WHERE status='stale_target'` clause (row-level atomic in sqlite); the
        read-before is only to build the durable audit event.
        """
        c = self._conn()
        try:
            r = c.execute("SELECT * FROM approval_requests WHERE id=?",
                          [rid]).fetchone()
            if r is None:
                return False
            row = dict(r)
            if row.get("status") != "stale_target":
                # wrong-state caller error (not a failed-leg rebind) — do not
                # pollute the audit trail with a misleading refusal event.
                return False
            current = None
            if current_epoch_for is not None:
                try:
                    current = current_epoch_for(row.get("seat_id"))
                except Exception:
                    current = None
            legs = {
                # same run_id, both non-null
                "run_id": bool(run_id) and row.get("run_id") == run_id,
                # claimed epoch == row epoch == current minted (cv4 present)
                "seat_epoch": (current is not None and seat_epoch is not None
                               and seat_epoch == current
                               and row.get("seat_epoch") == current),
                # authorized recovery path
                "recovery": bool(recovery_authorized),
            }
            ok = all(legs.values())
            event = {"at": _now(), "run_id": run_id, "seat_epoch": seat_epoch,
                     "current_epoch": current,
                     "recovery_authorized": bool(recovery_authorized),
                     "legs": legs, "result": "rebound" if ok else "refused"}
            try:
                hist = json.loads(row.get("rebind_event") or "[]")
                if not isinstance(hist, list):
                    hist = []
            except (json.JSONDecodeError, TypeError):
                hist = []
            hist.append(event)
            hist_json = json.dumps(hist)
            if ok:
                res = c.execute(
                    "UPDATE approval_requests SET status='answered', "
                    "rebound_at=?, rebind_event=? "
                    "WHERE id=? AND status='stale_target'",
                    [_now(), hist_json, rid])
                c.commit()
                return res.rowcount > 0
            c.execute("UPDATE approval_requests SET rebind_event=? WHERE id=?",
                      [hist_json, rid])
            c.commit()
            return False
        finally:
            c.close()

    def set_notified(self, rid):
        c = self._conn()
        try:
            c.execute("UPDATE approval_requests SET notified_at=? WHERE id=?", [_now(), rid]); c.commit()
        finally:
            c.close()
    def snooze(self, rid, hours=2):
        """§2 snooze: set snoozed_until = now + `hours`, leave status='pending'
        (the card never disappears). Does NOT call record_answer (that would set
        status='answered' + drop it from the pending feed) and does NOT fire a
        resume. The floor-ordering (watch_gateway._priority_score) + the dedicated
        re-notify sweep (approval_resume.renotify_snoozed_human_tasks) express the
        snooze; this just stamps the field. Only a still-'pending' row snoozes.
        Returns True iff a pending row was stamped."""
        until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET snoozed_until=? WHERE id=? AND status='pending'",
                [until, rid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()
    def snoozed_human_tasks_due(self):
        """§2 re-notify sweep source: pending human_task rows whose snooze has
        elapsed (snoozed_until <= now). Read-only."""
        c = self._conn()
        try:
            return [dict(r) for r in c.execute(
                "SELECT * FROM approval_requests WHERE kind='human_task' AND status='pending' "
                "AND snoozed_until IS NOT NULL AND snoozed_until<=?", [_now()])]
        finally:
            c.close()
    def pending_to_notify(self):
        c = self._conn()
        try:
            return [dict(r) for r in c.execute(
                "SELECT * FROM approval_requests WHERE status='pending' ORDER BY created_at ASC").fetchall()]
        finally:
            c.close()
    def history(self, limit=50, agents=None):
        """Every decision the user actually acted on, newest-first (DEC-1786664626
        Q3/Q5 + the operator F2 'nothing expires; completed OR discarded'). Widened from
        answered/resumed only — a GM-authored menu answer that ended
        'resume_failed' (source_session=null before the fix) or 'resolved_elsewhere'
        still carries a REAL answer and must show. Also includes 'discarded'
        (the operator dismissed without answering — saved, not deleted). Read-only;
        does not touch the pending/resume flow.
          - answered / resumed:            answered, keypress/inject delivered.
          - resolved_elsewhere:            menu taken elsewhere but answered.
          - resume_failed WITH an answer:  the 8 backfill rows (answer NOT NULL).
          - discarded:                     dismissed without answering.
          - retired:                       author/gm marked it no longer needed (who/why kept).
        """
        c = self._conn()
        try:
            # 'retired' (operator ruling 2026-10-10) joins history; its timestamp column exists only
            # once the m20261010_card_retire arm has applied, so the ORDER BY names it only then.
            ended = ("answered_at, discarded_at, retired_at, created_at"
                     if "retired_at" in _cols(c, "approval_requests")
                     else "answered_at, discarded_at, created_at")
            # `agents` (attention list, a scoped member): filtered in SQL before the limit, so a page is
            # never short because other agents' rows came first. None = every agent, as before.
            only, args = "", []
            if agents is not None:
                agents = list(agents)
                only = f" AND from_agent IN ({','.join('?' for _ in agents)})" if agents else " AND 0"
                args = agents
            return [dict(r) for r in c.execute(
                "SELECT * FROM approval_requests WHERE "
                "(status IN ('answered','resumed','resolved_elsewhere','discarded','retired') "
                "OR (status='resume_failed' AND answer IS NOT NULL))" + only +
                f" ORDER BY COALESCE({ended}) DESC LIMIT ?",
                args + [int(limit)]).fetchall()]
        finally:
            c.close()

    def mark_discarded(self, rid):
        """DEC-1786664626 Q3 + the operator F2: the operator dismissed a decision WITHOUT
        answering -> terminal 'discarded', SAVED to history (never deleted).
        Only a still-'pending' row can be discarded (an answered one goes to
        history via its answer). Idempotent: returns False if not pending."""
        c = self._conn()
        try:
            res = c.execute(
                "UPDATE approval_requests SET status='discarded', answered_at=?, discarded_at=? "
                "WHERE id=? AND status='pending'", [_now(), _now(), rid])
            c.commit()
            return res.rowcount > 0
        finally:
            c.close()
    def discard(self, rid):
        """R8 name for mark_discarded (questionnaire-era gateway + tests call
        store.discard). One implementation, two names."""
        return self.mark_discarded(rid)
    def answered_unacked(self):
        c = self._conn()
        try:
            return [dict(r) for r in c.execute(
                "SELECT * FROM approval_requests WHERE status='answered' AND resume_acked_at IS NULL").fetchall()]
        finally:
            c.close()
    def expire_due(self, dry_run=False, notify_fn=None):
        """Expire stale PENDING decisions past expires_at. Answered/acted rows are
        never touched here (so F2 'answered never expire' already holds). Q4 is
        the operator-gated behind EXPIRE_PENDING (the operator 2026-08-13 apr_10c0c829 option 1:
        nothing expires, pending too — so the gate is OFF by default): when disabled,
        pending never expires either and this is a no-op, even with dry_run.

        gm msg_86bcc168 item 4: ``dry_run=True`` lists what WOULD expire and writes
        nothing; ``notify_fn(row)`` is called once per row actually expired so the
        AUTHOR (from_agent) is told (a notice failure never blocks the sweep; the row
        carries notified=False). Legacy shape kept: with neither argument the return is
        the list of ids; otherwise a list of row dicts {id, from_agent, kind, summary,
        question, created_at, expires_at, notified}."""
        if not _expire_pending_enabled():
            return []
        detail = dry_run or notify_fn is not None
        c = self._conn()
        try:
            rows = [dict(r) for r in c.execute(
                "SELECT id, from_agent, kind, summary, question, created_at, expires_at "
                "FROM approval_requests WHERE status='pending' AND expires_at < ? "
                "ORDER BY expires_at ASC", [_now()]).fetchall()]
            if dry_run:
                return rows
            if rows:
                c.executemany("UPDATE approval_requests SET status='expired' WHERE id=? AND status='pending'",
                              [[r["id"]] for r in rows])
                c.commit()
        finally:
            c.close()
        for r in rows:
            r["notified"] = False
            if notify_fn is not None:
                try:
                    notify_fn(r)
                    r["notified"] = True
                except Exception:  # noqa: BLE001 — telling the author is best-effort
                    r["notified"] = False
        return rows if detail else [r["id"] for r in rows]
