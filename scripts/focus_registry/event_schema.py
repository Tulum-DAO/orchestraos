"""Hook-event-stream versioned schema (WS-A message bus seam, DEC-1786731957).

The lockable CONTRACT the bus is built against — SAME DISCIPLINE as the S3
observed-dict: define the shape once, keep it pure/versioned, and let the bus
(ob's successor) build against it. This is DRAFT/STAGING (no bus, no cron, no live
emit); it locks BEFORE any bus code so the wire is fast + stable.

GROUNDED in the real emitter (scripts/state-event-hook.py), which fires on the
actual Claude Code hook_event_name values. The bus generalises that last-write-wins
per-pane snapshot into an append event STREAM with per-event identity + receipts.

RCS = Receipt / delivery-Confirmation / Status. Every event carries a receipt whose
status climbs a monotonic ladder (emitted → delivered → read → processing → acked),
each rung stamping a timestamp — the effects-not-inference discipline (Finding 0.5)
generalised to the bus: a consumer stamps the RECEIPT, so "did it land / is it being
worked" is a checked field, never guessed from activity. The `nonce` correlates an
event with its ack (same role as the C2 correction_ack).
"""
import time
import uuid
from typing import Optional

SCHEMA_VERSION = 1

# Canonical stream event types (bus vocabulary).
EVENT_TYPES = (
    "session_start",
    "prompt_submit",
    "turn_ended",
    "session_end",
    "tool_use",
    "notification",
)

# Real Claude Code hook_event_name → canonical type (grounded in state-event-hook.py).
_HOOK_MAP = {
    "SessionStart": "session_start",
    "UserPromptSubmit": "prompt_submit",
    "Stop": "turn_ended",
    "SessionEnd": "session_end",
    "PreToolUse": "tool_use",
    "PostToolUse": "tool_use",
    "Notification": "notification",
}

# The RCS receipt status ladder (monotonic; never regresses).
RECEIPT_LADDER = ("emitted", "delivered", "read", "processing", "acked")

# Fields every event carries. `agent` is stamped by the bus at INGEST (Q2 lock), so
# the pre-ingest RAW validator does not require it (the dumb hook is registry-free).
_BASE_REQUIRED = ("schema_version", "event_id", "type", "ts", "receipt")
_REQUIRED_FIELDS = _BASE_REQUIRED + ("agent",)  # strict / post-ingest


def hook_to_type(hook_event_name: str) -> Optional[str]:
    """Map a Claude Code hook name to a canonical stream type, or None if unmapped."""
    return _HOOK_MAP.get(hook_event_name)


def new_receipt(nonce: Optional[str] = None) -> dict:
    """A fresh RCS receipt at 'emitted' with a correlation nonce and empty markers."""
    return {
        "nonce": nonce or uuid.uuid4().hex[:12],
        "status": "emitted",
        "emitted_at": time.time(),
        "delivered_at": None,
        "read_at": None,
        "processing_at": None,
        "acked_at": None,
    }


def advance_receipt(receipt: dict, to_status: str) -> dict:
    """Advance a receipt to `to_status`, stamping the matching timestamp. Monotonic:
    a stale/earlier status is IGNORED (never regresses). Unknown status is ignored.
    Returns a NEW dict (pure)."""
    if to_status not in RECEIPT_LADDER:
        return dict(receipt)
    cur = receipt.get("status", "emitted")
    if RECEIPT_LADDER.index(to_status) <= RECEIPT_LADDER.index(cur):
        return dict(receipt)
    out = dict(receipt)
    out["status"] = to_status
    stamp = {"delivered": "delivered_at", "read": "read_at",
             "processing": "processing_at", "acked": "acked_at"}.get(to_status)
    if stamp:
        out[stamp] = time.time()
    return out


def build_event(event_type: str, agent: str = None, pane: str = None,
                session_id: str = None, cwd: str = None, payload: dict = None,
                nonce: Optional[str] = None) -> dict:
    """Build a versioned stream event envelope. Pure; no IO, no emit."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "type": event_type,
        "ts": time.time(),
        "agent": agent,          # canonical entity id, resolved from pane/session
        "pane": pane,
        "session_id": session_id,
        "cwd": cwd,
        "payload": payload or {},
        "receipt": new_receipt(nonce),
    }


def build_raw_event(event_type: str, pane: str = None, session_id: str = None,
                    cwd: str = None, payload: dict = None,
                    nonce: Optional[str] = None) -> dict:
    """Build the PRE-INGEST raw event the dumb, registry-free hook emits: source
    identity (pane/session/cwd) only, agent=None (the bus resolves it at ingest, Q2)."""
    return build_event(event_type, agent=None, pane=pane, session_id=session_id,
                       cwd=cwd, payload=payload, nonce=nonce)


def _validate_core(event: dict, required) -> list:
    """Shared structural checks (envelope + type + version + receipt). Returns errors."""
    errors = []
    for f in required:
        if f not in event or event.get(f) in (None, ""):
            errors.append(f"missing required field: {f}")
    if event.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if event.get("type") not in EVENT_TYPES:
        errors.append(f"unknown type: {event.get('type')}")
    receipt = event.get("receipt")
    if isinstance(receipt, dict):
        if receipt.get("status") not in RECEIPT_LADDER:
            errors.append(f"receipt.status invalid: {receipt.get('status')}")
        if not receipt.get("nonce"):
            errors.append("receipt.nonce missing")
    else:
        errors.append("receipt missing or not a dict")
    return errors


def validate_raw_event(event: dict) -> dict:
    """PRE-INGEST validation (Q2 lock): the raw event the hook appends. Does NOT
    require `agent` (registry-free hook), but requires source identity — pane OR
    session_id — so the bus can resolve the agent at ingest. Returns {valid, errors}."""
    if not isinstance(event, dict):
        return {"valid": False, "errors": ["event is not a dict"]}
    errors = _validate_core(event, _BASE_REQUIRED)
    if not (event.get("pane") or event.get("session_id")):
        errors.append("raw event needs pane OR session_id (source identity)")
    return {"valid": not errors, "errors": errors}


def validate_event(event: dict) -> dict:
    """POST-INGEST strict validation. Requires the bus-stamped `agent` plus the core.
    The bus calls validate_raw_event at append, then validate_event after it stamps
    the canonical agent. Fail-closed on malformed — the schema is the contract."""
    if not isinstance(event, dict):
        return {"valid": False, "errors": ["event is not a dict"]}
    errors = _validate_core(event, _REQUIRED_FIELDS)
    return {"valid": not errors, "errors": errors}
