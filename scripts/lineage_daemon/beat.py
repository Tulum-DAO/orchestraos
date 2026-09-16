"""The canary-scoped rotation BEAT — the live-wiring seam (WS3 v2, DEC-1786731957).

All the v2 daemon pieces are built + green as PURE units (decide, author_gate,
execute_rotation+confirm_fn, hold_ledger, rotation_lock). What was MISSING is the
one place that COMPOSES them into a single rotation cycle. This module is that
composition — and it is CANARY-SCOPED BY CONSTRUCTION, exactly like
execute_rotation:

  * ONE agent per beat. `rotation_beat` acts on the single `--canary`; a non-canary
    agent short-circuits to "observe" (never armed) via canary.should_act. There is
    NO fleet loop and NO cron/@reboot here — the fleet beat over MANY agents is a
    later, separately-the operator-gated build. This module is what the throwaway
    content-bearing re-canary drives end to end.
  * Composes, in order, UNDER THE ROTATION LOCK (H10):
        decide() -> can_rotate()/acquire()
          -> soft_handoff: build_author_trigger + handoff_ready (S1 gate, H2)
          -> hard_rotate : execute_rotation(confirm_fn=<S3 + two-sample>)  (S3/S5)
          -> add_hold() on any HOLD outcome (H9)
        -> release()  (always, in finally)
  * The retire inside execute_rotation stays DOUBLE-GATED (safety re-check AND the operator
    tap); this module adds NOTHING that can kill un-gated. The TWO-SAMPLE gate (H5)
    is layered by wrapping the S3 loop as the confirm_fn (retire only after TWO
    passing samples separated by a settle window) — execute.py is untouched.

Every seam is injectable (executors, approval, safety, confirm, author-inject,
handoff-provider, now) so the re-canary + tests exercise the full cycle with FAKES
— nothing spawns, kills, injects, or pages the operator.
"""

from scripts.lineage_daemon.decide import decide
from scripts.lineage_daemon.canary import should_act
from scripts.lineage_daemon import rotation_lock as rlock
from scripts.lineage_daemon import hold_ledger as hledger
from scripts.lineage_daemon.author_gate import (
    build_author_trigger, handoff_ready, handoff_rev)
from scripts.lineage_daemon.confirm_correct import confirm_and_correct, CONFIRMED
from scripts.lineage_daemon import execute as ex


# --- beat outcome constants (effects, not prose) ------------------------------
OBSERVE = "beat:observe"                 # not the canary -> never armed
NOOP = "beat:noop"                       # decide() said noop (healthy ctx)
LOCKED = "beat:locked"                   # rotation lock blocked this beat
SOFT_AUTHORING = "beat:soft-authoring"   # author-trigger emitted, handoff NOT ready yet
SOFT_READY = "beat:soft-ready"           # handoff committed + rich + fresh -> hard next beat
SOFT_COMPLETE = "beat:soft-complete"     # three-artifact handoff already done -> no re-ping
# hard_rotate beats return execute_rotation's own status verbatim (ex.DONE / ex.HOLD_*).

# piece-3 freshness baseline (DEC-1788165818): sentinel recorded when the per-seat
# baseline is captured at soft-window-open and NO handoff existed yet. It is a
# real (truthy) baseline value — so a later AUTHORED rev differs from it and is
# FRESH — kept DISTINCT from an absent/None baseline (which fails CLOSED). Uses a
# \x00 prefix so it can never collide with a real "commit_sha:file_hash" rev.
_BASELINE_NO_HANDOFF = "\x00no-handoff-at-soft-open"


def set_promote_baseline(history, seat, handoff_provider, lineage_root=None):
    """piece-4 C3 reset-at-promote (DEC-1788165818): the PRIMARY freshness
    baseline. At a canonical identity swap set the per-seat baseline to the
    CONSUMED predecessor rev — the handoff the successor just inherited —
    computed via the SAME provider the beat uses (PATH-IDENTITY: the make-or-
    break catch — a different resolver would make baseline != current-rev every
    beat -> false SOFT_READY on the first authored beat). The new generation's
    FIRST authored handoff then has a rev that DIFFERS from this baseline -> it
    is FRESH -> eligible SOFT_READY; an UNCHANGED (non-authored) generation has
    rev == baseline -> stays SOFT. This is what makes C1 (capture-at-soft-open)
    a mere fallback and fixes the proactive-author false-negative.

    No handoff at promote -> the sentinel (distinct from an absent/None baseline
    which fails CLOSED). Returns the (mutated) history."""
    rev = None
    if handoff_provider is not None:
        try:
            hdict, _ = handoff_provider(seat, lineage_root)
        except TypeError:
            try:
                hdict, _ = handoff_provider(seat)
            except TypeError:
                hdict, _ = handoff_provider()
        rev = handoff_rev(hdict)
    baselines = (history if history is not None else {}).setdefault(
        "handoff_baseline", {})
    baselines[seat] = rev if rev is not None else _BASELINE_NO_HANDOFF
    return history


