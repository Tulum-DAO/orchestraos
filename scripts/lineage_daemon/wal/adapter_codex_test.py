"""RED-first tests for the codex WAL adapter (Build A, per-provider adapter).

codex live source = the rollout jsonl at ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.
Shape (verified against contract/transcript/fixtures/codex-basic.input.jsonl):
  {"type":"session_meta","payload":{"id":sid,"timestamp":..,"cwd":..}}
  {"type":"response_item","payload":{"type":"message","role":"user"|"assistant"|
      "developer","content":[{"type":"input_text"|"output_text","text":..}]}}
  {"type":"response_item","payload":{"type":"reasoning","content":".."}}
  {"type":"response_item","payload":{"type":"function_call","name":..,"arguments":..}}
  {"type":"response_item","payload":{"type":"function_call_output","output":..}}
  {"type":"event_msg","payload":{"type":"token_count","info":{"total_tokens":N}}}

DISCIPLINE (mirrors adapter_claude): byte-offset cursor, whole-newline-only
consume, model-voice bodies -> STRUCTURAL summaries (kind + size), never a
content snippet; full bodies stay in the rollout, resolved later via body_ref.

All fixture bodies here are SYNTHETIC (no real model voice) per the A0 protocol.
"""
import os
import tempfile

from lineage_daemon.wal.adapter_codex import CodexWalAdapter
from lineage_daemon.wal.store import WalStore


SID = "01a01801-b232-7760-b6f0-c6ea1d6e9152"

_ROLLOUT = "\n".join([
    '{"type":"session_meta","payload":{"id":"%s","timestamp":"2026-08-27T10:00:00Z","cwd":"/tmp/x"}}' % SID,
    '{"type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"please check status"}]}}',
    '{"type":"response_item","payload":{"type":"reasoning","content":"SYNTHETIC-THINK do a thing"}}',
    '{"type":"response_item","payload":{"type":"function_call","name":"exec_command","arguments":"{\\"CommandLine\\":\\"git status\\"}","call_id":"call_1"}}',
    '{"type":"response_item","payload":{"type":"function_call_output","call_id":"call_1","output":"On branch main\\nclean"}}',
    '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"SYNTHETIC-VOICE the tree is clean"}]}}',
    '{"type":"event_msg","payload":{"type":"token_count","info":{"total_tokens":1500}}}',
    "",
])


def _store(tmp):
    return WalStore(os.path.join(tmp, "codex.db"))


def _write(tmp, text):
    p = os.path.join(tmp, "rollout.jsonl")
    with open(p, "w") as fh:
        fh.write(text)
    return p


def test_runtime_is_codex():
    assert CodexWalAdapter.runtime == "codex"


def test_tail_emits_canonical_kinds():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        src = _write(tmp, _ROLLOUT)
        a = CodexWalAdapter(st, "codex-seat", 3)
        n = a.tail(src)
        assert n > 0
        evs = st.events()
        kinds = [e["kind"] for e in evs]
        assert "prompt" in kinds          # user message
        assert "response" in kinds        # reasoning + assistant output_text
        assert "tool_call" in kinds       # function_call
        assert "tool_result" in kinds     # function_call_output
        assert "ctx" in kinds             # token_count
        for e in evs:
            assert e["runtime"] == "codex"
            assert e["lineage_root"] == "codex-seat"
            assert e["generation"] == 3


def test_model_voice_summaries_are_structural_not_content():
    """The court-contagion vector: assistant text + reasoning summaries must be
    STRUCTURAL (size/kind), never a verbatim snippet of the model voice."""
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        src = _write(tmp, _ROLLOUT)
        CodexWalAdapter(st, "codex-seat", 0).tail(src)
        for e in st.events():
            if e["kind"] == "response":
                assert "SYNTHETIC-VOICE" not in (e["summary"] or "")
                assert "SYNTHETIC-THINK" not in (e["summary"] or "")
                assert e["body_ref"]  # body stays by-reference


def test_tool_call_summary_is_tool_name():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        src = _write(tmp, _ROLLOUT)
        CodexWalAdapter(st, "codex-seat", 0).tail(src)
        tcs = [e for e in st.events() if e["kind"] == "tool_call"]
        assert tcs and "exec_command" in (tcs[0]["summary"] or "")


def test_sid_tracked_from_session_meta():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        src = _write(tmp, _ROLLOUT)
        CodexWalAdapter(st, "codex-seat", 0).tail(src)
        # every non-meta event carries the session_meta sid
        sids = {e["sid"] for e in st.events() if e["kind"] != "marker"}
        assert SID in sids


def test_incremental_cursor_no_reread():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        src = _write(tmp, _ROLLOUT)
        a = CodexWalAdapter(st, "codex-seat", 0)
        first = a.tail(src)
        assert first > 0
        # a second tail with no new bytes appends nothing (cursor discipline)
        assert a.tail(src) == 0
        before = st.max_seq()
        # append one new complete line -> only that line is consumed
        with open(src, "a") as fh:
            fh.write('{"type":"event_msg","payload":{"type":"token_count","info":{"total_tokens":1800}}}\n')
        assert a.tail(src) == 1
        assert st.max_seq() == before + 1


def test_partial_trailing_line_not_consumed():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        # a line with no trailing newline is never a complete event
        src = _write(tmp, '{"type":"event_msg","payload":{"type":"token_count","info":{"total_tokens":1}}}')
        a = CodexWalAdapter(st, "codex-seat", 0)
        assert a.tail(src) == 0
        assert st.max_seq() == 0


def test_missing_source_is_zero_not_crash():
    with tempfile.TemporaryDirectory() as tmp:
        st = _store(tmp)
        a = CodexWalAdapter(st, "codex-seat", 0)
        assert a.tail(os.path.join(tmp, "nope.jsonl")) == 0
