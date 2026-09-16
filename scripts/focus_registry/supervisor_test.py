"""Tests for the WS4 supervisor focus-backlog beat (DRY-RUN half only).

The beat PROPOSES feeding a focus's idle owner-PM the next-ranked backlog item.
DRY-RUN = returns proposals; drives NO live agent (pure, no IO). Live feeding is
a separate the operator-gated build (post gm+agy congruence + trust window).
"""
from scripts.focus_registry.supervisor import score_backlog, next_item, propose_feeds


def _item(id, importance="M", relevance="M", completion=None, blocked=False):
    return {"id": id, "importance": importance, "relevance": relevance,
            "completion": completion, "blocked": blocked}


def test_score_backlog_ranks_importance_then_relevance():
    items = [_item("low", "L", "L"), _item("hi", "H", "H"), _item("mid", "M", "H")]
    ranked = [i["id"] for i in score_backlog({}, items)]
    assert ranked == ["hi", "mid", "low"]


def test_next_item_returns_top_incomplete_unblocked():
    items = [_item("a", "H"), _item("b", "L")]
    assert next_item({}, items)["id"] == "a"


def test_next_item_skips_complete_and_advances():
    # top item COMPLETE -> advance past it to the next actionable one.
    items = [_item("done", "H", completion="COMPLETE"), _item("next", "M")]
    assert next_item({}, items)["id"] == "next"


def test_next_item_returns_none_when_top_is_unknown():
    # UNKNOWN completion -> can't confirm the in-flight item is done -> never advance.
    items = [_item("murky", "H", completion="UNKNOWN"), _item("other", "M")]
    assert next_item({}, items) is None


def test_next_item_none_when_all_complete():
    items = [_item("a", completion="COMPLETE"), _item("b", completion="COMPLETE")]
    assert next_item({}, items) is None


def test_next_item_skips_blocked():
    items = [_item("blocked", "H", blocked=True), _item("free", "M")]
    assert next_item({}, items)["id"] == "free"


# --- the beat ---

_STORE = {
    "entities": {
        "focus:adaptiv-payments": {
            "id": "focus:adaptiv-payments", "type": "focus", "status": "active",
            "owner": "agent:pm-adaptiv-payments-6", "attrs": {"category": "CLIENT"},
        },
        "focus:orphan": {
            "id": "focus:orphan", "type": "focus", "status": "active",
            "owner": None, "attrs": {"category": "BUSINESS"},
        },
        "focus:retired-thing": {
            "id": "focus:retired-thing", "type": "focus", "status": "retired",
            "owner": "agent:someone", "attrs": {"category": "BUSINESS"},
        },
    },
    "edges": [],
}

_ITEMS = {
    "focus:adaptiv-payments": [_item("meta-activation", "H")],
    "focus:orphan": [_item("x", "H")],
    "focus:retired-thing": [_item("y", "H")],
}


def test_idle_owned_focus_yields_one_dry_run_proposal():
    props = propose_feeds(_STORE, _ITEMS, idle_owner_ids={"agent:pm-adaptiv-payments-6"})
    assert len(props) == 1
    p = props[0]
    assert p["focus"] == "focus:adaptiv-payments"
    assert p["owner"] == "agent:pm-adaptiv-payments-6"
    assert p["item"]["id"] == "meta-activation"
    assert p["live"] is False  # DRY-RUN — drives nothing


def test_busy_owner_yields_no_proposal():
    # owner NOT in the idle set (in-flight) -> no feed.
    props = propose_feeds(_STORE, _ITEMS, idle_owner_ids=set())
    assert props == []


def test_ownerless_focus_is_not_auto_fed():
    props = propose_feeds(_STORE, _ITEMS, idle_owner_ids={"agent:pm-adaptiv-payments-6"})
    assert all(p["focus"] != "focus:orphan" for p in props)


def test_retired_focus_is_skipped():
    props = propose_feeds(_STORE, _ITEMS, idle_owner_ids={"agent:someone"})
    assert all(p["focus"] != "focus:retired-thing" for p in props)


def test_verified_complete_current_item_advances_to_next():
    items = {"focus:adaptiv-payments": [
        _item("done", "H", completion="COMPLETE"), _item("meta-activation", "M")]}
    props = propose_feeds(_STORE, items, idle_owner_ids={"agent:pm-adaptiv-payments-6"})
    assert len(props) == 1 and props[0]["item"]["id"] == "meta-activation"


def test_unknown_current_item_never_advances():
    items = {"focus:adaptiv-payments": [
        _item("murky", "H", completion="UNKNOWN"), _item("meta-activation", "M")]}
    props = propose_feeds(_STORE, items, idle_owner_ids={"agent:pm-adaptiv-payments-6"})
    assert props == []


