"""Tool-pair repair (scripts/history_sanitize.py).

Ported to Python from CopilotKit/OpenBot `toolkit/tests/history-sanitize.test.ts` (commit 743649e).
Copyright (c) 2026 CopilotKit. MIT, see licenses/copilotkit-openbot-MIT.txt.
"""
import copy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from history_sanitize import sanitize_history  # noqa: E402


def call(cid, name="search", args="{}"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def ids(rows):
    return [m["id"] for m in rows]


def test_drops_an_unanswered_call_and_keeps_the_answered_one_with_all_text_intact():
    out = sanitize_history([
        {"id": "m1", "role": "user", "content": "Look two things up."},
        {"id": "m2", "role": "assistant", "content": "Looking them up.",
         "tool_calls": [call("call_answered"), call("call_dangling")]},
        {"id": "m3", "role": "tool", "content": "found x", "tool_call_id": "call_answered"},
        {"id": "m4", "role": "assistant", "content": "Here is x."},
    ])
    assert ids(out) == ["m1", "m2", "m3", "m4"]
    assert out[1]["content"] == "Looking them up." and out[1]["tool_calls"] == [call("call_answered")]


def test_drops_an_assistant_message_whose_only_content_was_a_dangling_call():
    out = sanitize_history([
        {"id": "m1", "role": "user", "content": "Look it up."},
        {"id": "m2", "role": "assistant", "tool_calls": [call("call_dangling")]},
        {"id": "m3", "role": "user", "content": "Anything?"},
    ])
    assert ids(out) == ["m1", "m3"]


def test_keeps_text_a_message_did_say_minus_the_call_it_cannot_complete():
    out = sanitize_history([{"id": "m1", "role": "assistant", "content": "Let me check.",
                             "tool_calls": [call("call_dangling")]}])
    assert out == [{"id": "m1", "role": "assistant", "content": "Let me check."}]


def test_drops_an_orphaned_tool_result():
    out = sanitize_history([
        {"id": "m1", "role": "user", "content": "Hello."},
        {"id": "m2", "role": "tool", "content": "left over", "tool_call_id": "gone"},
        {"id": "m3", "role": "assistant", "content": "Hello back."},
    ])
    assert ids(out) == ["m1", "m3"]


def test_a_result_that_lands_after_a_later_user_message_answers_nothing():
    out = sanitize_history([
        {"id": "u1", "role": "user", "content": "do it"},
        {"id": "a1", "role": "assistant", "content": "", "tool_calls": [call("late", "x")]},
        {"id": "u2", "role": "user", "content": "also this"},
        {"id": "t1", "role": "tool", "tool_call_id": "late", "content": "arrived too late"},
    ])
    assert ids(out) == ["u1", "u2"]


def test_a_result_ahead_of_its_own_call_answers_nothing():
    out = sanitize_history([
        {"id": "m1", "role": "tool", "content": "early", "tool_call_id": "call_1"},
        {"id": "m2", "role": "assistant", "tool_calls": [call("call_1")]},
    ])
    assert out == []


def test_a_result_that_lands_after_a_system_row_still_answers_its_call():
    out = sanitize_history([
        {"id": "u1", "role": "user", "content": "do it"},
        {"id": "a1", "role": "assistant", "tool_calls": [call("call_1")]},
        {"id": "s1", "role": "system", "content": "Use the audit skill."},
        {"id": "t1", "role": "tool", "content": "done", "tool_call_id": "call_1"},
        {"id": "u2", "role": "user", "content": "and then?"},
    ])
    assert ids(out) == ["u1", "a1", "s1", "t1", "u2"]


def test_a_result_ahead_of_its_call_is_dropped_even_when_a_real_answer_follows():
    out = sanitize_history([
        {"id": "m1", "role": "tool", "content": "early", "tool_call_id": "call_1"},
        {"id": "m2", "role": "assistant", "tool_calls": [call("call_1")]},
        {"id": "m3", "role": "tool", "content": "on time", "tool_call_id": "call_1"},
    ])
    assert ids(out) == ["m2", "m3"]


def test_a_second_answer_to_a_call_already_answered_is_dropped():
    out = sanitize_history([
        {"id": "m1", "role": "assistant", "tool_calls": [call("call_1")]},
        {"id": "m2", "role": "tool", "content": "first", "tool_call_id": "call_1"},
        {"id": "m3", "role": "tool", "content": "again", "tool_call_id": "call_1"},
    ])
    assert ids(out) == ["m1", "m2"]


def test_a_clean_history_passes_through_unchanged_object_for_object():
    clean = [
        {"id": "m1", "role": "user", "content": "Look it up."},
        {"id": "m2", "role": "assistant", "content": "Looking it up.", "tool_calls": [call("call_1")]},
        {"id": "m3", "role": "tool", "content": "found", "tool_call_id": "call_1"},
        {"id": "m4", "role": "assistant", "content": "Here it is."},
    ]
    snapshot = copy.deepcopy(clean)
    out = sanitize_history(clean)
    assert out == snapshot
    assert all(a is b for a, b in zip(out, clean))
    assert clean == snapshot, "the caller's list was not mutated"


# --- a call answered elsewhere survives ----------------------------------------------------------------

def test_an_id_named_by_the_caller_is_kept_and_the_message_is_the_very_same_object():
    rows = [{"id": "m1", "role": "user", "content": "Book it."},
            {"id": "m2", "role": "assistant", "content": "Asking first.", "tool_calls": [call("interrupt_1", "confirm")]}]
    out = sanitize_history(rows, {"interrupt_1"})
    assert len(out) == 2 and out[1] is rows[1]


def test_it_also_saves_a_message_that_would_otherwise_have_been_dropped_as_a_husk():
    rows = [{"id": "m1", "role": "assistant", "tool_calls": [call("interrupt_1")]}]
    assert len(sanitize_history(rows, {"interrupt_1"})) == 1
    assert sanitize_history(rows) == []


def test_only_the_named_ids_are_spared_alongside_the_ones_the_history_answers():
    out = sanitize_history([
        {"id": "m1", "role": "assistant", "content": "Two things.",
         "tool_calls": [call("interrupt_1"), call("call_answered"), call("call_dangling")]},
        {"id": "m2", "role": "tool", "content": "ok", "tool_call_id": "call_answered"},
    ], {"interrupt_1"})
    assert out[0]["tool_calls"] == [call("interrupt_1"), call("call_answered")]


def test_an_empty_set_is_the_default_and_changes_nothing():
    rows = [{"id": "m1", "role": "assistant", "tool_calls": [call("call_dangling")]}]
    assert sanitize_history(rows, set()) == sanitize_history(rows)


# --- the four shapes a seeded history arrives with, at once (OrchestraOS) -------------------------------

def test_a_broken_history_is_repaired_not_passed_through():
    out = sanitize_history([
        {"id": "u1", "role": "user", "content": "Build the Example report."},
        {"id": "a1", "role": "assistant", "content": "", "tool_calls": [call("t_no_result", "Bash")]},   # no result
        {"id": "r0", "role": "tool", "content": "stray", "tool_call_id": "t_orphan"},                    # orphan
        {"id": "a2", "role": "assistant", "content": "Writing it.", "tool_calls": [call("t_late", "Write")]},
        {"id": "u2", "role": "user", "content": "And add the Sample chart."},
        {"id": "r1", "role": "tool", "content": "written", "tool_call_id": "t_late"},                    # too late
        {"id": "a3", "role": "assistant", "content": []},                                                # empty husk
        {"id": "a4", "role": "assistant", "content": "Done: chart added."},
    ])
    assert ids(out) == ["u1", "a2", "u2", "a4"]
    assert "tool_calls" not in out[1] and out[1]["content"] == "Writing it."
