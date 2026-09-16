"""Multi-beat COMPLETION path for autonomous T2 auto-retire (DEC-1787789209).

`execute_rotation` (execute.py) spawns a successor and runs the S3 confirm IN THE
SAME BEAT — but a real Claude successor needs MINUTES to read the handoff + commit a
readback, so the confirm HELD and the rotation stalled forever (three fail-safes:
same-beat confirm impossible; SKIP_OPEN_HOLD blocks re-entry; resolve_hold had zero
callers). This module supplies the missing piece: a `complete_held_rotation` that runs
the POST-spawn steps on the ALREADY-live successor on a LATER beat —
S3 re-confirm -> strict grade -> KILL GATE 1 -> KILL GATE 2 (graduation) -> CAP ->
PROMOTE-then-RETIRE -> (caller) resolve_hold — WITHOUT re-spawning.

Every safety property is enforced HERE, in code (not by convention), fail-CLOSED:

  * D5  PROMOTE-THEN-RETIRE: the successor becomes canonical FIRST; a crash between
        promote and retire leaves a recoverable two-heads, NEVER a headless lineage.
  * D6  grade-check + graduation on the pre-promote ALIAS name, STRICTLY BEFORE promote
        (else the D5 rename diverges the artifact-name lookup and a real pass is missed).
  * D8  IDEMPOTENCY via DURABLE 3-STORE COHERENCE (registry + agent-sessions +
        state/agents, resolved against the successor's provenance sid): all-successor ->
        residual retire only (never re-promote); torn -> fail-closed HOLD-escalate;
        all-predecessor -> proceed. Crash-safe (durable mid-beat), unlike the
        end-of-beat-only ledger.
  * D9  LIVENESS BY SID: bind to the live successor session_id `L`, never a same-NAME
        pane; a killed+name-reused pane (L != provenance sid) fail-closes to HOLD.
  * D10 CAP AT PROMOTE: the identity-swap (promote), not just the retire, draws from the
        shared per-beat + rolling-hour budget; a completion over-budget HOLDs BEFORE
        promoting. A residual retire re-counts CONDITIONALLY (only if promoted_at absent).
  * round-11 TOCTOU: capture the live sid `L` at ENTRY; re-assert live==L (and readback
        mtime >= L.start_time) immediately BEFORE the grade AND live==L BEFORE the
        promote — so an intra-beat crash+respawn cannot substitute a different session.

Every seam is injectable (live/store/confirm/grade/safety/graduation/promote/retire) so
tests exercise the full ordering + gates WITHOUT shelling out, promoting, or killing.
It NEVER spawns (the successor already exists) and NEVER kills before all gates pass;
the ledger mutations (mark_promoted / resolve_hold) are the caller's (beat wiring).
"""

# --- outcome/status constants -------------------------------------------------
COMPLETED = "completed"                              # promote + retire done (full)
RESIDUAL_RETIRED = "residual:retired"                # prior-beat promote finished (retire only)
HOLD_NOT_LIVE = "hold:successor-not-live"            # D9: no live session by sid
HOLD_NOT_READY = "hold:not-completion-ready"         # D1: readback stale/absent
HOLD_UNCONFIRMED = "hold:successor-unconfirmed"      # S3 could not confirm
HOLD_GRADE = "hold:grade-not-pass"                   # strict grade FAIL/REFUSED
HOLD_UNSAFE = "hold:safety-recheck-failed"           # KILL GATE 1 (dirty/attached/recent)
HOLD_NOT_GRADUATED = "hold:not-graduated"            # KILL GATE 2 graduation not passed
HOLD_CAP = "hold:cap-exhausted"                      # D10 shared budget exhausted (no promote)
HOLD_TOCTOU = "hold:successor-respawned"             # round-11: live sid != entry-captured L
HOLD_TORN = "hold:torn-promote-escalate"             # D8: 3 stores incoherent (torn promote)
HOLD_LIVE_MISMATCH = "hold:live-provenance-mismatch"  # D8/D9: promote-already-happened / crossed sid
HOLD_PROMOTE_FAILED = "hold:promote-failed"          # promote_successor returned not-ok
HOLD_RESIDUAL_UNSAFE = "hold:residual-disarmed-or-unsafe"  # round-6: residual retire gate failed

# --- A.3 RECOVERY REWORK (DEC-1787808620 / re-congruence DEC-1787817982 CONSENSUS):
# prompt-retire + pre-retire context-assist + LOUD escalate. NO rollback (the operator rejected
# rollback-to-predecessor as a RAM hog). TWO watches, both keyed on hold state; the
# predecessor retires within a BOUNDED window (on-progress OR at the pre-retire bound)
# so it can NEVER linger = RAM-safe by construction.
AWAITING_PROGRESS = "promoted:awaiting-progress"     # PRE-retire: predecessor ALIVE, watching progress
RETIRED_AWAITING_EFFECT = "watch:awaiting-effect-post-retire"  # POST-retire effect-watch (retired!=resolved)
AWAITING_EFFECT = "watch:awaiting-effect-post-retire"  # post-retire, within the effect window (== above)
RETIRED_ESCALATED = "retired-escalated:never-progressed"  # pre-retire bound hit -> retire-anyway + escalate
ESCALATED = "escalated:no-first-effect"              # post-retire timeout -> escalate; successor KEEPS RUNNING

# Bounds OWNED BY THE COMPLETION MACHINERY (beats/minutes) — reconcile must NOT age the
# two watch statuses (its hour-scale re_page/park backstops are both moot AND wrong here).
BEAT_INTERVAL_S = 900.0
PRE_RETIRE_BOUND_BEATS = 2      # not-progressing after context-assist for N beats -> retire-anyway + escalate
EFFECT_DEADLINE_BEATS = 2       # progressed but no first_effect within ~2 beats/30min -> escalate (keep running)

# Fleet-ledger classification of a completion trace status (retired != resolved in the
# rework): a WATCH status keeps the hold OPEN + advances its status for the next beat;
# a TERMINAL status resolves the hold. Everything else is a pre-promote HOLD (ages as
# today). AWAITING_PROGRESS == the pre-retire watch; RETIRED_AWAITING_EFFECT == post-retire.
WATCH_STATUSES = frozenset({AWAITING_PROGRESS, RETIRED_AWAITING_EFFECT})
TERMINAL_STATUSES = frozenset({COMPLETED, RESIDUAL_RETIRED, ESCALATED, RETIRED_ESCALATED})


