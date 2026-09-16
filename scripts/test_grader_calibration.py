"""KEY-1 grader calibration — unit tests (DEC-1787728346, CONSENSUS_REACHED).

Synthetic-fixture tests for the citation-grounding grade path, the strict
(arming-precondition) mode, and every refuse-to-grade path (each asserts NO
artifact is written — a refusal must hold for a human, never feed a verdict to
a gate that can auto-kill). Real-readback RED tests live in
test_grader_calibration_real_fixtures.py.

Run:  python3 -m pytest scripts/test_grader_calibration.py -q
"""
import importlib.util
import json
import os
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

from focus_registry.comprehension import (  # noqa: E402
    grade_canary, check_comprehension, _norm, _distinctive)


# ---------------------------------------------------------------- fixtures ---

def _mk_transcript(tmp_path, texts):
    """A tiny Claude-shaped jsonl whose message texts are `texts`."""
    rows = []
    for i, t in enumerate(texts, 1):
        rows.append(json.dumps({
            "uuid": f"msg_t{i}", "timestamp": f"2026-08-20T01:{i:02d}:00Z",
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": t}]}}))
    p = tmp_path / "pred.jsonl"
    p.write_text("\n".join(rows))
    return str(p)


# A synthetic predecessor transcript rich enough to ground a genuine readback:
# distinct ids per topic + specific vocabulary.
_TEXTS = [
    "The ledger writeback fix landed at commit ab12cd34ef with walkgate "
    "serialization; the submit path returned HTTP 431 before the fix because "
    "the pending row kept only part-zero options.",
    "Lane-B chose the spooldrain hybrid: apr_77ffe201 approved the per-client "
    "fsync spool, draining with intent idempotency after daemon restart.",
    "The watchdog ruling qnr_55aa0011 kept the two-minute checker; the spool "
    "becomes the primary durability guarantee across the outage window.",
    "Phantom blocker audit: 47 messages piled at the dead alias seat-old9; "
    "the norm is a proactive card when a coordinated lane stalls. Unread "
    "messages accumulated silently; surfacing the stalled lane proactively "
    "and diverting nothing is the standing expectation.",
]
# region big enough (>REGION_OVERLAP_MAX_CHARS) to force the grounding path
_BIGREGION = ("noise " * 500)


def _canary_rows(n=4, region=_BIGREGION):
    qs = ["What was the ledger defect and its HTTP symptom?",
          "What enqueue-durability resolution did Lane-B use?",
          "What did the watchdog ruling change?",
          "Describe the phantom blocker incident."]
    return [{"id": f"q{i}", "answer": region, "from_pointer": True,
             "question": qs[i - 1]} for i in range(1, n + 1)]


_GOOD_ANSWERS = {
    "q1": "The pending row kept part-zero options only, so submits hit HTTP 431 "
          "until the writeback landed at ab12cd34ef with walkgate serialization.",
    "q2": "The spooldrain hybrid: apr_77ffe201 chose a per-client fsync spool "
          "drained with intent idempotency after a daemon restart.",
    "q3": "qnr_55aa0011 kept the two-minute checker, making the spool the "
          "primary durability guarantee across the outage window.",
    "q4": "47 messages piled at the dead alias seat-old9; the standing norm is "
          "a proactive card when a coordinated lane stalls.",
}

_WAFFLE = ("I fully absorbed the context. The defect was surfaced and resolved "
           "carefully through revision, addressing the durability and process "
           "concerns raised during the careful review and discussion process.")


@pytest.fixture
def tr_text():
    return "\n".join(_TEXTS)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    d = tmp_path / "handoffs"
    monkeypatch.setattr(RG, "HANDOFFS_DIR", d)
    return d


# ------------------------------------------------- grounding path verdicts ---

def test_good_readback_passes_supervised(tr_text):
    r = grade_canary(_GOOD_ANSWERS, _canary_rows(), transcript_text=tr_text)
    assert r["passed"] is True and r["missed"] == []
    assert r["aggregate"]["idclass"] is True


def test_absent_answers_fail(tr_text):
    r = grade_canary({}, _canary_rows(), transcript_text=tr_text)
    assert r["passed"] is False
    assert set(r["missed"]) == {"q1", "q2", "q3", "q4"}


def test_all_waffle_fails(tr_text):
    r = grade_canary({f"q{i}": _WAFFLE for i in range(1, 5)}, _canary_rows(),
                     transcript_text=tr_text)
    assert r["passed"] is False


def test_hallucinated_ids_fail(tr_text):
    fake = ("It was HTTP 909 from commit deadbeef99 fixed via apr_zz998877 "
            "and qnr_00zzz999.")
    r = grade_canary({f"q{i}": fake for i in range(1, 5)}, _canary_rows(),
                     transcript_text=tr_text)
    assert r["passed"] is False
    assert all(v == "HALLUCINATED"
               for v in (r["per_question"][q]["verdict"] for q in r["per_question"]))


def test_sprinkle_same_ids_everywhere_fails(tr_text):
    sprinkle = ("This relates to ab12cd34ef and apr_77ffe201 and qnr_55aa0011 "
                "and 431 as discussed.")
    r = grade_canary({f"q{i}": sprinkle for i in range(1, 5)}, _canary_rows(),
                     transcript_text=tr_text)
    assert r["passed"] is False               # distinct-evidence rule
    assert r["aggregate"]["distinct_q"] == 0


def test_question_echo_fails(tr_text):
    rows = _canary_rows()
    r = grade_canary({row["id"]: row["question"] * 3 for row in rows}, rows,
                     transcript_text=tr_text)
    assert r["passed"] is False


def test_no_transcript_is_ungradeable_failhold_not_vacuous_pass():
    r = grade_canary(_GOOD_ANSWERS, _canary_rows(), transcript_text=None)
    assert r["passed"] is False
    assert r.get("ungradeable")               # distinct reason, not a miss


def test_one_weak_tolerated_supervised_zero_in_strict(tr_text):
    ans = dict(_GOOD_ANSWERS)
    # a substantive but uncited answer: rare vocabulary, zero digit anchors
    ans["q4"] = ("Messages accumulated unread and silently; surfacing the "
                 "stalled coordinated lane proactively is the standing "
                 "expectation, diverting nothing.")
    sup = grade_canary(ans, _canary_rows(), transcript_text=tr_text,
                       mode="supervised")
    str_ = grade_canary(ans, _canary_rows(), transcript_text=tr_text,
                        mode="strict")
    assert sup["passed"] is True              # <=1 WEAK tolerated
    assert str_["passed"] is False            # zero-WEAK arming mode


def test_strict_requires_region_corroboration(tmp_path):
    """Strict mode: every resolved region must contain >=1 of that question's
    grounded anchors. Regions = the right transcript text -> PASS; regions =
    unrelated noise -> strict FAIL while supervised still passes."""
    ttext = "\n".join(_TEXTS)
    pad = " filler" * 400
    good_rows = [{"id": f"q{i}", "answer": _TEXTS[i - 1] + pad,
                  "from_pointer": True, "question": q["question"]}
                 for i, q in enumerate(_canary_rows(), 1)]
    ok = grade_canary(_GOOD_ANSWERS, good_rows, transcript_text=ttext,
                      mode="strict")
    assert ok["passed"] is True
    bad_rows = _canary_rows(region=_BIGREGION)     # noise regions
    sup = grade_canary(_GOOD_ANSWERS, bad_rows, transcript_text=ttext,
                       mode="supervised")
    bad = grade_canary(_GOOD_ANSWERS, bad_rows, transcript_text=ttext,
                       mode="strict")
    assert sup["passed"] is True
    assert bad["passed"] is False


def test_small_region_keeps_legacy_overlap_semantics():
    """The daemon recanary path plants one-sentence regions; those keep the
    legacy overlap rule (fair at that scale) — no transcript needed."""
    rows = [{"id": "q1", "from_pointer": True,
             "answer": "The rotation rule that gates actionability is "
                       "canarymatchonly - one agent per beat."}]
    ok = grade_canary({"q1": "the gating rule is canarymatchonly, one agent "
                             "per beat"}, rows)
    bad = grade_canary({"q1": "something entirely unrelated happened"}, rows)
    assert ok["passed"] is True
    assert bad["passed"] is False


def test_tokenization_pin():
    """The grader's calibration assumes _norm splits id prefixes off (the hex
    tail carries the signal) — a later _norm change silently recalibrates the
    grader (claude-leg build-time must #4)."""
    assert _norm("apr_80cf718f") == "apr 80cf718f"
    assert "80cf718f" in _distinctive("apr_80cf718f")
    assert _norm("DEC-1787728346") == "dec 1787728346"


# ------------------------------------------------------- wiring + refusals ---

def _author(gate, n=2):
    RG.author_canary("succ-x", [
        {"q": f"What happened in topic {i}?",
         "source_pointer": f"jsonl:turn-{i}"} for i in range(1, n + 1)])


def test_author_canary_rejects_prose_pointer(gate):
    with pytest.raises(ValueError, match="not mechanically resolvable"):
        RG.author_canary("succ-x", [
            {"q": "what?", "source_pointer": "the part where you drained it"}])


def test_author_canary_accepts_all_resolver_forms(gate):
    RG.author_canary("succ-x", [
        {"q": "a?", "source_pointer": "jsonl:msg_abc123"},
        {"q": "b?", "source_pointer": "turn-14"},          # prefix optional
        {"q": "c?", "source_pointer":
            "jsonl:2026-08-18T09:50:00Z..2026-08-18T09:55:00Z"}])


def test_split_sections_line_anchored_and_bounded():
    md = ("# readback\n**cq1 — first.** body one mentions **q2** inline "
          "without splitting.\n**cq2 — second.** body two.\n"
          "## Standing by\ntrailing prose never counts\n")
    out = RG.split_readback_sections(md, 2)
    assert set(out) == {"q1", "q2"}
    assert "inline" in out["q1"] and "body two" in out["q2"]
    assert "trailing prose" not in out["q2"]


def test_split_sections_duplicate_headers_refuse():
    md = "**q1 x**\n**q1 again**\n**q2 y**\n"
    with pytest.raises(RG.GradeRefusal, match="duplicate"):
        RG.split_readback_sections(md, 2)


def test_split_sections_count_mismatch_refuse():
    with pytest.raises(RG.GradeRefusal, match="do not match"):
        RG.split_readback_sections("**q1 only**\n", 3)


def _grade_args(gate, tmp_path, **kw):
    class A:
        successor_id = "succ-x"
        transcript = kw.get("transcript")
        predecessor_sid = kw.get("predecessor_sid")
        readback_file = kw.get("readback_file")
        answers_file = kw.get("answers_file")
        ground_truth = kw.get("ground_truth")
        strict = kw.get("strict", False)
    return A


def test_cli_grade_writes_artifact_and_passed_reads_it(gate, tmp_path):
    tpath = _mk_transcript(tmp_path, _TEXTS)
    _author(gate, 2)
    rb = tmp_path / "rb.md"
    rb.write_text("**q1** turn one content here in my own words.\n"
                  "**q2** turn two content here in my own words.\n")
    a = _grade_args(gate, tmp_path, transcript=tpath,
                    readback_file=str(rb))
    rc = RG._cli_grade(a)
    art = json.loads((gate / "succ-x.comprehension.json").read_text())
    assert art["mode"] == "supervised"
    # deterministic: content-free own-words vs the planted small regions FAILs
    assert art["pass"] is False and rc == 1
    # comprehension_passed consumes the same artifact fail-closed
    assert RG.comprehension_passed("succ-x") is art["pass"]


def test_cli_strict_flag_wins_over_ground_truth_mode(gate, tmp_path):
    """Review I-1: a ground-truth file carrying mode:'supervised' must NOT
    silently downgrade an operator's --strict arming-grade."""
    tpath = _mk_transcript(tmp_path, _TEXTS)
    _author(gate, 2)
    rb = tmp_path / "rb.md"
    rb.write_text("**q1** words one.\n**q2** words two.\n")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps({"mode": "supervised"}))
    a = _grade_args(gate, tmp_path, transcript=tpath, readback_file=str(rb),
                    ground_truth=str(gt), strict=True)
    RG._cli_grade(a)
    art = json.loads((gate / "succ-x.comprehension.json").read_text())
    assert art["mode"] == "strict"


def test_cli_grade_refusal_writes_no_artifact(gate, tmp_path):
    tpath = _mk_transcript(tmp_path, _TEXTS)
    _author(gate, 3)
    rb = tmp_path / "rb.md"
    rb.write_text("**q1 only one section**\n")
    a = _grade_args(gate, tmp_path, transcript=tpath, readback_file=str(rb))
    with pytest.raises(RG.GradeRefusal):
        RG._cli_grade(a)
    assert not (gate / "succ-x.comprehension.json").exists()


def test_sid_glob_zero_or_many_matches_refuse(gate, tmp_path, monkeypatch):
    import glob as _glob
    monkeypatch.setattr(_glob, "glob", lambda pat, *args, **kwargs: [])
    with pytest.raises(RG.GradeRefusal, match=r"(matched 0|not found)"):
        RG.find_transcript_by_sid("deadbeef")
    monkeypatch.setattr(_glob, "glob", lambda pat, *args, **kwargs: ["/a.jsonl", "/b.jsonl"])
    with pytest.raises(RG.GradeRefusal, match=r"(matched 2|not found)"):
        RG.find_transcript_by_sid("deadbeef")


def test_missing_artifact_still_fails_closed(gate):
    assert RG.comprehension_passed("never-graded") is False


def test_strict_region_failure_names_only_noncorroborated_qids():
    """gen29 follow-up: on a strict region-corroboration failure, missed must
    name ONLY the non-corroborated questions (listing all qids sent a reviewer
    to the wrong question on the live gen29 datum)."""
    ttext = "\n".join(_TEXTS)
    pad = " filler" * 400
    rows = []
    for i, q in enumerate(_canary_rows(), 1):
        # q1-q3 regions = the right transcript text; q4 region = pure noise
        region = (_TEXTS[i - 1] + pad) if i < 4 else _BIGREGION
        rows.append({"id": f"q{i}", "answer": region, "from_pointer": True,
                     "question": q["question"]})
    r = grade_canary(_GOOD_ANSWERS, rows, transcript_text=ttext, mode="strict")
    assert r["passed"] is False
    assert r["missed"] == ["q4"]              # the culprit, not all qids
    # collective failures still report all grounded questions (never silent):
    sprinkle = ("This relates to ab12cd34ef and apr_77ffe201 and qnr_55aa0011 "
                "and 431 as discussed.")
    r2 = grade_canary({f"q{i}": sprinkle for i in range(1, 5)}, _canary_rows(),
                      transcript_text=ttext)
    assert r2["passed"] is False and set(r2["missed"]) == {"q1", "q2", "q3", "q4"}


def test_canary_evidence_labeled_advisory_only(gate, tmp_path):
    """gen29 follow-up: the legacy overlap-evidence block must self-declare it
    has no grading effect (a reviewer diagnosed a live grade from it)."""
    _author(gate, 1)
    ev = RG.canary_evidence("succ-x", ["an answer"])
    assert ev["advisory_only"] is True
    assert "q1" in ev


# ---- codex rollouts: turn-<n> must index TEXT-BEARING message rows, not meta/event rows
# (gpt-6-astra-agent g3->g4, 2026-09-16: 'jsonl:turn-1' hit the session_meta row -> "" ->
# unpointable-canary on every question of a real, grounded readback). ---------------------
def test_turn_pointer_skips_codex_meta_and_event_rows(tmp_path):
    from rotation_gate_manual import resolve_pointer
    rows = [
        {"type": "session_meta", "payload": {"id": "01a0", "cwd": "/x"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "FIRST USER TURN about rent"}]}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {}}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                              "content": [{"type": "output_text", "text": "SECOND, the reply"}]}},
    ]
    p = tmp_path / "rollout.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert "FIRST USER TURN" in resolve_pointer(str(p), "jsonl:turn-1")
    assert "SECOND, the reply" in resolve_pointer(str(p), "jsonl:turn-2")
    assert resolve_pointer(str(p), "jsonl:turn-3") == ""


def test_author_canary_refusal_names_the_outside_transcript_rule(gate):
    """gm ruling msg_f937b086: the predecessor must see, at authoring time, that a fact living
    outside its transcript is cited by msg_store row / commit sha, never by file."""
    import pytest
    from rotation_gate_manual import author_canary
    with pytest.raises(ValueError) as ei:
        author_canary("x-g2", [{"q": "what changed?", "source_pointer": "jsonl:turn-1 (user block, see transcript.md)"}])
    msg = str(ei.value)
    assert "msg_store row or commit sha" in msg and "TEXT-BEARING" in msg
