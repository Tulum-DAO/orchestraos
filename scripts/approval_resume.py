"""Resume signal + liveness watchdog. A request is DONE at 'resumed', never at 'answered'.
menu: KEYPRESS into the source pane (_fire_menu_resume). pane: VERIFIED-INJECT the
answer into from_agent's LIVE HEAD pane AND write a durable msg_store row (dual:
inject for delivery, row for audit/watchdog). node: mark for orchestrator pickup.
watchdog: re-fire un-acked 'answered' rows; escalate after MAX attempts.

R3 (WS1 addendum): the pane path previously ONLY enqueued a msg_store row and
relied on a live router to inject it -- but no injector pushed it, so a GM/app
decision answer sat unread in the ledger. It now resolves from_agent -> live head
(post-rotation aware, the R2 primitive) and verified-injects the answer via the
gateway send-keys -l path, while STILL preserving the row as the durable record +
watchdog backstop."""
import os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root for msg_store
from approval_config import WATCHDOG_MINUTES, WATCHDOG_MAX_ATTEMPTS, NTFY_BASE, NTFY_APPROVALS_TOPIC, ntfy_token
from approval_config import (WATCHDOG_RETRY_BEAT_SECONDS, ESCALATE_STUCK_MINUTES,
                             ESCALATE_REPEAT_MINUTES)
from approval_schema import ApprovalStore

# The ack instruction delivered to a seat must name THIS checkout's CLI, not an operator
# layout (B1 run-2 finding B: a from-docs seat was told to run a path that does not exist).
_CHECKOUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVAL_CLI = os.path.join(_CHECKOUT, "scripts", "approval.py")

def _msg_store():
    from msg_store import MessageStore
    return MessageStore()


# ---------------------------------------------------------------------------
# Canonical-store guard (gm msg_3477cbfb, incident apr_07805db5). The resume
# path's REAL side-effect seams — the durable msg_store send and the live pane
# inject — must never run for a store that is not the canonical DB: a probe /
# test against a temporary store whose row named a live agent delivered a
# "[DECISION ANSWERED ...]" digest into that agent's pane, and it acted on it.
# fire_resume + every _fire_* stamp the ACTIVE store; the two real seams refuse
# (fail-CLOSED, logged) when it is non-canonical. Hermetic tests that patch
# _msg_store / _default_inject are untouched (a patched seam is not the real
# one). APPROVAL_RESUME_ALLOW_NONCANONICAL=1 opts a deliberate caller in;
# APPROVAL_RESUME_BLOCK_REAL_SEAMS=1 (scripts/conftest.py default) refuses the
# real seams under ANY pytest run, canonical or not.
# ---------------------------------------------------------------------------
_REAL_MSG_STORE_FACTORY = _msg_store
_ACTIVE_STORE = None
_ALLOW_NONCANONICAL = False       # per-call opt-in (fire_resume(allow_noncanonical=True))


def _set_active_store(store):
    global _ACTIVE_STORE
    _ACTIVE_STORE = store


def _store_is_canonical(store):
    import approval_config
    p = getattr(store, "db_path", None)
    if p is None:
        return True                              # unknown shape: never block legacy callers
    return os.path.realpath(str(p)) == os.path.realpath(str(approval_config.DB_PATH))


def _real_seam_refusal():
    """None if the real side-effect seam may run; else the refusal reason."""
    if os.environ.get("APPROVAL_RESUME_BLOCK_REAL_SEAMS") == "1":
        return "real_seams_blocked_by_env"
    if _ALLOW_NONCANONICAL or os.environ.get("APPROVAL_RESUME_ALLOW_NONCANONICAL") == "1":
        return None
    if _ACTIVE_STORE is not None and not _store_is_canonical(_ACTIVE_STORE):
        return "noncanonical_store_refused"
    return None


def _refuse_seam(seam, row=None, reason=None):
    rid = (row or {}).get("id"); agent = (row or {}).get("from_agent")
    print(f"[approval_resume] REFUSED real {seam} for {rid} -> {agent}: {reason} "
          f"(store={getattr(_ACTIVE_STORE, 'db_path', None)})", file=sys.stderr)
    if row is not None:
        _log_delivery(row, f"refused_{seam}", False, reason=reason)

def _log_delivery(row, lane, ok, reason=None, via=None):
    """R7b telemetry: emit one delivery-confirmation line for a recorded answer.
    Best-effort — a telemetry hiccup must NEVER break delivery."""
    try:
        import answer_telemetry
        answer_telemetry.log_delivery(row, lane=lane, ok=ok, reason=reason, via=via)
    except Exception as e:  # noqa: BLE001
        print(f"[approval_resume] telemetry emit failed for "
              f"{row.get('id')}: {e}", file=sys.stderr)

