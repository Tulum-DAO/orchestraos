"""The manual rotation gate must hold NO answer key either (the operator ruling, gm
msg_34625c5e). Companion to scripts/lineage_daemon/canary_no_answer_key_test.py —
the daemon path and the manual path must not carry two dialects of the gate.

Three properties:
  1. author_canary REFUSES an expected/answer key outright — you cannot write one.
  2. A canary question must carry a resolvable source_pointer into the
     predecessor's jsonl; an unpointable fact is not gradeable and was never a
     fair question (that is a defect in the CANARY, never a failure of the
     successor).
  3. Grading derives evidence from the transcript AT GRADE TIME. The mechanical
     grader only makes claims it can justify — pointer resolution and overlap
     evidence — and refuses to emit a silent all-missed verdict (P2-7).
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


def _jsonl(tmp_path):
    p = tmp_path / "pred.jsonl"
    rows = [
        {"type": "assistant", "timestamp": "2026-08-18T09:52:24Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Root cause pinned: extract_model derives the "
                                      "model from the jsonl message.model, the API "
                                      "id, so every scan strips the variant suffix."}]},
         "uuid": "msg_root"},
        {"type": "assistant", "timestamp": "2026-08-18T10:03:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "The parked-idle exemption ships shadow with "
                                      "armed=False and a 120s floor."}]},
         "uuid": "msg_shadow"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return p


# ---- 1. you cannot write an answer key -------------------------------------

def test_author_canary_refuses_an_expected_answer(gate):
    with pytest.raises(ValueError) as e:
        RG.author_canary("succ", [{"q": "why?", "expected": "because"}])
    assert "answer" in str(e.value).lower()


def test_author_canary_refuses_an_answer_key_by_any_name(gate):
    for key in ("answer", "expected_answer", "ANSWER"):
        with pytest.raises(ValueError):
            RG.author_canary("succ", [{"q": "why?", key: "leak",
                                       "source_pointer": "jsonl:msg_root"}])


def test_author_canary_requires_a_source_pointer(gate):
    with pytest.raises(ValueError) as e:
        RG.author_canary("succ", [{"q": "why?"}])
    assert "pointer" in str(e.value).lower()


def test_authored_artifact_contains_no_answer_field(gate):
    RG.author_canary("succ", [{"q": "why?", "source_pointer": "jsonl:msg_root"}])
    blob = (gate / "succ.canary.json").read_text()
    assert "expected" not in blob and "answer" not in blob, blob


# ---- 2. pointer resolution --------------------------------------------------

def test_pointer_resolves_by_message_id(tmp_path):
    region = RG.resolve_pointer(_jsonl(tmp_path), "jsonl:msg_root")
    assert "extract_model" in region


def test_pointer_resolves_by_turn_index(tmp_path):
    assert "parked-idle" in RG.resolve_pointer(_jsonl(tmp_path), "jsonl:turn-2")


def test_pointer_resolves_by_timestamp_range(tmp_path):
    region = RG.resolve_pointer(
        _jsonl(tmp_path), "jsonl:2026-08-18T09:00:00Z..2026-08-18T10:00:00Z")
    assert "extract_model" in region and "parked-idle" not in region


def test_unresolvable_pointer_is_a_canary_defect_not_a_successor_failure(tmp_path, gate):
    RG.author_canary("succ", [{"q": "why?", "source_pointer": "jsonl:msg_nope"}])
    ev = RG.canary_evidence("succ", ["some own-words answer"],
                            transcript_path=_jsonl(tmp_path))
    assert ev["q1"]["pointer_resolved"] is False
    assert ev["q1"]["defect"] == "unpointable-canary"


# ---- 3. grading evidence, and no silent all-missed (P2-7) -------------------

def test_evidence_reports_real_overlap_from_the_transcript(tmp_path, gate):
    RG.author_canary("succ", [{"q": "what did the scan strip?",
                               "source_pointer": "jsonl:msg_root"}])
    ev = RG.canary_evidence(
        "succ",
        ["extract_model derived the model from the transcript message.model, the "
         "API id, so the variant suffix was stripped by every scan"],
        transcript_path=_jsonl(tmp_path))
    assert ev["q1"]["pointer_resolved"] is True
    assert ev["q1"]["overlap_score"] > 0.2, ev
    assert "extract_model" in ev["q1"]["overlap_terms"]


def test_answers_may_be_a_dict_keyed_by_id_or_a_list(tmp_path, gate):
    """P2-7: zip(canary, list(answers)) turned a DICT into its KEYS, so every
    graded answer became '1'..'5' — well-shaped, content-free, and scored as a
    total miss indistinguishable from an incompetent successor."""
    RG.author_canary("succ", [{"q": "q?", "source_pointer": "jsonl:msg_root"}])
    txt = "extract_model stripped the variant suffix"
    as_list = RG.canary_evidence("succ", [txt], transcript_path=_jsonl(tmp_path))
    as_dict = RG.canary_evidence("succ", {"q1": txt}, transcript_path=_jsonl(tmp_path))
    as_recs = RG.canary_evidence("succ", [{"id": "q1", "answer": txt}],
                                 transcript_path=_jsonl(tmp_path))
    for other in (as_dict, as_recs):
        assert other["q1"]["overlap_score"] == as_list["q1"]["overlap_score"]


def test_degenerate_grader_input_raises_instead_of_scoring_zero(tmp_path, gate):
    RG.author_canary("succ", [{"q": "q?", "source_pointer": "jsonl:msg_root"}],)
    with pytest.raises(RG.AmbiguousGraderInput):
        RG.canary_evidence("succ", {"7": "unmatchable key", "9": "also unmatchable"},
                           transcript_path=_jsonl(tmp_path))


# ---- a POINTER can leak as badly as an ANSWER (found by orchestraos-app-dev-v14
# while complying, msg_9abbaee2) -------------------------------------------------
# Its case: c2 asks "what was the msg_store row id that carried it?" and the
# natural pointer under the new schema is 'jsonl:msg_demo0003_0000003' — which
# hands the successor the answer INSIDE the pointer. Deleting the answer key does
# not help if the pointer becomes one. Non-disclosing forms (turn index, ISO
# range) locate the fact without naming it.

def test_refuses_an_id_pointer_when_the_question_asks_for_that_id(gate):
    with pytest.raises(ValueError) as e:
        RG.author_canary("succ", [{
            "q": "During the E2E, what was the msg_store row id that carried it?",
            "source_pointer": "jsonl:msg_demo0003_0000003"}])
    assert "pointer" in str(e.value).lower() and "disclos" in str(e.value).lower()


@pytest.mark.parametrize("q", [
    "What is the exact agent id recovered after the plist clobber?",
    "Which commit hash fixed the clobber?",
    "Name the DEC id that unblocked the build.",
])
def test_identifier_questions_refuse_identifier_pointers(gate, q):
    with pytest.raises(ValueError):
        RG.author_canary("succ", [{"q": q,
                                   "source_pointer": "jsonl:msg_abc12345_99"}])


def test_iso_range_and_turn_pointers_are_accepted_for_identifier_questions(gate):
    """app-dev-v14's own remedy: locate the fact without naming it."""
    RG.author_canary("succ", [
        {"q": "What was the msg_store row id that carried it?",
         "source_pointer": "jsonl:2026-08-18T09:50:00Z..2026-08-18T09:55:00Z"},
        {"q": "Which commit hash fixed the clobber?",
         "source_pointer": "jsonl:turn-412"},
    ])


