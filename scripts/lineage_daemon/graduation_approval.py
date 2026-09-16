"""graduation_approval.py — the graduation-gated auto-approve factory (T2 wave).

Produces the `approval_fn(canary, successor)` seam that execute_rotation's
KILL GATE 2 calls. It is a THIN wrapper around the EXISTING the operator-card gate
(execute.default_approval_gate). The wrapper auto-approves — returning "approve"
with NO card + NO wait — under EXACTLY ONE condition:

    is_graduated_autoretire(seat) is True   (cold-verified graduated-T2:
        floor-absent AND tier∈{T2} AND lineage self-retire-armed AND a STRICT
        comprehension PASS — all resolved from durable sources, never self-report)
  AND
    the S3 confirm outcome for this rotation is confirmed
        (successor absorbed the deep context + began correct work, two-sample).

In EVERY other case — not graduated (supervised/no-mode grade, floor present,
not-armed, non-T2), S3 not-confirmed, a non-`True` return, or an EXCEPTION in
either signal — it FALLS BACK to `fallback_fn`, whose default IS the current
the operator-card gate. This is fail-toward-the-card by construction: no auto-approve on
any uncertain/missing signal, same KEEP-polarity as the rest of the retire path.

REVERSIBLE + INJECTABLE: a caller that does not opt into graduation still gets the
default the operator-card behavior — nothing here changes execute_rotation's default
seams. Both signals are injected, so the factory is hermetic under test and the
production wiring binds is_graduated_autoretire + the S3 confirm result.

This module NEVER retires, spawns, cards, or writes. It only chooses between
"return approve" and "delegate to the fallback card gate".
"""


def _is_exactly_true(value) -> bool:
    """Positive-signal-only: ONLY the boolean `True` counts. A truthy non-True
    value (1, 'yes', a dict) is NOT an affirmative — it falls toward the card. Any
    error while reading the signal is caught by the caller (also -> card)."""
    return value is True


def make_graduation_gated_approval(seat, *, graduated_fn, confirmed_fn,
                                   fallback_fn=None):
    """Return an `approval_fn(canary, successor) -> "approve"|"deny"|"timeout"`.

    Auto-approves (no card, no wait) ONLY when BOTH signals are exactly True:
      graduated_fn(seat) is True        (is_graduated_autoretire — cold-verified T2)
      confirmed_fn(canary, successor) is True   (S3 confirm outcome == confirmed)
    Otherwise DELEGATES to fallback_fn(canary, successor) (the the operator-card gate).

    Seams (injected so the factory is hermetic + reversible):
      graduated_fn(seat) -> bool     — production: is_graduated_autoretire.
      confirmed_fn(canary, successor) -> bool
                                     — production: a lightweight check of the S3
                                       confirm outcome for THIS rotation
                                       (outcome == "confirmed").
      fallback_fn(canary, successor) -> "approve"|"deny"|"timeout"
                                     — the EXISTING the operator one-tap card gate. Default
                                       = execute.default_approval_gate, so a caller
                                       that does not wire graduation keeps today's
                                       behavior exactly.

    Fail-toward-card invariants:
      * confirmed_fn checked FIRST — a not-confirmed rotation NEVER trusts the
        graduation signal; it goes straight to the card.
      * a non-`True` (truthy-but-not-True) return from EITHER seam -> card.
      * an EXCEPTION in EITHER seam is swallowed -> card (never auto-approve, never
        crash the kill gate).
    """
    if fallback_fn is None:
        from scripts.lineage_daemon import execute as _ex
        fallback_fn = _ex.default_approval_gate

    def approval_fn(canary, successor):
        # 1) S3 confirm FIRST: not-confirmed => card, no matter the grade.
        try:
            confirmed = confirmed_fn(canary, successor)
        except Exception:  # noqa: BLE001 -- fail toward the card, never auto-approve
            confirmed = None
        if not _is_exactly_true(confirmed):
            return fallback_fn(canary, successor)

        # 2) graduation gate: cold-verified graduated-T2 only.
        try:
            graduated = graduated_fn(seat)
        except Exception:  # noqa: BLE001 -- fail toward the card, never auto-approve
            graduated = None
        if not _is_exactly_true(graduated):
            return fallback_fn(canary, successor)

        # BOTH exactly True -> the ONE auto-approve path: no card, no wait.
        return "approve"

    # expose the bound fallback for wiring/tests (which gate we fall back to).
    approval_fn.__wrapped_fallback__ = fallback_fn
    return approval_fn