def _pane_resume_body(row):
    """The answer text delivered to the agent (identical on inject + row)."""
    return (f"[APPROVAL RESOLVED {row['id']}] Your request '{row['question']}' "
            f"was answered: {row['answer']} ({row['answer'].upper()})"
            + (f" — note: {row['answer_text']}" if row.get('answer_text') else "")
            + f". Act on it, then run: python3 {APPROVAL_CLI} ack {row['id']} --from {row['from_agent']}")

def _human_task_resume_body(row):
    """§Q1 house-style digest for a resolved human_task (matches
    [APPROVAL RESOLVED] shape): done -> 'the operator completed', cant -> "the operator couldn't".
    the operator's optional note is appended only when he wrote one. Ends with the ack
    instruction, mirroring _pane_resume_body."""
    verb = "the operator completed" if row.get("answer") == "done" else "the operator couldn't"
    subj = row.get("block_task") or row.get("question") or "(task)"
    return (f"[HUMAN-TASK {row['id']}] {verb}: {subj}"
            + (f" — note: {row['answer_text']}" if row.get('answer_text') else "")
            + f". Act, then run: python3 {APPROVAL_CLI} ack {row['id']} --from {row['from_agent']}")


def _fire_human_task_resume(row, store, msg_send=None, resolve=None, inject=None):
    """Deliver the human_task resolution to the parked from_agent — dual delivery
    (durable msg_store row FIRST try only + verified live-head inject), mirroring
    _fire_pane_resume, but with the §Q1 digest body. Seams injectable for tests.
    Returns a delivery-outcome dict."""
    _set_active_store(store)
    outcome = {"id": row["id"], "injected": False, "reason": None, "session": None}
    body = _human_task_resume_body(row)
    if not row.get("resumed_at") and not row.get("last_attempt_at"):
        (msg_send or _default_msg_send)(row, body)
    resolve = resolve or _default_resolve
    inject = inject or _default_inject
    session, reason = resolve(row["from_agent"])
    outcome["session"] = session
    if session:
        ok, info = inject(session, body)
        outcome["injected"] = bool(ok)
        outcome["reason"] = "delivered" if ok else ((info or {}).get("reason", "inject_refused") if isinstance(info, dict) else "inject_refused")
    else:
        outcome["reason"] = reason or "no-live-head"
    return outcome


def _default_msg_send(row, body):
    """Durable record: the msg_store row (audit + watchdog backstop)."""
    if _msg_store is _REAL_MSG_STORE_FACTORY:
        r = _real_seam_refusal()
        if r:
            _refuse_seam("msg_send", row, r)
            return None
    _msg_store().send(from_agent="approval-loop", to_agent=row["from_agent"],
                      type="approval_resolved", subject=f"Approval {row['id']} resolved",
                      body=body, priority="high", metadata={"approval_id": row["id"]})

def _fallback_msg_send(row, body):
    """R7b durable FALLBACK record (menu_gone: the in-agent keypress delivery was
    not registered). Marked delivered_via='fallback_durable' in the row metadata
    so telemetry/audit can count fallback firings distinctly from a normal
    authored-menu delivery (all-model-parity rider 2) — an invisible fallback
    would mask the primary lane's health."""
    if _msg_store is _REAL_MSG_STORE_FACTORY:
        r = _real_seam_refusal()
        if r:
            _refuse_seam("fallback_msg_send", row, r)
            return None
    _msg_store().send(from_agent="approval-loop", to_agent=row["from_agent"],
                      type="approval_resolved",
                      subject=f"Approval {row['id']} resolved (fallback)",
                      body=body, priority="high",
                      metadata={"approval_id": row["id"],
                                "delivered_via": "fallback_durable"})

def _default_resolve(agent_id):
    """Resolve from_agent -> live head session AT send-time (R2 primitive)."""
    from lineage_resolve import resolve_live_head
    return resolve_live_head(agent_id)

def _default_inject(session, text):
    """Verified bracketed-paste inject into a live pane via the gateway."""
    r = _real_seam_refusal()
    if r:
        _refuse_seam("inject", {"id": None, "from_agent": session} , r)
        return False, {"reason": r}
    import watch_gateway
    return watch_gateway.verified_inject(session, text)

def _f3_row_status(row, store):
    """F3 guard status resolution: a fresh `store.get` WINS (TOCTOU closure —
    the C5b canary held an 'answered' snapshot of a row whose DB state had gone
    stale_target) -> else the passed row's own 'status' -> else None
    (UNRESOLVABLE; the caller must FAIL CLOSED — verifier binding correction
    msg_2226ca2d: a forced invocation that cannot resolve a status IS the
    canary's reproduction shape, never a legitimate delivery)."""
    rid = (row or {}).get("id")
    if store is not None and rid:
        try:
            fresh = store.get(rid)
        except Exception as e:  # noqa: BLE001 — an unreadable store must not crash the loop
            print(f"[approval_resume] F3 guard: status re-read failed for {rid}: {e}",
                  file=sys.stderr)
            fresh = None
        if fresh:
            return fresh.get("status")
    return (row or {}).get("status")


