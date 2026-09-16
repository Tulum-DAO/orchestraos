"""RED-first tests for normalize-before-scrub (Build A scope item 3, court-rider-3).

The LOAD-BEARING ordering (COURT WRINKLE, per-provider, from the runbook + A0):
  every adapter normalizes provider-raw model-voice into canonical text BEFORE
  any scrub/flag check runs. For gemini this means protobuf-decode -> text FIRST
  (a claude-jsonl-tuned byte scrub can NEVER see contamination re-serialized as
  protobuf). The flagged-lineage -> block-mode fallback (class defense) applies
  to ALL providers uniformly and is provider-independent BY DESIGN.

This drives the three graduated A0 court-RED fixtures
(contract/transcript/fixtures/<provider>/a0-court-red.json) through render_body.
The contaminated bodies are SYNTHETIC sentinels (never real flagged bytes).
"""
import json
import os

from lineage_daemon.wal.normalize import normalize_body, render_body

_FIX = os.path.join(os.path.dirname(__file__), "..", "..", "..", "contract",
                    "transcript", "fixtures")


def _load_court_red(provider):
    with open(os.path.join(_FIX, provider, "a0-court-red.json")) as fh:
        return json.load(fh)


# --- protobuf-decode: gemini normalize precedes scrub ----------------------

def _pb_string_field(field_num, text):
    """Encode one length-delimited (wire type 2) protobuf string field."""
    tag = (field_num << 3) | 2
    body = text.encode("utf-8")
    return bytes([tag]) + _varint(len(body)) + body


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            break
    return bytes(out)


def _pb_message_field(field_num, inner_bytes):
    """Encode one length-delimited field whose body is a nested message."""
    tag = (field_num << 3) | 2
    return bytes([tag]) + _varint(len(inner_bytes)) + inner_bytes


def test_normalize_gemini_protobuf_to_text():
    sentinel = "SYNTHETIC-COURT-SENTINEL-DO-NOT-INGEST court court court"
    # emulate the real antigravity shape: 08 <type> then a nested message
    # (field 6) whose leaf (field 5) carries the model-voice text.
    inner = _pb_string_field(5, sentinel)
    outer = bytes([0x08, 0x0f]) + _pb_message_field(6, inner)
    text = normalize_body("gemini", outer)
    # protobuf-decode recovered the model voice as canonical text
    assert sentinel in text


def test_normalize_claude_codex_text_identity():
    # claude/codex bodies are already text; normalize is a decode-to-text identity
    assert normalize_body("claude", "hello world") == "hello world"
    assert normalize_body("codex", b"hello bytes") == "hello bytes"


# --- render_body disposition: the flag->block class defense -----------------

def test_flagged_lineage_blocks_verbatim_model_voice_all_providers():
    for provider in ("claude", "codex", "gemini"):
        fx = _load_court_red(provider)
        # recover the contaminated text this provider's serialization would carry
        if provider == "gemini":
            body = _pb_string_field(5, fx["input"]["step_payload_decoded_text"])
            secret = fx["input"]["step_payload_decoded_text"]
        elif provider == "codex":
            body = fx["input"]["response_item_text"]
            secret = body
        else:
            body = fx["input"]["assistant_text_block"]
            secret = body
        out = render_body(provider, body, lineage_flagged=True)
        assert out["stream_mode"] == "block", provider
        assert out["live_model_voice_tokens"] is False, provider
        # ZERO verbatim bytes of the (normalized) model voice leak
        assert secret not in out["text"], provider
        assert out["text"] == "", provider
        # normalize ran BEFORE the flag/scrub check (the court-wrinkle fix)
        assert out["normalized_before_check"] is True, provider


def test_fail_closed_on_unreadable_flag():
    # flag unreadable/absent-schema => block mode, never stream (rider ii)
    out = render_body("gemini", b"\x08\x0f", lineage_flagged=None, flag_readable=False)
    assert out["stream_mode"] == "block"
    assert out["text"] == ""


def test_clean_lineage_renders_normalized_text():
    out = render_body("claude", "a perfectly clean tool summary", lineage_flagged=False)
    assert out["stream_mode"] == "clean"
    assert out["text"] == "a perfectly clean tool summary"


def test_clean_lineage_known_instance_still_blocked():
    """Even on a CLEAN lineage, a KNOWN court signature (layer-2 known-instance)
    is hard-excluded — scrub runs on the NORMALIZED text."""
    from lineage_daemon.wal import court_scrub as cs
    # inject a temporary known anchor so the known-instance layer has something
    saved = cs._ANCHORS
    try:
        cs._ANCHORS = ["KNOWN-COURT-ANCHOR-xyzzy-1234567890abcdef"]
        out = render_body(
            "codex",
            "prefix KNOWN-COURT-ANCHOR-xyzzy-1234567890abcdef suffix",
            lineage_flagged=False)
        assert out["stream_mode"] == "block"
        assert out["contaminated"] is True
        assert out["text"] == ""
    finally:
        cs._ANCHORS = saved
