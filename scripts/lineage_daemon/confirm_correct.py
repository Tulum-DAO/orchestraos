"""S3 — the confirm-and-correct loop (WS3 v2, DEC-1786724046).

The load-bearing gate: after a successor is spawned + injected, the predecessor
(still ALIVE) verifies AT SOURCE that the successor absorbed the deep context and
began the correct work, and only then does the daemon proceed to retire. This is
the difference between "a successor exists" and "the work is continuing correctly"
(the operator, ACCEPTANCE_proper-self-rotation).

Design (red-team-hardened):
 - The GATE is CONTENT, not behavior: `rotation_gate` (PB) = floor(check_on_track)
   ∧ comprehension(read-back + canary vs DAEMON-HELD ground truth) ∧ effect. A
   shallow-read successor that is busy + on-focus + fired its callback STILL fails
   because it cannot produce the deep loops/rationales it never absorbed (F0).
 - GROUND TRUTH IS DAEMON-HELD: `ground_truth` (incl. canary ANSWERS) is built from
   the predecessor's in-process handoff — NEVER from the successor's echo. The
   successor supplies only `evidence` (its read-back + canary answers = a CLAIM).
 - EFFECT-FIRST (NEG-2): a produced first_effect excuses only lagging
   not-progressing, so a healthy-but-lagging successor is never churned (handled
   inside rotation_gate).
 - NONCE-IDEMPOTENT CORRECTIONS (H8): each corrective inject carries a unique nonce
   ("corr-<8hex>"); re-verify keys on the nonce being ACKED (via the callback's
   correction_ack or a msg_store ack row) — NEVER "renewed activity" (which gm
   mis-attributed for gen-7). Correction text is conditional ("if already done,
   ack; else do X").
 - BOUNDED + HOLD-ON-EXHAUSTION: at most N corrective rounds; if still off-track,
   HOLD + escalate — the predecessor STAYS ALIVE, NOTHING is retired.

Everything here is PURE over injected seams (read_successor / inject_correction /
now) so it is hermetically testable; execute_rotation passes the real IO seams.
"""

import uuid

# outcomes
CONFIRMED = "confirmed"        # gate passed -> proceed to retire gates
HELD = "held"                  # exhausted corrections -> predecessor stays alive


def make_nonce() -> str:
    """A unique correction id, one per corrective round. Opaque + short."""
    return "corr-" + uuid.uuid4().hex[:8]


def confirm_and_correct(
    successor,
    expected,
    ground_truth,
    first_effect,
    *,
    read_successor,      # () -> {observed, evidence}  (reads AT SOURCE)
    gate_fn,             # rotation_gate (PB, pure)
    correction_fn,       # (observed, expected, nonce) -> str  (PB correction_message + nonce framing)
    inject_correction,   # (successor, text, nonce) -> None   (durable msg_store + tmux nudge)
    max_rounds=3,
    cwd=".",
    effect_runner=None,
    nonce_factory=make_nonce,
):
    """Run the S3 loop. Returns a trace dict:
        {outcome: CONFIRMED|HELD, rounds: int, gate: <last gate result>,
         corrections: [{nonce, text}], reasons: [...]}

    Loop: read successor AT SOURCE -> rotation_gate -> if confirmed, done.
    Else inject a nonce-keyed correction, then RE-READ (the successor's next
    read reflects whether it acked/acted). Up to max_rounds corrections; then HOLD.
    """
    corrections = []
    gate = None
    rounds = 0

    # Round 0: initial read + gate (no correction yet).
    snap = read_successor()
    gate = gate_fn(
        snap.get("observed", {}), expected, snap.get("evidence", {}),
        ground_truth, effect=first_effect, cwd=cwd, effect_runner=effect_runner,
    )
    if gate.get("confirmed"):
        return {"outcome": CONFIRMED, "rounds": 0, "gate": gate,
                "corrections": corrections, "reasons": []}

    # Corrective rounds.
    while rounds < max_rounds:
        rounds += 1
        nonce = nonce_factory()
        text = correction_fn(snap.get("observed", {}), expected, nonce)
        inject_correction(successor, text, nonce)
        corrections.append({"nonce": nonce, "text": text})

        # RE-READ at source. The successor either acked+acted or not; the gate
        # re-runs over fresh evidence (NOT "renewed activity").
        snap = read_successor()
        gate = gate_fn(
            snap.get("observed", {}), expected, snap.get("evidence", {}),
            ground_truth, effect=first_effect, cwd=cwd,
            effect_runner=effect_runner,
        )
        if gate.get("confirmed"):
            return {"outcome": CONFIRMED, "rounds": rounds, "gate": gate,
                    "corrections": corrections, "reasons": []}

    # Exhausted: HOLD — predecessor stays alive, NOTHING retired, escalate upstream.
    return {"outcome": HELD, "rounds": rounds, "gate": gate,
            "corrections": corrections,
            "reasons": (gate or {}).get("reasons", [])}


def nonce_acked(evidence, nonce) -> bool:
    """True iff the successor's callback evidence acknowledges this correction
    nonce (correction_ack==nonce). The daemon's read_successor sets this from the
    callback row; keying re-verify on it (not on renewed pane activity) is the H8
    fix. A read_successor impl may also consult a msg_store ACK row."""
    return bool(evidence) and evidence.get("correction_ack") == nonce
