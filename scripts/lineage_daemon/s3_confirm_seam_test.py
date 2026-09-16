"""Tests for s3_confirm_seam.py — the REAL, injectable S3 confirm seam.

Proves the seam (a) HOLDS when no live seams are available (the first-window
posture — honest, never a deceptive constant that pretends), (b) runs the REAL
beat.two_sample_confirm when live seams ARE supplied, returning confirmed only on a
genuine two-sample pass, and (c) fails CLOSED to held on any provider/confirm error.
Hermetic: seams are injected, no live daemon IO.
"""
from scripts.lineage_daemon import s3_confirm_seam as s3


# --- no provider -> HELD (honest first-window default) -------------------------

def test_no_provider_holds():
    fn = s3.build_s3_confirm_fn()               # seams_provider defaults to None
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "held"
    assert out["reason"] == "s3-live-seams-unavailable"
    # the reason is HONEST — not the old "hard-rotation-not-enabled-first-window" lie
    assert "hard-rotation-not-enabled" not in out["reason"]


def test_provider_yields_none_holds():
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: None)
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "held"
    assert out["reason"] == "s3-live-seams-unavailable"


# --- provider raises -> HELD (fail closed) -------------------------------------

def test_provider_error_holds():
    def boom(c, s):
        raise RuntimeError("no successor transcript")
    fn = s3.build_s3_confirm_fn(seams_provider=boom)
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "held"
    assert out["reason"].startswith("s3-seams-provider-error:")


# --- real two_sample_confirm runs when live seams supplied ----------------------

def _live_seams(*, observed_on_track, evidence_ok):
    """A hermetic but REAL two_sample_confirm wiring: a fake gate_fn stands in for
    focus_registry.rotation_gate but the confirm PATH (two samples, settle, drift
    check) is the genuine beat.two_sample_confirm."""
    reads = {"n": 0}

    def read_successor():
        reads["n"] += 1
        return {"observed": {"on_track": observed_on_track},
                "evidence": {"ok": evidence_ok}}

    def gate_fn(observed, expected, evidence, ground_truth, **kw):
        ok = bool(observed.get("on_track")) and bool(evidence.get("ok"))
        return {"confirmed": ok, "reasons": [] if ok else ["fake:not-on-track"]}

    return {
        "expected": {"role": "x"},
        "ground_truth": {"canary_answers": {"q1": "a"}},   # daemon-held
        "first_effect": None,
        "read_successor": read_successor,
        "gate_fn": gate_fn,
        "correction_fn": lambda observed, expected, nonce: f"correct {nonce}",
        "inject_correction": lambda succ, text, nonce: None,
        "settle_fn": lambda: None,                          # no-op settle in test
        "max_rounds": 1,
    }


def test_live_seams_two_sample_pass_confirms():
    seams = _live_seams(observed_on_track=True, evidence_ok=True)
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: seams)
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "confirmed"        # both samples passed


def test_live_seams_gate_fails_holds():
    seams = _live_seams(observed_on_track=False, evidence_ok=True)
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: seams)
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "held"             # content gate never confirmed


# --- confirm-run error -> HELD (fail closed) -----------------------------------

def test_confirm_run_error_holds():
    # a seams dict missing a required key -> two_sample_confirm KeyError -> held
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: {"expected": {}})
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "held"
    assert out["reason"].startswith("s3-confirm-error:")


# --- gate_fn override at factory level is honored -------------------------------

def test_factory_gate_fn_used_when_seams_omit_it():
    calls = {"n": 0}

    def gate_fn(observed, expected, evidence, ground_truth, **kw):
        calls["n"] += 1
        return {"confirmed": True, "reasons": []}

    seams = _live_seams(observed_on_track=True, evidence_ok=True)
    seams.pop("gate_fn")                         # force the factory-level gate_fn
    fn = s3.build_s3_confirm_fn(seams_provider=lambda c, s: seams, gate_fn=gate_fn)
    out = fn("cand-g2", "cand-g3")
    assert out["outcome"] == "confirmed"
    assert calls["n"] >= 1                        # the injected gate was actually used