def _f3_refuse_if_not_answered(row, store, where):
    """R8/F3 in-function status guard (SPEC_all-model-parity §3.1 F3, spec :98):
    pre-R8 the only fence on `stale_target` was SELECTION (answered_unacked) +
    the ack() clause — a DIRECT forced fire_resume/_fire_pane_resume invocation
    was status-agnostic and INJECTED (canary C5b, twice-verified). This is the
    defense-in-depth layer INSIDE the fire path: a non-'answered' row is refused
    LOUDLY — no keypress, no inject, no durable send, no store mutation.

    Returns the refusal outcome dict, or None when the fire may proceed.
    FAIL-CLOSED on unknown: a row whose status cannot be resolved (no store row
    AND no status field) is refused with the DISTINCT reason
    'status_unresolvable' — that shape is exactly a forced synthetic invocation
    (the canary's reproduction path); every production caller passes a
    store-resident row, and test fixtures carry an explicit status."""
    rid = (row or {}).get("id")
    status = _f3_row_status(row, store)
    if status is None:
        print(f"[approval_resume] F3 guard ({where}): REFUSING fire on {rid} — "
              f"status_unresolvable (no store row, no status field). A forced "
              f"invocation that cannot prove 'answered' never injects or sends.",
              file=sys.stderr)
        return {"id": rid, "injected": False, "reason": "status_unresolvable",
                "status": None, "session": None}
    if status == "answered":
        return None
    print(f"[approval_resume] F3 guard ({where}): REFUSING fire on {rid} — "
          f"status={status!r} is not 'answered'. A stale_target/pending/terminal "
          f"row never injects or sends (forced-invocation defense).",
          file=sys.stderr)
    return {"id": rid, "injected": False, "reason": "not_answered",
            "status": status, "session": None}


def _fire_pane_resume(row, store, msg_send=None, resolve=None, inject=None):
    """R3 pane path: write the durable msg_store row, THEN resolve from_agent ->
    live head and verified-inject the answer into that pane. No live head / pane
    refuses (busy) -> the durable row + watchdog is the backstop (never inject
    blind). Seams (msg_send/resolve/inject) are injectable for hermetic tests.
    Returns a delivery-outcome dict for observability."""
    _set_active_store(store)
    refused = _f3_refuse_if_not_answered(row, store, "_fire_pane_resume")
    if refused is not None:
        return refused
    outcome = {"id": row["id"], "injected": False, "reason": None, "session": None}
    body = _pane_resume_body(row)
    # 1) DURABLE record first -- never lose the answer even if inject fails.
    #    FIRST try only (SLA spec: cheap per-beat retries must not spam the inbox).
    if not row.get("resumed_at") and not row.get("last_attempt_at"):
        (msg_send or _default_msg_send)(row, body)
    # 2) DELIVERY -- resolve to the LIVE HEAD (post-rotation aware) + inject.
    resolve = resolve or _default_resolve
    inject = inject or _default_inject
    session, reason = resolve(row["from_agent"])
    outcome["session"] = session
    if session:
        ok, info = inject(session, body)
        outcome["injected"] = bool(ok)
        if ok:
            outcome["reason"] = "delivered"
        else:
            outcome["reason"] = (info or {}).get("reason", "inject_refused") \
                if isinstance(info, dict) else "inject_refused"
    else:
        # No live head -> keep the row; watchdog re-fires; do NOT inject blind.
        outcome["reason"] = reason or "no-live-head"
    return outcome



def _is_cheap(outcome):
    """SLA spec 2026-08-14: busy / no-live-head = nothing entered the pane —
    a free retry that must NOT count toward escalation."""
    if outcome.get("injected"):
        return False
    return outcome.get("session") is None or outcome.get("reason") == "busy"


def _menu_transport():
    """The gateway owns tmux + the menu detector; import lazily (it imports us)."""
    import watch_gateway
    return watch_gateway

def _menu_dict(row):
    """The row's menu JSON as a dict (menu rows store it as a JSON string)."""
    import json as _json
    menu = row.get("menu")
    if isinstance(menu, str):
        try:
            menu = _json.loads(menu)
        except ValueError:
            menu = None
    return menu if isinstance(menu, dict) else {}


def _option_label(menu, key, row):
    """The chosen option's human label (matched on option_n), for the inject
    digest. Falls back to the raw answer / answer_text."""
    opt = next((o for o in (menu.get("options") or [])
                if isinstance(o, dict) and o.get("n") == key), {})
    return (opt.get("label") or opt.get("text") or row.get("answer")
            or row.get("answer_text") or f"option {key}")


def _menu_digest_body(row):
    """The answer digest injected into an authored decision's creator live head:
    the question + chosen option label + any answer_text + ack instruction."""
    menu = _menu_dict(row)
    label = _option_label(menu, row.get("option_n"), row)
    q = menu.get("question") or row.get("question") or "(decision)"
    return (f"[DECISION ANSWERED {row['id']}] '{q}' -> {label}"
            + (f" — note: {row['answer_text']}" if row.get('answer_text') else "")
            + f". Act on it, then run: python3 {APPROVAL_CLI} ack {row['id']} --from {row['from_agent']}")


