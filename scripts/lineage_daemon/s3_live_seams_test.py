"""Gap-1: live S3 seams provider — every path is FAIL-CLOSED (returns None => the
confirm HOLDs; NOTHING retires). These tests assert the fail-closed contract; the
positive-confirm fidelity is exercised by the step-4 WATCHED trial, not here.

RED-first (DEC-1787767251 congruence scope + the reviewer-surfaced degenerate-handoff
hardening). No test arms anything; the provider is only ever CALLED on an armed
hard_rotate, which stays gated behind SOFT_ONLY+floor+empty allowlist.
"""
import json
import os
import tempfile

import pytest

from scripts.lineage_daemon import s3_live_seams as S


def _od(tmp):
    os.makedirs(os.path.join(tmp, "state", "agent-handoffs"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "docs"), exist_ok=True)
    return tmp


# --- provider returns None (=> HOLD) on every missing/degenerate input ---

def test_provider_none_when_handoff_missing(tmp_path):
    od = _od(str(tmp_path))
    prov = S.build_live_seams_provider(orchestra_dir=od)
    # no committed handoff for the lineage => None => the confirm HOLDs
    assert prov("seatX", "seatX-2") is None


def test_provider_none_on_degenerate_handoff(tmp_path):
    """Reviewer-surfaced hardening: an empty/degenerate committed handoff (no
    first_effect, no canary_questions) must yield None => HOLD, never a weak pass —
    S3 must not lean on a trivially-authored handoff."""
    od = _od(str(tmp_path))
    hp = os.path.join(od, "docs", "HANDOFF_seatX-next.md")
    degenerate = {"current_goal": "", "first_effect": {}, "canary_questions": []}
    with open(hp, "w") as f:
        f.write("# handoff\n```json\n" + json.dumps(degenerate) + "\n```\n")
    prov = S.build_live_seams_provider(orchestra_dir=od)
    assert prov("seatX", "seatX-2") is None


def test_provider_none_when_transcript_missing(tmp_path):
    """A well-formed handoff but no resolvable predecessor transcript => None."""
    od = _od(str(tmp_path))
    hp = os.path.join(od, "docs", "HANDOFF_seatX-next.md")
    good = {"current_goal": "do the thing",
            "first_effect": {"kind": "command", "target": "x", "check": "y"},
            "canary_questions": [{"id": "q1", "question": "cite the sha",
                                  "source_pointer": "jsonl:turn-1"}]}
    with open(hp, "w") as f:
        f.write("# handoff\n```json\n" + json.dumps(good) + "\n```\n")
    # no transcript path resolvable for a nonexistent sid
    prov = S.build_live_seams_provider(
        orchestra_dir=od, transcript_of=lambda succ: None)
    assert prov("seatX", "seatX-2") is None


# --- helper fail-closed units ---

def test_load_handoff_missing_returns_none(tmp_path):
    od = _od(str(tmp_path))
    assert S._load_committed_handoff("nope", od) is None


def test_load_handoff_malformed_json_returns_none(tmp_path):
    od = _od(str(tmp_path))
    hp = os.path.join(od, "docs", "HANDOFF_seatX-next.md")
    with open(hp, "w") as f:
        f.write("# handoff\n```json\n{ not valid json \n```\n")
    assert S._load_committed_handoff("seatX", od) is None


def test_is_degenerate_true_on_empty_fields():
    assert S._is_degenerate_handoff({"current_goal": "", "first_effect": {},
                                     "canary_questions": []}) is True


def test_is_degenerate_false_on_wellformed():
    assert S._is_degenerate_handoff(
        {"current_goal": "g", "first_effect": {"kind": "command"},
         "canary_questions": [{"id": "q1"}]}) is False


def test_read_successor_missing_readback_is_empty_not_crash(tmp_path):
    od = _od(str(tmp_path))
    snap = S._read_successor_at_source("seatX-2", od)
    # fail-closed: empty observed/evidence (=> gate not-confirmed), never an exception
    assert snap == {"observed": {}, "evidence": {"readback": {}, "canary_answers": {}}}


def test_evidence_parsed_from_committed_readback(tmp_path):
    od = _od(str(tmp_path))
    rb = os.path.join(od, "state", "agent-handoffs", "seatX-2.readback.md")
    with open(rb, "w") as f:
        f.write("# READBACK\n\n**Q1** the fix landed at merge abc1234 apr_x9.\n\n"
                "**Q2** second anchor def5678.\n")
    snap = S._read_successor_at_source("seatX-2", od)
    ca = snap["evidence"]["canary_answers"]
    assert "q1" in ca and "abc1234" in ca["q1"]
    assert "q2" in ca and "def5678" in ca["q2"]
    assert snap["observed"].get("oriented") is True  # committed a readback => oriented


