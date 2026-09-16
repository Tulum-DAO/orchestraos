"""H9 — HOLD ledger + reconciler (WS3 v2, DEC-1786724046).

Every HOLD outcome (safety-recheck fail, approval deny/timeout, S3 unconfirmed)
leaves a TWO-HEADS state: predecessor + successor both resident. On a
RAM-constrained VPS, accumulated un-reconciled HOLDs recreate the exact OOM the
whole system exists to prevent. This ledger records each HOLD and ages it:
  - re-page gm at RE_PAGE_AGE_S,
  - if a hold is stale (older than PARK_PROPOSAL_AGE_S) AND the predecessor is
    still healthy, emit a REVERSIBLE successor-park proposal (park the successor,
    the predecessor keeps the canonical name) so holds can't pile up unbounded.

Pure over the ledger dict + injected `now`; the daemon persists the dict (flock
+ atomic write) and acts on the proposals. Escalation-during-escalator's-own-
rotation is handled by the caller via resolve_delivery_target; a hold whose
escalation target has no stable live head is tagged 'escalation-deferred'.
"""

RE_PAGE_AGE_S = 4 * 3600        # re-page gm after 4h
PARK_PROPOSAL_AGE_S = 12 * 3600  # propose reversible successor-park after 12h


def add_hold(ledger, *, canary, successor, status, reason, now,
             escalation_target="gm", trigger_evidence=None):
    """Append a HOLD row. Returns a NEW ledger dict (does not mutate input).

    `trigger_evidence` (Piece 1, DEC-1787861488): the S1 hold-open trigger
    snapshot ({used_pct, detector_ts, detector_path, snapshot_at,
    predecessor_sid}) verified fresh at the moment the rotation decision
    fired. Recorded ON the hold row so G7 can later re-read it FROM THE STORE
    (C1) when the completion path promotes a quiescent predecessor. None
    (default) => the row is byte-identical to before (no key)."""
    rows = list(ledger.get("holds", []))
    row = {
        "canary": canary,
        "successor": successor,
        "status": status,          # e.g. hold:successor-unconfirmed
        "reason": reason,
        "created_at": now,
        "escalation_target": escalation_target,
        "repaged_at": None,
        "resolved": False,
    }
    if trigger_evidence is not None:
        row["trigger_evidence"] = trigger_evidence
    rows.append(row)
    return {**ledger, "holds": rows}


def resolve_hold(ledger, canary, successor):
    """Mark the matching HOLD resolved (rotation later completed or parked).
    Returns a NEW ledger dict."""
    rows = []
    for h in ledger.get("holds", []):
        if h["canary"] == canary and h["successor"] == successor and not h["resolved"]:
            rows.append({**h, "resolved": True})
        else:
            rows.append(h)
    return {**ledger, "holds": rows}


# A.3 rework: the two multi-beat watch statuses the completion machinery drives a hold
# through post-promote. reconcile() must SKIP both (the completion machinery owns their
# beat/minute-scale bounds; reconcile's hour-scale re_page/park backstops are moot AND
# wrong — the predecessor is either still-alive-but-bounded or already retired, never a
# 12h-park two-heads).
WATCH_STATUSES = frozenset({
    "promoted:awaiting-progress",          # PRE-retire: predecessor alive, progressing-watch
    "watch:awaiting-effect-post-retire",   # POST-retire: predecessor gone, effect-watch
})


def set_hold_status(ledger, canary, successor, status, extra=None):
    """Transition the matching OPEN hold's `status` (A.3 rework: AWAITING_PROGRESS ->
    RETIRED_AWAITING_EFFECT as the completion machinery advances the watch across beats,
    so the next beat dispatches correctly), optionally merging `extra` fields (e.g. the
    promote_baseline_sha). Does NOT resolve. Returns a NEW ledger."""
    rows = []
    for h in ledger.get("holds", []):
        if (h["canary"] == canary and h["successor"] == successor
                and not h.get("resolved")):
            rows.append({**h, "status": status, **(extra or {})})
        else:
            rows.append(h)
    return {**ledger, "holds": rows}


def mark_promoted(ledger, canary, successor, *, now, promoted_by):
    """Stamp `promoted_at`/`promoted_by` on the matching OPEN hold (D8 durable audit
    marker + D10 crash-cap fast-path). SECONDARY to the durable 3-store coherence
    read — this marker can be lost if the beat crashes before end-of-beat save_state,
    so the completion path never treats its ABSENCE as proof of not-promoted (it
    re-reads the 3 stores). Marking promoted does NOT resolve the hold (the retire
    still owes `resolve_hold`). Returns a NEW ledger dict (does not mutate input)."""
    rows = []
    for h in ledger.get("holds", []):
        if (h["canary"] == canary and h["successor"] == successor
                and not h.get("resolved")):
            rows.append({**h, "promoted_at": now, "promoted_by": promoted_by})
        else:
            rows.append(h)
    return {**ledger, "holds": rows}


def reconcile(ledger, now, predecessor_healthy):
    """Age the OPEN holds. `predecessor_healthy(canary) -> bool` is injected.
    Returns {actions: [...], ledger: <new ledger with repaged_at stamped>}.

    actions entries:
      {"kind": "re_page", canary, successor, age_s}
      {"kind": "park_successor_proposal", canary, successor, age_s}  (reversible)
      {"kind": "escalation_deferred", ...}  (predecessor unhealthy — can't park)
    """
    actions = []
    rows = []
    for h in ledger.get("holds", []):
        if h.get("resolved"):
            rows.append(h)
            continue
        # A.3 rework DOUBLE-SKIP: a hold in either completion watch status is owned by
        # the completion machinery's beat/minute bounds, NOT reconcile's hour-scale
        # backstops. Never re_page the working successor, never park (there is no
        # 12h-park two-heads: pre-retire is bounded, post-retire the predecessor is gone).
        if h.get("status") in WATCH_STATUSES:
            rows.append(h)
            continue
        age = now - h["created_at"]
        new_h = dict(h)
        if age >= PARK_PROPOSAL_AGE_S:
            # stale hold: if the predecessor is still healthy, propose parking the
            # SUCCESSOR (reversible) so the two-heads state collapses to one.
            if predecessor_healthy(h["canary"]):
                actions.append({"kind": "park_successor_proposal",
                                "canary": h["canary"],
                                "successor": h["successor"], "age_s": age})
            else:
                actions.append({"kind": "escalation_deferred",
                                "canary": h["canary"],
                                "successor": h["successor"], "age_s": age,
                                "why": "predecessor unhealthy — needs human"})
        elif age >= RE_PAGE_AGE_S and h.get("repaged_at") is None:
            actions.append({"kind": "re_page", "canary": h["canary"],
                            "successor": h["successor"], "age_s": age})
            new_h["repaged_at"] = now
        rows.append(new_h)
    return {"actions": actions, "ledger": {**ledger, "holds": rows}}


def open_holds(ledger):
    """The currently-unresolved holds."""
    return [h for h in ledger.get("holds", []) if not h.get("resolved")]