def _menu_batch_digest_body(row, answers):
    """condition-6 delivery digest for a multi-part answer batch: the per-part
    chosen option LABELS (resolved from the hydrated parts[]), plus any own-words
    text and the ack instruction — the body injected into from_agent's live head
    (and written durably) when the pane replay can't deliver the batch."""
    menu = _menu_dict(row)
    parts = menu.get("parts") if isinstance(menu.get("parts"), list) else []
    by_index = {}
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("index"), int):
            by_index[p["index"]] = p
    seg = []
    for a in (answers or []):
        if not isinstance(a, dict):
            continue
        pi = a.get("part")
        part = by_index.get(pi, {})
        label = part.get("tab_label") or part.get("question") or f"part {pi}"
        opts = {str(o.get("n")): o.get("label", "")
                for o in (part.get("options") or []) if isinstance(o, dict)}
        chosen = [opts.get(str(n), str(n)) for n in (a.get("ns") or [])]
        if a.get("text"):
            chosen.append(f"\u201c{a['text']}\u201d")
        seg.append(f"[{label}: {', '.join(chosen) if chosen else '(none)'}]")
    q = menu.get("question") or row.get("question") or "(multi-part decision)"
    return (f"[MULTI-PART DECISION ANSWERED {row['id']}] '{q}' -> "
            + " ".join(seg)
            + f". Act on it, then run: python3 {APPROVAL_CLI} ack {row['id']} --from {row['from_agent']}")


def fire_menu_batch_resume(row, answers, store=None, msg_send=None,
                           resolve=None, inject=None, via=None):
    """condition-6 DELIVERY fallback for a durable-first multi-part submit: the
    answer batch is ALREADY persisted (record_batch_answer) — this SURFACES it
    for delivery when the live-pane replay could not (menu_gone / dry-run / no
    arm). Mirrors _fire_menu_inject: a durable msg_store row FIRST (first-try
    only), then resolve from_agent -> live head + verified-inject the per-part
    digest. Inject success acks the row (answered -> resumed); no live head /
    refusal keeps it 'answered' so the durable row + watchdog is the backstop —
    never a blind inject, never a lost answer. Seams injectable for tests."""
    rid = row["id"]
    body = _menu_batch_digest_body(row, answers)
    if not row.get("resumed_at") and not row.get("last_attempt_at"):
        (msg_send or _default_msg_send)(row, body)        # durable record first
    resolve = resolve or _default_resolve
    inject = inject or _default_inject
    session, reason = resolve(row["from_agent"])
    outcome = {"id": rid, "injected": False, "reason": reason, "session": session}
    if session:
        ok, info = inject(session, body)
        outcome["injected"] = bool(ok)
        if ok:
            outcome["reason"] = "delivered"
            if store is not None:
                store.ack(rid)                            # answered -> resumed
        else:
            outcome["reason"] = (info or {}).get("reason", "inject_refused") \
                if isinstance(info, dict) else "inject_refused"
    _log_delivery(row, "menu_batch_inject", outcome["injected"],
                  reason=outcome["reason"], via=via)
    return outcome


def _session_is_live(session, live_fn=None):
    """True iff `session` is a real, currently-live tmux session. Default reads
    tmux; injectable for tests. A null/empty session is never live."""
    if not session:
        return False
    if live_fn is not None:
        return session in live_fn()
    try:
        import watch_gateway
        r = watch_gateway._tmux("list-sessions", "-F", "#{session_name}")
        live = set((r.stdout or "").split()) if getattr(r, "returncode", 1) == 0 else set()
        return session in live
    except Exception:  # noqa: BLE001 -- unknown liveness -> not a keypress target
        return False


