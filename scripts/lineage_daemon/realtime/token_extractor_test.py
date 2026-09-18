"""RED tests for B1(b) token extractor — COURT-SCRUB GATED (audit criterion #3).

The emission-time PIN (ob, folded post-consensus): the raw pty stream is ONE
unlabeled interleaved flow — you CANNOT distinguish model-voice from tool-output
at emission time until the block boundary lands. Therefore a FLAGGED lineage =
FULL block-mode: NO pty streaming at all (not even "probably-tool-output"). The
world-output streaming allowance activates ONLY when an emission-time
discriminator is separately RED-proven (it is NOT, here).

RED against ALL 3 A0 court REDs (claude jsonl / codex jsonl / gemini protobuf):
flagged => block, ZERO verbatim leak, no live model-voice tokens. The gemini leg
also exercises normalize-before-scrub (protobuf-decode precedes any check).
"""
import json
import os
import tempfile

from lineage_daemon.realtime.lineage_flag import LineageFlagStore
from lineage_daemon.realtime.token_extractor import (
    stream_pty_delta, reconcile_clean_block, disposition_for_stream)

_FIX = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                    "contract", "transcript", "fixtures")


def _court_red(provider):
    with open(os.path.join(_FIX, provider, "a0-court-red.json")) as fh:
        return json.load(fh)


def _body(provider):
    red = _court_red(provider)
    inp = red["input"]
    return (inp.get("assistant_text_block") or inp.get("response_item_text")
            or inp.get("step_payload_decoded_text"))


# --- ANSI strip (provider-blind: a pty is a pty) ----------------------------

def test_ansi_strip_on_clean_stream():
    raw = "\x1b[38;5;111mhello\x1b[0m \x1b[1mworld\x1b[0m"
    d = stream_pty_delta("claude", raw, lineage_flagged=False, flag_readable=True)
    assert d["stream_mode"] == "clean"
    assert d["streamed"] is True
    assert d["text"] == "hello world"
    assert d["live_model_voice_tokens"] is True


# --- audit criterion #3: all 3 A0 court REDs => full block, zero leak --------

def test_all_three_court_reds_block_on_the_live_pty_path():
    for provider in ("claude", "codex", "gemini"):
        body = _body(provider)
        d = stream_pty_delta(provider, body, lineage_flagged=True, flag_readable=True)
        assert d["stream_mode"] == "block", provider
        assert d["streamed"] is False, provider
        assert d["live_model_voice_tokens"] is False, provider
        assert d["text"] == "", provider
        # ZERO verbatim bytes of the contaminated body leak into the output
        assert body[:24] not in json.dumps(d), provider


def test_all_three_court_reds_block_on_the_reconcile_path():
    # reconcile/replace-with-clean routes through Build A's normalize.render_body
    # (normalize-before-scrub, class defense). Flagged => block, zero leak.
    for provider in ("claude", "codex", "gemini"):
        body = _body(provider)
        d = reconcile_clean_block(provider, body, lineage_flagged=True, flag_readable=True)
        assert d["stream_mode"] == "block", provider
        assert d["text"] == "", provider
        assert d.get("normalized_before_check") is True, provider
        assert body[:24] not in json.dumps(d), provider


def test_gemini_protobuf_normalize_before_scrub_on_reconcile():
    # The gemini COURT WRINKLE: model voice arrives protobuf-serialized. Feed the
    # RAW protobuf blob; the reconcile MUST protobuf-decode BEFORE the flag/scrub
    # check — and (flagged) block, with zero verbatim bytes of either the raw
    # blob or the decoded text leaking.
    decoded = _body("gemini")
    # encode `decoded` as a single length-delimited protobuf string field (tag 1)
    b = decoded.encode("utf-8")
    blob = bytes([0x0A]) + _varint(len(b)) + b
    d = reconcile_clean_block("gemini", blob, lineage_flagged=True, flag_readable=True)
    assert d["stream_mode"] == "block"
    assert d["text"] == ""
    assert decoded[:24] not in json.dumps(d)


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


# --- emission-time PIN: flagged blocks EVEN probably-tool-output -------------

def test_emission_pin_blocks_probably_tool_output_on_flagged_lineage():
    # A builder may NOT read the "tool/world output may stream" allowance as
    # license to stream probably-tool-output on a flagged lineage. Full block.
    looks_like_tool_output = "$ ls -la\ntotal 24\ndrwxr-xr-x  4 user user"
    d = stream_pty_delta("claude", looks_like_tool_output,
                         lineage_flagged=True, flag_readable=True)
    assert d["stream_mode"] == "block"
    assert d["streamed"] is False
    assert d["text"] == ""


# --- fail-CLOSED -------------------------------------------------------------

def test_fail_closed_when_flag_unreadable():
    d = stream_pty_delta("claude", "anything", lineage_flagged=False,
                         flag_readable=False)
    assert d["stream_mode"] == "block"
    assert d["streamed"] is False


# --- mid-stream flag flip clean->flagged drops to block ---------------------

def test_mid_stream_flag_flip_drops_to_block():
    # a lineage that goes court MID-STREAM must drop to block on the next delta.
    clean = stream_pty_delta("claude", "still clean here",
                             lineage_flagged=False, flag_readable=True)
    assert clean["stream_mode"] == "clean"
    flipped = stream_pty_delta("claude", "now flagged content",
                               lineage_flagged=True, flag_readable=True)
    assert flipped["stream_mode"] == "block"
    assert flipped["text"] == ""


# --- integration: the flag store drives the disposition (fail-closed) --------

def test_disposition_reads_flag_store_and_blocks_missing_store():
    # store ABSENT => fail-closed => block EVERYTHING (the safe INERT default
    # until the detection path populates the denylist).
    d = disposition_for_stream("claude", "hello",
                               lineage_root="r1",
                               flag_store=LineageFlagStore("/nope/flags.json"))
    assert d["stream_mode"] == "block"


def test_disposition_streams_clean_lineage_blocks_flagged_lineage():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "lineage_flags.json")
        json.dump({"schema": "lineage-flags/v1",
                   "flagged": {"dirty": {"reason": "court"}}}, open(p, "w"))
        store = LineageFlagStore(p)
        clean = disposition_for_stream("claude", "hi", lineage_root="clean-one",
                                       flag_store=store)
        assert clean["stream_mode"] == "clean" and clean["streamed"] is True
        blocked = disposition_for_stream("claude", "hi", lineage_root="dirty",
                                         flag_store=store)
        assert blocked["stream_mode"] == "block" and blocked["streamed"] is False
