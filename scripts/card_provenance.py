"""card_provenance.py — who filed a card: the source agent and its provider, read at creation.

Every card the operator sees (approval_requests rows of every kind, questionnaires, and the
permission-prompt push state) is stamped, at the moment it is created and before it is stored,
with the identity of the agent it came from:

    seat_id              the agent's stable seat (registry id), resolved from the card's from_agent,
                         which may be the agent id or its tmux session (bridged menus)
    provider             the agent's runtime (claude / codex / gemini / ...), from the registry
    provider_session_id  the runtime session the agent is on now (its current conversation)
    generation           the agent's generation number at that moment

All four come from the registry, never from the card's author: an agent cannot claim another
seat's identity by what it writes. A value the caller passes (an adapter that captured it from the
runtime, a red alert naming the seat it is about) always wins; this only fills what was left empty.
A source that is not a registered agent (lineage-daemon, red-alert-builder, a backfilled file)
gets seat_id = its own name and no provider, the same rule as approval_adapters._registry_identity.

Best-effort and read-only: a missing or unreadable registry yields what can be known (seat_id) and
never stops a card from being created.
"""
import json
import os
from pathlib import Path

IDENTITY_FIELDS = ("seat_id", "provider", "provider_session_id", "generation")
_RETIRED = {"retired", "dead", "archived", "stopped"}


def _registry_path():
    try:
        import approval_config
        return approval_config.orchestra_dir() / "registry.json"
    except Exception:  # noqa: BLE001 — fall back to the env, then nothing
        d = os.environ.get("ORCHESTRA_DIR")
        return Path(d) / "registry.json" if d else None


def _load_agents(registry_path=None):
    path = registry_path or _registry_path()
    if not path:
        return {}
    try:
        with open(path) as f:
            agents = (json.load(f) or {}).get("agents") or {}
        return agents if isinstance(agents, dict) else {}
    except (OSError, ValueError):
        return {}


def _find(agents, from_agent):
    """(agent_id, meta) for a card's from_agent: the registry id itself, else the agent whose tmux
    session it is, preferring a live record over a retired one that still names the same session
    (a rotation can leave the predecessor pointing at the reused session name)."""
    meta = agents.get(from_agent)
    if isinstance(meta, dict):
        return from_agent, meta
    found = None
    for name, m in agents.items():
        if not isinstance(m, dict) or (m.get("tmux_session") or name) != from_agent:
            continue
        retired = str(m.get("status") or "").lower() in _RETIRED
        if found is None or (not retired and found[2]):
            found = (name, m, retired)
    return (found[0], found[1]) if found else (None, None)


def source_identity(from_agent, registry_path=None, agents=None):
    """{seat_id, provider, provider_session_id, generation} for the agent filing a card."""
    src = str(from_agent or "").strip()
    out = {k: None for k in IDENTITY_FIELDS}
    if not src:
        return out
    agents = _load_agents(registry_path) if agents is None else agents
    name, meta = _find(agents, src)
    out["seat_id"] = name or src
    if meta:
        runtime = meta.get("runtime") or meta.get("provider")
        out["provider"] = str(runtime).strip().lower() if runtime else None
        sid = meta.get("session_id")
        out["provider_session_id"] = str(sid) if sid else None
        gen = meta.get("generation")
        try:
            out["generation"] = int(gen) if gen is not None else None
        except (TypeError, ValueError):
            out["generation"] = None
    return out


def fill(from_agent, given, registry_path=None, agents=None):
    """`given` (field -> value the caller passed, None = not passed) with the empty ones filled from
    the registry. Never overwrites a value the caller gave. Never raises."""
    given = dict(given or {})
    if all(given.get(k) is not None for k in IDENTITY_FIELDS):
        return given
    try:
        ident = source_identity(from_agent, registry_path=registry_path, agents=agents)
    except Exception:  # noqa: BLE001 — provenance must never block a card
        return given
    for k in IDENTITY_FIELDS:
        if given.get(k) is None:
            given[k] = ident.get(k)
    return given
