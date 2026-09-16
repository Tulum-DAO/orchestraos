"""Piece-2 (DEC-1787861488): KEY-1 `auto_strict_grade` first-class ledger row.

The flagship promote cleared KEY-1 via a free-text --shadow-skip-reason even
though the daemon held a strict comprehension PASS — logging a *skip* where
honest machine evidence existed. Fix: the completion provider appends a
first-class shadow-ledger row (kind="auto_strict_grade") at grade time,
STRICTLY BEFORE promote, so `has_evidence_for()` passes naturally and the
skip hatch stays reserved for genuine emergencies.

BINDING peer conditions proven here:
  C3 — auto rows must NOT count toward KEY-1 arming/graduation stats (no
       independent supervisor = no discriminator). key1_status() is
       byte-identical with auto rows present (total_rows aside — it counts
       every physical line by definition).
  C4 — provenance: the row carries the comprehension.json sha256 + the grader
       commit. Non-strict or failing grades write NOTHING and the KEY-1 gate
       still refuses.

All tests use the REAL lineage_gate.shadow over a scratch ledger (injected
Path) — no reimplemented predicate, no mocks of the ledger.
"""
import hashlib
import json
import os
import sys

import pytest

from scripts.lineage_daemon import complete as C

_scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
from lineage_gate import shadow as SH                     # noqa: E402
import promote_successor as PS                            # noqa: E402


L_SID = "sess-successor-L"
PRED_SID = "sess-predecessor"
ROTATION = "pm-x <- pm-x-g2"
BUDGET = {"per_beat_slot": True, "hourly_remaining": 4, "cooldown_clear": True}


def _artifact(tmp_path, *, mode="strict", passed=True):
    p = tmp_path / "pm-x-g2.comprehension.json"
    p.write_text(json.dumps({
        "successor": "pm-x-g2", "mode": mode, "pass": passed,
        "provenance_sid": L_SID,
        "per_question_scores": {"q1": 1.0, "q2": 0.8}}))
    return p


def _seams(artifact, *, disposition="PASS", strict=True, calls=None):
    calls = calls if calls is not None else {"promote": []}
    return dict(
        live_sid_fn=lambda a: L_SID,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: 2000.0,
        comprehension_mtime_fn=lambda a: 2100.0,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: (PRED_SID, PRED_SID, PRED_SID),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": disposition,
                                    "strict": strict,
                                    "artifact": str(artifact),
                                    "artifact_written": disposition in ("PASS", "FAIL")},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=lambda c, a: calls["promote"].append((c, a)) or {"ok": True},
        retire_fn=lambda c: {"retired": True},
    ), calls


def _hold():
    return {"canary": "pm-x", "successor": "pm-x-g2",
            "status": "hold:successor-unconfirmed", "reason": "same-beat",
            "created_at": 0, "resolved": False}


def _run(tmp_path, seams, ledger):
    prov = C.build_completion_provider(shadow_ledger=ledger, **seams)
    return prov("pm-x", _hold(), armed=True, budget=BUDGET, now=9000)


# ---------------------------------------------------- first-class row on PASS
def test_strict_pass_appends_first_class_row_with_provenance(tmp_path):
    """NAMED: strict-pass-writes-honest-datum (C4 provenance). One row,
    kind=auto_strict_grade, sha256 of the ACTUAL artifact bytes + grader
    commit + recorder identity — an audit datum, not a skip."""
    art = _artifact(tmp_path)
    ledger = tmp_path / "shadow.jsonl"
    seams, _ = _seams(art)
    tr = _run(tmp_path, seams, ledger)
    assert tr["status"] == C.COMPLETED
    rows = [r for r in SH.read_rows(ledger) if r.get("kind") == "auto_strict_grade"]
    assert len(rows) == 1
    row = rows[0]
    assert row["rotation"] == ROTATION
    assert row["mode"] == "strict" and row["pass"] is True
    assert row["comprehension_sha256"] == hashlib.sha256(
        art.read_bytes()).hexdigest()
    assert row["comprehension_path"] == str(art)
    assert row["recorded_by"] == "completion_provider"
    assert "grader_commit" in row
    assert row["per_question_scores"] == {"q1": 1.0, "q2": 0.8}
    assert SH.has_evidence_for(ROTATION, ledger=ledger)   # the gate passes naturally