def _fire_menu_inject(row, store, msg_send=None, resolve=None, inject=None, via=None):
    """Authored/dead-source menu decision (source_session null or NOT a live
    pane): deliver the operator's answer by resolving from_agent -> live head and
    verified-injecting the answer DIGEST, plus the durable msg_store row.
    Mirrors _fire_pane_resume's dual delivery. Terminal on inject = 'resumed'
    (no agent ack will come for an authored decision); no live head / busy ->
    keep the row (backstop), status stays 'answered' so history still shows it
    and the watchdog can re-attempt. Seams injectable for hermetic tests."""
    _set_active_store(store)
    rid = row["id"]
    body = _menu_digest_body(row)
    # DURABLE record FIRST try only (SLA spec: cheap per-beat retries must not
    # spam the inbox). Mirrors _fire_pane_resume / coalesce. On the first
    # synchronous delivery the in-memory row has no resumed_at/last_attempt_at;
    # a watchdog re-fire hands a row that DOES, so the row is written exactly
    # once — load-bearing for the R7b menu_gone fallback, which re-enters this
    # path every watchdog beat until the pane acks.
    if not row.get("resumed_at") and not row.get("last_attempt_at"):
        (msg_send or _default_msg_send)(row, body)    # durable record first
    resolve = resolve or _default_resolve
    inject = inject or _default_inject
    session, reason = resolve(row["from_agent"])
    outcome = {"id": rid, "injected": False, "reason": reason, "session": session}
    if session:
        ok, info = inject(session, body)
        outcome["injected"] = bool(ok)
        if ok:
            outcome["reason"] = "delivered"
            store.ack(rid)                            # answered -> resumed
        else:
            outcome["reason"] = (info or {}).get("reason", "inject_refused") \
                if isinstance(info, dict) else "inject_refused"
            # leave 'answered' -> watchdog retries; history still shows it
    # no live head -> keep row; never inject blind; leave 'answered' for retry
    _log_delivery(row, "authored_menu_inject", outcome["injected"],
                  reason=outcome["reason"], via=via)
    return outcome


def _fire_menu_resume(row, store, *, live_fn=None, msg_send=None,
                      resolve=None, inject=None):
    """kind=='menu': the answer is delivered by ONE of two paths, chosen by
    whether `source_session` is a REAL LIVE pane.

    (A) BRIDGED native pane-menu (source_session present AND live): the EXISTING
        KEYPRESS path — direct = one digit, free_text = three-phase — NEVER
        msg_store text. Terminal 'resumed' (self-ack), 'resolved_elsewhere', or
        'resume_failed'. Byte-for-byte the pre-existing behavior (menu-bridge).
    (B) AUTHORED / dead-source decision (source_session null, or set but NOT a
        live tmux session — GM/agent-authored, the operator answered in the app):
        resolve from_agent -> live head + verified-inject the answer digest +
        durable row (_fire_menu_inject). This is the DEC-1786664626 fix; Q1 =
        dead-but-set source also takes this path.

    All terminals keep the row out of a keypress re-fire; at-most-once for the
    keypress lives in path (A)."""
    _set_active_store(store)
    rid = row["id"]
    store.mark_resumed_fired(rid)                     # stamp the attempt first
    # NOTE: mark_resumed_fired writes the DB but does NOT mutate this in-memory
    # `row` dict. The R7b menu_gone fallback's first-try guard in
    # _fire_menu_inject relies on that invariant (in-memory resumed_at stays None
    # on the synchronous first delivery; a watchdog re-fire hands a re-fetched row
    # that has it set) — do not start merging the updated row back in here.
    menu = _menu_dict(row)
    session = menu.get("source_session")
    key = row.get("option_n")

    # Path (B): no live source pane to keypress -> deliver by live-head inject.
    if not _session_is_live(session, live_fn=live_fn) or not key:
        _fire_menu_inject(row, store, msg_send=msg_send, resolve=resolve,
                          inject=inject)
        return

    # Path (A): bridged native pane-menu -> KEYPRESS (unchanged behavior).
    opt = next((o for o in (menu.get("options") or [])
                if isinstance(o, dict) and o.get("n") == key), {})
    input_kind = opt.get("input_kind") or "direct"
    expect_q = menu.get("question")
    t = _menu_transport()
    if input_kind == "free_text":
        ok, info = t.menu_resume_free_text(session, key, row.get("answer_text") or "",
                                           expect_question=expect_q)
        lane = "menu_free_text"
    else:
        ok, info = t.menu_resume_keypress(session, key, expect_question=expect_q)
        lane = "menu_keypress"
    # R7b telemetry: submit-tap -> menu-disappear for the in-agent-menu lane.
    # This is the measurement that isolates the menu_gone drop class to this lane.
    _log_delivery(row, lane, ok, reason=(None if ok else info.get("reason")))
    if ok:
        store.ack(rid)                                # answered -> resumed
    elif info.get("reason") in ("menu_gone", "no_menu"):
        # R7b FALLBACK (the operator ruling 2026-08-25): the in-agent menu was already
        # GONE at delivery time, so the keypress delivery is NOT registered with
        # the agent. Do NOT silently drop the operator's recorded answer to
        # resolved_elsewhere (the auq-clobber bug). The row is now a dead-source
        # menu decision -> deliver the recorded answer DURABLY via _fire_menu_inject
        # (msg_store row + best-effort live-head inject, watchdog-backed). This is
        # a FALLBACK-ONLY channel: reached solely because the keypress could not
        # register — never in parallel with a successful keypress.
        #
        # RIDER 3 / F3 GUARD (binding, gm caveat): the durable fallback rides the
        # delivery path, and pre-R8 fire_resume is status-agnostic (F3: a forced
        # invocation on a non-answered row could inject). Guard HERE at the call
        # site (defense in depth — the R8 in-function status-guard is not landed)
        # so the fallback fires ONLY for a genuinely-answered row whose keypress
        # delivery went unregistered. A pending / stale_target / resolved_elsewhere
        # / expired row never delivers or injects — we do NOT widen the F3 hole.
        fresh = store.get(rid) or {}
        if fresh.get("status") == "answered":
            _fire_menu_inject(row, store, msg_send=(msg_send or _fallback_msg_send),
                              resolve=resolve, inject=inject, via="fallback_durable")
        # else: not a legitimately-answered row -> no inject, no durable send.
    else:
        phase = f" phase={info['phase']}" if info.get("phase") else ""
        store.mark_resume_failed(rid, f"{info.get('reason', 'unknown')}{phase}")