def capture_soft_open_baseline(history, seat, handoff_provider, prior_rev_fn,
                               lineage_root=None):
    """A2 Axis-3(b) (DEC-1788207386): the C1 CONDITIONAL fallback that unlocks a
    PROACTIVELY-authored seat WITHOUT a promote. The pre-A2 C1 captured the CURRENT
    rev at soft-open, so a seat that authored a rich handoff BEFORE its first soft
    beat got baseline==current -> stale forever (deadlock). Instead seed the
    baseline to the seat's PRIOR committed handoff rev (`prior_rev_fn` = git parent
    of the CANONICAL handoff path, path-identity-consistent with the provider).
    The current (just-authored) handoff then differs from the prior -> FRESH.

    No-op guard (C1): if the prior content-id EQUALS the current one (only a
    whitespace/no-op recommit happened), handoff_ready's file_hash compare keeps it
    STALE — a prior-rev seed must never unlock an unchanged handoff. No prior rev
    (first-ever handoff) -> the sentinel (ELIGIBLE on first genuine authoring),
    kept DISTINCT from an absent/None baseline (fail-closed). Idempotent: only sets
    a baseline the seat does not already have. Returns the (mutated) history."""
    baselines = (history if history is not None else {}).setdefault(
        "handoff_baseline", {})
    if seat in baselines:
        return history                    # already seeded (C3 promote or a prior beat)
    prior = None
    try:
        prior = prior_rev_fn(seat, lineage_root)
    except TypeError:
        try:
            prior = prior_rev_fn(seat)
        except TypeError:
            prior = prior_rev_fn()
    baselines[seat] = prior if prior else _BASELINE_NO_HANDOFF
    return history


def _lineage_of(canary, pred_entry):
    """(lineage_root, predecessor_generation) — mirrors execute._lineage_of."""
    import re
    pred_entry = pred_entry or {}
    root = pred_entry.get("lineage_root") or re.sub(r"-g\d+$", "", canary) or canary
    gen = pred_entry.get("generation")
    if gen is None:
        m = re.search(r"-g(\d+)$", canary)
        gen = int(m.group(1)) if m else 1
    return root, int(gen)


def resolve_successor_name(canary, registry, sessions_meta):
    """Resolve the ACTUAL successor of `canary` from the lineage chain (pure).

    Fixes 2a: the beat used to hard-build `f"{root}-g{gen+1}"`, which NEVER matched
    real successor names (`-gen{N}` / `-{N}`, e.g. `second-brain-dev-3`), so the
    completion check never fired and the author-trigger re-pinged forever.

    Resolution order (SESSIONS-first — sessions is the source of truth for a live,
    uncommitted two-head created at spawn; registry is durable post-promote state):
      1. `succeeded_by` on the predecessor row (sessions, then registry) — set by
         promote; names the successor directly.
      2. reverse-lookup for the PRE-promote two-head (succeeded_by not yet set):
         a row declaring `parent == canary`, or a same-lineage row with a strictly
         higher generation. Sessions rows win over registry.
      3. else None — FAIL-CLOSED. The caller keeps the nudge rather than resurrect
         the brittle `-g{N+1}` guess (congruence: never let name-derivation revive it).

    Never returns `canary` itself (a self-naming row is incoherent -> None).
    """
    reg_agents = (registry or {}).get("agents", {})
    sess = sessions_meta or {}
    pred_reg = reg_agents.get(canary, {}) or {}
    pred_sess = sess.get(canary, {}) or {}

    # 1. succeeded_by (sessions-first).
    for src in (pred_sess, pred_reg):
        sb = src.get("succeeded_by")
        if sb and sb != canary:
            return sb

    # 2. reverse-lookup (pre-promote two-head).
    pred_root = pred_reg.get("lineage_root") or pred_sess.get("lineage_root")
    pred_gen = pred_reg.get("generation")
    if not isinstance(pred_gen, int):
        pred_gen = pred_sess.get("generation")

    def _scan(store):
        # a row naming canary as its parent is the successor (strongest signal).
        for aid, row in store.items():
            if aid == canary or not isinstance(row, dict):
                continue
            if row.get("parent") == canary:
                return aid
        # else a same-lineage row with a strictly higher generation.
        if pred_root is not None and isinstance(pred_gen, int):
            best, best_gen = None, pred_gen
            for aid, row in store.items():
                if aid == canary or not isinstance(row, dict):
                    continue
                if (row.get("lineage_root") == pred_root
                        and isinstance(row.get("generation"), int)
                        and row["generation"] > best_gen):
                    best, best_gen = aid, row["generation"]
            if best is not None:
                return best
        return None

    return _scan(sess) or _scan(reg_agents)


