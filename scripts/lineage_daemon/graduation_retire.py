"""Stage C (Piece 3) — the graduation-completion substrate (DEC-1787687601).

Finishes a GRADED-PASS rotation the self-trigger left INCOMPLETE — the
`second-brain-dev-3` class: successor graded PASS 5/5 but the rotation STALLED at
completion (live two-head, predecessor never retired). No existing system closes
it: the self-trigger didn't self-complete; `rotation_card` fires only on WEDGED
(not graded-PASS); nothing called `retire_predicate.may_retire()`.

This module is the SHARED SUBSTRATE both this completion fix AND self-retire-spec
converged onto (ob msg_37cc7017 / self-retire-spec msg_161a01cc) — ONE wiring, not
two. self-retire-spec's autonomy is a MODE on it (fill `is_graduated_autoretire`),
never a second `may_retire -> retire` path.

Flow (pure over injected seams; nothing here spawns/kills/cards for real):
    may_retire(ctx) -> if not may: KEEP.
    REQUIRE_CARD(seat) = True if ~/runtime/SELF_RETIRE_DISABLED exists
                         else True on ANY predicate error   (fail-toward-card)
                         else (not is_graduated_autoretire(seat))   # stub False->True
    if REQUIRE_CARD: emit promote_graded card (fork B) -> await the operator approve
    else:            proceed (graduated-T2 mode)
    KILL GATE 1 fresh-classify   # BOTH branches, POST-await / immediately-pre-retire
    park-idle single-writer atomic retire   (+ vouch consumption, a separate seam)

Design invariants (ob's 2 refinements, baked BEFORE build):
  R1  KILL GATE 1 is the LAST gate before retire on BOTH branches, POST-await — a
      deliberate improvement over execute.py:148-165 (which fresh-classifies BEFORE
      the human await, so the re-check goes STALE across a long await). Do NOT
      "correct" it back to pre-await order.
  R2  REQUIRE_CARD is positive-signal-only: only an AFFIRMATIVE cold-verified
      graduation skips the card. ANY error / None / unknown -> card, never skip
      (same KEEP-polarity as may_retire — fail toward not-acting).

Ownership: this module + the `is_graduated_autoretire` STUB are mine
(completion-fix-builder). self-retire-spec owns the real `is_graduated_autoretire`
impl + the graduation arming knob + the `~/runtime/SELF_RETIRE_DISABLED` file.
"""
import os

from scripts.retire_predicate import may_retire

# The non-synced runtime e-brake (fleet convention: ~/runtime lives OUTSIDE
# Syncthing so a stale Mac copy can't resurrect/delete it). self-retire-spec OWNS
# this file; the substrate only READS it. Present => force the card.
_DEFAULT_DISABLED_PATH = os.path.join(
    os.path.expanduser("~"), "runtime", "SELF_RETIRE_DISABLED")


def is_graduated_autoretire(seat) -> bool:
    """The autonomy predicate (self-retire-builder fills Stage C's False stub).
    True ONLY for a cold-verified-graduated-T2 seat (tier armed AND lineage
    self-retire-armed AND kill-switch OFF AND per-seat graduation affirmatively
    PASS). Absence of that affirmative signal -> False (keep the card). The real
    logic lives in the disjoint autonomy module; this delegates so the substrate
    diff stays to this one body. Imported lazily to avoid any import-time coupling
    of the substrate to the layer."""
    from scripts.lineage_daemon.self_retire_gate import (
        is_graduated_autoretire as _impl)
    return _impl(seat)


def require_card(seat, *, disabled_path=_DEFAULT_DISABLED_PATH,
                 graduated_fn=is_graduated_autoretire) -> bool:
    """The autonomy seam — positive-signal-only, fail-toward-card (R2).

    True (=> card the operator) UNLESS an AFFIRMATIVE cold-verified graduation says skip:
      * ~/runtime/SELF_RETIRE_DISABLED present -> True (brake forces the tap, even
        for a graduated seat).
      * graduated_fn raises / returns non-True (None/unknown/False) -> True.
      * graduated_fn returns exactly True -> False (skip the card).
    """
    try:
        if os.path.exists(disabled_path):
            return True                       # kill-switch: always card
    except OSError:
        return True                           # can't even check the brake -> card
    try:
        graduated = graduated_fn(seat)
    except Exception:  # noqa: BLE001 -- R2: any predicate error -> card, never skip
        return True
    return graduated is not True              # ONLY an affirmative True skips


def build_promote_graded_card(ctx: dict) -> dict:
    """Fork B (ob): a SIBLING of rotation_card — REUSES its emit/dedup/fail-safe/
    resolver MECHANISM but has its OWN builder, its OWN kind (`promote_graded`),
    and its OWN op_key namespace (`promote_graded:<root>:<gen>`) so a graduation
    card is NEVER conflated with a rotation_kill card (opposite the operator-facing
    meanings + separate dedup). Pure payload builder.

    ctx keys: agent_id (predecessor), successor, lineage_root, generation,
    grade_commit, beats_since_promotion.
    """
    root = ctx.get("lineage_root") or ctx.get("agent_id")
    gen = ctx.get("generation")
    pred = ctx.get("agent_id")
    succ = ctx.get("successor")
    beats = ctx.get("beats_since_promotion")
    return {
        "from_agent": pred,
        "kind": "promote_graded",
        "worker_kind": "fleet-rotation",
        "op_key": f"promote_graded:{root}:{gen}",
        "options": ["approve", "respond"],
        "question": (f"Promote {succ} to {root} and retire {pred}? "
                     f"(grade PASSED, handoff confirmed, {beats} healthy beats)"),
        "summary": (f"Successor {succ!r} graded PASS and has run {beats} healthy "
                    f"beats since promotion; {pred!r} is the quiescent rollback. "
                    f"Approve = promote (atomic identity swap via "
                    f"promote_successor.promote) + retire {pred} (park-idle "
                    f"single-writer). Respond = hold. This is a graduation "
                    f"(clean succession completion), not an emergency rotation."),
        "risk_level": "medium",
        "reversibility": "hard",
        "feature": "lineage-daemon / rotation",
        "evidence": {
            "predecessor": pred, "successor": succ, "lineage_root": root,
            "generation": gen, "grade_commit": ctx.get("grade_commit"),
            "beats_since_promotion": beats,
        },
    }


