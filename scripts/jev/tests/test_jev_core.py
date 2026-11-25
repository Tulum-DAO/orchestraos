"""Wire contract, builders, and the offline mock (parity with the TypeScript reference)."""
import inspect
import json
import os

import pytest

import jev
from jev import helpers, mock
from jev.types import LIMITS, QuestionValidationError, validate_questions, validate_request

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_package_resolves_to_this_tree():
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    assert os.path.realpath(os.path.dirname(inspect.getfile(jev))) == os.path.realpath(here)


# ---------------------------------------------------------------- builders

def test_builders_produce_wire_shapes():
    assert helpers.noul("Is it?") == {"type": "noul", "instructions": "Is it?"}
    assert helpers.noul("Is it?", {"true": "yes", "false": None}) == {
        "type": "noul", "instructions": "Is it?", "criteria": {"true": "yes"}}
    assert helpers.choice("Pick", {"a": "A", "b": None}) == {"type": "choice", "instructions": "Pick", "criteria": {"a": "A", "b": None}}
    assert helpers.score("Rate", ["lo", "hi"]) == {"type": "score", "instructions": "Rate", "criteria": ["lo", "hi"]}


# ---------------------------------------------------------------- validation

@pytest.mark.parametrize("questions, msg", [
    (None, "nonempty object"),
    ({}, "nonempty object"),
    ({"q": "x"}, "must be an object"),
    ({"q": {"type": "bool", "instructions": "x"}}, "unknown type"),
    ({"q": {"type": "noul", "instructions": "  "}}, "nonblank"),
    ({"q": {"type": "noul", "instructions": 3}}, "nonblank"),
    ({"q": {"type": "noul", "instructions": "x", "criteria": {"maybe": "y"}}}, "true/false"),
    ({"q": {"type": "noul", "instructions": "x", "criteria": {"true": 1}}}, "true/false"),
    ({"q": {"type": "choice", "instructions": "x", "criteria": ["a"]}}, "criteria must be an object"),
    ({"q": {"type": "choice", "instructions": "x", "criteria": {}}}, "no options"),
    ({"q": {"type": "choice", "instructions": "x", "criteria": {"a": 1}}}, "strings or null"),
    ({"q": {"type": "score", "instructions": "x", "criteria": {"a": "b"}}}, "must be an array"),
    ({"q": {"type": "score", "instructions": "x", "criteria": ["one"]}}, "between 2 and 10"),
    ({"q": {"type": "score", "instructions": "x", "criteria": [str(i) for i in range(11)]}}, "between 2 and 10"),
    ({"q": {"type": "score", "instructions": "x", "criteria": ["a", " "]}}, "nonblank strings"),
])
def test_validate_questions_rejects(questions, msg):
    with pytest.raises(QuestionValidationError, match=msg):
        validate_questions(questions)


def test_choice_cap_is_255_inclusive():
    ok = {f"o{i}": None for i in range(LIMITS.MAX_CHOICE_OPTIONS)}
    validate_questions({"q": helpers.choice("Pick", ok)})
    with pytest.raises(QuestionValidationError, match="maximum is 255"):
        validate_questions({"q": helpers.choice("Pick", {**ok, "extra": None})})


def test_object_instructions_and_empty_noul_criteria_are_valid():
    validate_questions({"q": {"type": "noul", "instructions": {"ask": "is it?", "field": "x"}}})
    validate_questions({"q": {"type": "noul", "instructions": "x", "criteria": {}}})


@pytest.mark.parametrize("request_, msg", [
    ("nope", "request object"),
    ({"state": 3, "questions": {"q": helpers.noul("x")}}, "string, object, or array"),
    ({"state": "s", "model": " ", "questions": {"q": helpers.noul("x")}}, "Model must be"),
])
def test_validate_request_rejects(request_, msg):
    with pytest.raises(QuestionValidationError, match=msg):
        validate_request(request_)


# ---------------------------------------------------------------- mock

def test_mock_matches_typescript_reference_golden():
    """Produced by scripts/jev/tests/fixtures/make_mock_golden.mjs against the reference mock.ts."""
    with open(os.path.join(FIXTURES, "mock_golden_ts.json")) as fh:
        cases = json.load(fh)
    assert len(cases) >= 6
    m = mock.MockJev()
    for case in cases:
        assert m.system_one(case["request"]) == case["response"]


def test_golden_comparison_is_sensitive(monkeypatch):
    """Control: a one-step change in the softmax temperature must break parity."""
    with open(os.path.join(FIXTURES, "mock_golden_ts.json")) as fh:
        cases = json.load(fh)
    real = mock._softmax
    monkeypatch.setattr(mock, "_softmax", lambda v, temperature=0.6: real(v, 0.61))
    m = mock.MockJev()
    assert any(m.system_one(c["request"]) != c["response"] for c in cases)


def test_mock_answer_shapes_and_distributions():
    m = mock.MockJev()
    res = m.system_one({"state": "The API returns 500 errors on the invoices endpoint", "questions": {
        "n": helpers.noul("Is this urgent?"),
        "c": helpers.choice("Which team?", {"billing": "Charges, invoices", "technical": "API bugs, 500 errors", "other": None}),
        "s": helpers.score("How bad?", ["fine", "degraded", "down"]),
    }})
    n, c, s = res["answers"]["n"], res["answers"]["c"], res["answers"]["s"]
    assert n["type"] == "noul" and 0 <= n["noul"] <= 1
    assert c["choice"] in ("billing", "technical", "other")
    assert abs(sum(c["probabilities"].values()) - 1) < 0.01
    assert c["confidence"] == max(c["probabilities"].values())
    assert s["legend"] == {"0": "fine", "1": "degraded", "2": "down"}
    assert 0 <= s["score"] <= 2
    assert res["usage"]["input_tokens"] > 0 and res["usage"]["output_tokens"] == 60


def test_mock_is_deterministic_and_neutral_on_no_signal():
    m = mock.MockJev()
    req = {"state": {"ticket": "export crashes in safari"}, "questions": {"q": helpers.choice("Kind?", {"bug": "crashes", "other": None})}}
    assert json.dumps(m.system_one(req)) == json.dumps(m.system_one(req))
    assert m.system_one({"state": "zzz", "questions": {"q": helpers.noul("qqq")}})["answers"]["q"]["noul"] == 0.5


def test_flatten_state_and_js_rounding():
    assert mock.flatten_state("plain") == "plain"
    assert mock.flatten_state({"a": 1}) == '{\n  "a": 1\n}'
    assert mock.flatten_state([1, 2]).startswith("[")
    assert mock._round(0.00005) == 0.0001  # JS Math.round rounds half up; Python round() would give 0.0