def fire_resume(row, store, allow_noncanonical=False):
    """Deliver the answer to the parked agent + record the attempt.
    allow_noncanonical=True is the explicit, logged opt-in for a deliberate
    caller running the real seams against a non-canonical store (gm
    msg_f1e50c66); it never persists past this call."""
    global _ALLOW_NONCANONICAL
    _set_active_store(store)
    if allow_noncanonical:
        print(f"[approval_resume] allow_noncanonical=True for {row.get('id')} -> {row.get('from_agent')} "
              f"(store={getattr(store, 'db_path', None)})", file=sys.stderr)
    _ALLOW_NONCANONICAL = bool(allow_noncanonical)
    try:
        return _fire_resume_inner(row, store)
    finally:
        _ALLOW_NONCANONICAL = False


def _fire_resume_inner(row, store):
    refused = _f3_refuse_if_not_answered(row, store, "fire_resume")
    if refused is not None:
        return refused
    if row.get("feature") == "demo":
        # B5 fixture card: no seat behind it. Ack right here so EVERY caller (gateway,
        # api answer path, watchdog) never resolves a pane, never injects, never sends a row.
        store.ack(row["id"])
        return {"id": row["id"], "injected": False, "reason": "demo-acked", "session": None}
    # R8 per-writer identity enforcement (SPEC §3/:179): shadow until the
    # 'approval_resume' writer is ARMED via its own the operator card; legacy rows
    # (no §1.1 identity fields) are exempt in both modes. Lazy import: an
    # absent/broken harness module must leave this path byte-identical.
    try:
        import r8_arming
        refused = r8_arming.enforce_resume_identity(row, store,
                                                    writer="approval_resume")
        if refused is not None:
            return refused
    except ImportError:
        pass                              # harness not landed => today-identical
    if row.get("kind") == "menu":
        _fire_menu_resume(row, store)
        return
    if row.get("kind") == "human_task":
        # §Q1: deliver the house-style digest (not _pane_resume_body's wording).
        # Cheap refusal (busy/no-live-head) -> stamp_retry (free retry, no
        # escalation count); real delivery / verify-fail -> mark_resumed_fired.
        outcome = _fire_human_task_resume(row, store)
        _log_delivery(row, "human_task_inject", outcome.get("injected"),
                      reason=outcome.get("reason"))
        if _is_cheap(outcome):
            store.stamp_retry(row["id"])
        else:
            store.mark_resumed_fired(row["id"])
        return
    if row["worker_kind"] == "pane":
        # R3: dual delivery -- durable msg_store row (FIRST try only) + verified
        # inject into the from_agent's LIVE HEAD. SLA spec 2026-08-14: cheap
        # refusals (busy/no-live-head) stamp_retry only; delivered/verify-failed
        # are REAL attempts.
        outcome = _fire_pane_resume(row, store)
        _log_delivery(row, "pane_inject", outcome.get("injected"),
                      reason=outcome.get("reason"))
        if _is_cheap(outcome):
            store.stamp_retry(row["id"])        # busy/no-head: free retry next beat
        else:
            store.mark_resumed_fired(row["id"]) # delivered or real verify-failure
        return
    # node path: nothing to inject — the orchestrator polls approval.py get and re-dispatches
    # via claude -p --resume <thread_key>; it acks on return. We only stamp the attempt.
    # TODO(node-pilot): orchestrator_client calls store.ack() after claude -p --resume returns;
    # until it exists, node rows escalate via the watchdog like pane rows.
    store.mark_resumed_fired(row["id"])

def deliver_discarded(row, store):
    """R8: one honest 'discarded without an answer' notify so a parked agent
    never waits forever on a dismissed ask (mirrors questionnaire_resume)."""
    body = (f"[APPROVAL DISCARDED {row['id']}] Your request '{row['question']}' was "
            f"DISMISSED without an answer. Do not keep waiting on it; proceed per "
            f"your own judgment or re-raise if it still matters.")
    _msg_store().send(from_agent="approval-loop", to_agent=row["from_agent"],
                      type="approval_discarded",
                      subject=f"Approval {row['id']} discarded",
                      body=body, priority="normal", metadata={"approval_id": row["id"]})

