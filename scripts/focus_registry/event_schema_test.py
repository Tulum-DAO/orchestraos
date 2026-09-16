"""Tests for the hook-event-stream versioned schema (WS-A bus seam, DEC-1786731957).

This is the lockable CONTRACT the bus is built against — same discipline as the S3
observed-dict: a versioned envelope + type map (grounded in the real Claude Code hook
names emitted by scripts/state-event-hook.py) + RCS receipt fields (delivered/read/
processing markers + nonce). Pure/no-IO/no-bus — staging only.
"""
import pytest

from scripts.focus_registry.event_schema import (
    SCHEMA_VERSION,
    EVENT_TYPES,
    hook_to_type,
    build_event,
    build_raw_event,
    validate_event,
    validate_raw_event,
    new_receipt,
    advance_receipt,
    RECEIPT_LADDER,
)


def test_hook_names_map_to_canonical_types():
    # grounded in state-event-hook.py's real Claude Code hook_event_name values.
    assert hook_to_type("SessionStart") == "session_start"
    assert hook_to_type("UserPromptSubmit") == "prompt_submit"
    assert hook_to_type("Stop") == "turn_ended"
    assert hook_to_type("SessionEnd") == "session_end"
    assert hook_to_type("PreToolUse") == "tool_use"
    assert hook_to_type("PostToolUse") == "tool_use"
    assert hook_to_type("Notification") == "notification"


def test_unknown_hook_maps_to_none():
    assert hook_to_type("Nonsense") is None


def test_build_event_has_versioned_envelope():
    e = build_event("prompt_submit", agent="agent:gm", pane="%3",
                    session_id="sid-1", cwd="/x", payload={"source": "user"})
    assert e["schema_version"] == SCHEMA_VERSION
    assert e["type"] == "prompt_submit"
    assert e["agent"] == "agent:gm"
    assert e["pane"] == "%3"
    assert e["session_id"] == "sid-1"
    assert e["cwd"] == "/x"
    assert e["payload"] == {"source": "user"}
    assert isinstance(e["ts"], float)
    assert e["event_id"]                    # unique dedup id present
    assert e["receipt"]["status"] == "emitted"


def test_event_id_is_unique_per_event():
    a = build_event("turn_ended", agent="agent:x")
    b = build_event("turn_ended", agent="agent:x")
    assert a["event_id"] != b["event_id"]


def test_validate_accepts_a_well_formed_event():
    e = build_event("session_start", agent="agent:gm", payload={"source": "startup"})
    r = validate_event(e)
    assert r["valid"] is True
    assert r["errors"] == []


def test_validate_rejects_unknown_type():
    e = build_event("session_start", agent="agent:gm")
    e["type"] = "telepathy"
    r = validate_event(e)
    assert r["valid"] is False
    assert any("type" in x for x in r["errors"])


def test_validate_rejects_wrong_schema_version():
    e = build_event("turn_ended", agent="agent:x")
    e["schema_version"] = 999
    r = validate_event(e)
    assert r["valid"] is False
    assert any("schema_version" in x for x in r["errors"])


def test_validate_rejects_missing_required_fields():
    e = build_event("turn_ended", agent="agent:x")
    del e["event_id"]
    r = validate_event(e)
    assert r["valid"] is False
    assert any("event_id" in x for x in r["errors"])


# --- RCS receipt ladder (delivered/read/processing markers + nonce) ---

def test_new_receipt_starts_emitted_with_a_nonce():
    r = new_receipt()
    assert r["status"] == "emitted"
    assert r["nonce"]
    assert r["delivered_at"] is None
    assert r["read_at"] is None
    assert r["processing_at"] is None
    assert r["acked_at"] is None


def test_receipt_advances_monotonically_stamping_timestamps():
    r = new_receipt()
    r = advance_receipt(r, "delivered")
    assert r["status"] == "delivered" and r["delivered_at"] is not None
    r = advance_receipt(r, "read")
    assert r["status"] == "read" and r["read_at"] is not None
    r = advance_receipt(r, "processing")
    assert r["status"] == "processing" and r["processing_at"] is not None
    r = advance_receipt(r, "acked")
    assert r["status"] == "acked" and r["acked_at"] is not None


def test_receipt_cannot_go_backwards():
    r = advance_receipt(new_receipt(), "processing")
    # a stale 'delivered' arriving after 'processing' must NOT regress the ladder.
    r2 = advance_receipt(r, "delivered")
    assert r2["status"] == "processing"          # unchanged
    assert RECEIPT_LADDER.index("processing") > RECEIPT_LADDER.index("delivered")


def test_receipt_unknown_status_is_ignored():
    r = advance_receipt(new_receipt(), "banana")
    assert r["status"] == "emitted"


# --- Q2 lock: split raw (pre-ingest, no agent) vs strict (post-ingest, w/ agent) ---

def test_raw_event_has_source_identity_but_null_agent():
    # what the dumb registry-free hook emits: pane/session, agent=None.
    e = build_raw_event("turn_ended", pane="%3", session_id="sid-1", cwd="/x")
    assert e["agent"] is None
    assert e["pane"] == "%3"
    assert e["type"] == "turn_ended"
    assert e["receipt"]["status"] == "emitted"


def test_validate_raw_accepts_agentless_event_with_pane():
    e = build_raw_event("prompt_submit", pane="%3", session_id=None)
    assert validate_raw_event(e)["valid"] is True


def test_validate_raw_accepts_agentless_event_with_session_only():
    e = build_raw_event("prompt_submit", pane=None, session_id="sid-9")
    assert validate_raw_event(e)["valid"] is True


def test_validate_raw_rejects_event_with_neither_pane_nor_session():
    e = build_raw_event("turn_ended", pane=None, session_id=None)
    r = validate_raw_event(e)
    assert r["valid"] is False
    assert any("pane" in x or "session" in x for x in r["errors"])


def test_validate_raw_does_not_require_agent():
    e = build_raw_event("session_start", pane="%1")
    assert "agent" not in " ".join(validate_raw_event(e)["errors"])


def test_strict_validate_still_requires_agent_post_ingest():
    # after the bus stamps agent, the strict validator applies.
    e = build_raw_event("turn_ended", pane="%3", session_id="sid-1")
    assert validate_event(e)["valid"] is False          # agent still None
    e["agent"] = "agent:gm"                              # bus resolved it at ingest
    assert validate_event(e)["valid"] is True


def test_raw_and_strict_agree_on_type_and_version():
    e = build_raw_event("prompt_submit", pane="%3")
    e["type"] = "telepathy"
    assert validate_raw_event(e)["valid"] is False
    e["type"] = "prompt_submit"
    e["schema_version"] = 999
    assert validate_raw_event(e)["valid"] is False
