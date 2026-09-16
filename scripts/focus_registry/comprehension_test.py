"""Tests for the comprehension gate (RED-TEAM Finding 0 + H4).

The real 'absorbed the context' gate: verifiable only by GENERATION, never by
observing reads. The successor generates a read-back (its own words) + answers
predecessor-authored canary questions; we grade MECHANICALLY (daemon-safe, no LLM)
against DAEMON-HELD ground truth. A 400-line shallow read (gen-7) must FAIL because
the deep items (open_loops/hazards buried mid/late) are not covered.
"""
from scripts.focus_registry.comprehension import (
    grade_canary,
    grade_readback,
    check_comprehension,
)

# Daemon-held ground truth (from S1's committed handoff; NEVER shown to successor
# for the canary ANSWERS).
_GT = {
    "goal": "ship the audience staging chart",
    "guards": ["single-trunk", "never co-edit agents.ts"],
    "open_loops": ["merge-9 holding land of 77eeb56", "filter-scaling fix owed to opus-11"],
    "hazards": ["prod test-row dddddddd must be cleaned", "staging logo-seed unexecuted"],
    "canary": [
        {"id": "q1", "question": "which commit is merge-9 holding?", "answer": "77eeb56"},
        {"id": "q2", "question": "what model does app-dev lineage run?", "answer": "fable-5"},
    ],
}


def test_canary_all_correct_passes():
    given = {"q1": "it's holding the land of 77eeb56", "q2": "app-dev runs fable-5"}
    r = grade_canary(given, _GT["canary"])
    assert r["passed"] is True
    assert r["missed"] == []


def test_canary_wrong_or_missing_fails_with_qid():
    given = {"q1": "77eeb56", "q2": "opus"}   # q2 wrong (not fable-5), q-missing none
    r = grade_canary(given, _GT["canary"])
    assert r["passed"] is False
    assert "q2" in r["missed"]


def test_canary_missing_answer_fails():
    r = grade_canary({"q1": "77eeb56"}, _GT["canary"])   # q2 absent
    assert r["passed"] is False
    assert "q2" in r["missed"]


def test_readback_full_deep_coverage_passes():
    # successor genuinely absorbed: cites the deep guards + open_loops + hazards.
    readback = {
        "goal": "shipping the staging chart for audiences",
        "guards": ["stay single-trunk", "don't co-edit agents.ts"],
        "open_loops": ["merge-9 is sitting on the 77eeb56 land", "owe opus-11 the filter-scaling fix"],
        "hazards": ["clean the dddddddd prod test row", "logo-seed on staging still not run"],
    }
    r = grade_readback(readback, _GT)
    assert r["passed"] is True


def test_readback_missing_guards_fails():
    # v2 §S3.4: read-back must cover goal/GUARDS/open_loops/hazards. A successor that
    # cites everything EXCEPT the standing guards has not absorbed the lane boundaries.
    no_guards = {
        "goal": "shipping the staging chart for audiences",
        "guards": [],   # did not cite single-trunk / never-co-edit-agents.ts
        "open_loops": ["merge-9 is sitting on the 77eeb56 land", "owe opus-11 the filter-scaling fix"],
        "hazards": ["clean the dddddddd prod test row", "logo-seed on staging still not run"],
    }
    r = grade_readback(no_guards, _GT)
    assert r["passed"] is False
    assert any("guard" in m.lower() or "single-trunk" in m or "agents.ts" in m for m in r["missed"])


def test_readback_full_deep_coverage_with_guards_passes():
    full = {
        "goal": "shipping the staging chart",
        "guards": ["stay single-trunk", "do not co-edit agents.ts"],
        "open_loops": ["merge-9 on the 77eeb56 land", "owe opus-11 filter-scaling"],
        "hazards": ["clean the dddddddd prod row", "logo-seed staging not run"],
    }
    assert grade_readback(full, _GT)["passed"] is True


def test_readback_header_only_shallow_read_fails():
    # gen-7: has the GOAL (header) but not the buried open_loops/hazards.
    shallow = {
        "goal": "ship the audience staging chart",
        "guards": [],
        "open_loops": ["there are some open items"],   # generic, no deep tokens
        "hazards": [],
    }
    r = grade_readback(shallow, _GT)
    assert r["passed"] is False
    # names what was missed (the deep tokens)
    assert any("77eeb56" in m or "merge-9" in m for m in r["missed"]) or r["missed"]


def test_check_comprehension_requires_both_canary_and_readback():
    good_readback = {
        "goal": "staging chart", "guards": ["single-trunk", "agents.ts"],
        "open_loops": ["merge-9 77eeb56", "filter-scaling opus-11"],
        "hazards": ["dddddddd test-row", "logo-seed staging"],
    }
    good_canary = {"q1": "77eeb56", "q2": "fable-5"}
    assert check_comprehension({"readback": good_readback, "canary_answers": good_canary}, _GT)["comprehended"] is True
    # canary fails -> not comprehended even with good readback
    assert check_comprehension({"readback": good_readback, "canary_answers": {"q1": "77eeb56", "q2": "opus"}}, _GT)["comprehended"] is False
    # readback fails -> not comprehended even with good canary
    assert check_comprehension({"readback": {"goal": "x", "open_loops": [], "hazards": []}, "canary_answers": good_canary}, _GT)["comprehended"] is False
