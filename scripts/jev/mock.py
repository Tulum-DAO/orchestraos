"""MockJev: a deterministic, offline stand-in for the real System One endpoint.

Port of ten-levels-of-jev src/core/mock.ts, kept numerically identical (tests/jev
golden fixtures were produced by the TypeScript mock). It mimics the wire contract:
typed answers, distributions that sum to 1, legends on scores, confidence from how
peaked the distribution is.

How it decides: token overlap between the flattened state and each option / level /
criteria description, through a softmax. It stands in for *shape*, not intelligence.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Set

from .types import Answer, Question, State

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "of", "to", "in", "on", "at", "for",
    "with", "without", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "done",
    "this", "that", "these", "those", "it", "its", "as", "by", "from", "into", "about", "against",
    "between", "through", "during", "before", "after", "above", "below", "up", "down", "out", "off",
    "over", "under", "again", "further", "once", "here", "there", "when", "where", "why", "how",
    "all", "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "too", "very", "s", "t", "can", "will", "just", "don", "should",
    "now", "d", "ll", "m", "o", "re", "ve", "y", "i", "you", "we", "they", "he", "she", "what",
    "which", "who", "whom", "am", "has", "have", "had", "having", "message", "text", "given",
}

_PUNCT = re.compile(r"[`_*#>/\[\]{}()\"',.;:!?\\-]")
_WS = re.compile(r"\s+")


def _stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ing"):
        return t[:-3]
    if len(t) > 4 and t.endswith("ed"):
        return t[:-2]
    if len(t) > 3 and t.endswith("es"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str) -> Set[str]:
    parts = _WS.split(_PUNCT.sub(" ", text.lower()))
    return {_stem(t) for t in parts if len(t) > 1 and t not in STOPWORDS}


def _js_json(value: Any, indent: int | None = None) -> str:
    """JSON.stringify-compatible text for the shapes states take (ints stay ints)."""
    return json.dumps(value, indent=indent, ensure_ascii=False,
                      separators=(",", ": ") if indent else (",", ":"))


def flatten_state(state: State) -> str:
    """Flatten any state shape into text the overlap engine can read."""
    if isinstance(state, str):
        return state
    return _js_json(state, indent=2)


def _instructions_text(q: Question) -> str:
    ins = q["instructions"]
    return ins if isinstance(ins, str) else _js_json(ins)


def _softmax(values: List[float], temperature: float = 0.6) -> List[float]:
    if all(v == 0 for v in values):
        return [1 / len(values)] * len(values)
    top = max(values)
    exps = [math.exp((v - top) / temperature) for v in values]
    total = sum(exps)
    return [e / total for e in exps]


def _affinity(state_tokens: Set[str], description: str) -> int:
    return sum(1 for t in tokenize(description) if t in state_tokens)


def _round(n: float, places: int = 4) -> float:
    """JavaScript Math.round(n * 10**places) / 10**places (half rounds toward +inf)."""
    f = 10 ** places
    return math.floor(n * f + 0.5) / f


def _noul_answer(q: Question, st: Set[str]) -> Answer:
    criteria = q.get("criteria") or {}
    yes = _affinity(st, _instructions_text(q) + " " + (criteria.get("true") or ""))
    no = _affinity(st, criteria.get("false") or "")
    k = 2.5
    signal = (yes - no) / (yes + no + k)
    p = 1 / (1 + math.exp(-4 * signal))
    return {"type": "noul", "noul": _round(p)}


def _choice_answer(q: Question, st: Set[str]) -> Answer:
    entries = list(q["criteria"].items())
    affinities = [
        _affinity(st, option.replace("_", " ")) * 1.5 + _affinity(st, desc or "") * 1.0
        for option, desc in entries
    ]
    probs = _softmax(affinities)
    best = 0
    for i in range(len(entries)):
        if probs[i] > probs[best]:
            best = i
    return {
        "type": "choice",
        "choice": entries[best][0],
        "probabilities": {opt: _round(probs[i]) for i, (opt, _) in enumerate(entries)},
        "confidence": _round(probs[best]),
    }


def _score_answer(q: Question, st: Set[str]) -> Answer:
    levels: List[str] = q["criteria"]
    probs = _softmax([_affinity(st, level) + 1e-9 for level in levels])
    return {
        "type": "score",
        "score": _round(sum(p * i for i, p in enumerate(probs)), 2),
        "legend": {str(i): level for i, level in enumerate(levels)},
        "probabilities": {str(i): _round(p) for i, p in enumerate(probs)},
        "confidence": _round(max(probs)),
    }


class MockJev:
    model = "jev-1.13.0-mock"

    def system_one(self, request: Dict[str, Any]) -> Dict[str, Any]:
        flat = flatten_state(request["state"])
        st = tokenize(flat)
        answers: Dict[str, Answer] = {}
        for qid, q in request["questions"].items():
            if q["type"] == "noul":
                answers[qid] = _noul_answer(q, st)
            elif q["type"] == "choice":
                answers[qid] = _choice_answer(q, st)
            else:
                answers[qid] = _score_answer(q, st)
        n = len(request["questions"])
        usage = {"input_tokens": math.ceil(len(flat) / 4) + 20 * n, "output_tokens": 20 * n}
        return {"model": self.model, "answers": answers, "usage": usage}
