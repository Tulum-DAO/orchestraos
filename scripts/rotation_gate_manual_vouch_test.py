"""Piece 1 (DEC-1787687601): the grade artifact carries a POSITIVE identity vouch
that survives BY-REFERENCE inits, and comprehension.json carries both `pass`
(bool) and `result` ("PASS"|"FAIL") with a single derivation so they can never
disagree.

WHY the vouch: a by-reference init (`Read /tmp/agent-init-<id>.md`) yields
`declared_identity()==None` in promote_successor._assert_written_identity, forcing
the human operator-assertion escape hatch each rotation. For a MECHANICAL Key-1
grader (no human) the grade artifact must carry init_ref + operator_sid +
grade_linkage so an automated grader can vouch identity without a human.

WHY both keys: the completion path (completion_fn / comprehension_passed) reads
`.get("pass")`, which this writer already emits — that path is coherent. The
mechanical Key-1 / lineage_gate grader reasons in PASS|FAIL verdicts
(`--grader-verdict PASS|FAIL`); emitting a mirrored `result` (single-derivation
`pass == (result=="PASS")`) lets that grader read the same artifact without a
second dialect. The invariant test guarantees the two keys never drift.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "rotation_gate_manual", ROOT / "scripts" / "rotation_gate_manual.py")
RG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RG)


@pytest.fixture()
def gate(tmp_path, monkeypatch):
    monkeypatch.setattr(RG, "HANDOFFS_DIR", str(tmp_path))
    return tmp_path


def _grade(gate, successor="succ-3", *, identity=None):
    # a minimal PASS grade (no canary questions -> canary vacuously passes; a
    # non-empty readback covers the read-back classes).
    return RG.record_readback(
        successor, "absorbed the goal, guards, open loops and hazards in my own "
        "words after reading the predecessor jsonl", [], {}, identity=identity)


def _read(gate, successor="succ-3"):
    return json.loads((gate / f"{successor}.comprehension.json").read_text())


# ---- both keys, single derivation (invariant) ------------------------------

def test_comprehension_writes_both_pass_and_result(gate):
    art = _grade(gate)
    assert "pass" in art and "result" in art
    assert art["result"] in ("PASS", "FAIL")


def test_pass_and_result_never_disagree_on_disk(gate):
    art = _grade(gate)
    on_disk = _read(gate)
    assert on_disk["pass"] == (on_disk["result"] == "PASS")
    assert art["pass"] == (art["result"] == "PASS")


def test_result_fail_when_pass_false(gate, monkeypatch):
    # force the grader to a FAIL and assert result tracks pass.
    monkeypatch.setattr(RG, "check_comprehension", lambda ev, gt: {"comprehended": False})
    art = _grade(gate)
    assert art["pass"] is False and art["result"] == "FAIL"


# ---- identity vouch (survives by-reference inits) --------------------------

def test_identity_vouch_written_when_provided(gate):
    ident = {"init_ref": "/tmp/agent-init-succ-3.md",
             "operator_sid": "sid-succ-bbbb1111",
             "grade_linkage": "succ-3.canary.json"}
    _grade(gate, identity=ident)
    art = _read(gate)
    v = art.get("identity_vouch")
    assert v is not None
    assert v["init_ref"] == "/tmp/agent-init-succ-3.md"
    assert v["operator_sid"] == "sid-succ-bbbb1111"
    assert v["grade_linkage"] == "succ-3.canary.json"


def test_identity_vouch_absent_when_not_provided(gate):
    # backward-compat: no identity => no vouch block (never a half-populated one).
    _grade(gate)
    art = _read(gate)
    assert art.get("identity_vouch") is None


def test_identity_vouch_reader_returns_block(gate):
    ident = {"init_ref": "/tmp/agent-init-succ-3.md",
             "operator_sid": "sid-x", "grade_linkage": "L"}
    _grade(gate, identity=ident)
    got = RG.identity_vouch("succ-3")
    assert got.get("operator_sid") == "sid-x"


def test_identity_vouch_reader_empty_when_absent(gate):
    _grade(gate)
    assert RG.identity_vouch("succ-3") == {}


def test_partial_identity_is_rejected_not_half_written(gate):
    # a vouch missing a field can't vouch identity — refuse rather than write a
    # half-populated block that a mechanical grader would trust.
    with pytest.raises(ValueError):
        _grade(gate, identity={"init_ref": "/tmp/x.md"})  # no sid / linkage


# ---- the completion path still reads `pass` (unchanged) --------------------

def test_comprehension_passed_still_reads_pass(gate):
    _grade(gate)
    assert RG.comprehension_passed("succ-3") is True


# ---- D9: the successor provenance sid is stamped into the artifact ----------

def test_successor_sid_stamped_as_provenance(gate):
    """DEC-1787789209 D9: record_readback stamps the successor's live session_id as
    the IMMUTABLE provenance sid so the completion path can assert live-sid ==
    provenance-sid (defeating a killed+name-reused pane re-authoring the artifact)."""
    RG.record_readback(
        "succ-3", "absorbed the goal, guards, open loops and hazards in my own "
        "words after reading the predecessor jsonl", [], {},
        successor_sid="sess-live-abcd1234")
    art = _read(gate)
    assert art.get("provenance_sid") == "sess-live-abcd1234"


def test_provenance_sid_absent_when_not_provided(gate):
    """Backward-compat: no successor_sid => no provenance_sid key (a legacy artifact;
    the completion path treats a missing provenance sid as fail-closed NOT-ready)."""
    _grade(gate)
    art = _read(gate)
    assert art.get("provenance_sid") is None
