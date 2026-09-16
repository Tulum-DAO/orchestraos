"""Integration: drive ob's S3 loop against the v2 CONTENT-GATE (rotation_gate).

Extends correctness_loop_test from the floor-only v1 check to the full v2 gate
(floor ∧ comprehension ∧ effect, with the NEG-2 effect-first override). Models ob's
bounded confirm-and-correct loop over an evolving (observed, evidence) sequence and
proves the two acceptance NEGs:
  NEG-1: a replayed gen-7 (busy, on-focus, callback fired, but shallow read-back) MUST
         fail the gate and enter correction — never pass.
  NEG-2: a healthy-but-lagging successor (effect produced, telemetry stale) MUST NOT be
         corrected (effect-first override holds).
"""
from scripts.focus_registry.gate import rotation_gate
from scripts.focus_registry.correctness import expected_work
from scripts.focus_registry.store import load_store, save_store

CONFIRMED = "confirmed_gate_open"
HELD = "held_escalate_no_retire"

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


def _observed(**over):
    base = {"successor": "agent:opus-11-g2", "works_on": "focus:custom-intent-audiences",
            "oriented": True, "state": "working",
            "touched_files": ["dashboard/src/audience/chart.tsx"],
            "confirmed_focus": "focus:custom-intent-audiences"}
    base.update(over)
    return base


_SHALLOW = {"readback": {"goal": "ship the audience staging chart", "guards": [],
                         "open_loops": ["some items"], "hazards": []},
            "canary_answers": {"q1": "not sure"}}
_DEEP = {"readback": {"goal": "shipping the staging chart", "guards": ["stay single-trunk"],
                      "open_loops": ["merge-9 on the 77eeb56 land", "owe opus-11 filter-scaling"],
                      "hazards": ["clean the dddddddd prod row"]},
         "canary_answers": {"q1": "77eeb56"}}


def _simulate(seq, exp, effect=None, effect_runner=None, N=3):
    """Model ob's loop over a sequence of (observed, evidence). Corrects on gate-fail."""
    corrections = 0
    for observed, evidence in seq:
        g = rotation_gate(observed, exp, evidence, _GT, effect=effect, effect_runner=effect_runner)
        if g["confirmed"]:
            return {"result": CONFIRMED, "corrections": corrections}
        if corrections >= N:
            return {"result": HELD, "corrections": corrections}
        corrections += 1
    return {"result": HELD, "corrections": corrections}


def test_NEG1_replayed_gen7_fails_gate_and_never_confirms(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # gen-7: busy, on-focus, callback fired, but shallow read-back — every sample.
    seq = [(_observed(), _SHALLOW)] * 4
    r = _simulate(seq, exp, N=3)
    assert r["result"] == HELD          # never confirmed
    assert r["corrections"] == 3        # entered correction, exhausted, HELD (not retired)


def test_NEG1_gen7_converges_once_it_produces_a_deep_readback(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # shallow first, then after correction the successor re-reads and cites deep items.
    seq = [(_observed(), _SHALLOW), (_observed(), _DEEP)]
    r = _simulate(seq, exp, N=3)
    assert r["result"] == CONFIRMED
    assert r["corrections"] == 1


def test_NEG2_healthy_lagging_is_not_corrected(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    # deep comprehension + effect exists, but pane/jsonl LAGS (state idle, no touched files).
    lagging = _observed(state="idle", touched_files=[])
    seq = [(lagging, _DEEP)]
    r = _simulate(seq, exp, effect={"kind": "session", "target": "opus-11-g2"},
                  effect_runner=lambda a, cwd=None: 0, N=3)
    assert r["result"] == CONFIRMED     # gate opens on first sample
    assert r["corrections"] == 0        # NO spurious correction


def test_POS_healthy_successor_confirms_in_one_round(tmp_path):
    exp = expected_work("agent:opus-11", _HANDOFF, _store(tmp_path))
    r = _simulate([(_observed(), _DEEP)], exp, N=3)
    assert r["result"] == CONFIRMED
    assert r["corrections"] == 0
