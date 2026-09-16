"""Tests for handoff richness + staleness predicates (RED-TEAM H2).

'Deathbed authoring + syntax-only validation = unenforceably hollow handoffs.' These
are the pure min-richness predicates for Handoff.validate() (ob owns the file; these
are importable so a garbage-but-parseable handoff FAILS the gate, not just syntax).
"""
from scripts.focus_registry.richness import check_richness, check_staleness, min_decisions_for

_RICH = {
    "current_goal": "ship the audience staging chart",
    "phase_state": {"next_gate": "chart renders on audience 421e3319"},
    "decisions": [
        {"text": "single-trunk", "rationale": "avoids the 331-line drift incident"},
        {"text": "never co-edit agents.ts", "rationale": "chatmode owns it"},
    ],
    "open_loops": ["merge-9 holding 77eeb56"],
    "hazards": ["prod test-row dddddddd", "logo-seed unexecuted", "meta unblock owed"],
    "canary_questions": [
        {"id": "q1", "question": "which commit is merge-9 holding?", "answer": "77eeb56"},
        {"id": "q2", "question": "what model does app-dev run?", "answer": "fable-5"},
        {"id": "q3", "question": "what did the operator rule about $99?", "answer": "no-$99"},
    ],
}


def test_rich_handoff_passes():
    r = check_richness(_RICH)
    assert r["rich"] is True
    assert r["reasons"] == []


def test_empty_next_gate_fails():
    h = {**_RICH, "phase_state": {"next_gate": ""}}
    r = check_richness(h)
    assert r["rich"] is False
    assert any("next_gate" in x for x in r["reasons"])


def test_too_few_decisions_with_rationale_fails():
    h = {**_RICH, "decisions": [{"text": "x", "rationale": ""}]}  # rationale-less
    r = check_richness(h)
    assert r["rich"] is False
    assert any("decision" in x.lower() for x in r["reasons"])


def test_too_few_canary_questions_fails():
    h = {**_RICH, "canary_questions": [{"id": "q1", "question": "?", "answer": "a"}]}
    r = check_richness(h)
    assert r["rich"] is False
    assert any("canary" in x.lower() for x in r["reasons"])


def test_no_hazards_fails():
    h = {**_RICH, "hazards": []}
    r = check_richness(h)
    assert r["rich"] is False
    assert any("hazard" in x.lower() for x in r["reasons"])


def test_no_open_loops_fails():
    h = {**_RICH, "open_loops": []}
    r = check_richness(h)
    assert r["rich"] is False


def test_staleness_detects_a_handoff_used_hours_after_authoring():
    # authored at t=1000, used at t=1000+2h -> stale for a 1h bound.
    r = check_staleness(handoff_mtime=1000.0, now=1000.0 + 7200, max_age_s=3600)
    assert r["stale"] is True
    assert r["age_s"] == 7200


def test_fresh_handoff_is_not_stale():
    r = check_staleness(handoff_mtime=1000.0, now=1000.0 + 60, max_age_s=3600)
    assert r["stale"] is False


# --- C4: min_decisions scaled by session length = clamp(1, ceil(turns/60), 3) ---

def test_min_decisions_curve():
    assert min_decisions_for(0) == 1
    assert min_decisions_for(59) == 1
    assert min_decisions_for(60) == 1
    assert min_decisions_for(61) == 2      # ceil(61/60)=2
    assert min_decisions_for(120) == 2
    assert min_decisions_for(121) == 3     # ceil(121/60)=3
    assert min_decisions_for(600) == 3     # clamped at 3


def test_check_richness_honors_scaled_min_decisions():
    # a handoff with exactly 2 good decisions passes at min=2 but fails at min=3.
    h = {**_RICH}  # _RICH has 2 decisions with rationale
    assert check_richness(h, min_decisions=2)["rich"] is True
    r3 = check_richness(h, min_decisions=3)
    assert r3["rich"] is False
    assert any("decision" in x.lower() for x in r3["reasons"])


def test_check_richness_min_decisions_from_session_turns():
    # a short session (<=60 turns) only needs 1 decision.
    short = {**_RICH, "decisions": [{"text": "x", "rationale": "y"}]}
    assert check_richness(short, min_decisions=min_decisions_for(30))["rich"] is True
    # a long session (>120 turns) needs 3 -> the same handoff fails.
    assert check_richness(short, min_decisions=min_decisions_for(200))["rich"] is False
