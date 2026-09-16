"""S3 confirm-and-correct loop (WS3 v2, DEC-1786724046). Drives the loop over a
real PB rotation_gate with synthetic successor snapshots. The two acceptance
negatives live here at the LOOP level:
  NEG-1: a shallow-read gen-7 (busy/on-focus/callback-fired but no deep cite-back)
         -> HELD after N rounds, never CONFIRMED.
  NEG-2: a healthy-but-lagging successor (effect produced, telemetry stale)
         -> CONFIRMED in round 0, ZERO corrections.
"""

from scripts.lineage_daemon.confirm_correct import (
    confirm_and_correct, make_nonce, nonce_acked, CONFIRMED, HELD,
)
from scripts.focus_registry.gate import rotation_gate


# --- ground truth (daemon-held) + expected ---
_GT = {
    "goal": "wire the content gate",
    "guards": ["single-trunk", "never-kill-services"],
    "open_loops": ["reconcile-edges-abc123", "deep-loop-xyz789"],
    "hazards": ["shared-dirty-tree", "gm-mid-rotation"],
    "canary": [
        {"id": "q1", "question": "why fable?", "answer": "shawexception"},
        {"id": "q2", "question": "8a guard?", "answer": "unpushedcommits"},
    ],
}
_EXPECTED = {
    "focus_id": "focus:ws3",
    "next_actions": ["wire the gate"],
    "file_roots": ["scripts/lineage_daemon"],
}
_EFFECT = {"kind": "session", "target": "ob-g2"}


def _deep_evidence():
    """A successor that ACTUALLY absorbed the context: cites every deep loop +
    hazard + correct canary answers."""
    return {
        "readback": {
            "goal": "wire the content gate",
            "guards": ["single-trunk", "never-kill-services"],
            "open_loops": ["reconcile-edges-abc123", "deep-loop-xyz789"],
            "hazards": ["shared-dirty-tree", "gm-mid-rotation"],
        },
        "canary_answers": {"q1": "shawexception", "q2": "unpushedcommits"},
    }


def _shallow_evidence():
    """gen-7: read the header only — misses the deep loop + hazards + canary."""
    return {
        "readback": {"goal": "wire the content gate", "guards": [],
                     "open_loops": [], "hazards": []},
        "canary_answers": {},
    }


def _observed(**over):
    o = {"successor": "ob-g2", "works_on": "focus:ws3", "oriented": True,
         "state": "working", "touched_files": ["scripts/lineage_daemon/x.py"],
         "confirmed_focus": "focus:ws3"}
    o.update(over)
    return o


def _effect_ok(*a, **k):
    return 0   # rc 0 -> effect exists


# --- make_nonce / nonce_acked ---

def test_make_nonce_unique():
    assert make_nonce() != make_nonce()
    assert make_nonce().startswith("corr-")


def test_nonce_acked():
    assert nonce_acked({"correction_ack": "corr-abc"}, "corr-abc") is True
    assert nonce_acked({"correction_ack": "corr-abc"}, "corr-zzz") is False
    assert nonce_acked({}, "corr-abc") is False


# --- POSITIVE: deep successor confirms round 0 ---

def test_positive_deep_successor_confirms_no_corrections():
    reads = [{"observed": _observed(), "evidence": _deep_evidence()}]
    r = confirm_and_correct(
        "ob-g2", _EXPECTED, _GT, _EFFECT,
        read_successor=lambda: reads[0],
        gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: "x",
        inject_correction=lambda s, t, n: None,
        effect_runner=_effect_ok,
    )
    assert r["outcome"] == CONFIRMED
    assert r["rounds"] == 0
    assert r["corrections"] == []


# --- NEG-1: shallow-read gen-7 -> HELD, never confirmed ---

def test_neg1_shallow_read_gen7_is_HELD():
    # busy, on-focus, callback fired, but shallow evidence EVERY round (never learns).
    snap = {"observed": _observed(), "evidence": _shallow_evidence()}
    injects = []
    r = confirm_and_correct(
        "ob-g2", _EXPECTED, _GT, _EFFECT,
        read_successor=lambda: snap,
        gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: f"refocus [{n}]",
        inject_correction=lambda s, t, n: injects.append(n),
        max_rounds=3,
        effect_runner=_effect_ok,
    )
    assert r["outcome"] == HELD, "shallow-read gen-7 must NOT be confirmed"
    assert r["rounds"] == 3
    assert len(injects) == 3
    assert any("comprehension" in x or "canary" in x or "readback" in x
               for x in r["reasons"])


# --- NEG-2: healthy-but-lagging -> confirmed round 0, no corrections ---

def test_neg2_healthy_lagging_not_corrected():
    # effect PRODUCED (rc 0) but pane telemetry lags: state=idle, no touched_files.
    lagging = _observed(state="idle", touched_files=[])
    reads = [{"observed": lagging, "evidence": _deep_evidence()}]
    injects = []
    r = confirm_and_correct(
        "ob-g2", _EXPECTED, _GT, _EFFECT,
        read_successor=lambda: reads[0],
        gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: "x",
        inject_correction=lambda s, t, n: injects.append(n),
        effect_runner=_effect_ok,   # effect exists -> effect-first override
    )
    assert r["outcome"] == CONFIRMED, "healthy-lagging must not be churned"
    assert r["rounds"] == 0
    assert injects == []


# --- CORRECTION-THEN-CONVERGE: off-track round 0, learns after 1 correction ---

def test_off_track_then_converges_after_correction():
    seq = [
        {"observed": _observed(), "evidence": _shallow_evidence()},   # round 0: fail
        {"observed": _observed(), "evidence": _deep_evidence()},      # after corr: pass
    ]
    calls = {"n": 0}

    def read():
        r = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return r

    injects = []
    r = confirm_and_correct(
        "ob-g2", _EXPECTED, _GT, _EFFECT,
        read_successor=read,
        gate_fn=rotation_gate,
        correction_fn=lambda o, e, n: f"cite the deep loop [{n}]",
        inject_correction=lambda s, t, n: injects.append(n),
        max_rounds=3,
        effect_runner=_effect_ok,
    )
    assert r["outcome"] == CONFIRMED
    assert r["rounds"] == 1
    assert len(injects) == 1
