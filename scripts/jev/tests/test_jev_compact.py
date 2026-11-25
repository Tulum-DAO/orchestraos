"""Level 7: the four-question boundary rubric, tiers, transcript reading, and the hook pair."""
import json
import os

import pytest

from _jevtest import answers_decide, choice_a, noul_a, score_a
from jev import compact
from jev.client import JevTransportError
from jev.types import validate_questions

LINES = {"notice": 600, "recommend": 700, "request": 800}


def ans(switched=0.1, boundary=0.1, history=2.0, mid=0.1):
    return {"switched_gears": noul_a(switched), "at_boundary": noul_a(boundary),
            "needs_history": score_a(history), "mid_operation": noul_a(mid)}


def use(tokens, window=1000):
    return {"tokens": tokens, "pct": tokens / window * 100}


def test_questions_are_valid_wire_blocks():
    validate_questions(compact.COMPACT_QUESTIONS)
    assert set(compact.COMPACT_QUESTIONS) == {"switched_gears", "at_boundary", "needs_history", "mid_operation"}


@pytest.mark.parametrize("a, tokens, compactable, tier, why", [
    (ans(switched=0.9), 900, False, "silent", "nothing to compact"),
    (ans(switched=0.9, mid=0.61), 900, True, "silent", "mid operation"),
    (ans(switched=0.7, boundary=0.9, history=1.0), 900, True, "silent", "same work"),
    (ans(switched=0.9), 599, True, "silent", "below the notice"),
    (ans(switched=0.9), 600, True, "notice", "task changed"),
    (ans(boundary=0.61, history=0.9), 700, True, "recommend", "unit of work"),
    (ans(switched=0.9), 800, True, "request", "task changed"),
])
def test_decide_tier_matrix(a, tokens, compactable, tier, why):
    d = compact.decide_tier(a, use(tokens), compactable, LINES)
    assert d["tier"] == tier and why.lower() in d["reason"].lower()


def test_tier_messages_and_rotation_actions():
    assert compact.tier_message({"tier": "silent", "reason": "x"}, use(1)) is None
    for tier, word in (("notice", "optional"), ("recommend", "Recommended"), ("request", "Please rotate")):
        assert word in compact.tier_message({"tier": tier, "reason": "R."}, use(750_000, 1_000_000))
    assert compact.ROTATION_ACTIONS == {"silent": "none", "notice": "notice", "recommend": "recommend_rotation",
                                        "request": "prewarm_green_swap"}


def test_verdict_shape():
    v = compact.compact_verdict({"tier": "request", "reason": "r"}, ans(switched=0.9), use(850), LINES)
    assert v["should_compact"] and v["rotation_action"] == "prewarm_green_swap"
    assert v["signals"] == {"switched_gears": 0.9, "at_boundary": 0.1, "needs_history": 2.0, "mid_operation": 0.1}
    assert not compact.compact_verdict({"tier": "notice", "reason": "r"}, ans(), use(650), LINES)["should_compact"]


def test_lines():
    assert compact.lines_for_window(1_000_000) == {"notice": 600_000, "recommend": 700_000, "request": 800_000}
    with pytest.raises(ValueError):
        compact.lines_for_window(1000, (0.8, 0.7, 0.9))


def test_env_lines(monkeypatch):
    assert compact.env_lines(1000) == {"notice": 600, "recommend": 700, "request": 800}
    monkeypatch.setenv("JEV_COMPACT_LINES", "0.5,0.6,0.7")
    assert compact.env_lines(1000) == {"notice": 500, "recommend": 600, "request": 700}
    monkeypatch.setenv("JEV_COMPACT_LINES", "6000,10000,14000")
    assert compact.env_lines(1000) == compact.DEFAULT_LINES
    monkeypatch.setenv("JEV_COMPACT_LINES", "1,2")
    with pytest.raises(ValueError):
        compact.env_lines(1000)


def test_cut_point():
    turns = [{"index": 0, "request": "fix auth"}, {"index": 1, "request": "build billing"}]
    q = compact.cut_point_question(turns)
    validate_questions(q)
    assert set(q["live_from"]["criteria"]) == {"0", "1", "none"}
    cp = compact.cut_point_instructions(turns, choice_a("1", 0.8))
    assert cp["live_from"] == 1 and '"build billing"' in cp["instructions"]
    assert compact.cut_point_instructions(turns, choice_a("1", 0.5))["live_from"] is None
    assert compact.cut_point_instructions(turns, choice_a("none", 0.9))["live_from"] is None


# ---------------------------------------------------------------- transcript

def entry(kind, content, **kw):
    e = {"type": kind, "message": {"role": kind, "content": content}}
    e.update(kw)
    return e


