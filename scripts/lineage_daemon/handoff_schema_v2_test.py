"""v2 red-team-hardened handoff_schema additions (DEC-1786724046):
canary_questions/hazards/first_effect fields, to_successor_init() redaction
(anti-Goodhart), inline richness thresholds, richness-gated validate()."""

from scripts.lineage_daemon.handoff_schema import (
    Handoff, min_decisions_for, is_stale, RICHNESS_MIN_CANARY,
)


def _rich_handoff():
    """A handoff that PASSES the richness gate."""
    return Handoff(
        current_goal="continue WS3 build",
        working_state="mid-build",
        open_loops=["wire the content-gate"],
        decisions=[{"text": "use effects substrate", "rationale": "no lag"}],
        next_3_actions=["do the thing"],
        canary_questions=[
            {"id": "q1", "question": "why fable for app-dev?",
             "source_pointer": "jsonl:msg_f1f2162d"},
            {"id": "q2", "question": "what is the 8a guard?",
             "source_pointer": "jsonl:turn-412"},
            {"id": "q3", "question": "port reserved?",
             "source_pointer": "jsonl:2026-08-15T04:10Z..04:20Z"},
        ],
        hazards=["shared dirty main tree", "gm mid-rotation"],
        first_effect={"kind": "file", "target": "docs/x.md", "check": None},
    )
    from scripts.lineage_daemon.handoff_schema import PhaseState  # noqa


# --- new fields round-trip ---

def test_new_fields_roundtrip():
    h = _rich_handoff()
    h.phase_state.next_gate = "gate-A"
    d = h.to_dict()
    assert d["canary_questions"][0]["source_pointer"] == "jsonl:msg_f1f2162d"
    assert d["hazards"] == ["shared dirty main tree", "gm mid-rotation"]
    assert d["first_effect"]["kind"] == "file"
    h2 = Handoff.from_dict(d)
    assert h2.canary_questions == h.canary_questions
    assert h2.hazards == h.hazards
    assert h2.first_effect == h.first_effect


# --- anti-Goodhart, post-the operator-ruling: there is NO answer key to redact ---
# The old invariant was "the successor-visible form STRIPS the answers while the
# daemon side keeps them". That design protected a secret; the operator's ruling deletes
# it. The invariant now is stronger and needs no trust: NO surface has an answer,
# and the POINTER survives on both — telling the successor where to read is the
# point of protocol-v2, not a leak.

def test_no_surface_carries_an_answer_and_pointers_survive():
    h = _rich_handoff()
    redacted = h.to_successor_init()
    for q in redacted["canary_questions"]:
        assert "answer" not in q and "expected" not in q
        assert q["question"], "questions must survive"
        assert q["id"], "ids must survive"
        assert q["source_pointer"], "the pointer is the whole mechanism"
    for q in h.to_dict()["canary_questions"]:
        assert "answer" not in q, "the daemon side must not hold answers either"
    # everything else identical
    assert redacted["hazards"] == h.hazards
    assert redacted["first_effect"] == h.first_effect


# --- min_decisions curve ---

def test_min_decisions_curve():
    assert min_decisions_for(0) == 1
    assert min_decisions_for(30) == 1
    assert min_decisions_for(59) == 1
    assert min_decisions_for(60) == 1
    assert min_decisions_for(61) == 2
    assert min_decisions_for(150) == 3
    assert min_decisions_for(151) == 3
    assert min_decisions_for(10000) == 3  # capped


def test_is_stale():
    assert is_stale(0.0, 3601.0) is True
    assert is_stale(0.0, 3599.0) is False


# --- richness-gated validate ---

def test_validate_structural_only_backward_compat():
    """Without require_richness, a thin handoff still validates structurally."""
    h = Handoff(current_goal="g", next_3_actions=["a"])
    assert h.validate() == []


def test_validate_richness_fails_hollow_handoff():
    """A hollow-but-parseable handoff FAILS the richness gate (H2)."""
    h = Handoff(current_goal="g", next_3_actions=["a"])
    errs = h.validate(require_richness=True)
    assert any("canary" in e for e in errs)
    assert any("hazards" in e for e in errs)
    assert any("first_effect" in e for e in errs)
    assert any("next_gate" in e for e in errs)


def test_validate_richness_passes_rich_handoff():
    h = _rich_handoff()
    h.phase_state.next_gate = "gate-A"
    assert h.validate(require_richness=True) == []


def test_validate_richness_scales_decisions_by_turns():
    """>150 turns requires 3 decisions-with-rationale; 1 is insufficient."""
    h = _rich_handoff()
    h.phase_state.next_gate = "gate-A"  # 1 decision only
    errs = h.validate(session_turns=200, require_richness=True)
    assert any("decisions with rationale" in e for e in errs)
