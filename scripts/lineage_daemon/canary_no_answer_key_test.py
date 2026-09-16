"""P0: the canary gate must have NO ANSWER KEY, anywhere, ever (the operator ruling
2026-08-18 via gm msg_34625c5e; found by initiative-architect, verified by gm).

The defect had three faces and they are one bug:
  1. author_gate's own schema instruction ORDERED `{id,question,answer}` into the
     committed handoff — the document protocol-v2 requires the successor to read
     IN FULL. Every daemon-driven rotation produced a compromised gate BY DESIGN;
     the two catches tonight were agents beating their own instructions.
  2. Nothing linted the COMMITTED DOC (the redaction only ever applied to the
     daemon-generated init surface, and humans/agents author the doc by hand).
  3. "Daemon-held" answers landed in state/agent-handoffs/<successor>.canary.json
     — inside the tree the successor works in. The control was the candidate's
     willingness not to look. Honor is a fine bonus and a terrible mechanism.

the operator's correction, which supersedes the vault design: do not PROTECT a secret,
DELETE it. The canary stores {id, question, source_pointer}; the pointer names
where the fact lives in the predecessor's jsonl; the grader verifies the
successor's own words against that transcript AT GRADE TIME. Leaking the artifact
becomes harmless by construction — it tells the successor to go read the
transcript, which is exactly what protocol-v2 wants.

ACCEPTANCE (the operator's bar, mechanical): a successor with full read access to EVERY
artifact — repo, handoffs, canary file, daemon state — still cannot obtain an
expected answer, because none was ever written.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from author_gate import build_author_trigger, handoff_ready  # noqa: E402
from handoff_schema import Handoff  # noqa: E402


def _canary(n=3, with_answer=False, with_pointer=True):
    out = []
    for i in range(1, n + 1):
        q = {"id": f"q{i}", "question": f"question {i}?"}
        if with_pointer:
            q["source_pointer"] = f"jsonl:msg_abc{i}"
        if with_answer:
            q["answer"] = f"the secret answer {i}"
        out.append(q)
    return out


def _handoff(**kw):
    base = dict(
        current_goal="own the delivery lane",
        phase_state={"plan_ref": "spec.md", "phase": "build", "next_gate": "soak"},
        next_3_actions=[{"action": "a", "first_effect": {"kind": "file", "target": "x",
                                                         "check": "exists"}}],
        decisions=[{"text": "t", "rationale": "r"}] * 3,
        open_loops=["l"],
        file_roots_touched=["scripts/"],
        hazards=["h"],
        first_effect={"kind": "file", "target": "x", "check": "exists"},
        canary_questions=_canary(),
    )
    base.update(kw)
    return Handoff.from_dict(base)


# ---- face 1: the schema instruction itself --------------------------------

def test_author_prompt_never_asks_for_an_answer():
    p = build_author_trigger("some-agent", "docs/HANDOFF_x.md")
    assert "{id,question,answer}" not in p.replace(" ", "")
    assert "source_pointer" in p


def test_author_prompt_states_the_no_answer_rule_explicitly():
    p = build_author_trigger("some-agent", "docs/HANDOFF_x.md").lower()
    assert "never" in p and "answer" in p


# ---- face 2: lint the COMMITTED artifact ----------------------------------

def test_validate_rejects_a_canary_carrying_an_answer():
    errs = _handoff(canary_questions=_canary(with_answer=True)).validate(
        require_richness=True)
    assert any("answer" in e.lower() for e in errs), errs


@pytest.mark.parametrize("leak_key", ["answer", "expected", "Answer", "EXPECTED"])
def test_validate_rejects_every_answer_shaped_key(leak_key):
    qs = _canary()
    qs[0][leak_key] = "leaked"
    errs = _handoff(canary_questions=qs).validate(require_richness=True)
    assert any("answer" in e.lower() for e in errs), (leak_key, errs)


def test_readiness_refuses_a_leaking_handoff():
    r = handoff_ready(_handoff(canary_questions=_canary(with_answer=True)).to_dict(),
                      mtime=None, now=0)
    assert r["ready"] is False
    assert any("answer" in x.lower() for x in r["reasons"]), r


def test_readiness_accepts_a_pointer_only_canary():
    r = handoff_ready(_handoff().to_dict(), mtime=None, now=0)
    assert r["ready"] is True, r


# ---- an unpointable "fact" was never a fair question ----------------------

def test_validate_rejects_a_canary_without_a_source_pointer():
    errs = _handoff(canary_questions=_canary(with_pointer=False)).validate(
        require_richness=True)
    assert any("pointer" in e.lower() for e in errs), errs


# ---- face 3: nothing to protect ------------------------------------------

def test_successor_visible_form_is_already_the_whole_truth():
    """to_successor_init used to STRIP answers. With no answer key there is
    nothing to strip: the redacted and full forms carry the same canary facts,
    so a leak is harmless by construction."""
    h = _handoff()
    full = {q["id"]: q for q in h.to_dict()["canary_questions"]}
    shown = {q["id"]: q for q in h.to_successor_init()["canary_questions"]}
    assert set(full) == set(shown)
    for qid, q in full.items():
        assert "answer" not in q and "expected" not in q
        assert shown[qid].get("question") == q.get("question")


# ---- the matcher must not become a false-failure machine -------------------
# Ground truth is now a transcript REGION, not a short answer token. Requiring
# every distinctive word of a whole region to appear in an own-words answer would
# fail every honest successor — a gate that fails good candidates is worse than
# no gate, and it is the exact "loud rather than correct" pattern.

def _grade(answer, expected):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from focus_registry.comprehension import grade_canary
    return grade_canary({"q1": answer}, [{"id": "q1", "answer": expected}])


REGION = ("Root cause pinned: extract_model derives the model from the jsonl "
          "message.model, the API id, which has no CLI variant suffix, so every "
          "ten-minute scan overwrote the sessions model field and stripped it.")


def test_own_words_answer_passes_against_a_transcript_region():
    ans = ("extract_model read the model out of the transcript message.model — the "
           "API id — so each scan stripped the variant suffix from the row")
    assert _grade(ans, REGION)["passed"], "an honest own-words answer must pass"


def test_an_empty_or_off_topic_answer_still_fails_against_a_region():
    assert not _grade("", REGION)["passed"]
    assert not _grade("the deploy pipeline uses netlify and a cron job",
                      REGION)["passed"]


def test_short_expected_answers_keep_the_strict_containment_rule():
    """A supervisor grading in-context may still pass a short factual answer;
    that path must stay strict."""
    assert _grade("the fix is c1965256b", "c1965256b")["passed"]
    assert not _grade("some other hash", "c1965256b")["passed"]
