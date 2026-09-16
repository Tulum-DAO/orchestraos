"""Proper-self-rotation content seam (WS3 acceptance Req 2+3, the operator 2026-08-14).

The daemon does PLUMBING; this module produces the CONTENT that makes a rotation
*proper*: a focus-bearing lineage-init the successor receives (so it works the
RIGHT tasks, not a generic "read your handoff"), and the online+oriented callback
the successor fires to gm/owner. ob's daemon injects build_lineage_init() at
hard_rotate and the successor emits build_online_callback() on orientation.

Seam split: platform-builder = this content (route/focus/notify); orchestra-builder
= the daemon-internal handoff-author trigger + inject mechanics + retire; the
next-agent skill = the predecessor's authoring template for the handoff dict.
"""
from typing import Callable, Optional

from scripts.focus_registry.resolve import focus_of
from scripts.focus_registry.route import route_handoff, default_reviewer_fn

# Standing guards every successor inherits regardless of lane (from CLAUDE.md +
# fleet memory). Lane-specific guards come from the authored handoff decisions.
_STANDING_GUARDS = [
    "single-trunk: commit direct to main, no long-lived feature branches",
    "never kill live services; verify-before-completion (evidence before 'done')",
    "--dangerously-skip-permissions; multi-model-congruence for high-stakes changes",
    "hand off at ~85% ctx via next-agent; never die at 100%",
]


def inherited_focus(pred_id: str, store: dict) -> Optional[dict]:
    """The focus the successor inherits from its predecessor (WS1 focus-edge)."""
    return focus_of(pred_id, store)


def _canonical(focus: Optional[dict]) -> Optional[str]:
    return focus.get("canonical") if focus else None


def build_lineage_init(
    pred_id: str,
    successor_id: str,
    handoff: dict,
    store: dict,
    reviewer_fn: Callable[[str], Optional[str]] = default_reviewer_fn,
) -> dict:
    """The focus-bearing init the successor is injected with at hard_rotate (Req 2+3).

    Carries: the inherited focus (name/%done/category), the SPECIFIC next work
    (from the authored handoff, not generic), the guards (standing + authored
    decision rationales), and who to notify online. `focus_confirm_required` +
    `online_callback_required` make the successor prove Req 2/3 on orientation.
    """
    focus = inherited_focus(pred_id, store)
    phase = handoff.get("phase_state", {}) or {}
    guards = list(_STANDING_GUARDS)
    for d in handoff.get("decisions", []) or []:
        # handoff_schema.py decisions are {"text", "rationale"} (schema field is
        # 'text' — reading 'decision' silently dropped every authored guard).
        dec = d.get("text")
        why = d.get("rationale")
        if dec:
            guards.append(f"{dec}" + (f" (why: {why})" if why else ""))

    focus_payload = None
    if focus:
        a = focus.get("attrs", {}) or {}
        focus_payload = {
            "id": focus["id"],
            "canonical": focus.get("canonical"),
            "category": a.get("category"),
            "pct_done": a.get("pct_done"),
            "notes": a.get("notes"),
        }

    return {
        "successor": successor_id,
        "predecessor": pred_id,
        "focus": focus_payload,
        "focus_confirm_required": True,   # Req 2: name your focus on orientation
        "current_goal": handoff.get("current_goal"),
        "specific_work": {
            "next_actions": handoff.get("next_3_actions", []),
            "plan_ref": phase.get("plan_ref"),
            "phase": phase.get("phase"),
            "next_gate": phase.get("next_gate"),
        },
        "open_loops": handoff.get("open_loops", []),
        "file_roots_touched": handoff.get("file_roots_touched", []),
        "guards": guards,
        "notify": route_handoff(pred_id, store, reviewer_fn),  # gm/owner-PM/parent
        "online_callback_required": True,  # Req 3: message gm/parent on orientation
    }


def build_online_callback(
    successor_id: str,
    focus_id: Optional[str],
    notify_target: str,
    handoff: dict,
    store: dict,
    readback: Optional[dict] = None,
    canary_answers: Optional[dict] = None,
    nonce: Optional[str] = None,
) -> dict:
    """The successor's online EVIDENCE submission (RED-TEAM H4).

    NOT a confirmation — the callback is self-attestation wearing a uniform, so it
    carries EVIDENCE (the successor's generated read-back + canary answers) that the
    DAEMON grades via gate.rotation_gate against daemon-held ground truth. `claim_focus`
    is a CLAIM; 'confirmed' is the daemon's verdict, never self-asserted here.

    `correction_ack` (C2 / ob §2.6): when this callback responds to a correction, it
    echoes that correction's nonce so ob's re-verify keys on the ACK, not renewed
    activity (H8). None on the initial R1 online callback."""
    focus = (store.get("entities", {}) or {}).get(focus_id) if focus_id else None
    focus_name = _canonical(focus) or "no active focus (drift — flagged)"
    phase = handoff.get("phase_state", {}) or {}
    next_gate = phase.get("next_gate")
    goal = handoff.get("current_goal")

    body = (
        f"{successor_id} online. CLAIM (pending daemon verification): oriented on focus "
        f"{focus_name}, continuing on {next_gate or goal or 'the handed-off next actions'}. "
        f"Read-back + canary answers submitted as EVIDENCE — grade before opening the gate. "
        f"Inherited {len(handoff.get('open_loops', []))} open loop(s)."
    )
    return {
        "from": successor_id,
        "to": notify_target,
        "kind": "successor_online",
        "claim_focus": focus_id,   # a CLAIM to be graded — NOT self-confirmed
        "evidence": {"readback": readback or {}, "canary_answers": canary_answers or {}},
        "correction_ack": nonce,   # echoes a correction nonce when responding to one (H8)
        "body": body,
    }