def two_sample_confirm(
    *,
    expected,
    ground_truth,
    first_effect,
    read_successor,
    gate_fn,
    correction_fn,
    inject_correction,
    settle_fn=None,
    second_read=None,
    max_rounds=3,
    cwd=".",
    effect_runner=None,
):
    """Build the confirm_fn execute_rotation calls before the kill gates (H5).

    Returns a `confirm_fn(canary, successor) -> {outcome: confirmed|held, ...}`
    that requires TWO passing samples separated by a settle window:

      sample 1 = the full S3 confirm-and-correct loop (content gate + bounded
                 nonce corrections). A HELD here is an unconfirmed successor —
                 return held (execute_rotation retires NOTHING).
      <settle>  = injected settle_fn() (a real daemon sleeps; tests pass a no-op).
      sample 2 = a FRESH read + one rotation_gate pass, NO corrections — a pure
                 drift check. A successor that passed at T but drifted at T+settle
                 is caught HERE, before the safety net (the still-live predecessor)
                 is destroyed.

    Only CONFIRMED at BOTH samples returns confirmed; execute_rotation then runs
    its own safety-recheck + the operator-tap gates before the kill.
    """
    def _confirm(canary, successor):
        s1 = confirm_and_correct(
            successor, expected, ground_truth, first_effect,
            read_successor=read_successor, gate_fn=gate_fn,
            correction_fn=correction_fn, inject_correction=inject_correction,
            max_rounds=max_rounds, cwd=cwd, effect_runner=effect_runner,
        )
        if s1.get("outcome") != CONFIRMED:
            return {"outcome": "held", "sample": 1, "s1": s1,
                    "reasons": s1.get("reasons", [])}

        if settle_fn is not None:
            settle_fn()

        snap = (second_read or read_successor)()
        g2 = gate_fn(
            snap.get("observed", {}), expected, snap.get("evidence", {}),
            ground_truth, effect=first_effect, cwd=cwd, effect_runner=effect_runner,
        )
        if not g2.get("confirmed"):
            return {"outcome": "held", "sample": 2, "s1": s1, "g2": g2,
                    "reasons": ["two-sample:drift-at-sample-2"] + g2.get("reasons", [])}

        return {"outcome": "confirmed", "sample": 2, "s1": s1, "g2": g2}

    return _confirm