def assistant(blocks, tokens=None):
    e = entry("assistant", blocks)
    if tokens is not None:
        e["message"]["usage"] = {"input_tokens": 1, "cache_creation_input_tokens": tokens - 11,
                                 "cache_read_input_tokens": 0, "output_tokens": 10}
    return e


def write_transcript(path, entries):
    with open(path, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
        fh.write('{"type": "assistant", "torn')  # a writer mid-append
    return str(path)


TRANSCRIPT = [
    entry("user", "fix the auth session timeout bug"),
    assistant([{"type": "text", "text": "Looking."}, {"type": "tool_use", "name": "Read"}], tokens=30_000),
    entry("user", [{"type": "tool_result", "content": "file"}]),
    entry("user", [{"type": "tool_result", "content": "out"}, {"type": "text", "text": "<system-reminder>x</system-reminder>"}]),
    entry("user", "meta note", isMeta=True),
    entry("user", "sidechain", isSidechain=True),
    entry("user", "Summary: auth fixed", isCompactSummary=True),
    entry("user", [{"type": "text", "text": "now build the billing export page"}]),
    assistant([{"type": "tool_use", "name": "Edit"}, {"type": "tool_use", "name": "Bash"}], tokens=40_000),
    assistant([{"type": "text", "text": "Tests passed and committed."}], tokens=750_000),
]


def test_transcript_snapshot(tmp_path):
    snap = compact.transcript_snapshot(compact.read_transcript(write_transcript(tmp_path / "t.jsonl", TRANSCRIPT)))
    s = snap["state"]
    assert snap["prompts"] == 2 and snap["tokens"] == 750_000
    assert s["current_request"] == "now build the billing export page"
    assert "fix the auth" in s["previous_work"] and "Summary so far: Summary: auth fixed" in s["previous_work"]
    assert s["recent_turn"] == "Tests passed and committed."
    assert s["tools_this_turn"] == ["Edit", "Bash"]
    assert [t["index"] for t in snap["turns"]] == [0, 1]


def test_first_request_makes_no_call():
    snap = {"state": {}, "prompts": 1, "tokens": 900_000, "turns": []}
    ev = compact.evaluate(snap, 1_000_000, compact.lines_for_window(1_000_000), 20_000, decide=None)
    assert ev["skipped"] and ev["decision"]["tier"] == "silent" and ev["message"] is None


def test_stop_then_prompt_injects_once(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", TRANSCRIPT)
    calls = []
    compact.stop_hook({"session_id": "sess-1", "transcript_path": t}, answers_decide(ans(switched=0.95), calls))
    assert calls and calls[0][1] is compact.COMPACT_QUESTIONS
    v = compact.latest_verdict("sess-1")
    assert v["tier"] == "recommend" and v["rotation_action"] == "recommend_rotation" and v["delivered"] is False
    assert os.path.dirname(compact.verdict_path("sess-1")) == os.environ["JEV_STATE_DIR"]
    first = compact.user_prompt_submit_hook({"session_id": "sess-1"})
    assert first["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert first["hookSpecificOutput"]["additionalContext"].startswith("[jev-compact] Recommended")
    assert compact.user_prompt_submit_hook({"session_id": "sess-1"}) is None


def test_silent_verdict_injects_nothing(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", TRANSCRIPT)
    compact.stop_hook({"session_id": "s2", "transcript_path": t}, answers_decide(ans()))
    assert compact.latest_verdict("s2")["tier"] == "silent"
    assert compact.user_prompt_submit_hook({"session_id": "s2"}) is None


def test_stop_hook_guards(tmp_path):
    def boom(s, q):
        raise JevTransportError("down")
    t = write_transcript(tmp_path / "t.jsonl", TRANSCRIPT)
    assert compact.stop_hook({"session_id": "s3", "transcript_path": t, "stop_hook_active": True}, boom) is None
    assert compact.stop_hook({"session_id": "s3", "transcript_path": str(tmp_path / "nope")}, boom) is None
    assert compact.stop_hook({"session_id": "s3", "transcript_path": t}, boom) is None
    assert compact.latest_verdict("s3") is None
    assert "JevTransportError" in open(os.environ["JEV_LOG"]).read()


def test_session_id_is_sanitized():
    assert compact.verdict_path("../../etc/passwd").endswith("etcpasswd.json")
    with pytest.raises(ValueError):
        compact.verdict_path("../..")


def test_should_i_compact_on_demand(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_CONTEXT_WINDOW", "1000000")
    t = write_transcript(tmp_path / "t.jsonl", TRANSCRIPT)
    v = compact.should_i_compact(t, answers_decide(ans(switched=0.95)))
    assert v["tier"] == "recommend" and v["context_percent"] == 75.0
