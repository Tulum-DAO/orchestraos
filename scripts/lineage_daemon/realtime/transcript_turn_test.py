"""RED tests for transcript_turn_complete — E2 turn-completion signal.

The missed-Stop residual (hook file frozen at 'working' because Claude emits no
Stop on interrupt/kill) is disambiguated from a genuine mid-tool hang by the
claude transcript: the LAST real message entry tells us whether the turn COMPLETED
(assistant end_turn, no pending tool_use -> idle) or is still OPEN (assistant
pending tool_use, or a trailing user tool_result/prompt -> genuine working/stall).
"""
import json
import os

from .transcript_turn import transcript_turn_complete


def _write(path, entries):
    with open(path, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _assistant(stop_reason, content):
    return {"type": "assistant", "message": {"role": "assistant",
            "stop_reason": stop_reason, "content": content}}


def _text(s):
    return [{"type": "text", "text": s}]


def _tool_use(name="Read"):
    return [{"type": "tool_use", "id": "tu_1", "name": name, "input": {}}]


def test_completed_turn_end_turn_text_reads_complete(tmp_path):
    p = str(tmp_path / "s.jsonl")
    _write(p, [_assistant("tool_use", _tool_use()),
               {"type": "user", "message": {"role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}]}},
               _assistant("end_turn", _text("done"))])
    assert transcript_turn_complete(p) is True


def test_pending_tool_use_reads_open(tmp_path):
    # last entry = assistant that requested a tool and is waiting -> OPEN (a genuine
    # mid-tool hang must stay stalled, NOT be masked as idle).
    p = str(tmp_path / "s.jsonl")
    _write(p, [_assistant("end_turn", _text("thinking")),
               _assistant("tool_use", _tool_use("Bash"))])
    assert transcript_turn_complete(p) is False


def test_tool_use_content_even_if_stop_reason_missing_reads_open(tmp_path):
    p = str(tmp_path / "s.jsonl")
    _write(p, [_assistant(None, _tool_use())])
    assert transcript_turn_complete(p) is False


def test_trailing_user_tool_result_reads_open(tmp_path):
    # tool finished, model about to continue -> turn NOT complete.
    p = str(tmp_path / "s.jsonl")
    _write(p, [_assistant("tool_use", _tool_use()),
               {"type": "user", "message": {"role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "x"}]}}])
    assert transcript_turn_complete(p) is False


def test_trailing_meta_entries_after_end_turn_ignored(tmp_path):
    # meta entries (ai-title, mode, ...) written after the final assistant turn
    # must NOT flip the reading — the last real MESSAGE is what matters.
    p = str(tmp_path / "s.jsonl")
    _write(p, [_assistant("end_turn", _text("done")),
               {"type": "ai-title", "title": "whatever"},
               {"type": "mode", "mode": "default"}])
    assert transcript_turn_complete(p) is True


def test_missing_file_reads_unknown(tmp_path):
    assert transcript_turn_complete(str(tmp_path / "nope.jsonl")) is None


def test_none_path_reads_unknown():
    assert transcript_turn_complete(None) is None


def test_empty_file_reads_unknown(tmp_path):
    p = str(tmp_path / "empty.jsonl")
    open(p, "w").close()
    assert transcript_turn_complete(p) is None


def test_only_meta_entries_reads_unknown(tmp_path):
    p = str(tmp_path / "meta.jsonl")
    _write(p, [{"type": "ai-title", "title": "x"}, {"type": "mode", "mode": "y"}])
    assert transcript_turn_complete(p) is None


def test_truncated_first_line_in_tail_window_is_skipped(tmp_path):
    # reading only the tail bytes can slice a line mid-JSON; a partial first line
    # must be skipped, and the last COMPLETE line still classified.
    p = str(tmp_path / "s.jsonl")
    _write(p, [_assistant("end_turn", _text("A" * 500)),
               _assistant("end_turn", _text("final"))])
    # tail window slices the big first line mid-JSON but fully contains the last
    # line; the partial first line must be dropped and the last line classified.
    assert transcript_turn_complete(p, tail_bytes=200) is True


def test_end_turn_with_stop_variants_read_complete(tmp_path):
    for sr in ("end_turn", "stop", "stop_sequence", "max_tokens"):
        p = str(tmp_path / f"s_{sr}.jsonl")
        _write(p, [_assistant(sr, _text("done"))])
        assert transcript_turn_complete(p) is True, sr