def completion_ready(canary, successor, hold_row, *, now,
                     live_sid_fn, l_start_time_fn, readback_mtime_fn,
                     provenance_sid_fn, clock_skew_s=2.0) -> bool:
    """D1 completion-readiness predicate (fail-CLOSED). True ONLY when ALL hold:
      (a) the open hold names THIS successor;
      (b) the successor is LIVE BY SID (live_sid_fn resolves a session id `L`);
      (c) `<succ>.readback.md` mtime is STRICTLY NEWER than the hold-created time AND
          >= `L`.start_time (minus a small clock-skew floor) — proving the CURRENTLY
          -live session authored the readback (a file cannot predate its writer),
          defeating a killed-and-name-reused pane riding a dead pane's readback.
    Does NOT require `<succ>.comprehension.json` (round-8): that artifact is produced by
    the grade at Step 3 of `complete_held_rotation`, so requiring it here would deadlock.
    The D9 sid-provenance check only fires when the artifact ALREADY exists (a re-entry):
    if its stamped provenance sid != the live sid `L`, NOT ready. Any False -> the hold
    ages exactly as today (SKIP_OPEN_HOLD)."""
    if hold_row.get("successor") != successor:                       # (a)
        return False
    live = live_sid_fn(successor)                                    # (b) live by sid
    if not live:
        return False
    rb = readback_mtime_fn(successor)
    if rb is None or not (rb > hold_row["created_at"]):             # (c) newer than hold
        return False
    start = l_start_time_fn(live)
    if start is None or not (rb >= start - clock_skew_s):            # (c) newer than L.start
        return False
    prov = provenance_sid_fn(successor)                             # D9 (only if artifact exists)
    if prov is not None and prov != live:
        return False
    return True


def _grade_pass(grade) -> bool:
    return isinstance(grade, dict) and grade.get("disposition") == "PASS"


def _budget_ok(budget) -> bool:
    """D10 shared churn budget at the PROMOTE point. None => uncapped (test/no-cap
    path); otherwise require a free per-beat identity-swap slot AND hourly headroom
    AND the per-lineage cooldown clear."""
    if budget is None:
        return True
    return bool(budget.get("per_beat_slot")
                and budget.get("hourly_remaining", 0) > 0
                and budget.get("cooldown_clear"))


def _call_retire(retire_fn, canary, alias=None, promote_res=None):
    if retire_fn is None:
        return None
    try:
        return retire_fn(canary, alias, promote_res)
    except TypeError:
        try:
            return retire_fn(canary, alias)
        except TypeError:
            return retire_fn(canary)