def escalate(row, store, cheap=False):
    """Alert the operator (DEC-1786690995 wording caveat): cheap=True -> undeliverable
    (target busy / no live head for the stuck window, still retrying every
    beat); cheap=False -> delivered N times, agent not acking."""
    import urllib.request
    from approval_config import ESCALATE_STUCK_MINUTES
    headers = {"Authorization": f"Bearer {ntfy_token()}",
               "X-Title": (f"[!] Approval {row['id']} undelivered" if cheap
                           else f"[!] Approval {row['id']} not acking"),  # ASCII-only: latin-1 headers
               "Priority": "high"}
    if cheap:
        data = (f"Answer for '{row['question']}' undeliverable to {row['from_agent']} "
                f"for {ESCALATE_STUCK_MINUTES}+ min (target busy or no live head). "
                f"Still retrying every beat.").encode()
    else:
        data = (f"Resume to {row['from_agent']} for '{row['question']}' fired "
                f"{row['resume_attempts']}x with no ack. Check the pane.").encode()
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"{NTFY_BASE}/{NTFY_APPROVALS_TOPIC}", data=data, headers=headers, method="POST"), timeout=10)
    except Exception as e:  # noqa: BLE001
        print(f"[approval_resume] escalate push failed for {row['id']}: {e}", file=sys.stderr)
    store.mark_escalated(row["id"])

def _is_authored_menu(row, live_fn=None):
    """True iff this is an AUTHORED menu decision (kind='menu' with NO live
    source pane) -> the coalescible live-head-inject path, not a keypress."""
    if row.get("kind") != "menu":
        return False
    menu = _menu_dict(row)
    return not _session_is_live(menu.get("source_session"), live_fn=live_fn) \
        or not row.get("option_n")


def coalesce_menu_injects(rows, store, *, resolve=None, inject=None,
                          msg_send=None):
    """COALESCE (DEC-1786664626 Q2/Q3, per-cycle): group AUTHORED menu decisions
    by their resolved live-head session and deliver ONE combined inject per
    session per cycle (not N sequential), marking every row in the group
    'resumed'. The durable msg_store row is STILL written per row (audit). No
    live head -> keep the rows (backstop); busy pane -> keep (retry next cycle).
    Returns a list of per-session outcome dicts."""
    resolve = resolve or _default_resolve
    inject = inject or _default_inject
    msg_send = msg_send or _default_msg_send

    groups = {}   # session -> list[(row, body)]
    no_head = []
    for row in rows:
        body = _menu_digest_body(row)
        if not row.get("resumed_at") and not row.get("last_attempt_at"):
            msg_send(row, body)                      # durable record, FIRST try only
        session, reason = resolve(row["from_agent"])
        if session:
            groups.setdefault(session, []).append((row, body))
        else:
            no_head.append((row, reason))

    outcomes = []
    for session, items in groups.items():
        if len(items) == 1:
            combined = items[0][1]
        else:
            lines = [f"[{len(items)} DECISIONS ANSWERED]"] + \
                    [f"  • {b.split('] ', 1)[-1]}" for (_r, b) in items]
            combined = "\n".join(lines)
        ok, info = inject(session, combined)
        c_reason = "delivered" if ok else ((info or {}).get("reason", "inject_refused")
                                           if isinstance(info, dict) else "inject_refused")
        if ok:
            for (row, _b) in items:
                store.mark_resumed_fired(row["id"])  # real delivered attempt
                store.ack(row["id"])                 # answered -> resumed
                _log_delivery(row, "coalesce_inject", True, reason="delivered")
        else:
            cheap = isinstance(info, dict) and info.get("reason") == "busy"
            for (row, _b) in items:
                (store.stamp_retry if cheap else store.mark_resumed_fired)(row["id"])
                _log_delivery(row, "coalesce_inject", False, reason=c_reason)
        outcomes.append({"session": session, "count": len(items),
                         "injected": bool(ok),
                         "reason": "delivered" if ok else
                         ((info or {}).get("reason", "inject_refused")
                          if isinstance(info, dict) else "inject_refused")})
    for (row, reason) in no_head:
        store.stamp_retry(row["id"])                 # cheap: no live head
        _log_delivery(row, "coalesce_inject", False, reason=reason or "no-live-head")
        outcomes.append({"session": None, "count": 1, "injected": False,
                         "reason": reason or "no-live-head", "id": row["id"]})
    return outcomes


