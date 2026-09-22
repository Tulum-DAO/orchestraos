"""operator_store — the ONE place the operator's own facts live (name, timezone, role, pronouns).

Why (Shaw, 2026-09-22): onboarding used to parse the operator's name with a regex in the browser and
keep it in localStorage, so the brain never learned who it was talking to, a second device asked
again, and "hi my name is Shaw nice to meet you" was recorded as "Hi". Facts about the person are
conversation content: the brain extracts them with the set_operator_fact tool and they persist HERE,
server-side, read by every surface through /health and /text.

File: <ARTURO_STATE>/operator.json — {"facts": {"name": {"value", "source", "set_at"}}}. source is
"brain" (the tool) or "typed" (a surface stored a verbatim answer for a field the brain does not
own). Atomic whole-file replace. Never raises into a turn: a bad file reads as no facts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("name", "timezone", "role", "pronouns")
_MAX_VALUE = 120


def _path(state_dir) -> Path:
    return Path(state_dir) / "operator.json"


def load(state_dir) -> dict:
    try:
        data = json.loads(_path(state_dir).read_text())
        facts = data.get("facts") if isinstance(data, dict) else None
        return {"facts": facts if isinstance(facts, dict) else {}}
    except (OSError, ValueError):
        return {"facts": {}}


def get(state_dir, field: str):
    """The value of one fact, or None."""
    f = load(state_dir)["facts"].get(field)
    return (f or {}).get("value") or None


def set_fact(state_dir, field: str, value: str, source: str = "brain") -> dict:
    """Record one fact. Returns the stored entry. Refuses unknown fields and empty values
    (the caller gets a ValueError, never a half-written file)."""
    if field not in FIELDS:
        raise ValueError(f"unknown operator field {field!r}; one of {FIELDS}")
    value = " ".join(str(value or "").split())[:_MAX_VALUE]
    if not value:
        raise ValueError("empty value")
    data = load(state_dir)
    entry = {"value": value, "source": source, "set_at": datetime.now(timezone.utc).isoformat()}
    data["facts"][field] = entry
    p = _path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)
    return entry


def public(state_dir) -> dict:
    """The shape surfaces read: {"name": "Shaw"|None, ...} — values only, no provenance."""
    facts = load(state_dir)["facts"]
    return {k: (facts.get(k) or {}).get("value") or None for k in FIELDS}


def context_line(state_dir) -> str:
    """One line for the system context, or '' when nothing is known. The brain addresses the
    operator by name only when it actually knows it — never a placeholder."""
    facts = public(state_dir)
    bits = []
    if facts["name"]:
        bits.append(f"their name is {facts['name']} — use it when natural")
    if facts["pronouns"]:
        bits.append(f"pronouns {facts['pronouns']}")
    if facts["role"]:
        bits.append(f"role: {facts['role']}")
    if facts["timezone"]:
        bits.append(f"timezone {facts['timezone']}")
    return ("OPERATOR: " + "; ".join(bits) + ".") if bits else ""


TOOL = {
    "type": "function",
    "function": {
        "name": "set_operator_fact",
        "description": (
            "Record one durable fact about the OPERATOR you are talking to (the person, not the system): "
            "their name, how they like to be addressed, their timezone, their role. Call it the moment "
            "they tell you such a thing, so every future conversation on every surface knows it. Copy "
            "the value from what they said; capitalise a name the way a name is written. Never guess."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": list(FIELDS)},
                "value": {"type": "string", "description": "The fact, verbatim. For name: only the name they want to be called."},
            },
            "required": ["field", "value"],
        },
    },
}
