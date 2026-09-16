"""Acceptance tests for the rotation gate (RED-TEAM v2 contract).

`rotation_gate` = floor (check_on_track — misfocus/stall/dead-spawn) AND comprehension
(generated read-back + canary vs daemon-held truth) AND effect (declared first-action
effect exists). The NEGATIVE TEST is the acceptance of the whole effort: a synthetic
gen-7 — oriented-looking, busy, right focus, callback fired, but failing DEEP cite-back
— must NOT confirm.
"""
from scripts.focus_registry.gate import rotation_gate
from scripts.focus_registry.correctness import expected_work
from scripts.focus_registry.store import load_store, save_store

_HANDOFF = {
    "phase_state": {"plan_ref": "docs/spec.md", "next_gate": "chart renders on audience 421e3319"},
    "next_3_actions": ["fix the staging chart query"],
    "file_roots_touched": ["dashboard/src/audience"],
}
_GT = {
    "goal": "ship the audience staging chart",
    "guards": ["single-trunk"],
    "open_loops": ["merge-9 holding land of 77eeb56", "filter-scaling fix owed to opus-11"],
    "hazards": ["prod test-row dddddddd must be cleaned"],
    "canary": [{"id": "q1", "question": "which commit is merge-9 holding?", "answer": "77eeb56"}],
}


def _store(tmp_path):
    store = {
        "version": 1, "entities": {
            "focus:custom-intent-audiences": {
                "id": "focus:custom-intent-audiences", "type": "focus", "status": "active",
                "canonical": "Custom-intent audiences", "owner": "agent:opus-11",
                "attrs": {"category": "ACME-APP", "pct_done": 80, "agents": ["agent:opus-11"]},
            },
        }, "edges": [{"src": "agent:opus-11", "rel": "works_on", "dst": "focus:custom-intent-audiences"}],
    }
    p = tmp_path / "focus-registry.json"
    save_store(store, str(p))
    return load_store(str(p))


def _ontrack_observed():
    return {
        "successor": "agent:opus-11-g2", "works_on": "focus:custom-intent-audiences",
        "oriented": True, "state": "working",
        "touched_files": ["dashboard/src/audience/chart.tsx"],
        "confirmed_focus": "focus:custom-intent-audiences",
    }


def _shallow_evidence():
    # gen-7: has the goal header, but NOT the deep open_loops/hazards, canary wrong.
    return {"readback": {"goal": "ship the audience staging chart", "open_loops": ["some items"], "hazards": []},
            "canary_answers": {"q1": "not sure"}}


def _deep_evidence():
    return {"readback": {"goal": "shipping the staging chart",
                         "guards": ["stay single-trunk"],
                         "open_loops": ["merge-9 on the 77eeb56 land", "owe opus-11 filter-scaling"],
                         "hazards": ["clean the dddddddd prod row"]},
            "canary_answers": {"q1": "77eeb56"}}


def test_gen7_shallow_read_is_NOT_confirmed(tmp_path):
    """THE ACCEPTANCE: behaviorally on-track + callback fired, but half-informed."""
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    g = rotation_gate(_ontrack_observed(), exp, _shallow_evidence(), _GT)
    assert g["confirmed"] is False              # the gate closes on the founding incident
    assert g["floor"]["on_track"] is True       # floor passes (that was the whole problem)
    assert g["comprehension"]["comprehended"] is False   # knowledge gate catches it


def test_deep_comprehension_on_track_confirms(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    g = rotation_gate(_ontrack_observed(), exp, _deep_evidence(), _GT)
    assert g["confirmed"] is True


def test_floor_offtrack_blocks_even_if_comprehended(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    off = {**_ontrack_observed(), "works_on": "focus:billing", "state": "idle"}
    g = rotation_gate(off, exp, _deep_evidence(), _GT)
    assert g["confirmed"] is False              # floor is still a required floor


def test_declared_effect_missing_blocks_confirm(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    effect = {"kind": "session", "target": "opus-11-g2"}
    g = rotation_gate(_ontrack_observed(), exp, _deep_evidence(), _GT,
                      effect=effect, effect_runner=lambda a, cwd=None: 1)  # rc!=0 -> missing
    assert g["confirmed"] is False
    assert g["effect"]["exists"] is False


def test_declared_effect_present_confirms(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    effect = {"kind": "session", "target": "opus-11-g2"}
    g = rotation_gate(_ontrack_observed(), exp, _deep_evidence(), _GT,
                      effect=effect, effect_runner=lambda a, cwd=None: 0)
    assert g["confirmed"] is True


def test_no_effect_declared_does_not_block(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    g = rotation_gate(_ontrack_observed(), exp, _deep_evidence(), _GT, effect=None)
    assert g["confirmed"] is True


# --- NEG-2: effect-first override (v2 §S3.3) — never manufacture a failure on a
# healthy-but-lagging successor. When first_effect exists + passes, the floor's
# lagging not-progressing (stale idle/pane) is treated as SATISFIED. ---

def test_neg2_healthy_lagging_successor_with_effect_is_confirmed(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # jsonl/pane LAGS -> observed.state=idle (stale), touched_files empty — the floor
    # would call this not-progressing. But it PRODUCED the declared effect.
    lagging = {**_ontrack_observed(), "state": "idle", "touched_files": []}
    effect = {"kind": "session", "target": "opus-11-g2"}
    g = rotation_gate(lagging, exp, _deep_evidence(), _GT,
                      effect=effect, effect_runner=lambda a, cwd=None: 0)  # effect EXISTS
    assert g["confirmed"] is True                    # NOT spuriously corrected
    assert g["effect"]["exists"] is True
    assert g["floor_effect_override"] is True         # override fired


def test_neg2_no_effect_lagging_successor_still_fails_floor(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # same lagging state but NO effect declared -> floor's not-progressing stands.
    lagging = {**_ontrack_observed(), "state": "idle", "touched_files": []}
    g = rotation_gate(lagging, exp, _deep_evidence(), _GT, effect=None)
    assert g["confirmed"] is False
    assert g["floor_effect_override"] is False


def test_neg2_effect_missing_does_not_override_floor(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    lagging = {**_ontrack_observed(), "state": "idle", "touched_files": []}
    effect = {"kind": "session", "target": "opus-11-g2"}
    g = rotation_gate(lagging, exp, _deep_evidence(), _GT,
                      effect=effect, effect_runner=lambda a, cwd=None: 1)  # effect MISSING
    assert g["confirmed"] is False                   # no effect -> no override, floor fails
    assert g["floor_effect_override"] is False


def test_neg2_override_does_not_rescue_a_real_misfocus(tmp_path):
    # the override only excuses lagging NOT-PROGRESSING; a genuine WRONG-FOCUS still fails.
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    misfocus = {**_ontrack_observed(), "works_on": "focus:billing", "state": "idle"}
    effect = {"kind": "session", "target": "opus-11-g2"}
    g = rotation_gate(misfocus, exp, _deep_evidence(), _GT,
                      effect=effect, effect_runner=lambda a, cwd=None: 0)
    assert g["confirmed"] is False                   # wrong focus is not lag — still blocked
