"""Confirm-and-correct CONTRACT (WS3 acceptance step 3, the operator 2026-08-14).

The load-bearing step: a rotation is only correct if the SUCCESSOR actually began
the RIGHT work. platform-builder owns WHAT 'correct work' means (this module); ob
owns the verify->correct->re-verify LOOP mechanism in the daemon.

  - expected_work(pred, handoff, store) -> the ground truth (focus + task + files).
  - check_on_track(observed, expected) -> {on_track, reasons, corrections} — pure
    comparison; ob supplies `observed` from reading the successor at source.
  - correction_message(observed, expected) -> the specific tmux-injectable text ob
    sends the successor when off-track (empty when on-track).

`observed` (built by ob from the successor's pane/jsonl/edges):
  {successor, works_on(focus_id|None), oriented(bool), state(working|idle|error|
   stranded|...), touched_files([...]), confirmed_focus(focus_id|None)}
"""
from typing import List, Optional

# agent-status states that mean the successor is NOT progressing. 'stalled' added
# per ob; 'waiting_permission' is NEUTRAL (mid-work awaiting approval, not off-track);
# 'unknown' is NEUTRAL too (a detector miss must not force a needless correction —
# orientation is confirmed separately via the online-callback arrival).
_NOT_PROGRESSING = {"idle", "stalled", "stranded", "stranded_input", "error", "errored"}


def expected_work(pred_id: str, handoff: dict, store: dict) -> dict:
    """The ground-truth spec the successor is checked against: the inherited focus
    + the specific next task/files from the authored handoff."""
    from scripts.focus_registry.resolve import focus_of

    focus = focus_of(pred_id, store)
    phase = handoff.get("phase_state", {}) or {}
    return {
        "focus_id": focus["id"] if focus else None,
        "focus_canonical": focus.get("canonical") if focus else None,
        "next_actions": handoff.get("next_3_actions", []),
        "file_roots": handoff.get("file_roots_touched", []),
        "plan_ref": phase.get("plan_ref"),
        "next_gate": phase.get("next_gate"),
    }


def _first_action(expected: dict) -> Optional[str]:
    acts = expected.get("next_actions") or []
    return acts[0] if acts else None


def check_on_track(observed: dict, expected: dict) -> dict:
    """Compare the successor's observed behavior to the expected-work ground truth.

    Off-track on ANY of: not oriented, not progressing (idle/stranded/error), wrong
    focus, or working the wrong file area. Each failure yields a SPECIFIC correction
    string ob can tmux-inject. on_track = no failures.
    """
    reasons: List[str] = []
    corrections: List[str] = []
    first = _first_action(expected)
    focus_name = expected.get("focus_canonical") or "the inherited focus"
    focus_id = expected.get("focus_id")

    # 1. oriented?
    if not observed.get("oriented"):
        reasons.append("not oriented")
        corrections.append(
            "Read your authored handoff + MEMORY.md before working — you have not oriented yet."
        )

    # 2. progressing?
    state = (observed.get("state") or "").lower()
    if state in _NOT_PROGRESSING:
        reasons.append(f"not progressing (state={state})")
        if first:
            corrections.append(f"You are {state}, not progressing. Resume the next action: {first}.")
        else:
            corrections.append(f"You are {state}, not progressing. Continue the handed-off work.")

    # 3. right focus?
    if focus_id and observed.get("works_on") != focus_id:
        on = observed.get("works_on") or "no focus"
        reasons.append(f"wrong focus ({on} != {focus_id})")
        corrections.append(
            f"You are on {on} but your inherited focus is {focus_name} ({focus_id}). "
            f"Work that focus" + (f": {first}." if first else ".")
        )

    # 4. right file area?
    roots = expected.get("file_roots") or []
    touched = observed.get("touched_files") or []
    if roots and touched and not any(any(t.startswith(r) for r in roots) for t in touched):
        reasons.append("wrong file area")
        corrections.append(
            f"You are editing {touched} but the focus work lives under {roots}"
            + (f" — {expected.get('next_gate')}." if expected.get("next_gate") else ".")
        )

    return {"on_track": not reasons, "reasons": reasons, "corrections": corrections}


def correction_message(observed: dict, expected: dict, nonce: str = None) -> str:
    """The tmux-injectable correction text (empty when on-track). Leads with the
    focus so the successor re-anchors, then the specific corrections.

    When `nonce` is set (C2 / ob §2.6), append the conditional idempotent framing so
    a healthy successor that already complied just acks (no churn) and re-verify keys
    on the ACK, not renewed activity (H8). nonce=None → unchanged behavior."""
    v = check_on_track(observed, expected)
    if v["on_track"]:
        return ""
    focus_name = expected.get("focus_canonical") or "your inherited focus"
    header = f"[rotation-correct] Refocus on {focus_name} ({expected.get('focus_id')}). "
    body = header + " ".join(v["corrections"])
    if nonce:
        body += (f"\n[correction:{nonce}] If you have already done this, "
                 f"reply ACK {nonce} and ignore; else: {' '.join(v['corrections'])}")
    return body