def rotation_beat(
    agent,
    registry,
    *,
    canary,
    now,
    locks=None,
    ledger=None,
    history=None,
    # soft-path seams (S1 author gate)
    author_inject=None,
    handoff_provider=None,
    handoff_path=None,
    session_turns=0,
    completion_fn=None,
    sessions_meta=None,
    prior_rev_fn=None,   # A2 Axis-3(b): git-parent-rev of the canonical handoff path
    # hard-path seams (forwarded verbatim to execute_rotation)
    executors_impl=None,
    approval_fn=None,
    safety_fn=None,
    successor_live_fn=None,
    confirm_fn=None,
    grade_fn=None,
    orchestra_dir=None,
    # Piece 1 (DEC-1787861488): S1 hold-open trigger snapshot seam.
    # trigger_snapshot_fn(canary) -> the verified-fresh trigger evidence dict
    # (or None). Called ONLY when a hard_rotate HOLDs, so the snapshot is taken
    # at the exact moment the hold row opens. None (default) => holds are
    # byte-identical to before. Fail-safe: a raising/None seam records the
    # hold WITHOUT evidence, never fabricates and never wedges the ledger.
    trigger_snapshot_fn=None,
):
    """Run ONE canary-scoped rotation beat. Pure over injected seams + returns a
    trace dict; the daemon persists locks/ledger (flock + atomic write) and does
    the real IO. NEVER acts on any agent but `canary`.

    Returns:
        {status, action, canary, successor, lineage_root, reason,
         locks, ledger, author_trigger, readiness, rotation}
    where `status` is one of OBSERVE / NOOP / LOCKED / SOFT_AUTHORING /
    SOFT_READY / ex.DONE / ex.HOLD_* / ex.ABORT_*.
    """
    locks = locks or {"rotations": []}
    ledger = ledger or {"holds": []}

    d = decide(agent)
    aid = d["agent_id"]

    trace = {
        "status": None, "action": d["action"], "canary": canary,
        "successor": None, "lineage_root": None, "reason": d["reason"],
        "locks": locks, "ledger": ledger, "author_trigger": None,
        "readiness": None, "rotation": None,
    }

    # CANARY SCOPE: only the exact canary is ever actionable (airtight guard).
    if not should_act(aid, canary):
        trace["status"] = OBSERVE
        return trace

    if d["action"] == "noop":
        trace["status"] = NOOP
        return trace

    pred_entry = (registry or {}).get("agents", {}).get(canary, {})
    lineage_root, pred_gen = _lineage_of(canary, pred_entry)
    # 2a: resolve the ACTUAL successor from the lineage chain when sessions are
    # injected (production). `completion_name` is what the completion check reads:
    #   - sessions injected + resolved -> the real successor name.
    #   - sessions injected + UNRESOLVABLE -> None => FAIL-CLOSED (skip the
    #     completion suppression -> keep nudging; never guess the brittle string).
    #   - sessions NOT injected (legacy/test path) -> the historical -g{N+1} name
    #     (behavior preserved for callers that pre-date chain resolution).
    fallback = f"{lineage_root}-g{pred_gen + 1}"
    if sessions_meta is not None:
        resolved = resolve_successor_name(canary, registry, sessions_meta)
        successor = resolved or fallback
        completion_name = resolved
    else:
        successor = fallback
        completion_name = fallback
    trace["lineage_root"] = lineage_root
    trace["successor"] = successor

    # --- ROTATION LOCK (H10): refuse if this lineage is already rotating, or the
    # canary is the verifier / a party of another in-flight rotation. ---
    gate = rlock.can_rotate(locks, canary=canary, lineage_root=lineage_root, now=now)
    if not gate["ok"]:
        trace["status"] = LOCKED
        trace["reason"] = gate["reason"]
        return trace

    locks = rlock.acquire(locks, canary=canary, lineage_root=lineage_root,
                          successor=successor, now=now)
    trace["locks"] = locks
    try:
        if d["action"] == "soft_handoff":
            # F17: re-verify current completion state AT EMIT (b53311d69 dedup
            # class). A COMPLETED three-artifact handoff (doc committed + canary
            # authored + readback PASS for the successor) must NOT be re-pinged —
            # the author-trigger would otherwise fire every beat the predecessor
            # sits at SOFT ctx, spamming an already-oriented rotation.
            if (completion_fn is not None and completion_name is not None
                    and completion_fn(completion_name)):
                trace["status"] = SOFT_COMPLETE
                return trace

            # S1: first resolve the handoff dictionary to evaluate readiness
            if handoff_provider is not None:
                try:
                    handoff_dict, mtime = handoff_provider(canary, lineage_root)
                except TypeError:
                    try:
                        handoff_dict, mtime = handoff_provider(canary)
                    except TypeError:
                        handoff_dict, mtime = handoff_provider()
            else:
                handoff_dict, mtime = (None, None)

            # Piece-3 FRESHNESS BASELINE (DEC-1788165818 axis-2, C1/C3): the
            # per-seat baseline lives in the durable history store (parallel to
            # soft_reminders). C3 (reset-at-promote, complete.py) is the PRIMARY
            # source — set to the CONSUMED predecessor rev. C1 here is the
            # CONDITIONAL fallback: at soft-window OPEN, if no baseline exists,
            # capture the CURRENT (pre-authoring) rev over the SAME provider-
            # resolved handoff (path-consistency — handoff_rev over the dict the
            # provider returned, whose provenance is the canonical docs path). A
            # distinct sentinel marks "no handoff at open" (so a later authored
            # rev is fresh) — kept SEPARATE from an absent/null baseline, which
            # fail-closes. rev-vs-baseline REPLACES the mtime gate in
            # handoff_ready. With no durable history store (history is None) we
            # fall back to the legacy call (structural+richness only, no gate).
            if history is not None:
                baselines = history.setdefault("handoff_baseline", {})
                if canary not in baselines:
                    # A2 Axis-3(b): when a prior_rev_fn is wired, seed the C1
                    # fallback baseline to the seat's PRIOR committed rev (git
                    # parent of the canonical handoff path) so a PROACTIVELY-
                    # authored seat is FRESH (the current handoff differs from its
                    # prior). Without the seam, fall back to the legacy capture of
                    # the CURRENT rev (unchanged behavior for callers that don't
                    # supply it). C3 reset-at-promote remains the PRIMARY baseline.
                    if prior_rev_fn is not None:
                        capture_soft_open_baseline(
                            history, canary, handoff_provider, prior_rev_fn,
                            lineage_root=lineage_root)
                    else:
                        cur = handoff_rev(handoff_dict)
                        baselines[canary] = (cur if cur is not None
                                             else _BASELINE_NO_HANDOFF)
                ready = handoff_ready(handoff_dict, mtime, now, session_turns,
                                      baseline_rev=baselines.get(canary))
            else:
                ready = handoff_ready(handoff_dict, mtime, now, session_turns)
            trace["readiness"] = ready

            hp = handoff_path or f"docs/HANDOFF_{lineage_root}-next.md"
            trig = build_author_trigger(canary, hp)
            trace["author_trigger"] = trig

            # If the handoff is ready, return SOFT_READY and suppress nudge!
            if ready["ready"]:
                trace["status"] = SOFT_READY
                return trace

            # Otherwise (SOFT_AUTHORING), nudge with deduplication tracking
            should_inject = True
            if history is not None:
                import json
                import hashlib
                if handoff_dict is None:
                    rev = "missing"
                else:
                    sha = handoff_dict.get("handoff_commit_sha") or ""
                    fhash = handoff_dict.get("file_hash") or ""
                    if sha or fhash:
                        rev = f"{sha}:{fhash}"
                    else:
                        rev = hashlib.sha256(json.dumps(handoff_dict, sort_keys=True).encode("utf-8")).hexdigest()
                soft_reminders = history.setdefault("soft_reminders", {})
                if soft_reminders.get(canary) == rev:
                    should_inject = False
                else:
                    soft_reminders[canary] = rev

            if should_inject and author_inject is not None:
                author_inject(canary, trig)

            trace["status"] = SOFT_AUTHORING
            return trace

        # d["action"] == "hard_rotate"
        rot = ex.execute_rotation(
            canary, registry, executors_impl=executors_impl,
            approval_fn=approval_fn, safety_fn=safety_fn,
            successor_live_fn=successor_live_fn, confirm_fn=confirm_fn,
            grade_fn=grade_fn, orchestra_dir=orchestra_dir,
        )
        trace["rotation"] = rot
        trace["successor"] = rot.get("successor", successor)
        trace["status"] = rot["status"]

        # H9: any HOLD leaves a two-heads state -> record it in the ledger so it
        # ages (re-page 4h / reversible successor-park 12h) and can't OOM the VPS.
        if rot["status"].startswith("hold:"):
            # Piece 1 (DEC-1787861488): record the S1 trigger snapshot ON the
            # hold row at hold-open — the one moment the predecessor's detector
            # is still live-fresh. G7 later re-reads it FROM THE STORE (C1).
            snapshot = None
            if trigger_snapshot_fn is not None:
                try:
                    snapshot = trigger_snapshot_fn(canary)
                except Exception:  # noqa: BLE001 -- never fabricate, never wedge
                    snapshot = None
            ledger = hledger.add_hold(
                ledger, canary=canary, successor=trace["successor"],
                status=rot["status"], reason=rot["status"], now=now,
                trigger_evidence=snapshot)
            trace["ledger"] = ledger
        return trace
    finally:
        locks = rlock.release(locks, canary=canary, lineage_root=lineage_root)
        trace["locks"] = locks
