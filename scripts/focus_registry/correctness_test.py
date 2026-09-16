"""Tests for the confirm-and-correct CONTRACT (WS3 acceptance step 3, the operator 2026-08-14).

platform-builder owns WHAT 'correct work' means: the expected-focus/expected-task
ground truth + the on-track predicate + the corrective tmux-inject text. ob owns the
verify->correct->re-verify LOOP mechanism (reads the successor at source, builds
`observed`, calls check_on_track, injects correction_message, re-checks, gates retire).
"""
from scripts.focus_registry.correctness import (
    expected_work,
    check_on_track,
    correction_message,
)
from scripts.focus_registry.store import load_store, save_store

_HANDOFF = {
    "current_goal": "ship the audience staging chart",
    "phase_state": {"plan_ref": "docs/spec.md", "phase": "3 of 5",
                    "next_gate": "chart renders on audience 421e3319"},
    "next_3_actions": ["fix the staging chart query", "re-run importer", "verify render"],
    "file_roots_touched": ["dashboard/src/audience"],
}


def _store(tmp_path):
    store = {
        "version": 1, "source": "t", "updated_at": None,
        "entities": {
            "focus:custom-intent-audiences": {
                "id": "focus:custom-intent-audiences", "type": "focus", "status": "active",
                "canonical": "Custom-intent audiences", "owner": "agent:opus-11",
                "attrs": {"category": "ACME-APP", "pct_done": 80,
                          "agents": ["agent:opus-11"]},
            },
        },
        "edges": [{"src": "agent:opus-11", "rel": "works_on", "dst": "focus:custom-intent-audiences"}],
    }
    p = tmp_path / "focus-registry.json"
    save_store(store, str(p))
    return load_store(str(p))


def test_expected_work_is_the_ground_truth(tmp_path):
    store = _store(tmp_path)
    exp = expected_work("agent:opus-11", _HANDOFF, store)
    assert exp["focus_id"] == "focus:custom-intent-audiences"
    assert exp["focus_canonical"] == "Custom-intent audiences"
    assert exp["next_actions"] == _HANDOFF["next_3_actions"]
    assert exp["file_roots"] == ["dashboard/src/audience"]
    assert exp["next_gate"] == "chart renders on audience 421e3319"


def _observed(**over):
    base = {
        "successor": "agent:opus-11-g2",
        "works_on": "focus:custom-intent-audiences",
        "oriented": True,
        "state": "working",
        "touched_files": ["dashboard/src/audience/chart.tsx"],
        "confirmed_focus": "focus:custom-intent-audiences",
    }
    base.update(over)
    return base


def test_fully_aligned_successor_is_on_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(), exp)
    assert v["on_track"] is True
    assert v["corrections"] == []


def test_wrong_focus_is_off_track_with_named_correction(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(works_on="focus:billing", confirmed_focus="focus:billing"), exp)
    assert v["on_track"] is False
    joined = " ".join(v["corrections"]).lower()
    assert "custom-intent audiences" in joined          # names the expected focus
    assert "focus:custom-intent-audiences" in " ".join(v["corrections"])


def test_idle_successor_is_off_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(state="idle"), exp)
    assert v["on_track"] is False
    assert any("fix the staging chart query" in c for c in v["corrections"])  # resume next action


def test_stalled_successor_is_off_track(tmp_path):
    # agent-status 'stalled' state must count as not-progressing (ob's ask).
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(state="stalled"), exp)
    assert v["on_track"] is False


def test_waiting_permission_is_neutral_not_off_track(tmp_path):
    # a permission prompt is NOT off-track — the agent is mid-work awaiting approval.
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(state="waiting_permission"), exp)
    assert v["on_track"] is True


def test_unoriented_successor_is_off_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(oriented=False), exp)
    assert v["on_track"] is False
    assert any("handoff" in c.lower() for c in v["corrections"])


def test_wrong_file_area_is_off_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    v = check_on_track(_observed(touched_files=["api/routes/unrelated.js"]), exp)
    assert v["on_track"] is False
    assert any("dashboard/src/audience" in c for c in v["corrections"])


def test_check_on_track_is_the_FLOOR_not_the_gate(tmp_path):
    # RED-TEAM Finding 0 resolution: check_on_track is deliberately the behavioral
    # FLOOR (catches misfocus/stall/dead-spawn). A busy, right-focus, self-certified
    # gen-7 CORRECTLY passes the floor — the context-absorption GATE (gate.py::
    # rotation_gate + comprehension) is what catches the shallow read. The xfail
    # marker is retired: the acceptance now lives in gate_test.py
    # ::test_gen7_shallow_read_is_NOT_confirmed.
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    assert check_on_track(_observed(oriented=True), exp)["on_track"] is True


def test_correction_message_is_empty_when_on_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    assert correction_message(_observed(), exp) == ""


def test_correction_message_is_injectable_text_when_off_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    msg = correction_message(_observed(works_on=None, state="idle", oriented=False), exp)
    assert msg  # non-empty, tmux-injectable
    assert "Custom-intent audiences" in msg


def test_correction_message_without_nonce_is_unchanged(tmp_path):
    # C2: default nonce=None -> behaves exactly as before (no idempotent framing).
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    off = _observed(works_on=None, state="idle", oriented=False)
    assert correction_message(off, exp) == correction_message(off, exp, nonce=None)
    assert "[correction:" not in correction_message(off, exp)


def test_correction_message_with_nonce_appends_idempotent_framing(tmp_path):
    # C2 (ob §2.6): when nonce set, append the conditional ack-or-do framing (H8).
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    off = _observed(works_on=None, state="idle", oriented=False)
    msg = correction_message(off, exp, nonce="corr-a1b2c3d4")
    assert msg.startswith("[rotation-correct] Refocus on")   # focus lead preserved
    assert "[correction:corr-a1b2c3d4]" in msg
    assert "reply ACK corr-a1b2c3d4 and ignore" in msg
    assert "already done this" in msg


def test_correction_message_nonce_empty_when_on_track(tmp_path):
    # even with a nonce, an on-track successor gets no correction.
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    assert correction_message(_observed(), exp, nonce="corr-deadbeef") == ""
