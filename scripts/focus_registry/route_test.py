"""Tests for PM-per-focus handoff routing (WS2, Decisions 1+2).

route_handoff resolves a finishing agent -> the entity that should own its
backlog: focus owner-PM first, then ob's reviewer_of walk (CONSUMED, not
re-derived), then a category fallback. It NEVER returns None (gm is terminal).
"""
from scripts.focus_registry.route import route_handoff, category_fallback, default_reviewer_fn

_STORE = {
    "entities": {
        "focus:adaptiv-payments": {
            "id": "focus:adaptiv-payments", "type": "focus", "canonical": "Adaptiv Payments",
            "owner": "agent:pm-adaptiv-payments-6", "status": "active",
            "attrs": {"category": "CLIENT", "agents": ["agent:pm-adaptiv-payments-6", "agent:adaptiv-industries-chart"]},
        },
        "focus:ws1-lineage-daemon": {
            "id": "focus:ws1-lineage-daemon", "type": "focus", "canonical": "WS1 lineage daemon",
            "owner": None, "status": "active",
            "attrs": {"category": "ORCHESTRAOS", "agents": ["agent:some-daemon-dev"]},
        },
        "focus:b2b-copy": {
            "id": "focus:b2b-copy", "type": "focus", "canonical": "B2B copy",
            "owner": None, "status": "active",
            "attrs": {"category": "BUSINESS", "agents": ["agent:b2b-writer"]},
        },
    },
    "edges": [
        {"src": "agent:pm-adaptiv-payments-6", "rel": "works_on", "dst": "focus:adaptiv-payments"},
        {"src": "agent:adaptiv-industries-chart", "rel": "works_on", "dst": "focus:adaptiv-payments"},
        {"src": "agent:some-daemon-dev", "rel": "works_on", "dst": "focus:ws1-lineage-daemon"},
        {"src": "agent:b2b-writer", "rel": "works_on", "dst": "focus:b2b-copy"},
    ],
}

_NO_REVIEWER = lambda agent: None


def test_owned_focus_routes_to_owner_pm():
    assert route_handoff("agent:adaptiv-industries-chart", _STORE, _NO_REVIEWER) == "agent:pm-adaptiv-payments-6"


def test_ownerless_focus_uses_reviewer_walk_when_available():
    reviewer = lambda agent: "gm" if agent == "agent:some-daemon-dev" else None
    # reviewer returns a real reviewer -> routes there (normalized to entity id).
    assert route_handoff("agent:some-daemon-dev", _STORE, reviewer) == "agent:gm"


def test_ownerless_orchestraos_focus_falls_back_to_orchestra_builder():
    assert route_handoff("agent:some-daemon-dev", _STORE, _NO_REVIEWER) == "agent:orchestra-builder"


def test_ownerless_business_focus_falls_back_to_gm():
    assert route_handoff("agent:b2b-writer", _STORE, _NO_REVIEWER) == "agent:gm"


def test_drift_agent_with_no_reviewer_routes_to_gm():
    # No focus at all (Q4: drift -> gm-first triage).
    assert route_handoff("agent:ghost", _STORE, _NO_REVIEWER) == "agent:gm"


def test_drift_agent_uses_reviewer_when_present():
    reviewer = lambda agent: "orchestra-builder"
    assert route_handoff("agent:ghost", _STORE, reviewer) == "agent:orchestra-builder"


def test_route_never_returns_none():
    assert route_handoff("agent:ghost", _STORE, _NO_REVIEWER) is not None


def test_category_fallback_mapping():
    assert category_fallback("ORCHESTRAOS") == "agent:orchestra-builder"
    assert category_fallback("ACME-APP") == "agent:gm"
    assert category_fallback("CLIENT") == "agent:gm"
    assert category_fallback(None) == "agent:gm"


def test_default_reviewer_fn_is_safe_when_mission_supervisor_absent():
    # Until ob lands mission_supervisor on main, the default reviewer degrades to
    # None (never raises) so routing falls through to the category fallback.
    result = default_reviewer_fn("agent:anything")
    assert result is None or isinstance(result, str)
