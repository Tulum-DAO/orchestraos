from lineage_daemon.handoff_schema import (
    Handoff, PhaseState, make_callback, mark_callback_sent, is_callback,
    CB_PENDING, CB_SENT,
)


def _valid_handoff():
    return Handoff(
        current_goal="Ship lineage daemon phase 1",
        phase_state=PhaseState(
            plan_ref="docs/plan.md",
            phase_n=2,
            phase_m=3,
            gates_passed=["gate-a"],
            gates_total=3,
            current_step="implement death.py",
            next_gate="gate-b",
        ),
        working_state="tests green for modules 1-2",
        open_loops=["wire orchestrator later"],
        decisions=[{"text": "pure units only", "rationale": "no side effects in phase 1"}],
        file_roots_touched=["scripts/lineage_daemon/"],
        next_3_actions=["write handoff test", "impl schema", "commit"],
    )


# --- JSON round-trip ---

def test_json_round_trip_equal():
    h = _valid_handoff()
    rebuilt = Handoff.from_json(h.to_json())
    assert rebuilt == h

def test_json_round_trip_nested_phase_state():
    h = _valid_handoff()
    rebuilt = Handoff.from_json(h.to_json())
    assert isinstance(rebuilt.phase_state, PhaseState)
    assert rebuilt.phase_state == h.phase_state
    assert rebuilt.phase_state.phase_n == 2
    assert rebuilt.phase_state.phase_m == 3

def test_dict_round_trip_equal():
    h = _valid_handoff()
    assert Handoff.from_dict(h.to_dict()) == h

def test_to_dict_phase_state_is_nested_dict():
    d = _valid_handoff().to_dict()
    assert isinstance(d["phase_state"], dict)
    assert d["phase_state"]["phase_n"] == 2


# --- validate ---

def test_valid_handoff_returns_empty():
    assert _valid_handoff().validate() == []

def test_validate_empty_current_goal():
    h = _valid_handoff()
    h.current_goal = ""
    errs = h.validate()
    assert any("current_goal" in e for e in errs)

def test_validate_phase_n_out_of_range_high():
    h = _valid_handoff()
    h.phase_state.phase_n = 5
    h.phase_state.phase_m = 3
    errs = h.validate()
    assert any("phase_n" in e for e in errs)

def test_validate_phase_n_zero_when_m_positive():
    h = _valid_handoff()
    h.phase_state.phase_n = 0
    h.phase_state.phase_m = 3
    errs = h.validate()
    assert any("phase_n" in e for e in errs)

def test_validate_phase_m_zero_ok():
    h = _valid_handoff()
    h.phase_state.phase_n = 0
    h.phase_state.phase_m = 0
    assert h.validate() == []

def test_validate_empty_next_3_actions():
    h = _valid_handoff()
    h.next_3_actions = []
    errs = h.validate()
    assert any("next_3_actions" in e for e in errs)

def test_validate_decision_missing_text():
    h = _valid_handoff()
    h.decisions = [{"text": "", "rationale": "why"}]
    errs = h.validate()
    assert any("decision" in e.lower() for e in errs)

def test_validate_accumulates_multiple_errors():
    h = Handoff()  # empty goal, empty next_3_actions
    errs = h.validate()
    assert len(errs) >= 2


# --- render_md ---

def test_render_md_contains_goal():
    h = _valid_handoff()
    md = h.render_md()
    assert "Ship lineage daemon phase 1" in md

def test_render_md_contains_phase_n_of_m():
    md = _valid_handoff().render_md()
    assert "phase 2 of 3" in md.lower()

def test_render_md_contains_next_gate():
    md = _valid_handoff().render_md()
    assert "gate-b" in md

def test_render_md_has_headings():
    md = _valid_handoff().render_md()
    for token in ["current_goal", "working_state", "decisions", "next_3_actions"]:
        assert token in md

def test_render_md_coerces_int_gates_passed():
    h = _valid_handoff()
    h.phase_state.gates_passed = [1, 2, 3]
    md = h.render_md()  # must not raise on int gates
    assert "1, 2, 3" in md


# --- R1 responder-side: inherited outbound callbacks ---

def _handoff_with_callback():
    """A handoff where B owes A a completion reply (request req-42)."""
    h = _valid_handoff()
    h.open_loops = [
        "wire orchestrator later",  # plain-string loop stays a plain string
        make_callback(to="agent-A", what_for="cohort count for TTSP",
                      request_id="req-42"),
    ]
    return h


def test_make_callback_defaults_pending():
    cb = make_callback("agent-A", "why", "req-1")
    assert is_callback(cb)
    assert cb["state"] == CB_PENDING
    assert cb["to"] == "agent-A" and cb["request_id"] == "req-1"


def test_callback_survives_json_round_trip():
    h = _handoff_with_callback()
    rebuilt = Handoff.from_json(h.to_json())
    assert rebuilt == h
    assert len(rebuilt.pending_callbacks()) == 1
    assert rebuilt.pending_callbacks()[0]["request_id"] == "req-42"


def test_pending_callbacks_excludes_plain_loops_and_sent():
    h = _handoff_with_callback()
    # add an already-sent callback — must not be reported pending
    h.open_loops.append(
        make_callback("agent-C", "done thing", "req-99", state=CB_SENT))
    pend = h.pending_callbacks()
    assert [c["request_id"] for c in pend] == ["req-42"]


def test_mark_callback_sent_is_idempotent():
    h = _handoff_with_callback()
    once = mark_callback_sent(h.open_loops, "req-42")
    twice = mark_callback_sent(once, "req-42")
    sent = [l for l in twice if is_callback(l) and l["request_id"] == "req-42"]
    assert sent and sent[0]["state"] == CB_SENT
    assert once == twice  # second mark is a no-op
    # plain-string loop untouched
    assert "wire orchestrator later" in twice


def test_mark_callback_sent_unknown_id_noop():
    h = _handoff_with_callback()
    out = mark_callback_sent(h.open_loops, "nope")
    assert out == h.open_loops


def test_inherit_and_fire_once():
    """The core R1 path: successor B' inherits B's pending callback, fires it
    exactly once, and it is never double-emitted on a re-run."""
    h = _handoff_with_callback()
    sent = []
    h.fire_inherited_callbacks(send=lambda to, cb: sent.append((to, cb["request_id"])))
    assert sent == [("agent-A", "req-42")]
    # callback is now marked sent
    assert h.pending_callbacks() == []
    # re-run (retry / a second successor) fires NOTHING — idempotent
    h.fire_inherited_callbacks(send=lambda to, cb: sent.append((to, cb["request_id"])))
    assert sent == [("agent-A", "req-42")]


def test_fire_uses_resolver_for_live_head():
    """fire_inherited_callbacks routes through the R2 resolver so the reply
    lands in A's LIVE HEAD (successor A'), not dead A."""
    h = _handoff_with_callback()
    got = []
    h.fire_inherited_callbacks(
        send=lambda to, cb: got.append(to),
        resolve=lambda to: "agent-A-g3" if to == "agent-A" else to,
    )
    assert got == ["agent-A-g3"]


def test_callback_validation_requires_to_and_request_id():
    h = _valid_handoff()
    h.open_loops = [{"kind": "callback", "state": CB_PENDING}]  # missing to+req
    errs = h.validate()
    assert any("'to'" in e for e in errs)
    assert any("request_id" in e for e in errs)


def test_render_md_renders_callback_readably():
    md = _handoff_with_callback().render_md()
    assert "[callback:pending]" in md
    assert "reply to agent-A" in md
    assert "req-42" in md