def watchdog(store=None, *, live_fn=None):
    """Cron-driven. SLA spec 2026-08-14 (DEC-1786690995): cheap refusals retry
    every beat (55s guard band) so an idle target gets the answer within ~1 min
    of going idle; REAL attempts respect the 5-min ack window; escalation = 3
    real failures OR the 15-min wall-clock backstop, throttled 30 min, wording
    split cheap/real. Authored menu decisions still COALESCE per cycle."""
    store = store or ApprovalStore()
    now = datetime.now(timezone.utc)
    beat_cut = (now - timedelta(seconds=WATCHDOG_RETRY_BEAT_SECONDS)).isoformat()
    ack_cut = (now - timedelta(minutes=WATCHDOG_MINUTES)).isoformat()
    stuck_cut = (now - timedelta(minutes=ESCALATE_STUCK_MINUTES)).isoformat()
    esc_cut = (now - timedelta(minutes=ESCALATE_REPEAT_MINUTES)).isoformat()
    coalesce_batch = []
    for row in store.answered_unacked():
        if row.get("feature") == "demo":
            # B5 fixture card: nothing to deliver to (no seat behind it). Ack on answer so it
            # never injects, never escalates, never notifies.
            try:
                store.ack(row["id"])
            except Exception as e:  # noqa: BLE001
                print(f"[approval_resume] demo row {row['id']} ack failed: {e}", file=sys.stderr)
            continue
        if row.get("last_attempt_at") and row["last_attempt_at"] > beat_cut:
            continue                              # tried within a beat — wait
        if row["resumed_at"] and row["resumed_at"] > ack_cut:
            continue                              # real attempt landed — ack window
        recently_escalated = (row.get("escalated_at") and row["escalated_at"] > esc_cut)
        if row["resume_attempts"] >= WATCHDOG_MAX_ATTEMPTS:
            if not recently_escalated:
                try:
                    escalate(row, store, cheap=False)
                except Exception as e:
                    print(f"[approval_resume] watchdog row {row['id']} failed: {e}", file=sys.stderr)
            continue                              # real failures maxed — stop firing
        if (row.get("answered_at") and row["answered_at"] < stuck_cut
                and not recently_escalated):
            try:
                escalate(row, store, cheap=True)  # wall-clock backstop; retries continue
            except Exception as e:
                print(f"[approval_resume] watchdog row {row['id']} failed: {e}", file=sys.stderr)
            continue
        if _is_authored_menu(row, live_fn=live_fn):
            coalesce_batch.append(row)            # stamped per-outcome after the batch
            continue
        try:
            fire_resume(row, store)
        except Exception as e:
            print(f"[approval_resume] watchdog row {row['id']} failed: {e}", file=sys.stderr)
            # A crash before any stamp re-entered the row every beat, and the pane path's
            # "first try only" durable send fired again each time (duplicate rows, by effect
            # 2026-09-16). Throttle-stamp only: a crash is not a real delivery attempt.
            try:
                store.stamp_retry(row["id"])
            except Exception as e2:  # noqa: BLE001
                print(f"[approval_resume] watchdog row {row['id']} stamp failed: {e2}", file=sys.stderr)
            continue
    if coalesce_batch:
        try:
            coalesce_menu_injects(coalesce_batch, store)
        except Exception as e:  # noqa: BLE001
            print(f"[approval_resume] coalesce inject failed: {e}", file=sys.stderr)

def renotify_snoozed_human_tasks(store=None, notify_fn=None):
    """§2 dedicated snooze re-notify sweep (rides the existing approvals cron
    beat — no new daemon). For each PENDING kind='human_task' row whose
    snoozed_until <= now: re-notify the operator, then re-arm snoozed_until = now + 2h
    (keeps nagging every 2h; the card stays floored + visible, never disappears).
    Its OWN field, OWN scan, OWN cadence — no collision with notified_at /
    cron_backstop / watchdog (which never re-push a pending row)."""
    store = store or ApprovalStore()
    if notify_fn is None:
        from approval_notify import notify as notify_fn
    for row in store.snoozed_human_tasks_due():
        try:
            notify_fn(row["id"], store=store)
        except TypeError:
            notify_fn(row["id"])
        except Exception as e:  # noqa: BLE001
            print(f"[approval_resume] snooze re-notify failed for {row['id']}: {e}",
                  file=sys.stderr)
        store.snooze(row["id"])   # re-arm +2h


if __name__ == "__main__":
    watchdog()
    # §2 snooze re-notify rides the SAME cron tick as the approvals watchdog.
    try:
        renotify_snoozed_human_tasks()
    except Exception as e:  # noqa: BLE001 — the approvals sweep must not die on this
        print(f"[approval_resume] snooze re-notify sweep failed: {e}", file=sys.stderr)
    # Questionnaires ride the same cron beat (spec §4.3): re-fire submitted-unacked
    # digests (idempotent) so a busy-pane refusal at submit time is never final.
    try:
        import questionnaire_resume
        questionnaire_resume.watchdog()
        # G3 answer-resume (spec 2026-08-18): age escalation rides the SAME cron
        # tick — no new daemon. Shadow-first: [resume-retry-shadow] WOULD-lines
        # only until RESUME_RETRY_ARMED=1 (gm flips; live the operator-facing path).
        questionnaire_resume.age_escalation_sweep()
    except Exception as e:  # noqa: BLE001 — approvals sweep must never die on qnr issues
        print(f"[approval_resume] questionnaire watchdog failed: {e}", file=sys.stderr)