def test_row_written_strictly_before_promote(tmp_path):
    """NAMED: evidence-precedes-promote. At the moment the promote seam fires,
    has_evidence_for() must ALREADY be true — the KEY-1 gate inside a real
    promote reads the ledger, so ordering is the whole point."""
    art = _artifact(tmp_path)
    ledger = tmp_path / "shadow.jsonl"
    seen = {}

    def probe_promote(c, a):
        seen["evidence_at_promote"] = SH.has_evidence_for(ROTATION, ledger=ledger)
        return {"ok": True}
    seams, _ = _seams(art)
    seams["promote_fn"] = probe_promote
    tr = _run(tmp_path, seams, ledger)
    assert tr["status"] == C.COMPLETED
    assert seen["evidence_at_promote"] is True


# ------------------------------------------- C4: non-strict / failing = NOTHING
def test_failing_grade_writes_nothing_and_gate_refuses(tmp_path, monkeypatch):
    """NAMED: fail-writes-nothing-gate-refuses (C4). A FAIL grade appends no
    row; the real _key1_evidence_gate still refuses without a skip reason."""
    art = _artifact(tmp_path, passed=False)
    ledger = tmp_path / "shadow.jsonl"
    seams, _ = _seams(art, disposition="FAIL")
    tr = _run(tmp_path, seams, ledger)
    assert tr["status"] == C.HOLD_GRADE
    assert SH.read_rows(ledger) == []
    monkeypatch.setattr(SH, "LEDGER", ledger)
    with pytest.raises(PS.PromotionRefused):
        PS._key1_evidence_gate("pm-x", "pm-x-g2", None)


def test_non_strict_artifact_writes_nothing(tmp_path):
    """NAMED: supervised-mode-is-not-auto-evidence (C4). Even on a PASS, an
    artifact whose mode != strict writes NOTHING — the arming precondition is
    mode==strict, mirrored from the self-retire _graduation_pass hardening."""
    art = _artifact(tmp_path, mode="supervised")
    ledger = tmp_path / "shadow.jsonl"
    seams, _ = _seams(art)
    _run(tmp_path, seams, ledger)
    assert SH.read_rows(ledger) == []


def test_grade_dict_strict_but_artifact_unreadable_writes_nothing(tmp_path):
    """NAMED: artifact-is-the-authority. The grade dict claims strict PASS but
    the artifact is absent — nothing to hash, nothing to vouch: NO row."""
    ledger = tmp_path / "shadow.jsonl"
    seams, _ = _seams(tmp_path / "missing.json")
    tr = _run(tmp_path, seams, ledger)
    assert SH.read_rows(ledger) == []
    assert tr["status"] == C.COMPLETED    # injected promote seam is unguarded here


# ----------------------------------------------------------------- idempotency
def test_reentry_beat_does_not_double_append(tmp_path):
    """NAMED: one-rotation-one-datum. A re-entry beat (same rotation) must not
    append a second row — has_evidence_for() short-circuits the recorder."""
    art = _artifact(tmp_path)
    ledger = tmp_path / "shadow.jsonl"
    seams, _ = _seams(art)
    _run(tmp_path, seams, ledger)
    _run(tmp_path, seams, ledger)
    rows = [r for r in SH.read_rows(ledger) if r.get("kind") == "auto_strict_grade"]
    assert len(rows) == 1


# ------------------------------------------------- C3: counters byte-identical
def test_c3_graduation_counters_byte_identical_with_auto_rows(tmp_path):
    """NAMED: auto-rows-never-count (C3). key1_status() over a ledger with two
    real supervised rotation rows is IDENTICAL before and after auto rows are
    appended (total_rows aside — it counts physical lines by definition):
    no independent supervisor = no discriminator = no arming credit."""
    ledger = tmp_path / "shadow.jsonl"
    for i in (1, 2):
        SH.record_rotation(rotation=f"seat-a <- seat-a-g{i + 1}",
                           grader_verdict="PASS", supervisor_verdict="PASS",
                           author=f"seat-a-g{i}", supervisor="gm",
                           ledger=ledger)
    before = SH.key1_status(ledger=ledger)
    art = _artifact(tmp_path)
    seams, _ = _seams(art)
    _run(tmp_path, seams, ledger)
    rows = [r for r in SH.read_rows(ledger) if r.get("kind") == "auto_strict_grade"]
    assert len(rows) == 1                                 # the auto row IS present
    after = SH.key1_status(ledger=ledger)
    before.pop("total_rows"), after.pop("total_rows")
    assert after == before
