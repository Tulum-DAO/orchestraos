"""Which project an agent is in: the project of the first PM up its reports_to chain.

A project is a card in <data>/state/knowledge/projects.json (the only project store), and the card names its
PM by the PM's permanent id ("pm": "pm-demo"). Workers inherit it by walking up reports_to, so nobody tags a
worker, and moving a worker to another PM moves it to that project. A T0 (the manager) is in no project, and
an agent with no PM above it is in none either.

The same rule as the dashboard's (api/src/lib/agentIdentity.ts), kept here for the name rules and the resolver.
"""
import json
import os

from scripts.identity_store.seat_names import base_id

MAX_HOPS = 12


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def project_cards(orchestra_dir) -> dict:
    """{slug: {"slug", "label", "name", "pm"}} from projects.json. `label` is the short label when the card has
    one ("short_name"), else its name, else its slug."""
    d = _read(os.path.join(str(orchestra_dir), "state", "knowledge", "projects.json")) or {}
    out = {}
    for slug, card in d.items() if isinstance(d, dict) else ():
        if not isinstance(card, dict):
            continue
        name = card.get("name") or slug
        out[slug] = {"slug": slug, "name": name, "label": card.get("short_name") or name,
                     "pm": base_id(card["pm"]) if card.get("pm") else None}
    return out


def seat_graph(orchestra_dir) -> dict:
    """{id: {"reports_to", "tier"}} from the registry and session files (lineage roots)."""
    g = {}
    for rel, pick in (("registry.json", lambda j: j.get("agents") or {}),
                      (os.path.join("state", "agent-sessions.json"), lambda j: j)):
        for k, v in (pick(_read(os.path.join(str(orchestra_dir), rel)) or {}) or {}).items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            rec = g.setdefault(base_id(v.get("lineage_root") or k), {})
            if v.get("reports_to") and not rec.get("reports_to"):
                rec["reports_to"] = base_id(v["reports_to"])
            if v.get("tier") and not rec.get("tier"):
                rec["tier"] = v["tier"]
    return g


def project_of(root, orchestra_dir=None, cards=None, graph=None):
    """The project card the agent is in, or None."""
    cards = project_cards(orchestra_dir) if cards is None else cards
    graph = seat_graph(orchestra_dir) if graph is None else graph
    by_pm = {c["pm"]: c for c in cards.values() if c.get("pm")}
    cur, seen = base_id(root), set()
    for _ in range(MAX_HOPS):
        if not cur or cur in seen:
            return None
        seen.add(cur)
        if (graph.get(cur) or {}).get("tier") == "T0":
            return None
        if cur in by_pm:
            return by_pm[cur]
        cur = (graph.get(cur) or {}).get("reports_to")
    return None


def full_name(name, root, project):
    """"Demo · dev" for a renamed agent in a project; its own name (an id says enough) otherwise."""
    if project and name and name != root:
        return f"{project['label']} · {name}"
    return name or root
