"""TypeSafe System One wire contract for Jev, ported from ten-levels-of-jev src/core/types.ts.

One endpoint: POST https://api.typesafe.ai/v1/systemone (or OpenRouter's decisions API).
You send a `state` and a map of typed `questions`; you get back one typed answer per
question, keyed by the IDs you chose. Question IDs are for your code; the model never
sees them.

Questions and answers stay plain dicts on the wire. The TypedDicts below document the
shapes; validation is done by validate_request / validate_questions before any transport.

  noul    {"type": "noul",   "instructions": str|dict, "criteria"?: {"true"?: str, "false"?: str}}
          -> {"type": "noul", "noul": 0..1}
  choice  {"type": "choice", "instructions": str|dict, "criteria": {option: str|None}}  (1..255 options)
          -> {"type": "choice", "choice": str, "probabilities": {option: p}, "confidence": 0..1}
  score   {"type": "score",  "instructions": str|dict, "criteria": [str, ...]}  (2..10 levels, low->high)
          -> {"type": "score", "score": float, "legend": {"0": str, ...}, "probabilities": {...}, "confidence": 0..1}

NOTE: this module is named `types` to match the spec. It lives inside the `jev` package, so
it never shadows the stdlib. Never run a file in this directory as a script (python3
scripts/jev/x.py puts this directory on sys.path and breaks stdlib imports); use
`python3 -m jev.<module>` with scripts/ on the path, or the scripts/jev_hook.py and
scripts/jev_mcp.py entry points.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

State = Union[str, Dict[str, Any], List[Any]]
Instructions = Union[str, Dict[str, Any]]


class NoulQuestion(TypedDict, total=False):
    type: Literal["noul"]
    instructions: Instructions
    criteria: Dict[str, str]


class ChoiceQuestion(TypedDict):
    type: Literal["choice"]
    instructions: Instructions
    criteria: Dict[str, Optional[str]]


class ScoreQuestion(TypedDict):
    type: Literal["score"]
    instructions: Instructions
    criteria: List[str]


Question = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]
Questions = Dict[str, Question]


class NoulAnswer(TypedDict):
    type: Literal["noul"]
    noul: float


class ChoiceAnswer(TypedDict):
    type: Literal["choice"]
    choice: str
    probabilities: Dict[str, float]
    confidence: float


class ScoreAnswer(TypedDict):
    type: Literal["score"]
    score: float
    legend: Dict[str, str]
    probabilities: Dict[str, float]
    confidence: float


Answer = Union[NoulAnswer, ChoiceAnswer, ScoreAnswer]

QUESTION_TYPES = ("noul", "choice", "score")


class _Limits:
    """Client-side validation limits, mirrored from the published API."""
    MAX_CHOICE_OPTIONS = 255
    MIN_SCORE_LEVELS = 2
    MAX_SCORE_LEVELS = 10
    # Approximate shared token budget for state + all questions.
    TOTAL_TOKEN_BUDGET = 64_000


LIMITS = _Limits()


class QuestionValidationError(ValueError):
    """A request or question block is malformed. Raised before anything is sent."""


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _nonblank_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_request(request: Any) -> None:
    """Validate a whole request ({model?, state, questions}) before any transport."""
    if not _is_record(request):
        raise QuestionValidationError("Expected a request object.")
    state = request.get("state")
    if not isinstance(state, (str, dict, list)):
        raise QuestionValidationError("State must be a string, object, or array.")
    if "model" in request and request["model"] is not None and not _nonblank_str(request["model"]):
        raise QuestionValidationError("Model must be a nonblank string.")
    validate_questions(request.get("questions"))


def validate_questions(questions: Any) -> None:
    """Validate question shapes. Mirrors validateQuestions in the reference exactly."""
    if not _is_record(questions) or not questions:
        raise QuestionValidationError("Questions must be a nonempty object.")
    for qid, q in questions.items():
        if not _is_record(q):
            raise QuestionValidationError(f'Question "{qid}" must be an object.')
        qtype = q.get("type")
        if qtype not in QUESTION_TYPES:
            raise QuestionValidationError(f'Question "{qid}" has a missing or unknown type.')
        instructions = q.get("instructions")
        if not (_nonblank_str(instructions) or _is_record(instructions)):
            raise QuestionValidationError(f'Question "{qid}" needs nonblank string or object instructions.')
        criteria = q.get("criteria")
        if qtype == "noul" and "criteria" in q and criteria is not None:
            if not _is_record(criteria) or any(
                k not in ("true", "false") or (v is not None and not isinstance(v, str))
                for k, v in criteria.items()
            ):
                raise QuestionValidationError(f'Noul "{qid}" criteria must map true/false to descriptions.')
        if qtype == "choice":
            if not _is_record(criteria):
                raise QuestionValidationError(f'Choice "{qid}" criteria must be an object.')
            if not criteria:
                raise QuestionValidationError(f'Choice "{qid}" has no options.')
            if len(criteria) > LIMITS.MAX_CHOICE_OPTIONS:
                raise QuestionValidationError(
                    f'Choice "{qid}" has {len(criteria)} options; the maximum is {LIMITS.MAX_CHOICE_OPTIONS}.')
            if any(v is not None and not isinstance(v, str) for v in criteria.values()):
                raise QuestionValidationError(f'Choice "{qid}" descriptions must be strings or null.')
        if qtype == "score":
            if not isinstance(criteria, list):
                raise QuestionValidationError(f'Score "{qid}" criteria must be an array.')
            if not LIMITS.MIN_SCORE_LEVELS <= len(criteria) <= LIMITS.MAX_SCORE_LEVELS:
                raise QuestionValidationError(
                    f'Score "{qid}" must have between {LIMITS.MIN_SCORE_LEVELS} and '
                    f'{LIMITS.MAX_SCORE_LEVELS} levels; got {len(criteria)}.')
            if any(not _nonblank_str(level) for level in criteria):
                raise QuestionValidationError(f'Score "{qid}" levels must be nonblank strings.')