def complete_held_rotation(canary, successor, hold_row, *, now,
                           live_sid_fn, l_start_time_fn, readback_mtime_fn,
                           comprehension_mtime_fn, provenance_sid_fn,
                           canonical_store_sids_fn, confirm_fn, grade_fn,
                           safety_fn, graduation_fn, promote_fn, retire_fn,
                           armed=True, budget=None, clock_skew_s=2.0,
                           completion_mode=False, first_effect=None,
                           auto_resume_fn=None, rotation_store=None,
                           rotation_id=None, execute_tmux_repin_fn=None):
    """Complete a held rotation on the ALREADY-live successor. Returns a trace:
        {canary, successor, status, steps:[...], promoted, retired,
         budget_consumed, recount}

    The caller (beat wiring) reads `promoted`/`retired` to mutate the ledger
    (mark_promoted after promote; resolve_hold after retire) and `budget_consumed`/
    `recount` to decrement the shared per-beat + hourly budget.

    Seams (all injectable):
      live_sid_fn(alias)           -> live session id `L` (or None) — D9 liveness by sid.
      l_start_time_fn(L)           -> `L`.start_time float (transcript first timestamp)
                                      or None (transcript absent => fail-closed).
      readback_mtime_fn(alias)     -> mtime of <alias>.readback.md (or None).
      comprehension_mtime_fn(alias)-> mtime of <alias>.comprehension.json (or None).
      provenance_sid_fn(alias)     -> the sid stamped in <alias>.comprehension.json
                                      (or None when the artifact does not yet exist).
      canonical_store_sids_fn(canary) -> (registry_sid, sessions_sid, state_sid): the
                                      sid each durable store currently maps the canonical
                                      name to (D8 3-store coherence).
      confirm_fn(canary, alias)    -> {"outcome": "confirmed"|"held"} (S3).
      grade_fn(canary, alias, sid) -> {"disposition": "PASS"|"FAIL"|"REFUSED", ...}
                                      (sid == the entry-captured L, round-11).
      safety_fn(canary)            -> (category, reason) (KILL GATE 1 fresh classify).
      graduation_fn(alias)         -> bool (KILL GATE 2, evaluated on the pre-promote alias).
      promote_fn(canary, alias)    -> {"ok": bool} (promote_successor: alias -> canonical).
      retire_fn(canary)            -> the predecessor kill.
      armed                        -> is the lineage armed (residual retire gate, round-6).
      budget                       -> shared churn budget (D10); None => uncapped.
    """
    steps = []
    trace = {"canary": canary, "successor": successor, "status": None,
             "steps": steps, "promoted": False, "retired": False,
             "budget_consumed": False, "recount": False}
    alias = successor           # the pre-promote alias name captured at ENTRY (D6/round-11)

    def done(status, **kw):
        trace["status"] = status
        trace.update(kw)
        return trace

    # Step 0/1 — capture the live sid `L` at ENTRY (round-11 TOCTOU anchor; D9).
    steps.append("capture_L")
    L = live_sid_fn(alias)
    if not L:
        return done(HOLD_NOT_LIVE)

    # --- D8 idempotency: durable 3-store coherence, tri-state, fail-closed ---
    prov = provenance_sid_fn(alias)                 # None if comprehension.json missing
    stores = canonical_store_sids_fn(canary)        # (registry_sid, sessions_sid, state_sid)
    if prov is None:
        # No provenance sid to compare. This does NOT prove pre-promote — a manual/torn
        # write could have moved a store to the live successor. Check the 3 stores
        # against the LIVE sid `L`: if ANY resolves canonical -> L, a promote already
        # happened -> FAIL-CLOSED (a blind proceed would re-run promote_successor and
        # mis-archive the already-promoted successor). Else proceed as first entry.
        if L in stores:
            return done(HOLD_LIVE_MISMATCH)
        route = "proceed"
    else:
        # Artifact exists: assert the live session == the stamped provenance sid
        # (round-9: catches a killed+name-reused pane re-authoring under the alias).
        if L != prov:
            return done(HOLD_LIVE_MISMATCH)
        matches = [s == prov for s in stores]
        if all(matches):
            route = "residual"          # promote COMPLETE -> retire only, never re-promote
        elif not any(matches):
            route = "proceed"           # not yet promoted -> first entry
        else:
            return done(HOLD_TORN)       # TORN promote -> escalate, neither retire nor promote

    # --- RESIDUAL: a prior beat promoted but crashed/held before the retire (D3/D8) ---
    if route == "residual":
        steps.append("residual")
        # The residual retire is a REAL kill -> re-check armed AND a fresh KILL GATE 1
        # (round-6): disarmed since the promote, or a human attached/dirty -> HOLD, never
        # blind-retire (leaving the two-heads is the fail-safe direction).
        if not armed:
            return done(HOLD_RESIDUAL_UNSAFE)
        cat, _reason = safety_fn(canary)
        steps.append("safety_recheck")
        if cat != "SUPERSEDED_SAFE":
            return done(HOLD_RESIDUAL_UNSAFE)
        steps.append("retire")
        retire_fn(canary)
        # D10/round-7: re-consume one budget unit IFF the promote-beat ledger was lost
        # (promoted_at absent). If present, the swap was already counted -> re-count zero.
        recount = hold_row.get("promoted_at") is None
        return done(RESIDUAL_RETIRED, retired=True, recount=recount,
                    budget_consumed=recount)

    # --- PROCEED (all-predecessor, first entry): run the full gated completion ---
    # D1 readiness re-assert (readback committed AFTER hold-created AND >= L.start_time).
    rb = readback_mtime_fn(alias)
    if rb is None or not (rb > hold_row["created_at"]):
        return done(HOLD_NOT_READY)
    start = l_start_time_fn(L)
    if start is None or not (rb >= start - clock_skew_s):
        return done(HOLD_NOT_READY)

    # Step 2 — S3 confirm-and-correct (the successor now has real evidence).
    steps.append("confirm")
    if (confirm_fn(canary, alias) or {}).get("outcome") != "confirmed":
        return done(HOLD_UNCONFIRMED)

    # Step 3 — grade. RE-ASSERT (check-at-use, round-11) live==L AND readback fresh
    # BEFORE grading, and pass EXACTLY the entry-captured L to the grader so the stamped
    # provenance sid is the entry identity (never a respawned empty pane).
    if live_sid_fn(alias) != L:
        return done(HOLD_TOCTOU)
    rb2 = readback_mtime_fn(alias)
    if rb2 is None or not (rb2 >= start - clock_skew_s):
        return done(HOLD_TOCTOU)
    steps.append("grade")
    if not _grade_pass(grade_fn(canary, alias, L)):
        return done(HOLD_GRADE)
    # Two-artifact same-generation coherence: comprehension.json AND readback.md BOTH >
    # hold-created (DIFFERENT authorities — a successor must not ride an earlier strict
    # grade with a stale current readback).
    comp = comprehension_mtime_fn(alias)
    if comp is None or not (comp > hold_row["created_at"]):
        return done(HOLD_GRADE)

    # Step 4 — KILL GATE 1: fresh safety re-classify.
    cat, _reason = safety_fn(canary)
    steps.append("safety_recheck")
    if cat != "SUPERSEDED_SAFE":
        return done(HOLD_UNSAFE)

    # Step 5 — KILL GATE 2: graduation on the pre-promote ALIAS name, BEFORE promote (D6).
    steps.append("graduation")
    if not graduation_fn(alias):
        return done(HOLD_NOT_GRADUATED)

    # Step 5.5 — CAP GATE (D10): the shared per-beat + hourly budget, BEFORE promote.
    if not _budget_ok(budget):
        return done(HOLD_CAP)

    # Step 6 — RE-ASSERT LIVE(alias) == L STRICTLY BEFORE promote (check-at-use, round-11),
    # then PROMOTE (D5 promote-then-retire: the lineage always has a valid canonical head
    # before the predecessor is destructively killed).
    if live_sid_fn(alias) != L:
        return done(HOLD_TOCTOU)
    steps.append("promote")
    
    from scripts.continuity.rotation_store import RotationState
    
    # State projection Integration: READY_TO_CUTOVER -> CUTOVER_COMMITTED
    if rotation_store and rotation_id:
        rot = rotation_store.get_rotation(rotation_id)
        if rot and rot["state"] not in RotationState.terminal_states() and rot["state"] != RotationState.CUTOVER_COMMITTED.value:
            # Maybe it's READY_TO_CUTOVER
            pass

    try:
        promote_res = promote_fn(canary, alias, rotation_store=rotation_store, rotation_id=rotation_id)
    except TypeError:
        promote_res = promote_fn(canary, alias)
    if not (promote_res or {}).get("ok"):
        return done(HOLD_PROMOTE_FAILED)
        
    trace["promoted"] = True
    trace["budget_consumed"] = True
    



    # --- COMPLETION MODE (A.3 rework): DEFER the retire, but only to the PROGRESSING
    # watch (NOT a rollback net). The predecessor stays ALIVE (not retired) so it can
    # answer a pre-retire context-assist if the successor struggles; a LATER beat runs
    # complete_progressing_watch, which retires PROMPTLY once the successor is confirmed
    # progressing (or retires-anyway + escalates at the bound — never lingers). Item#4
    # auto-resume injects the declared first_effect so the successor starts working now.
    if completion_mode:
        steps.append("auto_resume_inject")
        if auto_resume_fn is not None:
            auto_resume_fn(canary, alias, first_effect)
        return done(AWAITING_PROGRESS, promoted=True, retired=False,
                    budget_consumed=True, first_effect=first_effect)

    # Step 7 — RETIRE the predecessor (the real kill), inside the consumed budget.
    steps.append("retire")
    retire_res = _call_retire(retire_fn, canary, alias, promote_res)
    trace["retired"] = True
    
    # Postcondition Verification fail-closed
    if retire_res:
        receipt = retire_res.get("receipt", {})
        if receipt and not receipt.get("postcondition_verified", False):
            # HOLD since postcondition failed
            return done("hold:retire-postcondition-failed")
            
    if rotation_store and rotation_id:
        rot = rotation_store.get_rotation(rotation_id)
        if rot:
            rotation_store.transition(rotation_id, rot["state_version"], RotationState.RETIRE_COMMITTED, retire_receipt_json=retire_res)

    # Step 7.5 - CANONICAL_REPINNED
    steps.append("repin")
    if execute_tmux_repin_fn:
        repin_res = execute_tmux_repin_fn(canary, alias, L, armed=armed)
        receipt = repin_res.get("receipt", {})
        if receipt and not receipt.get("verified_after_rename", False):
            return done("hold:repin-failed")
            
        if rotation_store and rotation_id:
            rot = rotation_store.get_rotation(rotation_id)
            if rot:
                rotation_store.transition(rotation_id, rot["state_version"], RotationState.CANONICAL_REPINNED, repin_receipt_json=repin_res)
    
    if rotation_store and rotation_id:
        rot = rotation_store.get_rotation(rotation_id)
        if rot:
            rotation_store.transition(rotation_id, rot["state_version"], RotationState.COMPLETE)

    # Step 8 — resolve_hold is the caller's (beat wiring) job.
    return done(COMPLETED, promoted=True, retired=True, budget_consumed=True)


