"""Tests for focus resolution + drift detection (WS1, Decision 6).

Pure functions over a store dict (no IO) — the drift signal is the loop's
"moving in the right direction" check.
"""
from scripts.focus_registry.resolve import focus_of, focuses_of, is_drift

_STORE = {
    "entities": {
        "focus:custom-intent-audiences": {
            "id": "focus:custom-intent-audiences",
            "type": "focus",
            "canonical": "Custom-intent audiences",
            "owner": "agent:opus-11",
            "status": "active",
            "attrs": {"agents": ["agent:opus-11", "agent:merge-9"]},
        },
        "focus:datamoon-integration": {
            "id": "focus:datamoon-integration",
            "type": "focus",
            "canonical": "DataMoon integration",
            "owner": "agent:datamoon-api",
            "status": "active",
            "attrs": {"agents": ["agent:datamoon-api", "agent:merge-9"]},
        },
        "focus:court-bug-research": {
            "id": "focus:court-bug-research",
            "type": "focus",
            "canonical": "court bug research",
            "owner": None,
            "status": "retired",
            "attrs": {"agents": ["agent:court-issue-research"]},
        },
    },
    "edges": [
        {"src": "agent:opus-11", "rel": "works_on", "dst": "focus:custom-intent-audiences"},
        {"src": "agent:merge-9", "rel": "works_on", "dst": "focus:custom-intent-audiences"},
        {"src": "agent:merge-9", "rel": "works_on", "dst": "focus:datamoon-integration"},
        {"src": "agent:datamoon-api", "rel": "works_on", "dst": "focus:datamoon-integration"},
        {"src": "agent:court-issue-research", "rel": "works_on", "dst": "focus:court-bug-research"},
    ],
}


def test_focus_of_resolves_the_agents_focus():
    f = focus_of("agent:opus-11", _STORE)
    assert f["id"] == "focus:custom-intent-audiences"


def test_focus_of_unknown_agent_is_none():
    assert focus_of("agent:ghost", _STORE) is None


def test_focuses_of_returns_all_memberships():
    ids = {f["id"] for f in focuses_of("agent:merge-9", _STORE)}
    assert ids == {"focus:custom-intent-audiences", "focus:datamoon-integration"}


def test_agent_on_active_focus_is_not_drift():
    assert is_drift("agent:opus-11", _STORE) is False


def test_agent_on_no_focus_is_drift():
    assert is_drift("agent:ghost", _STORE) is True


def test_agent_only_on_retired_focus_is_drift():
    # court-issue-research's only focus is status=retired -> not moving on an
    # active north-star -> drift (flag to gm).
    assert is_drift("agent:court-issue-research", _STORE) is True


def test_focus_of_prefers_an_active_focus_over_retired():
    # If an agent is on both, focus_of returns an active one.
    store = {
        "entities": {
            **_STORE["entities"],
            "focus:datamoon-integration": {
                **_STORE["entities"]["focus:datamoon-integration"],
                "attrs": {"agents": ["agent:court-issue-research"]},
            },
        },
        "edges": _STORE["edges"] + [
            {"src": "agent:court-issue-research", "rel": "works_on", "dst": "focus:datamoon-integration"},
        ],
    }
    f = focus_of("agent:court-issue-research", store)
    assert f["status"] == "active"
