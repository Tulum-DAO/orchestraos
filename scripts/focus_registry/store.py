"""Thin focus-entity store (WS1, Q1 resolution).

Keyed by entity id and shaped to match the WS1 entity-registry `entities`/`edges`
tables (docs/superpowers/specs/2026-08-12-orchestra-registry-and-lineage-daemon.md
§4.1) so it migrates into registry.db later with no schema divergence. Until
registry.db is materialised (align with orchestra-builder) this is a JSON file.

Store shape:
    {
      "version": 1,
      "source": "docs/FLEET_AUDIT_2026-08-14.md",
      "updated_at": "<iso>",
      "entities": { "<id>": {entity dict}, ... },
      "edges": [ {"src","rel","dst"}, ... ]
    }
"""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import List

from scripts.focus_registry.parse import Focus

DEFAULT_STORE = os.path.join(
    os.environ.get("ORCHESTRA_DIR", os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "state",
    "focus-registry.json",
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def focus_id(canonical: str) -> str:
    slug = _SLUG_RE.sub("-", canonical.lower()).strip("-")
    return f"focus:{slug}"


def to_entity(f: Focus) -> dict:
    """Project a Focus into the WS1 `entities`-table shape."""
    return {
        "id": focus_id(f.canonical),
        "type": "focus",
        "canonical": f.canonical,
        "owner": f.owner,
        "status": "active",
        "source": "fleet-audit",
        "attrs": {
            "pct_done": f.pct_done,
            "relevance": f.relevance,
            "importance": f.importance,
            "category": f.category,
            "agents": list(f.agents),
            "notes": f.notes,
        },
    }


def _edges_for(f: Focus) -> List[dict]:
    fid = focus_id(f.canonical)
    edges: List[dict] = []
    if f.owner:
        edges.append({"src": fid, "rel": "owned_by", "dst": f.owner})
    for agent in f.agents:
        edges.append({"src": agent, "rel": "works_on", "dst": fid})
    return edges


def load_store(path: str = DEFAULT_STORE) -> dict:
    if not os.path.exists(path):
        return {"version": 1, "source": None, "updated_at": None, "entities": {}, "edges": []}
    with open(path) as fh:
        return json.load(fh)


def save_store(store: dict, path: str = DEFAULT_STORE) -> None:
    """Public atomic write of a full store dict (used by the WS3 hook bodies)."""
    _atomic_write(path, store)


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def import_focuses(focuses: List[Focus], path: str = DEFAULT_STORE, source: str = "fleet-audit") -> dict:
    """Idempotent upsert of focus entities + their edges into the store.

    Re-running with the same focuses yields an identical store (no dupes). A
    changed score upserts in place (same id). Focus edges are rebuilt for the
    imported focuses (an owned_by/works_on edge that no longer applies is dropped).
    """
    store = load_store(path)
    original_entities = store.get("entities", {})
    original_edges = store.get("edges", [])
    entities = dict(original_entities)  # snapshot so the no-op guard can compare
    # Rebuild edges only for the focus ids we are importing; keep unrelated edges.
    imported_focus_ids = {focus_id(f.canonical) for f in focuses}

    def _touches_imported(edge: dict) -> bool:
        return edge.get("src") in imported_focus_ids or edge.get("dst") in imported_focus_ids

    edges = [e for e in original_edges if not _touches_imported(e)]

    for f in focuses:
        entities[focus_id(f.canonical)] = to_entity(f)
        edges.extend(_edges_for(f))

    # Deterministic edge ordering so idempotent re-runs are byte-stable.
    edges = _dedupe_edges(edges)

    # No-op guard: if the meaningful content is unchanged, do NOT rewrite (no
    # spurious updated_at bump / git churn on every supervisor beat).
    if entities == original_entities and edges == original_edges:
        return store

    store.update(
        {
            "version": 1,
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entities": entities,
            "edges": edges,
        }
    )
    _atomic_write(path, store)
    return store


def _dedupe_edges(edges: List[dict]) -> List[dict]:
    seen = set()
    out = []
    for e in sorted(edges, key=lambda x: (x["src"], x["rel"], x["dst"])):
        key = (e["src"], e["rel"], e["dst"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out