def complete_progressing_watch(canary, successor, hold_row, *, now,
                               progressing_fn, context_assist_fn, retire_fn,
                               escalate_fn, predecessor_live_fn=None,
                               beat_interval_s=BEAT_INTERVAL_S,
                               pre_retire_bound_beats=PRE_RETIRE_BOUND_BEATS):
    """PRE-retire progressing-watch (A.3 rework). Runs on the beats AFTER a completion-
    mode promote while the predecessor is STILL ALIVE (hold status AWAITING_PROGRESS),
    until the predecessor is retired. RAM-safe: the predecessor retires within a BOUNDED
    window and can never linger.

      * CONFIRMED PROGRESSING (positive task artifact) -> PROMPT retire (D5 order) +
        transition to the effect-watch. status RETIRED_AWAITING_EFFECT.
      * STRUGGLING, within the bound -> fire pre-retire CONTEXT-ASSIST to the still-alive
        predecessor; predecessor NOT retired; keep watching. status AWAITING_PROGRESS.
      * NOT progressing PAST the bound (context-assist did not help) -> RETIRE the
        predecessor ANYWAY (the operator RAM-first: no linger) + escalate LOUDLY. Never reverts.
        status RETIRED_ESCALATED.

    Seams (all injectable):
      progressing_fn(successor)     -> bool: POSITIVE task-directed artifact (the SOLE
                                      gate before the predecessor is destroyed).
      context_assist_fn(canary, successor): route the pre-retire enrichment to the
                                      still-alive predecessor (fires only when struggling).
      retire_fn(canary)             -> the predecessor kill.
      escalate_fn(canary, successor, ctx): LOUD, unmissable human surface (never silent).
      predecessor_live_fn(canary)   -> bool (optional): predecessor still alive (assist
                                      only routes to a LIVE predecessor).
    """
    steps = ["progressing_watch"]
    trace = {"canary": canary, "successor": successor, "status": None, "steps": steps,
             "promoted": False, "retired": False, "budget_consumed": False,
             "escalated": False}

    def done(status, **kw):
        trace["status"] = status
        trace.update(kw)
        return trace

    if progressing_fn(successor):
        # confirmed progressing on real task-work -> retire the predecessor PROMPTLY.
        steps.append("retire")
        _call_retire(retire_fn, canary, successor)
        return done(RETIRED_AWAITING_EFFECT, retired=True)

    # struggling: has the pre-retire bound elapsed?
    promoted_at = hold_row.get("promoted_at")
    past_bound = (promoted_at is not None
                  and (now - promoted_at) > pre_retire_bound_beats * beat_interval_s)
    if not past_bound:
        # fire the pre-retire context-assist to the LIVE predecessor, keep watching.
        from scripts.lineage_daemon.recovery import maybe_context_assist
        steps.append("context_assist")
        maybe_context_assist(
            canary, successor, struggling=True,
            predecessor_live_fn=(predecessor_live_fn or (lambda c: True)),
            assist_fn=lambda p, s: context_assist_fn(p, s))
        return done(AWAITING_PROGRESS)

    # never-progressing past the bound -> RETIRE ANYWAY (RAM-first) + escalate LOUD.
    steps.append("retire")
    _call_retire(retire_fn, canary, successor)
    steps.append("escalate")
    escalate_fn(canary, successor, {"reason": "never-progressed-after-context-assist",
                                    "beats": pre_retire_bound_beats})
    return done(RETIRED_ESCALATED, retired=True, escalated=True)


def complete_effect_verify(canary, successor, hold_row, *, now,
                           effect_check_fn, escalate_fn, retire_fn=None,
                           beat_interval_s=BEAT_INTERVAL_S,
                           effect_deadline_beats=EFFECT_DEADLINE_BEATS):
    """POST-retire effect-watch (A.3 rework). Runs after the predecessor has ALREADY
    been retired (hold status watch:awaiting-effect-post-retire). NO rollback (the operator
    rejected it) — the predecessor is gone, nothing to revert to:

      * first_effect PRESENT        -> RESOLVE the watch. status COMPLETED. (No retire
                                       here — the predecessor already retired promptly.)
      * ABSENT, within ~2 beats     -> keep watching. status AWAITING_EFFECT.
      * ABSENT, past ~2 beats/30min -> escalate_fn LOUDLY + surface; the successor KEEPS
                                       RUNNING (never killed, never reverted). status ESCALATED.

    Seams:
      effect_check_fn()             -> bool: does the successor's first_effect exist?
      escalate_fn(canary, successor, ctx): LOUD, unmissable human surface.
      retire_fn                     -> unused here (kept for a uniform provider signature).
    """
    steps = ["effect_verify"]
    trace = {"canary": canary, "successor": successor, "status": None, "steps": steps,
             "promoted": False, "retired": False, "budget_consumed": False,
             "escalated": False}

    def done(status, **kw):
        trace["status"] = status
        trace.update(kw)
        return trace

    if effect_check_fn():
        # the successor produced its first_effect -> the completion is proven.
        return done(COMPLETED)

    promoted_at = hold_row.get("promoted_at")
    within_window = (promoted_at is not None
                     and (now - promoted_at) <= effect_deadline_beats * beat_interval_s)
    if within_window:
        return done(AWAITING_EFFECT)

    # no first_effect within the window -> LOUD escalation; successor KEEPS RUNNING.
    steps.append("escalate")
    escalate_fn(canary, successor, {"reason": "no-first-effect-within-window",
                                    "beats": effect_deadline_beats})
    return done(ESCALATED, escalated=True)


