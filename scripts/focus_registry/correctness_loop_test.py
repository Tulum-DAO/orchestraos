"""Integration tests: drive ob's S3 confirm-and-correct LOOP against the PB contract.

ob owns the real loop (reads the successor at source, builds `observed`, tmux-injects,
re-verifies, bounds at N=3). This harness MODELS that loop over an evolving `observed`
sequence to prove the platform-builder CONTRACT (check_on_track + correction_message)
drives it to the right outcome: on-track opens the gate; off-track emits a focus-anchored
correction and re-verifies; exhaustion after N holds (predecessor stays alive, no retire).

The `observed` shape + states are LOCKED per the unified spec S3 (d118f33e9).
"""
from scripts.focus_registry.correctness import (
    expected_work,
    check_on_track,
    correction_message,
)
from scripts.focus_registry.store import load_store, save_store

ON_TRACK = "on_track_gate_open"
HELD = "held_escalate_no_retire"

_HANDOFF = {
    "phase_state": {"plan_ref": "docs/spec.md", "next_gate": "chart renders on audience 421e3319"},
    "next_3_actions": ["fix the staging chart query", "re-run importer"],
    "file_roots_touched": ["dashboard/src/audience"],
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


def _obs(**over):
    base = {
        "successor": "agent:opus-11-g2", "works_on": "focus:custom-intent-audiences",
        "oriented": True, "state": "working",
        "touched_files": ["dashboard/src/audience/chart.tsx"],
        "confirmed_focus": "focus:custom-intent-audiences",
    }
    base.update(over)
    return base


def _simulate_loop(observed_seq, expected, N=3):
    """Model ob's bounded confirm-and-correct loop over an evolving observed seq."""
    injected = []
    for observed in observed_seq:
        v = check_on_track(observed, expected)
        if v["on_track"]:
            return {"result": ON_TRACK, "injected": injected}
        if len(injected) >= N:               # exhausted N corrective injects
            return {"result": HELD, "injected": injected}
        injected.append(correction_message(observed, expected))  # tmux-inject + re-verify
    return {"result": HELD, "injected": injected}


def test_loop_opens_gate_when_successor_starts_on_track(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    r = _simulate_loop([_obs()], exp)
    assert r["result"] == ON_TRACK
    assert r["injected"] == []               # no correction needed


def test_loop_converges_after_one_correction(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # 1st observe: idle + no focus (off-track). After the correction, successor
    # re-anchors and is on-track.
    seq = [_obs(works_on=None, state="idle", oriented=False, touched_files=[]), _obs()]
    r = _simulate_loop(seq, exp)
    assert r["result"] == ON_TRACK
    assert len(r["injected"]) == 1
    assert "Custom-intent audiences" in r["injected"][0]   # focus-anchored correction


def test_loop_holds_and_escalates_on_exhaustion(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # successor never gets on-track across the bound -> HOLD, predecessor stays alive.
    off = _obs(works_on="focus:billing", state="idle", confirmed_focus="focus:billing")
    r = _simulate_loop([off, off, off, off], exp, N=3)
    assert r["result"] == HELD
    assert len(r["injected"]) == 3           # exactly N corrective injects, then HOLD


def test_every_offtrack_inject_is_nonempty_and_focus_led(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    off = _obs(works_on=None, state="stalled", oriented=False, touched_files=["api/x.js"])
    r = _simulate_loop([off, off], exp, N=3)
    assert all(msg.startswith("[rotation-correct] Refocus on") for msg in r["injected"])
    assert all(msg for msg in r["injected"])


def test_waiting_permission_does_not_trigger_a_correction(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # a permission prompt is neutral -> gate opens, no needless inject.
    r = _simulate_loop([_obs(state="waiting_permission")], exp)
    assert r["result"] == ON_TRACK
    assert r["injected"] == []
