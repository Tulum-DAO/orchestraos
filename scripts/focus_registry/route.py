"""PM-per-focus handoff routing (WS2, Decisions 1+2).

A finishing agent's handoff smart-routes to whoever should own its backlog:

    1. its focus's owner-PM (focus.owner), else
    2. ob's reviewer_of walk (orchestrated_by/reviews -> spawned_by -> gm) — this
       is CONSUMED from scripts.mission_supervisor.reviewer, NOT re-derived here
       (gm Q5 resolution). Injected as `reviewer_fn` for testability; the default
       tries ob's module and degrades to None if it isn't on main yet, else
    3. a category fallback: fleet-infra (ORCHESTRAOS) -> orchestra-builder,
       everything else -> gm.

Never returns None — gm is the terminal fallback.
"""
from typing import Callable, Optional

from scripts.focus_registry.resolve import focus_of

GM = "agent:gm"
ORCHESTRA_BUILDER = "agent:orchestra-builder"


def _as_entity_id(agent: Optional[str]) -> Optional[str]:
    if not agent:
        return None
    return agent if agent.startswith("agent:") else f"agent:{agent}"


def default_reviewer_fn(agent_id: str) -> Optional[str]:
    """Consume ob's reviewer_of if mission_supervisor is on main; else None.

    We do NOT re-derive the walk — we call ob's ratified primitive. Until ob
    lands the package to main this import fails and we degrade to the category
    fallback (the routing still works, just without the reviewer hop).
    """
    try:
        from scripts.mission_supervisor.reviewer import reviewer_of  # type: ignore
    except Exception:
        return None
    try:
        # ob's signature: reviewer_of(agent_id, sessions, edges, default). We have
        # no sessions/edges wired here yet; pass empties so it falls to its own
        # default. When WS4 wires the real registry edges, pass them through.
        bare = _strip(agent_id)
        return reviewer_of(bare, {}, [], default=None)
    except Exception:
        return None


def _strip(agent_id: str) -> str:
    return agent_id[len("agent:"):] if agent_id.startswith("agent:") else agent_id


def category_fallback(category: Optional[str]) -> str:
    """Fleet-infra (ORCHESTRAOS) -> orchestra-builder; everything else -> gm."""
    if category == "ORCHESTRAOS":
        return ORCHESTRA_BUILDER
    return GM


def route_handoff(
    agent_id: str,
    store: dict,
    reviewer_fn: Callable[[str], Optional[str]] = default_reviewer_fn,
) -> str:
    """Resolve the owner of a finishing agent's backlog. Never None."""
    f = focus_of(agent_id, store)
    if f and f.get("owner"):
        return f["owner"]

    reviewer = _as_entity_id(reviewer_fn(agent_id))
    if reviewer:
        return reviewer

    if f:
        return category_fallback((f.get("attrs") or {}).get("category"))

    # No focus at all -> drift -> gm-first triage (Q4).
    return GM
