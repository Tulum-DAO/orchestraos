"""Focus resolution + drift detection (WS1, Decision 6).

Pure functions over a store dict. `is_drift` is the loop's "moving in the RIGHT
direction" signal: an agent attached to no ACTIVE focus is drifting off the
tracked north-stars and gets flagged (gm-first triage, per Q4).
"""
from typing import List, Optional

ACTIVE = "active"


def _works_on_ids(agent_id: str, store: dict) -> List[str]:
    return [
        e["dst"]
        for e in store.get("edges", [])
        if e.get("rel") == "works_on" and e.get("src") == agent_id
    ]


def focuses_of(agent_id: str, store: dict) -> List[dict]:
    """All focus entities the agent has a works_on edge to."""
    entities = store.get("entities", {})
    return [entities[fid] for fid in _works_on_ids(agent_id, store) if fid in entities]


def focus_of(agent_id: str, store: dict) -> Optional[dict]:
    """The agent's primary focus, preferring an ACTIVE one, else None."""
    memberships = focuses_of(agent_id, store)
    if not memberships:
        return None
    active = [f for f in memberships if f.get("status") == ACTIVE]
    return active[0] if active else memberships[0]


def is_drift(agent_id: str, store: dict) -> bool:
    """True if the agent is attached to NO active focus (off the north-stars)."""
    return not any(f.get("status") == ACTIVE for f in focuses_of(agent_id, store))