# --- idle-owner derivation from read-only agent-status (pure) ---
from scripts.focus_registry.supervisor import idle_owners_from_status

_STATUS = [
    {"session": "pm-adaptiv-payments-6", "state": "idle"},
    {"session": "opus-11", "state": "working"},
    {"session": "acme-merge-9", "state": "idle"},   # prefixed session name
    {"session": "datamoon-api", "state": "stranded_input"},
]


def test_idle_owner_matches_exact_session_name():
    idle = idle_owners_from_status(_STATUS, {"agent:pm-adaptiv-payments-6", "agent:opus-11"})
    assert idle == {"agent:pm-adaptiv-payments-6"}  # opus-11 is working


def test_idle_owner_matches_suffixed_session_name():
    # session 'acme-merge-9' should satisfy owner 'agent:merge-9' via suffix.
    idle = idle_owners_from_status(_STATUS, {"agent:merge-9"})
    assert idle == {"agent:merge-9"}


def test_non_idle_states_are_not_idle():
    # stranded_input / working / unknown are NOT idle (conservative: don't feed
    # an owner we can't confirm is free).
    idle = idle_owners_from_status(_STATUS, {"agent:datamoon-api", "agent:opus-11"})
    assert idle == set()


# --- WS4 event-stream wire: idle owners from the hook-event bus (effects, not pane
# inference). Last event per agent == turn_ended/session_end -> idle. ---
from scripts.focus_registry.supervisor import idle_owners_from_events


def _evt(agent, type_, ts):
    return {"agent": agent, "type": type_, "ts": ts}


def test_idle_owner_when_last_event_is_turn_ended():
    events = [_evt("agent:pm-adaptiv-payments-6", "prompt_submit", 1.0),
              _evt("agent:pm-adaptiv-payments-6", "turn_ended", 2.0)]
    idle = idle_owners_from_events(events, {"agent:pm-adaptiv-payments-6"})
    assert idle == {"agent:pm-adaptiv-payments-6"}


def test_not_idle_when_last_event_is_prompt_submit():
    events = [_evt("agent:opus-11", "turn_ended", 1.0),
              _evt("agent:opus-11", "prompt_submit", 2.0)]  # started a new turn
    assert idle_owners_from_events(events, {"agent:opus-11"}) == set()


def test_session_end_counts_as_idle():
    events = [_evt("agent:x", "session_end", 5.0)]
    assert idle_owners_from_events(events, {"agent:x"}) == {"agent:x"}


def test_latest_event_by_ts_decides_regardless_of_order():
    # out-of-order stream: the highest-ts event is authoritative.
    events = [_evt("agent:x", "turn_ended", 9.0),
              _evt("agent:x", "tool_use", 3.0)]
    assert idle_owners_from_events(events, {"agent:x"}) == {"agent:x"}


def test_owner_with_no_events_is_not_idle():
    # no evidence of idleness -> conservative, don't feed.
    assert idle_owners_from_events([], {"agent:x"}) == set()


def test_only_considers_requested_owner_ids():
    events = [_evt("agent:other", "turn_ended", 1.0)]
    assert idle_owners_from_events(events, {"agent:x"}) == set()


# --- dry_run_from_events: the WS4 beat consuming the bus stream (staging, no live feed) ---
from scripts.focus_registry.supervisor import dry_run_from_events


def test_dry_run_from_events_proposes_for_idle_owned_focus():
    store = {
        "entities": {
            "focus:adaptiv-payments": {
                "id": "focus:adaptiv-payments", "type": "focus", "status": "active",
                "owner": "agent:pm-adaptiv-payments-6", "attrs": {"category": "CLIENT"},
            },
        }, "edges": [],
    }
    events = [_evt("agent:pm-adaptiv-payments-6", "turn_ended", 5.0)]
    items = {"focus:adaptiv-payments": [_item("meta-activation", "H")]}
    report = dry_run_from_events(store, events, items)
    assert report["drives_live_agent"] is False
    assert report["source"] == "event-stream"
    assert len(report["proposals"]) == 1
    assert report["proposals"][0]["owner"] == "agent:pm-adaptiv-payments-6"


def test_dry_run_from_events_no_proposal_when_owner_busy():
    store = {
        "entities": {
            "focus:adaptiv-payments": {
                "id": "focus:adaptiv-payments", "type": "focus", "status": "active",
                "owner": "agent:pm-adaptiv-payments-6", "attrs": {"category": "CLIENT"},
            },
        }, "edges": [],
    }
    events = [_evt("agent:pm-adaptiv-payments-6", "prompt_submit", 5.0)]  # busy
    items = {"focus:adaptiv-payments": [_item("meta-activation", "H")]}
    assert dry_run_from_events(store, events, items)["proposals"] == []
