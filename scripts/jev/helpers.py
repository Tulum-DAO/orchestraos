"""Question builders, matching the @typesafe-ai/sdk helper style (reference src/core/helpers.ts)."""
from __future__ import annotations

from typing import Dict, List, Optional

from .types import ChoiceQuestion, Instructions, NoulQuestion, ScoreQuestion


def noul(instructions: Instructions, criteria: Optional[Dict[str, Optional[str]]] = None) -> NoulQuestion:
    """A yes/no question. criteria: {"true": what yes means, "false": what no means}; None values dropped."""
    q: NoulQuestion = {"type": "noul", "instructions": instructions}
    if criteria:
        cleaned = {k: v for k, v in criteria.items() if v is not None}
        if cleaned:
            q["criteria"] = cleaned
    return q


def choice(instructions: Instructions, criteria: Dict[str, Optional[str]]) -> ChoiceQuestion:
    """Pick one option. criteria maps option -> description (or None). Add an `other` exit."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score(instructions: Instructions, criteria: List[str]) -> ScoreQuestion:
    """A position on 2..10 levels, low to high. Describe situations, not degrees."""
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}
