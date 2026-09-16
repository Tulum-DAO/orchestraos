"""H3 — death-path verifier assignment (WS3 v2, DEC-1786724046).

S3 confirm-and-correct assumes the predecessor stays ALIVE as the verifier. But
decide() returns hard_rotate on `death:*` (crash / OOM / court-wedge) — the whole
class of rotations MOST likely to produce a disoriented successor — and there the
predecessor is GONE. There is nobody to run S3.

Ruling: do NOT pretend S3 ran. On a death-triggered rotation the daemon still
spawns + injects the successor (continuity), but then HOLDS-FOR-REVIEW: the
successor is marked `unconfirmed`, gm/the operator are notified, and NOTHING is
auto-retired (there is nothing to retire — the predecessor is already dead) and
the successor is NOT trusted as a confirmed head until a VERIFIER passes the
content gate. The verifier is a fresh-context entity (a dedicated verifier agent
or a human), NEVER the dead predecessor.

Pure: maps a decide() result to a verification plan. The daemon acts on it.
"""

VERIFY_LIVE_PREDECESSOR = "verify:live-predecessor-s3"   # normal ctx-triggered path
VERIFY_HOLD_FOR_REVIEW = "verify:death-path-hold-for-review"  # death path


def is_death_triggered(decision) -> bool:
    """True iff this rotation was triggered by a death signal (reason 'death:*'
    or a non-null death field), so no live predecessor exists to run S3."""
    if not decision:
        return False
    if decision.get("death"):
        return True
    return str(decision.get("reason", "")).startswith("death:")


def verification_plan(decision) -> dict:
    """Return the verification plan for a rotation decision.

    {mode, auto_retire, mark, notify, verifier}
      mode        VERIFY_LIVE_PREDECESSOR | VERIFY_HOLD_FOR_REVIEW
      auto_retire whether the daemon may auto-retire on confirm (False on death:
                  the predecessor is already dead — nothing to retire)
      mark        successor status to record ('confirmed-pending' vs 'unconfirmed')
      notify      who to page (None on the live path; gm on death path)
      verifier    'live-predecessor' vs 'fresh-context' (never the dead pred)
    """
    if is_death_triggered(decision):
        return {
            "mode": VERIFY_HOLD_FOR_REVIEW,
            "auto_retire": False,
            "mark": "unconfirmed",
            "notify": "gm",
            "verifier": "fresh-context",
            "reason": "death-triggered rotation: no live predecessor to run S3; "
                      "spawn+inject then HOLD-for-review, no auto-retire",
        }
    return {
        "mode": VERIFY_LIVE_PREDECESSOR,
        "auto_retire": True,
        "mark": "confirmed-pending",
        "notify": None,
        "verifier": "live-predecessor",
        "reason": "ctx-triggered: the still-alive predecessor runs S3",
    }
