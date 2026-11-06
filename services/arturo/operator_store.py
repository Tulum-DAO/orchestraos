"""operator_store — the ONE place the operator's own facts live (name, timezone, role, pronouns, devices).

Why (Shaw, 2026-09-22): onboarding used to parse the operator's name with a regex in the browser and
keep it in localStorage, so the brain never learned who it was talking to, a second device asked
again, and "hi my name is Shaw nice to meet you" was recorded as "Hi". Facts about the person are
conversation content: the brain extracts them with the set_operator_fact tool and they persist HERE,
server-side, read by every surface through /health and /text.

File: <ARTURO_STATE>/operator.json — {"facts": {"name": {"value", "source", "set_at", "source_thread"?}}}. source
is "brain" (the tool) or "typed" (a surface stored a verbatim answer for a field the brain does not
own). source_thread is the chat the fact was learned in; a fact written before it existed has none
("source unknown") and is never removed by deleting a chat. Atomic whole-file replace. Never raises
into a turn: a bad file reads as no facts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("name", "timezone", "role", "pronouns", "devices")
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


def set_fact(state_dir, field: str, value: str, source: str = "brain", source_thread: str | None = None) -> dict:
    """Record one fact. Returns the stored entry. Refuses unknown fields and empty values
    (the caller gets a ValueError, never a half-written file). `source_thread`: the chat it was
    learned in, when there is one."""
    if field not in FIELDS:
        raise ValueError(f"unknown operator field {field!r}; one of {FIELDS}")
    value = " ".join(str(value or "").split())[:_MAX_VALUE]
    if not value:
        raise ValueError("empty value")
    data = load(state_dir)
    entry = {"value": value, "source": source, "set_at": datetime.now(timezone.utc).isoformat()}
    prior = data["facts"].get(field)
    if isinstance(prior, dict) and prior.get("value") == value:
        # Said again: it was first learned where it was first learned (or nowhere known). Restating a fact in
        # another chat never makes deleting that chat able to forget it.
        if prior.get("source_thread"):
            entry["source_thread"] = prior["source_thread"]
    elif source_thread:
        entry["source_thread"] = str(source_thread)[:200]
    data["facts"][field] = entry
    _write(state_dir, data)
    return entry


def _write(state_dir, data: dict) -> None:
    p = _path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)


def from_thread(state_dir, thread_id: str) -> dict:
    """{field: value} for the facts first learned in this chat (their source_thread is it)."""
    if not thread_id:
        return {}
    facts = load(state_dir)["facts"]
    return {k: (f or {}).get("value") for k, f in facts.items()
            if isinstance(f, dict) and f.get("source_thread") == thread_id and f.get("value")}


def forget_thread(state_dir, thread_id: str) -> list:
    """Remove the facts learned in this chat; returns the fields removed. A fact with no source_thread is never
    touched. Raises OSError when the file cannot be written."""
    gone = sorted(from_thread(state_dir, thread_id))
    if gone:
        data = load(state_dir)
        for k in gone:
            data["facts"].pop(k, None)
        _write(state_dir, data)
    return gone


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
    if facts["devices"]:
        bits.append(f"devices they use: {facts['devices']}")
    return ("OPERATOR: " + "; ".join(bits) + ".") if bits else ""


TOOL = {
    "type": "function",
    "function": {
        "name": "set_operator_fact",
        "description": (
            "Record one durable fact about the OPERATOR you are talking to (the person, not the system): "
            "their name, how they like to be addressed, their timezone, their role, the devices they use. Call it the moment "
            "they tell you such a thing, so every future conversation on every surface knows it. Copy "
            "the value from what they said; capitalise a name the way a name is written. Never guess."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "field": {"type": "string", "enum": list(FIELDS)},
                "value": {"type": "string", "description": "The fact, verbatim. For name: only the name they want to be called. For devices: the device names, comma-separated."},
            },
            "required": ["field", "value"],
        },
    },
}
