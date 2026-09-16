"""Voice duplicate-inject (gm commission msg_c74d3ae3 / originally msg_e290c9bd,
the operator watched Arturo inject the same message 3x into v2's live pane).

Root at source: the tool loop `for tool_round in range(MAX_TOOL_ROUNDS)` executes
whatever tool_calls the model returns each round, with NO dedup — so a model that
repeats an identical side-effecting call re-executes it. Log evidence
(logs/arturo-proxy.log): exact-duplicate inject_message args appear 2x for three
distinct messages, two of them into initiative-pm-architect-v2's live pane.

Rule: SIDE-EFFECTING tools execute at most ONCE per turn per identical (name,args).
Read-only tools may repeat freely (re-reading is how a model refines an answer).
"""
from services.arturo import voice_guards as vg


def test_side_effecting_tools_are_classified():
    for t in ("inject_message", "send_telegram", "spawn_agent", "kill_agent",
              "agent_message", "async_task", "run_command", "remember_note"):
        assert vg.is_side_effecting(t), t


def test_read_only_tools_are_not():
    for t in ("knowledge", "get_agent_output", "list_agents", "read_file",
              "read_agent_conversation", "query_roadmap", "read_screen_context"):
        assert not vg.is_side_effecting(t), t


def test_identical_side_effecting_call_is_skipped_second_time():
    led = vg.ToolDedupLedger()
    args = {"session_name": "initiative-pm-architect-v2", "message": "same text"}
    assert led.check("inject_message", args) is None            # first: execute
    led.record("inject_message", args, "Message injected (verified landed).")
    prior = led.check("inject_message", args)                   # second: skip
    assert prior is not None and "already" in prior.lower()
    assert "verified landed" in prior                            # prior result surfaced


def test_argument_order_does_not_defeat_dedup():
    led = vg.ToolDedupLedger()
    a = {"session_name": "gm", "message": "hi"}
    b = {"message": "hi", "session_name": "gm"}                  # same call, keys reordered
    led.record("inject_message", a, "ok")
    assert led.check("inject_message", b) is not None


def test_different_args_still_execute():
    led = vg.ToolDedupLedger()
    led.record("inject_message", {"session_name": "gm", "message": "one"}, "ok")
    assert led.check("inject_message", {"session_name": "gm", "message": "two"}) is None
    assert led.check("inject_message", {"session_name": "v3", "message": "one"}) is None


def test_read_only_tools_never_deduped():
    led = vg.ToolDedupLedger()
    args = {"session_name": "gm"}
    led.record("get_agent_output", args, "pane text")
    assert led.check("get_agent_output", args) is None          # may repeat freely


def test_ledger_is_per_turn_not_global():
    a = {"session_name": "gm", "message": "x"}
    l1 = vg.ToolDedupLedger(); l1.record("inject_message", a, "ok")
    l2 = vg.ToolDedupLedger()
    assert l2.check("inject_message", a) is None                # a new turn may re-send


def test_unhashable_or_odd_args_never_crash():
    led = vg.ToolDedupLedger()
    assert led.check("inject_message", {"x": {"nested": [1, 2]}}) is None
    led.record("inject_message", {"x": {"nested": [1, 2]}}, "ok")
    assert led.check("inject_message", {"x": {"nested": [1, 2]}}) is not None