# --- inject_correction is durable-first + best-effort nudge, never raises ---

def test_inject_correction_never_raises_on_nudge_failure(tmp_path, monkeypatch):
    od = _od(str(tmp_path))
    calls = {"send": 0, "nudge": 0}

    def _send(*a, **k):
        calls["send"] += 1
        return {"sent": True}

    def _nudge(*a, **k):
        calls["nudge"] += 1
        raise RuntimeError("tmux pane gone")

    # durable send happens; nudge raising must be swallowed (never aborts the loop)
    S._inject_correction("seatX-2", "please resume", "nonce-1",
                         orchestra_dir=od, send_fn=_send, nudge_fn=_nudge)
    assert calls["send"] == 1 and calls["nudge"] == 1  # both attempted, no raise


# --- the provider, fully assembled over fakes, yields the 7 required seam keys ---

def test_provider_returns_full_seam_dict_when_wellformed(tmp_path):
    od = _od(str(tmp_path))
    # committed handoff
    hp = os.path.join(od, "docs", "HANDOFF_seatX-next.md")
    good = {"current_goal": "do the thing",
            "phase_state": {"plan_ref": "p", "phase_n": 1, "phase_m": 2,
                            "current_step": "s", "next_gate": "g"},
            "file_roots_touched": ["scripts/"],
            "first_effect": {"kind": "command", "target": "x", "check": "y"},
            "canary_questions": [{"id": "q1", "question": "cite sha",
                                  "source_pointer": "jsonl:turn-1"},
                                 {"id": "q2", "question": "cite id",
                                  "source_pointer": "jsonl:turn-1"}]}
    with open(hp, "w") as f:
        f.write("# handoff\n```json\n" + json.dumps(good) + "\n```\n")
    # fake predecessor transcript
    tpath = os.path.join(str(tmp_path), "pred.jsonl")
    with open(tpath, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "landed at abc1234 and def5678"}]},
            "timestamp": "2026-08-26T00:00:00Z"}) + "\n")
    prov = S.build_live_seams_provider(
        orchestra_dir=od, transcript_of=lambda succ: tpath)
    seams = prov("seatX", "seatX-2")
    assert seams is not None
    for k in ("expected", "ground_truth", "first_effect", "read_successor",
              "correction_fn", "inject_correction"):
        assert k in seams, f"missing seam key {k}"
    assert callable(seams["read_successor"])
    assert seams["ground_truth"].get("transcript_path") == tpath
    # ground_truth canary resolved AT build (from pointers), never a stored key
    assert isinstance(seams["ground_truth"].get("canary"), list)


def test_build_ground_truth_imports_rgm_without_scripts_on_path(tmp_path, monkeypatch):
    """Live-daemon context regression: rotation_gate_manual lives in <od>/scripts/
    which is NOT on sys.path when the daemon runs. _build_ground_truth must insert it
    (via _import_rgm) rather than a bare import that raises ModuleNotFoundError ->
    swallowed -> provider None -> S3 HOLDs for EVERY seat. Simulate by popping the
    module + removing scripts dirs from path, then asserting the resolve works."""
    import sys as _sys
    # simulate daemon context: rotation_gate_manual not already imported, scripts/ off path
    monkeypatch.delitem(_sys.modules, "rotation_gate_manual", raising=False)
    real_od = os.path.expanduser("~/scripts/agent-orchestra")
    monkeypatch.setattr(_sys, "path",
                        [p for p in _sys.path if not p.rstrip("/").endswith("/scripts")])
    # a pointer-bearing canary + a tiny transcript
    tpath = str(tmp_path / "pred.jsonl")
    with open(tpath, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {
            "content": [{"type": "text", "text": "landed abc1234"}]},
            "timestamp": "2026-08-26T00:00:00Z"}) + "\n")
    h = {"current_goal": "g", "first_effect": {"kind": "command"},
         "canary_questions": [{"id": "q1", "question": "cite", "source_pointer": "jsonl:turn-1"}]}
    gt = S._build_ground_truth(h, tpath, orchestra_dir=real_od)  # must NOT raise
    assert gt["mode"] == "strict"
    assert isinstance(gt["canary"], list) and gt["canary"][0]["id"] == "q1"
