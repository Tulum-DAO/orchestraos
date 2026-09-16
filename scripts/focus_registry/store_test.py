"""Tests for the thin focus-entity store (WS1, Q1 resolution).

The store is keyed by entity id and shaped to match the WS1 entity-registry
`entities`/`edges` tables so it migrates into registry.db later with no schema
divergence.
"""
import json

from scripts.focus_registry.parse import Focus
from scripts.focus_registry.store import (
    to_entity,
    focus_id,
    import_focuses,
    load_store,
)

_F = Focus(
    canonical="Custom-intent audiences",
    category="ACME-APP",
    pct_done=80,
    relevance="H",
    importance="H",
    owner="agent:opus-11",
    agents=["agent:opus-11", "agent:merge-9"],
    notes="Live feature.",
)


def test_focus_id_is_slugged_and_namespaced():
    assert focus_id("OpSystem / billing hub") == "focus:opsystem-billing-hub"
    assert focus_id("Custom-intent audiences") == "focus:custom-intent-audiences"


def test_to_entity_matches_registry_schema():
    e = to_entity(_F)
    assert e["id"] == "focus:custom-intent-audiences"
    assert e["type"] == "focus"
    assert e["canonical"] == "Custom-intent audiences"
    assert e["owner"] == "agent:opus-11"
    assert e["status"] == "active"
    assert e["attrs"]["pct_done"] == 80
    assert e["attrs"]["relevance"] == "H"
    assert e["attrs"]["importance"] == "H"
    assert e["attrs"]["category"] == "ACME-APP"


def test_import_writes_store_keyed_by_id(tmp_path):
    p = tmp_path / "focus-registry.json"
    import_focuses([_F], str(p))
    store = load_store(str(p))
    assert "focus:custom-intent-audiences" in store["entities"]
    assert store["entities"]["focus:custom-intent-audiences"]["attrs"]["pct_done"] == 80


def test_import_builds_owned_by_and_works_on_edges(tmp_path):
    p = tmp_path / "focus-registry.json"
    import_focuses([_F], str(p))
    edges = load_store(str(p))["edges"]
    assert {"src": "focus:custom-intent-audiences", "rel": "owned_by", "dst": "agent:opus-11"} in edges
    assert {"src": "agent:opus-11", "rel": "works_on", "dst": "focus:custom-intent-audiences"} in edges
    assert {"src": "agent:merge-9", "rel": "works_on", "dst": "focus:custom-intent-audiences"} in edges


def test_import_is_idempotent(tmp_path):
    p = tmp_path / "focus-registry.json"
    import_focuses([_F], str(p))
    first = json.dumps(load_store(str(p)), sort_keys=True)
    import_focuses([_F], str(p))
    second = json.dumps(load_store(str(p)), sort_keys=True)
    assert first == second


def test_import_upserts_changed_scores_in_place(tmp_path):
    p = tmp_path / "focus-registry.json"
    import_focuses([_F], str(p))
    updated = Focus(**{**_F.__dict__, "pct_done": 95})
    import_focuses([updated], str(p))
    store = load_store(str(p))
    ents = store["entities"]
    assert len(ents) == 1  # same id, updated not duplicated
    assert ents["focus:custom-intent-audiences"]["attrs"]["pct_done"] == 95


def test_owner_none_writes_no_owned_by_edge(tmp_path):
    p = tmp_path / "focus-registry.json"
    ownerless = Focus(**{**_F.__dict__, "owner": None})
    import_focuses([ownerless], str(p))
    edges = load_store(str(p))["edges"]
    assert not any(e["rel"] == "owned_by" for e in edges)
    assert load_store(str(p))["entities"]["focus:custom-intent-audiences"]["owner"] is None
