"""KEY-1 grader calibration — RED tests BY EFFECT on real readbacks
(DEC-1787728346). These are the brief's two hard requirements plus the FAIL
direction, run through the REAL record_readback path (not probe code):

  * all-model-parity-gen4's readback MUST grade PASS (genuine cold-hand 5/5;
    the OLD grader's artifact shows FAIL at overlap 0.031-0.104 vs 0.25).
  * gm-gen28's readback MUST grade PASS (hand-graded 5/5; prose pointers, so
    every question takes the full-transcript grounding path).
  * degenerate/absent readbacks against the SAME real canaries MUST FAIL.

Grades are written ONLY to a tmp handoffs dir (never the live
state/agent-handoffs — a promoted rotation is never retro-graded; these are
verification fixtures, not ledger writes). Skips cleanly if the real
transcripts are absent (other machines).

Run:  python3 -m pytest scripts/test_grader_calibration_real_fixtures.py -q
"""
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "rotation_gate_manual", ROOT / "scripts" / "rotation_gate_manual.py")
RG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RG)

# Real artifacts live in the LIVE orchestra tree (read-only from here), so the
# fixtures stay real even when this suite runs from a worktree. This suite is
# inherently non-hermetic — it grades against a specific operator's real
# rotation history — and skips cleanly (see `need` below) on any install that
# doesn't have these exact artifacts, which is every install but the one that
# produced them.
LIVE = Path(os.environ.get("ORCHESTRA_DIR", str(ROOT))) / "state" / "agent-handoffs"
PROJ = Path.home() / ".claude/projects" / ("-" + str(ROOT).strip("/").replace("/", "-"))
GEN4_T = PROJ / "58ce7bcd-8b00-43d2-9a66-1987ec448eab.jsonl"   # gen3 (pred)
GEN28_T = PROJ / "fae5463b-2a41-44cb-adfb-f3c45f4c2a82.jsonl"  # gen27 (pred)

need = [LIVE / "all-model-parity-gen4.canary.json",
        LIVE / "all-model-parity-gen4.readback.md",
        LIVE / "gm-gen28.canary.json", LIVE / "gm-gen28.readback.md",
        GEN4_T, GEN28_T]
pytestmark = pytest.mark.skipif(
    not all(p.exists() for p in need),
    reason="real rotation fixtures/transcripts not present on this machine")


@pytest.fixture
def gate(tmp_path, monkeypatch):
    d = tmp_path / "handoffs"
    d.mkdir()
    for f in ("all-model-parity-gen4.canary.json", "gm-gen28.canary.json"):
        shutil.copy(LIVE / f, d / f)
    monkeypatch.setattr(RG, "HANDOFFS_DIR", d)
    return d


def _grade(gate, successor, transcript, readback_md, strict=False):
    class A:
        successor_id = successor
        transcript = None
        predecessor_sid = None
        readback_file = None
        answers_file = None
        ground_truth = None
    A.transcript = str(transcript)
    A.readback_file = str(readback_md)
    A.strict = strict
    rc = None
    try:
        rc = RG._cli_grade(A)
    except SystemExit as e:      # _cli_grade doesn't sys.exit, but be safe
        rc = e.code
    art = json.loads((Path(gate) / f"{successor}.comprehension.json").read_text())
    return rc, art


def test_gen4_genuine_readback_grades_PASS(gate):
    rc, art = _grade(gate, "all-model-parity-gen4", GEN4_T,
                     LIVE / "all-model-parity-gen4.readback.md")
    assert art["pass"] is True and art["result"] == "PASS" and rc == 0
    per_q = art["detail"]["canary"]["per_question"]
    assert len(per_q) == 5
    assert art["detail"]["canary"]["aggregate"]["idclass"] is True


def test_gen28_genuine_readback_grades_PASS(gate):
    rc, art = _grade(gate, "gm-gen28", GEN28_T, LIVE / "gm-gen28.readback.md")
    assert art["pass"] is True and art["result"] == "PASS" and rc == 0
    # prose pointers resolve to nothing -> ALL questions took the grounding
    # path; the old grader would have vacuously skipped every one of them.
    agg = art["detail"]["canary"]["aggregate"]
    assert agg["union"] >= 4 and agg["distinct_q"] >= 3


def test_degenerate_readback_against_real_canary_FAILS(gate, tmp_path):
    waffle = ("I fully absorbed the predecessor context and the mission. The "
              "defect was surfaced and resolved through careful revision, "
              "addressing concurrency and durability concerns from the review.")
    md = "\n".join(f"**cq{i} —** {waffle}" for i in range(1, 6))
    p = tmp_path / "degenerate.md"
    p.write_text(md)
    rc, art = _grade(gate, "all-model-parity-gen4", GEN4_T, p)
    assert art["pass"] is False and art["result"] == "FAIL" and rc == 1


def test_hallucinated_readback_against_real_canary_FAILS(gate, tmp_path):
    fake = ("It was HTTP 503 from commit deadbeef123 via apr_zzzz9999 and "
            "DEC-9999123456 with build 977.")
    md = "\n".join(f"**cq{i} —** {fake}" for i in range(1, 6))
    p = tmp_path / "halluc.md"
    p.write_text(md)
    rc, art = _grade(gate, "all-model-parity-gen4", GEN4_T, p)
    assert art["pass"] is False and rc == 1


def test_absent_readback_stays_fail_closed(gate):
    # No grade run at all -> no artifact -> fail-closed reader (unchanged).
    assert RG.comprehension_passed("all-model-parity-gen4") is False


def test_gen4_strict_mode_documented_behavior(gate):
    """Strict mode additionally requires region corroboration. gen4's canary
    windows are WRONG (root cause 3) — the regions its pointers resolve to are
    not where the cited facts live — so strict, which exists to demand
    correct-pointer canaries before ARMING, must grade it conservatively.
    Whatever it returns must at minimum never be MORE lenient than supervised."""
    rc, art = _grade(gate, "all-model-parity-gen4", GEN4_T,
                     LIVE / "all-model-parity-gen4.readback.md", strict=True)
    assert art["mode"] == "strict"
    sup_rc, sup_art = _grade(gate, "all-model-parity-gen4", GEN4_T,
                             LIVE / "all-model-parity-gen4.readback.md")
    assert (sup_art["pass"], art["pass"]) != (False, True)  # never more lenient