def test_id_pointer_is_still_fine_for_a_non_identifier_question(gate):
    """Not a blanket ban — a msg-id pointer is the clearest locator when the
    question is not asking for an identifier."""
    RG.author_canary("succ", [{
        "q": "Why did the first fix revert two agents to the exhausted pool?",
        "source_pointer": "jsonl:msg_abc12345_99"}])


# ---- determinism: ISO comparison must not be a STRING compare (agy, DEC-1787055812)
# resolve_pointer compared timestamps as strings, so mixed subsecond precision or a
# timezone offset silently changes which turns a range resolves to — i.e. the same
# pointer resolves differently depending on how the transcript happened to be
# written. That is a determinism leak in a module whose whole value is determinism.

def _mixed_precision_jsonl(tmp_path):
    import json as _j
    rows = [
        {"uuid": "m1", "timestamp": "2026-08-18T09:52:24.5Z", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text",
                                                       "text": "early fact alpha"}]}},
        {"uuid": "m2", "timestamp": "2026-08-18T09:52:24.123456+00:00",
         "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text",
                                                       "text": "early fact beta"}]}},
        {"uuid": "m3", "timestamp": "2026-08-18T11:00:00+02:00", "type": "assistant",
         "message": {"role": "assistant", "content": [{"type": "text",
                                                       "text": "offset fact gamma"}]}},
    ]
    p = tmp_path / "mixed.jsonl"
    p.write_text("\n".join(_j.dumps(r) for r in rows))
    return p


def test_iso_range_is_compared_as_time_not_as_a_string(tmp_path):
    p = _mixed_precision_jsonl(tmp_path)
    region = RG.resolve_pointer(p, "jsonl:2026-08-18T09:52:00Z..2026-08-18T09:53:00Z")
    assert "alpha" in region and "beta" in region, region
    assert "gamma" not in region, "a +02:00 09:00 event is 07:00Z — outside the range"


def test_offset_timestamps_land_in_their_real_utc_window(tmp_path):
    p = _mixed_precision_jsonl(tmp_path)
    region = RG.resolve_pointer(p, "jsonl:2026-08-18T08:59:00Z..2026-08-18T09:01:00Z")
    assert "gamma" in region, "11:00+02:00 is 09:00Z and must fall in this window"