def plan_graduation_retire(ctx: dict, seat: dict, *,
                           disabled_path=_DEFAULT_DISABLED_PATH,
                           graduated_fn=is_graduated_autoretire) -> dict:
    """The substrate DECISION (pure): given the retire ctx + seat, decide whether
    to KEEP, emit a promote_graded card and await the operator, or (graduated mode) proceed
    without a card. The execute step (KILL GATE 1 -> promote -> retire) is separate
    (execute_graduation_retire) so the card-await gap sits BETWEEN them (R1).

    Returns {action, reason, card|None, seat, ctx}:
      action == "keep"             -> may_retire said not-yet (reason names why)
      action == "await_card"       -> emit card, act on Approve via execute step
      action == "proceed_no_card"  -> graduated-T2 mode; go straight to execute step
    """
    may, reason = may_retire(ctx)
    if not may:
        return {"action": "keep", "reason": reason, "card": None,
                "seat": seat, "ctx": ctx}

    if require_card(seat, disabled_path=disabled_path, graduated_fn=graduated_fn):
        return {"action": "await_card", "reason": "graded-PASS retire-ready; "
                "REQUIRE_CARD -> the operator tap", "card": build_promote_graded_card(seat),
                "seat": seat, "ctx": ctx}

    return {"action": "proceed_no_card",
            "reason": "graded-PASS retire-ready; graduated-T2 autonomy -> no card",
            "card": None, "seat": seat, "ctx": ctx}


def execute_graduation_retire(seat: dict, *, kill_gate1_fn, promote_fn, retire_fn) -> dict:
    """The EXECUTE step, reached on the operator Approve (carded branch) OR immediately
    (graduated skip branch). KILL GATE 1 is the LAST gate before any write, run
    FRESH here — POST-await by construction (R1): the substrate calls this only
    after the human tap resolves / the skip branch is taken, so the fresh-classify
    can't go stale across the await like execute.py:148-165 does.

    Seams: kill_gate1_fn(seat)->(ok:bool, reason) fresh safety re-classify;
    promote_fn(seat) = promote_successor.promote() wrapper; retire_fn(seat) =
    park-idle single-writer atomic retire. A STALE gate aborts (nothing written).
    """
    ok, why = kill_gate1_fn(seat)
    if not ok:
        return {"action": "aborted_kill_gate1", "reason": why, "seat": seat}
    promoted = promote_fn(seat)
    retired = retire_fn(seat)
    return {"action": "promoted_and_retired", "seat": seat,
            "promote": promoted, "retire": retired}


def resolve_promote_graded_cards(rows, *, execute_fn, route_fn, dry_run=True) -> list:
    """Resolve answered promote_graded rows — the fork-B sibling of
    rotation_card.resolve_rotation_cards, with promote-not-kill semantics.

    approve -> execute_fn(from_agent) = the graduation execute step (KILL GATE 1
      -> promote -> retire), EXACTLY once (a resumed_at row already acted -> no-op).
      dry_run logs would_promote_retire.
    respond -> route_fn(...) to gm, NO promote/retire (a hold).
    Fail-safe: an execute error is RECORDED, never raised, and does NOT mark the
    row acted (so it isn't silently dropped). Only acts on kind == 'promote_graded'.
    """
    out = []
    for row in rows or []:
        rid = row.get("id")
        aid = row.get("from_agent")
        if row.get("kind") != "promote_graded":
            out.append({"id": rid, "action": "skip_not_promote_graded"})
            continue
        if row.get("status") != "answered" or not row.get("answer"):
            out.append({"id": rid, "action": "skip_unanswered"})
            continue
        if row.get("resumed_at"):
            out.append({"id": rid, "action": "already_acted"})     # idempotent
            continue
        ans = str(row.get("answer")).lower()
        if ans == "approve":
            if dry_run:
                out.append({"id": rid, "action": "would_promote_retire",
                            "agent": aid})
                continue
            try:
                result = execute_fn(aid)
                out.append({"id": rid, "action": "promoted_and_retired",
                            "agent": aid, "result": result})
            except Exception as e:  # noqa: BLE001 -- record, never raise/drop
                out.append({"id": rid, "action": "execute_error", "agent": aid,
                            "error": f"{type(e).__name__}: {e}"})
        elif ans == "respond":
            route_fn(from_agent=aid, text=row.get("answer_text") or "",
                     op_key=row.get("op_key"))
            out.append({"id": rid, "action": "routed_no_action", "agent": aid})
        else:
            out.append({"id": rid, "action": f"skip_unknown_answer:{ans}"})
    return out
