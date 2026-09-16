"""verify_green — the WAL-probe shadow-verify seam (gm bar #4, lossless-by-effect).

Returns True ONLY when Green provably ingested Blue's WAL: Green answers the last-K
events + working-set probe, and `grade_probe` checks that answer against the WAL
ITSELF (the WAL is the answer key — deterministic, un-gameable). This is BY EFFECT
(WAL-recall proof), NEVER a timed poll:

  * a Green still warming (no answer artifact yet)      -> False (stay PREWARMING);
  * a booted-but-wedged Green (pane alive, wrong recall)-> False (aliveness is NOT
    readiness — the premature-grade lesson);
  * a Green that answers correctly                      -> True (lossless: zero
    context/intent loss — gm bar #4).

Green surfaces its answer via the injected `answer_fn(root, green_alias) -> dict|None`
(in the live drill: an answer artifact / boot-WAL event Green writes). None means
"no answer yet" => not ready => False. verify NEVER raises on a not-ready Green;
raising is reserved for the M-beat stall bound (see the PREWARMING stall guard) and
unrecoverable errors, so the firewall's alarm/disarm is not tripped by normal warmup.
"""
from .probe import grade_probe


def verify_green(root, green_alias, *, wal_dir, store, answer_fn, k=5, scope=None,
                 live_fn=None):
    """True iff Green's probe answer matches the WAL truth (lossless ingest) AND the
    green AGENT is provably live. False while warming / on a wedged-or-lossy answer /
    on a green that is not a live agent. Never a timed pass.

    ``scope`` (bar#4 gap-c): the BLUE-AUTHORITATIVE delivered slice
    ``{since_seq, through_seq}`` — Green only received the delta after its hydrate
    baseline, so grading the full WAL would FALSE-FAIL a perfect mid-life Green.
    The caller (bg_beat) sources it from bg_state (since=first_hydrated_seq,
    through=last_hydrated_seq), never from Green. None => full-WAL grading.

    ``live_fn`` (Layer-2 green-liveness gate, DEC-1788655588): the CHANNEL probe is
    bg_arm-invoked orchestrator-side, so it passes even for a green frozen/hung
    PRE-READY (trust dialog / init hang) -> reap-blue-promote-DEAD. ``live_fn(root,
    green_alias) -> bool`` proves the green PROCESS is a live agent (>=1 assistant
    turn in its own transcript, bound to green_alias). It is ANDed with the channel
    probe (strictly stricter — never weakens bar#4) and FAIL-CLOSED (a resolver error
    => not ready). ``None`` => channel-only (back-compat for unit callers); bg_beat
    ALWAYS wires the real green_liveness.green_is_live in production."""
    try:
        answer = answer_fn(root, green_alias)
    except Exception:  # noqa: BLE001 -- a failed answer fetch = not-ready, not fatal
        return False
    if not answer:
        return False   # still warming — no answer artifact yet
    if not grade_probe(store, root, answer, k=k, scope=scope).get("ok"):
        return False   # channel/WAL-recall probe failed (lossy/wedged ingest)
    if live_fn is None:
        return True    # channel-only (unit back-compat); prod wires live_fn
    try:
        return bool(live_fn(root, green_alias))
    except Exception:  # noqa: BLE001 -- a liveness-resolver error is NOT ready
        return False
