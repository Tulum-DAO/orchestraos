#!/usr/bin/env python3
# scripts/retire_predicate.py
"""RETIRE-PREDICATE — the terminal step the rotation transaction never had.

the operator directive 2026-08-19: "just retire the agents after handoff confirmed and
one or two beats pass where they're no longer needed." T1..T10 ended at the
grade; nothing retired the predecessor, so rollback seats piled up holding RAM
until gm swept them by hand, and the lineage-daemon nudged them each beat.

This module is the DECISION only (may_retire) — pure, so it is testable and so
the execution (park-idle's real retire verb) stays the single writer. Promotion
already stamps the predecessor's archive succeeded_by (the reachability
foundation); this decides WHEN park-idle may act on it.

THE DEFAULT IS KEEP. An unproven condition must never retire the safety net —
under promote-fast the predecessor is the rollback for the whole grade window,
so retiring it early is a correctness bug, not tidiness (the same polarity as
every gate in this lane: fail toward not-acting).
"""
from __future__ import annotations

# Promote-fast keeps the predecessor as the rollback net through the grade
# window; it may only retire after the successor has proven live for a couple
# of beats past promotion.
RETIRE_MIN_BEATS = 2


def may_retire(ctx: dict) -> tuple[bool, str]:
    """(may_retire, reason). Retire iff ALL conditions hold; else KEEP with the
    blocking reason named. ctx keys:
      handoff_confirmed     bool — successor committed its readback (T4)
      grade_agree           True retires; None(pending)/False(failed)/absent KEEP
      beats_since_promotion int
      pending_duty          list — post-grade duties routed to the predecessor
      successor_live        bool
    """
    if not isinstance(ctx, dict):
        return False, "KEEP: no context — default is never to retire the net"

    if not ctx.get("successor_live"):
        return False, ("KEEP: successor is not live — retiring the predecessor "
                       "would leave the seat with no head")

    if not ctx.get("handoff_confirmed"):
        return False, ("KEEP: handoff not confirmed — the successor has not "
                       "committed its readback (T4); the predecessor is still "
                       "the only oriented head")

    # grade: ONLY an affirmative PASS retires (gm ruling msg_8d07220c). None
    # (pending), False (failed), and missing all KEEP — pending is no-data,
    # and under promote-fast the rollback net must survive the ENTIRE grade
    # window until a PASS. Retiring on pending strips the net before the grade
    # resolves; if it then FAILS there is no rollback. Same
    # no-data-is-not-permission law as 542faac17 (three nulls != coherent).
    grade = ctx.get("grade_agree", "__missing__")
    if grade is not True:
        if grade is False:
            return False, ("KEEP: grade FAILED — the predecessor is the "
                           "ROLLBACK target and must stay resumable")
        return False, ("KEEP: grade not an affirmative PASS "
                       f"({grade!r}) — no-data/pending is not permission; the "
                       "rollback net survives the grade window until a PASS")

    beats = ctx.get("beats_since_promotion")
    if not isinstance(beats, int) or beats < RETIRE_MIN_BEATS:
        return False, (f"KEEP: only {beats} healthy beat(s) since promotion "
                       f"(need {RETIRE_MIN_BEATS}) — the grade window is the "
                       f"rollback window; the net stays up until it closes")

    duty = ctx.get("pending_duty")
    if duty:
        return False, (f"KEEP: pending post-grade duty {duty} routed to the "
                       f"predecessor — re-route it FIRST, then retire "
                       f"(retiring destroys the designated performer)")

    return True, (f"RETIRE: handoff confirmed, grade PASSED, {beats} healthy "
                  f"beats, no pending duty, successor live")
