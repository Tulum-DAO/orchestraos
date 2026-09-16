"""lane_charter — ONE HOME for the G1/G3 charter predicate (the operator directive
2026-08-19, docs/SPEC_lane-charter-gate-and-done-hook.md).

Extracted from message-router.py so BOTH sides import the same rule instead
of copying it:
  * G1 (receipt side)  — the router holds off-charter drive-class mail.
  * G3 leg-1 (send side, gm's dispatch harness) — the SAME check pre-send,
    which gm's own measurement shows is the leg that actually catches the
    off-lane flood (the unacked-brake, leg-2, misses a responsive floodee).

`message-router.py` is not a legal module name (hyphen), so a second copy was
the only alternative — exactly the drift 'one rule, one home' forbids. Import
from here: `from lane_charter import charter_gate, load_charter`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))

# The drive-class message types the charter gates. Communication
# (reply/report/decision/ack/status) is never gated — the bumper gates WORK,
# not mail.
DRIVE_CLASS_TYPES = ("task_request", "directive", "request")


def load_charter(agent_id: str) -> dict | None:
    """A lane's charter, or None. ONE pinned location resolved from the agent
    id (the T4 path law): state/lanes/<agent_id>.charter.json. Absent or
    malformed -> None: lanes OPT IN — a bumper that holds the whole fleet by
    default is the idle-driver false-escalation defect wearing a new name."""
    p = ORCHESTRA_DIR / "state" / "lanes" / f"{agent_id}.charter.json"
    try:
        obj = json.loads(p.read_text())
        return obj if isinstance(obj, dict) and obj.get("accepts") else None
    except (OSError, ValueError):
        return None


def charter_gate(msg, charter, *, recipient_provisioning: bool = False) -> str | None:
    """THE bumper predicate. Returns a HOLD reason (caller stamps + logs +
    skips/refuses; the message is never dropped or status-mangled), or None =
    allow.

    Holds ONLY when ALL of: drive-class type; recipient HAS a charter;
    recipient is NOT provisioning; sender/source is not shaw; no
    charter_override; and contributes_to is missing/unmatched against the
    charter's accepts tags (case-insensitive substring either direction). The
    hold reason NAMES both sides of the mismatch — a first false hold must be
    diagnosable, not authoritative (the T4 print-the-path law). 'already-held'
    short-circuits re-decisions.

    BOOTSTRAP EXEMPTION (ob lived it, msg_6c15fe27 / contract f6009ffdd): a
    successor's DURABLE SPAWN ORDERS are task_request — drive-class — and carry
    NO lane tag by nature, because the newborn does not yet know its lane. If
    its canonical name already has a charter, this gate would hold the very
    mail that tells it what its lane IS. So a PROVISIONING recipient is exempt:
    it is being oriented, not dispatched off-lane. Orientation-TYPE mail
    (spawn_orders/handoff/canary) is already exempt by not being drive-class;
    this closes the task_request-typed-orders-to-a-chartered-newborn gap that
    type alone misses.

    Pure and side-effect-free so the send side (G3 leg-1) and the receipt side
    (G1) share ONE verdict; the caller decides refuse-vs-hold."""
    if msg.get("type") not in DRIVE_CLASS_TYPES:
        return None
    if recipient_provisioning:
        return None
    if not charter:
        return None
    if msg.get("from_agent") == "operator" or msg.get("source") == "operator":
        return None
    md = msg.get("metadata")
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except (ValueError, TypeError):
            md = {}
    if not isinstance(md, dict):
        md = {}
    if md.get("charter_held_at"):
        return "already-held"
    if md.get("charter_override"):
        return None
    accepts = [str(t).lower() for t in (charter.get("accepts") or []) if t]
    contrib = str(md.get("contributes_to") or "").lower()
    if contrib:
        for tag in accepts:
            if tag in contrib or contrib in tag:
                return None
    lane = charter.get("lane") or msg.get("to_agent")
    return (f"off-charter drive-class HELD: contributes_to="
            f"{md.get('contributes_to')!r} matches no accepts tag of lane "
            f"{lane!r} (accepts={accepts}; charter at state/lanes/"
            f"{msg.get('to_agent')}.charter.json). Resend with a matching "
            f"contributes_to or an explicit charter_override reason — "
            f"possible, expensive, never silent.")