# ======================================================================
# REAL-SEAM PROVIDER FACTORY — wrap the EXISTING live seams into a
# completion_provider(canary, hold_row, *, armed, budget, now) -> trace that
# plan_fleet's D3 branch consumes. Every sub-seam is injectable (tests drive a
# sandbox); the defaults wrap promote_successor / auto_grade / self_retire_gate /
# execute.default_safety_recheck / executors.plan_retire / enrich.live_sid — NEVER
# reimplemented. INERT-UNTIL-ARMED: plan_fleet only calls the provider for an armed
# lineage, so a dry beat with no armed+ready lineage never fires it.
# ======================================================================

def _mtime(path):
    import os
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _read_json_field(path, field):
    import json
    try:
        with open(path) as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj.get(field) if isinstance(obj, dict) else None


def _transcript_start_time(sid):
    """`L`.start_time (round-10) = the first transcript line WITH a present
    `timestamp` (line #0 is a snapshot with timestamp:None), as an epoch float.
    Exactly-one-match glob (matching s3_live_seams:272 / find_transcript_by_sid);
    absent/ambiguous/unparseable => None (fail-CLOSED / NOT-ready)."""
    import glob
    import json
    import os
    from datetime import datetime
    if not sid:
        return None
    hits = sorted(p for p in glob.glob(
        os.path.expanduser(f"~/.claude/projects/*/{sid}*.jsonl"))
        if "/subagents/" not in p)
    if len(hits) != 1:
        return None
    try:
        with open(hits[0]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = json.loads(line).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if ts:
                    try:
                        return datetime.fromisoformat(
                            str(ts).replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
    except OSError:
        return None
    return None


def _canonical_store_sids(orchestra_dir, canary):
    """(registry_sid, sessions_sid, state_agents_sid) — the sid each DURABLE store
    currently maps the canonical name to (D8 3-store coherence). Each independently
    None on read failure (a None never == a real sid, so a torn read fail-closes)."""
    import json
    import os

    def _field(path, *keys):
        try:
            with open(path) as f:
                obj = json.load(f)
        except (OSError, ValueError):
            return None
        for k in keys:
            obj = obj.get(k) if isinstance(obj, dict) else None
        return obj if isinstance(obj, str) else None

    reg = _field(os.path.join(orchestra_dir, "registry.json"),
                 "agents", canary, "session_id")
    sess = _field(os.path.join(orchestra_dir, "state", "agent-sessions.json"),
                  canary, "session_id")
    state = _field(os.path.join(orchestra_dir, "state", "agents",
                                f"{canary}.json"), "session_id")
    return (reg, sess, state)


def _load_registry(orchestra_dir):
    import json
    import os
    try:
        with open(os.path.join(orchestra_dir, "registry.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --- Piece 2 (DEC-1787861488): KEY-1 auto_strict_grade first-class row ---------

def _shadow_module():
    """The SAME module object promote_successor's _key1_evidence_gate reads
    (`lineage_gate.shadow` off the scripts dir) — two import spellings would be
    two module objects and a row written to one would be invisible to the
    gate's default ledger."""
    import os
    import sys
    scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from lineage_gate import shadow as SH
    return SH


def _grader_commit():
    """The commit of the grader code actually running (C4 provenance) — the
    HEAD of the code tree this module executes from. None on any failure
    (recorded as null, never fabricated)."""
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        sha = (out.stdout or "").strip()
        return sha if out.returncode == 0 and sha else None
    except Exception:  # noqa: BLE001 -- provenance is best-effort, never invented
        return None


def _record_auto_strict_grade(canary, alias, grade_result, *, now, ledger=None):
    """Append the first-class KEY-1 row for a STRICT machine PASS, STRICTLY
    BEFORE promote (Piece 2, DEC-1787861488) — so `has_evidence_for()` passes
    naturally and the --shadow-skip-reason hatch stays reserved for genuine
    emergencies (a skip is an ABSENCE explanation; this is honest evidence).

    Preconditions (C4, the self-retire `_graduation_pass` hardening mirrored):
    the ARTIFACT is the authority — comprehension.json must exist, parse, and
    carry mode=="strict" AND pass==true, and the grade dict must be a strict
    PASS with a written artifact. ANYTHING less writes NOTHING (the gate then
    refuses exactly as today). Idempotent: an existing row naming this
    rotation short-circuits (one rotation, one datum). C3: the new kind is
    invisible to key1_status()/arming counters BY CONSTRUCTION (only
    kind=="rotation" rows count) — it clears the gate as an honest audit
    datum, never as arming credit."""
    import hashlib
    import json
    import os
    if not isinstance(grade_result, dict):
        return None
    if (grade_result.get("disposition") != "PASS"
            or grade_result.get("strict") is not True
            or grade_result.get("artifact_written") is not True):
        return None
    path = grade_result.get("artifact")
    if not path or not os.path.exists(path):
        return None
    try:
        raw = open(path, "rb").read()
        art = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(art, dict) or art.get("mode") != "strict" \
            or art.get("pass") is not True:
        return None
    SH = _shadow_module()
    rotation = f"{canary} <- {alias}"
    if SH.has_evidence_for(rotation, ledger=ledger):
        return None                              # one rotation, one datum
    return SH.append_row({
        "kind": "auto_strict_grade",
        "rotation": rotation,
        "comprehension_path": str(path),
        "comprehension_sha256": hashlib.sha256(raw).hexdigest(),
        "mode": "strict",
        "pass": True,
        "per_question_scores": art.get("per_question_scores"),
        "graded_at": now,
        "recorded_by": "completion_provider",
        "grader_commit": _grader_commit(),
    }, ledger=ledger)


def build_completion_provider(*, orchestra_dir=None, sessions_meta=None,
                              registry=None, dry=False, clock_skew_s=2.0,
                              confirm_fn=None, grade_fn=None, safety_fn=None,
                              graduation_fn=None, promote_fn=None, retire_fn=None,
                              live_sid_fn=None, l_start_time_fn=None,
                              readback_mtime_fn=None, comprehension_mtime_fn=None,
                              provenance_sid_fn=None, canonical_store_sids_fn=None,
                              completion_mode=False, first_effect_fn=None,
                              auto_resume_fn=None, effect_check_fn=None,
                              progressing_fn=None, context_assist_fn=None,
                              escalate_fn=None, predecessor_live_fn=None,
                              beat_interval_s=BEAT_INTERVAL_S,
                              effect_deadline_beats=EFFECT_DEADLINE_BEATS,
                              pre_retire_bound_beats=PRE_RETIRE_BOUND_BEATS,
                              timing_sink=None, shadow_ledger=None):
    """Assemble a `completion_provider(canary, hold_row, *, armed, budget, now)` that
    runs `complete_held_rotation` over the REAL live seams. Any sub-seam left None is
    built from the live sources; tests inject fakes for a hermetic sandbox. `dry` makes
    the promote/retire wrappers plan-only (promote(dry_run=True), retire un-armed).

    `completion_mode` (A.3 rework, DEC-1787808620 / re-congruence DEC-1787817982): the
    SEPARATE comprehension-gated completion path, dispatched 3-way by hold state:
      * promoted_at ABSENT  -> complete_held_rotation: gated promote + item#4 auto-resume,
        DEFER retire -> AWAITING_PROGRESS (predecessor kept ALIVE for the progressing-watch).
      * promoted_at PRESENT, status AWAITING_PROGRESS -> complete_progressing_watch:
        retire-on-confirmed-progress / pre-retire context-assist / bound -> retire-anyway+
        escalate. RAM-safe (predecessor retires within a bounded window, never lingers).
      * status RETIRED_AWAITING_EFFECT -> complete_effect_verify: first_effect present ->
        COMPLETED / timeout -> LOUD escalate, successor keeps running. NO rollback.
    completion_mode=False (default) preserves the spawn-style promote-then-retire path.

    Completion sub-seams (injectable; real defaults built below):
      first_effect_fn(canary)      -> the declared first_effect dict (from the handoff).
      auto_resume_fn(canary,alias,effect) -> inject the resume instruction on promote.
      effect_check_fn()            -> bool: does that first_effect exist yet?
      progressing_fn(successor)    -> bool: POSITIVE task artifact (the retire trigger).
      context_assist_fn(canary,successor) -> pre-retire enrichment to the live predecessor.
      escalate_fn(canary,successor,ctx)   -> LOUD human surface (via first_escalation_gate).
      predecessor_live_fn(canary)  -> bool: predecessor still alive (assist routing)."""
    import os
    od = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))
    handoffs = os.path.join(od, "state", "agent-handoffs")

    def _sessions():
        if sessions_meta is not None:
            return sessions_meta
        import json
        try:
            with open(os.path.join(od, "state", "agent-sessions.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _live_sid(alias):
        from scripts.lineage_daemon import enrich
        return enrich.live_sid((_sessions() or {}).get(alias) or {}) or None

    def _pred_sid(canary):
        return ((_sessions() or {}).get(canary) or {}).get("session_id")

    def _grade(canary, alias, sid):
        from scripts.lineage_daemon.auto_grade import auto_grade_successor
        return auto_grade_successor(alias, _pred_sid(canary),
                                    successor_sid=sid, strict=True)

    def _safety(canary):
        from scripts.lineage_daemon import execute as _ex
        return _ex.default_safety_recheck(canary, orchestra_dir=od)

    def _retire(canary, alias=None, promote_res=None):
        from scripts.lineage_daemon import executors as _ex

        # 1. Targets for predecessor retirement:
        targets = []
        if isinstance(promote_res, dict):
            report = promote_res.get("report") or promote_res
            if report.get("predecessor_archived"):
                targets.append(report["predecessor_archived"])
            for p in report.get("predecessors_marked") or []:
                if p not in targets:
                    targets.append(p)

        if not targets:
            try:
                reg = registry if registry is not None else _load_registry(od)
                for k, v in (reg.get("agents") or {}).items():
                    if k != canary and isinstance(v, dict) and v.get("succeeded_by") == canary:
                        targets.append(k)
            except Exception:
                pass

        res = {"retired": True} # Fallback
        for target in targets:
            res = _ex.plan_retire(target, armed=(not dry), orchestra_dir=od)

        if alias and (not dry):
            import subprocess
            subprocess.run(["tmux", "kill-session", "-t", canary], capture_output=True)
            subprocess.run(["tmux", "rename-session", "-t", alias, canary], capture_output=True)

        return res

    def _promote(canary, alias, trigger_evidence=None, rotation_store=None, rotation_id=None):
        # `live` (the effective live-sid seam) is bound below and resolved at CALL
        # time (this runs only inside provider(), after `live` is assigned). Step 6
        # of complete_held_rotation already re-asserted live == entry-captured L
        # immediately before calling this, so the fresh resolve is that same L.
        # `trigger_evidence` (Piece 1, DEC-1787861488): the S1 hold-open snapshot
        # from THIS hold row, threaded so G7 validates it against the STORE row
        # (C1) instead of demanding a live-detector re-read a quiescent
        # predecessor can never produce. None => hand-driven G7 path, unchanged.
        from scripts.promote_successor import promote, PromotionRefused
        try:
            res = promote(canary, alias, session_id=live(alias) or None, dry_run=dry,
                          trigger_evidence=trigger_evidence, rotation_store=rotation_store, rotation_id=rotation_id)
            return {"ok": True, "report": res}
        except PromotionRefused:
            return {"ok": False}
        except Exception:  # noqa: BLE001 -- any promote failure -> HOLD (both live)
            return {"ok": False}

    def _graduation(canary, alias):
        from scripts.lineage_daemon.self_retire_gate import is_graduated_autoretire
        reg = registry if registry is not None else _load_registry(od)
        seat = dict(((reg.get("agents") or {}).get(canary)) or {})
        seat.setdefault("agent_id", canary)
        seat["successor"] = alias
        return is_graduated_autoretire(seat)

    # --- completion-mode real defaults (item#4 auto-resume + effect-verify seams) ---
    def _default_first_effect(canary):
        """The declared first_effect from the predecessor's committed handoff (the
        SAME source s3_live_seams builds the confirm's first_effect from). None on
        any absence => the effect can never verify => a lossless rollback after N
        beats (fail toward restoring the resumable predecessor)."""
        try:
            from scripts.lineage_daemon.s3_live_seams import _load_committed_handoff
            h = _load_committed_handoff(canary, od) or {}
            return h.get("first_effect")
        except Exception:  # noqa: BLE001 -- absence => None (rollback-safe)
            return None

    def _default_effect_check(canary):
        eff = (first_effect_fn or _default_first_effect)(canary)
        if not eff:
            return False
        from scripts.focus_registry.effects import check_effect
        return bool(check_effect(eff, cwd=eff.get("cwd") or od).get("exists"))

    def _default_auto_resume(canary, alias, effect):
        """Item#4: inject the declared first_effect and next actions as the resume
        instruction into the now-canonical successor's pane so it resumes the lineage work.
        dry => no-op (plan-only). Best-effort: a send failure never breaks the promote."""
        if dry:
            return None
        try:
            from msg_store import MessageStore
            tgt = "your first_effect"
            if isinstance(effect, dict):
                tgt = effect.get("target") or effect.get("check") or "your first_effect"

            actions_str = ""
            try:
                from scripts.lineage_daemon.handoff_provider import read_committed_handoff
                h, _ = read_committed_handoff(canary, lineage_root=canary, orchestra_dir=od)
                if h and isinstance(h, dict) and h.get("next_3_actions"):
                    actions_str = "\nNext actions:\n" + "\n".join(f"- {a}" for a in h["next_3_actions"])
            except Exception:
                pass

            body = (f"You have been promoted to canonical for lineage {canary}. "
                    f"Resume the lineage work NOW — make real task-directed progress "
                    f"(edit the focus files) and produce your declared first_effect "
                    f"({tgt}).{actions_str}\n"
                    f"The predecessor retires as soon as you are progressing.")

            store = MessageStore()
            store.send(
                from_agent="lineage-daemon", to_agent=alias,
                type="lineage_auto_resume", priority="high",
                subject="[LINEAGE RESUME] you are canonical — start the lineage work now",
                body=body, source="fleet-beat")
            if alias != canary:
                store.send(
                    from_agent="lineage-daemon", to_agent=canary,
                    type="lineage_auto_resume", priority="high",
                    subject="[LINEAGE RESUME] you are canonical — start the lineage work now",
                    body=body, source="fleet-beat")

            try:
                import subprocess
                pane_target = f"{canary}:0"
                cmd = f"echo '[LINEAGE RESUME] Resuming task: {tgt}'\n"
                subprocess.run(["tmux", "send-keys", "-t", pane_target, cmd], capture_output=True)
            except Exception:
                pass
        except Exception:  # noqa: BLE001 -- resume is best-effort
            return None

    def _default_context_assist(canary, successor):
        """A.3 rework / ARM-PREP: real inject to the STILL-ALIVE predecessor. Delegates
        to the bound arm-seam (dry-safe, best-effort)."""
        from scripts.lineage_daemon.completion_arm_seams import build_context_assist_fn
        return build_context_assist_fn(od, dry=dry)(canary, successor)

    def _default_escalate(canary, successor, ctx):
        """A.3 rework / ARM-PREP: LOUD, unmissable the operator card via approval.py, first-
        escalation human-gated. Delegates to the bound arm-seam. Never breaks the beat."""
        try:
            from scripts.lineage_daemon.completion_arm_seams import build_escalate_fn
            return build_escalate_fn(od, dry=dry)(canary, successor, ctx)
        except Exception:  # noqa: BLE001 -- escalation must never break the beat
            return None

    def _default_progressing_for(canary, hold_row):
        """ARM-PREP: a repo-bound progressing_fn for THIS lineage — resolves the work
        repo from the registry (registry.agents[canary].cwd), the focus roots from the
        committed handoff, and the commit-delta baseline from the promote-stamp on the
        hold. POSITIVE-ARTIFACT-only; no repo => fail-closed to NOT-progressing."""
        from scripts.lineage_daemon.completion_arm_seams import (
            build_progressing_fn, resolve_repo)
        repo = resolve_repo(od, canary)
        focus_roots = None
        try:
            from scripts.lineage_daemon.s3_live_seams import _load_committed_handoff
            focus_roots = (_load_committed_handoff(canary, od) or {}).get(
                "file_roots_touched") or None
        except Exception:  # noqa: BLE001
            focus_roots = None
        baseline = (hold_row or {}).get("promote_baseline_sha")
        return build_progressing_fn(repo, focus_roots=focus_roots, baseline_sha=baseline)

    def _default_predecessor_live(canary):
        # the predecessor is the canonical row itself (pre prompt-retire it is alive).
        return bool(live(canary))

    # effective seams (injected wins over the real default)
    live = live_sid_fn or _live_sid
    start = l_start_time_fn or _transcript_start_time
    rbm = readback_mtime_fn or (
        lambda a: _mtime(os.path.join(handoffs, f"{a}.readback.md")))
    compm = comprehension_mtime_fn or (
        lambda a: _mtime(os.path.join(handoffs, f"{a}.comprehension.json")))
    prov = provenance_sid_fn or (
        lambda a: _read_json_field(
            os.path.join(handoffs, f"{a}.comprehension.json"), "provenance_sid"))
    stores = canonical_store_sids_fn or (lambda c: _canonical_store_sids(od, c))
    confirm = confirm_fn or (lambda c, s: {"outcome": "held"})  # no S3 seam => HOLD
    grade = grade_fn or _grade
    safety = safety_fn or _safety
    promote_seam = promote_fn or _promote
    retire = retire_fn or _retire
    resume_seam = auto_resume_fn or _default_auto_resume
    context_assist_seam = context_assist_fn or _default_context_assist
    escalate_seam = escalate_fn or _default_escalate
    pred_live_seam = predecessor_live_fn or _default_predecessor_live
    # progressing is bound PER-HOLD in the dispatch (it needs the promote-baseline sha
    # stamped on the hold); an injected progressing_fn overrides the repo-bound default.

    def _repo_head_for(canary):
        """The work-repo HEAD (the commit-delta baseline) stamped at the promote beat."""
        from scripts.lineage_daemon.completion_arm_seams import resolve_repo, repo_head
        repo = resolve_repo(od, canary)
        return repo_head(repo) if repo else None

    def _writer_for(canary, successor, now):
        """A per-rotation timing writer (spec §B). rotation_id accumulates across the
        beats of ONE rotation via the successor sid, so summarize() can report the
        inter-beat gap. Returns None when no timing_sink is wired (=> no wrapping =>
        byte-identical behavior)."""
        if timing_sink is None:
            return None
        from scripts.lineage_daemon import rotation_timing as _rt
        sid = live(successor) or "unknown"
        return _rt.make_writer(
            timing_sink, rotation_id=f"{canary}:{sid}", lineage_root=canary,
            canary=canary, successor=successor, successor_sid=sid,
            beat_id=int(now) if isinstance(now, (int, float)) else 0)

    def provider(canary, hold_row, *, armed, budget, now):
        successor = hold_row["successor"]
        grad = ((lambda alias: graduation_fn(alias)) if graduation_fn is not None
                else (lambda alias: _graduation(canary, alias)))

        # PURE-ADDITIVE timing (spec §B): wrap the seams the provider owns. `w` is None
        # when no timing_sink is wired -> `_t` is identity -> byte-identical behavior.
        w = _writer_for(canary, successor, now)

        def _t(seam, section, **kw):
            if w is None:
                return seam
            from scripts.lineage_daemon import rotation_timing as _rt
            return _rt.timed(seam, section, w, **kw)

        confirm_t = _t(confirm, "s3_sample1",
                       disposition_fn=lambda r: (r or {}).get("outcome"))

        # Piece 2 (DEC-1787861488): on a STRICT machine PASS, append the
        # first-class KEY-1 `auto_strict_grade` row at grade time — STRICTLY
        # BEFORE promote (grade is Step 3; promote is Step 6), so the KEY-1
        # gate passes on honest evidence instead of a --shadow-skip-reason.
        # A failed append is swallowed here and caught at the promote gate
        # (refuses -> HOLD, fail-closed); non-strict/failing grades write
        # NOTHING inside the recorder (C4).
        def _grade_recorded(c, a, sid):
            g = grade(c, a, sid)
            try:
                _record_auto_strict_grade(c, a, g, now=now, ledger=shadow_ledger)
            except Exception:  # noqa: BLE001 -- gate refuses at promote; never wedge grade
                pass
            return g

        grade_t = _t(_grade_recorded, "grade",
                     disposition_fn=lambda r: (r or {}).get("disposition"))
        safety_t = _t(safety, "safety_gate",
                      disposition_fn=lambda r: r[0] if isinstance(r, tuple) else None)
        grad_t = _t(grad, "graduation_gate", disposition_fn=lambda r: str(bool(r)))
        # Piece 1 (DEC-1787861488): the DEFAULT promote seam is bound PER-HOLD so
        # the S1 hold-open trigger snapshot rides from the hold row into G7's
        # store-validated evidence path. An INJECTED promote_fn keeps its 2-arg
        # contract untouched (tests / future wiring).
        if promote_fn is None:
            _te = hold_row.get("trigger_evidence")
            promote_eff = (lambda c, a, __te=_te: _promote(c, a, trigger_evidence=__te))
        else:
            promote_eff = promote_seam
        promote_t = _t(promote_eff, "promote",
                       disposition_fn=lambda r: "ok" if (r or {}).get("ok") else "failed")
        retire_t = _t(retire, "retire")
        resume_t = _t(resume_seam, "auto_resume_inject")
        escalate_t = _t(escalate_seam, "escalate")

        # A.3-rework completion dispatch (3-way, keyed on hold state):
        #   * promoted_at ABSENT           -> complete_held_rotation (gated promote +
        #     item#4 auto-resume, DEFER retire) -> AWAITING_PROGRESS.
        #   * promoted_at PRESENT, NOT yet retired (status AWAITING_PROGRESS) ->
        #     complete_progressing_watch (retire-on-progress / assist / bound-escalate).
        #   * retired (status RETIRED_AWAITING_EFFECT) -> complete_effect_verify
        #     (effect->COMPLETED / timeout->escalate; successor keeps running).
        if completion_mode and hold_row.get("promoted_at") is not None:
            if hold_row.get("status") == RETIRED_AWAITING_EFFECT:
                echk = effect_check_fn or (lambda: _default_effect_check(canary))
                echk_t = _t(echk, "effect_verify", disposition_fn=lambda r: str(bool(r)))
                return complete_effect_verify(
                    canary, successor, hold_row, now=now,
                    effect_check_fn=echk_t, escalate_fn=escalate_t, retire_fn=retire_t,
                    beat_interval_s=beat_interval_s,
                    effect_deadline_beats=effect_deadline_beats)
            # progressing is repo-bound PER-HOLD (needs the promote-baseline sha).
            prog_seam = progressing_fn or _default_progressing_for(canary, hold_row)
            prog_t = _t(prog_seam, "progressing_watch",
                        disposition_fn=lambda r: str(bool(r)))
            return complete_progressing_watch(
                canary, successor, hold_row, now=now,
                progressing_fn=prog_t, context_assist_fn=context_assist_seam,
                retire_fn=retire_t, escalate_fn=escalate_t,
                predecessor_live_fn=pred_live_seam,
                beat_interval_s=beat_interval_s,
                pre_retire_bound_beats=pre_retire_bound_beats)

        # PROMOTE beat: stamp the work-repo HEAD as the commit-delta baseline for the
        # progressing-watch (so a successor that COMMITS its work is detected as progress).
        fe = ((first_effect_fn or _default_first_effect)(canary)
              if completion_mode else None)
        tr = complete_held_rotation(
            canary, successor, hold_row, now=now,
            live_sid_fn=live, l_start_time_fn=start, readback_mtime_fn=rbm,
            comprehension_mtime_fn=compm, provenance_sid_fn=prov,
            canonical_store_sids_fn=stores, confirm_fn=confirm_t, grade_fn=grade_t,
            safety_fn=safety_t, graduation_fn=grad_t, promote_fn=promote_t,
            retire_fn=retire_t, armed=armed, budget=budget, clock_skew_s=clock_skew_s,
            completion_mode=completion_mode, first_effect=fe,
            auto_resume_fn=resume_t)
        # stamp the commit-delta baseline on a completion-mode promote so later
        # progressing-watch beats can detect COMMITTED successor work (fleet persists it).
        if completion_mode and tr.get("promoted") and tr.get("status") == AWAITING_PROGRESS:
            try:
                tr["promote_baseline_sha"] = _repo_head_for(canary)
            except Exception:  # noqa: BLE001 -- baseline is best-effort (worktree-edit still works)
                pass
        return tr

    provider.retire = retire
    provider.promote = promote_seam
    provider.auto_resume = resume_seam
    provider.grade = grade
    provider.confirm = confirm
    provider.safety = safety

    return provider
