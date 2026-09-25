"""RED first — congruence DEC-1790305211739337, gm ruling 03:1xZ.

A real voice call (vc_client_05776e069dad turns 30-34) fired spawn_agent THREE times, 7 s and 41 s
apart, with three differently-worded tasks for ONE session_name. The operator saw three "I'll text
you" acknowledgements and asked why. Only one seat exists, because spawn keys on session_name
downstream — the net that held was luck of naming, not a guard.

ToolDedupLedger was already wired and working. Its rule ("identical (name,args) at most once per
turn") is right; its KEY is too narrow: reworded prose makes three distinct keys.

Both peers COUNTER_PROPOSEd my first design and found more than the reported bug:
  A  check() ran for every tool_call BEFORE any record(), so two IDENTICAL calls in ONE round both
     dispatched. Reserve-at-check is the load-bearing fix; identity keying alone never touched it.
  B  spawn_agent's `machine` defaults to "vps", so one call omitting it and one passing machine="vps"
     are the same seat with different keys. Defaults must be normalised before hashing.
  F  spawn X -> kill_agent X -> spawn X in one turn is TWO legitimate spawns; an opposing lifecycle
     call must invalidate the identity or we replay "running" for a dead seat.
  G  the replayed result must say the later, reworded task was NOT delivered — otherwise Arturo
     narrates three queued tasks when one ran. That is the operator-visible half of the bug.
  H  identity keys belong ONLY to lifecycle tools. Two different messages to one session, or two
     shell commands, legitimately share a target.
"""
from services.arturo import voice_guards as vg


def _turn():
    return vg.ToolDedupLedger()


# --------------------------------------------------------------- A (load-bearing)

def test_within_one_round_an_identical_call_is_reserved_not_dispatched_twice():
    """A. The proxy checks every tool_call in a round BEFORE recording any result, so a same-round
    twin used to slip through. reserve() must claim the key at CHECK time."""
    led = _turn()
    args = {"session_name": "voice-agent", "task": "do the thing"}
    assert led.reserve("spawn_agent", args) is None, "first call must dispatch"
    prior = led.reserve("spawn_agent", args)
    assert prior is not None, "a same-round twin must be suppressed before any result exists"


def test_a_reserved_call_that_has_not_finished_says_so():
    led = _turn()
    args = {"session_name": "gm", "task": "run the fleet"}
    led.reserve("spawn_agent", args)
    prior = led.reserve("spawn_agent", args)
    assert "already" in prior.lower()


# --------------------------------------------------------------- the reported incident

def test_three_reworded_tasks_for_one_session_dispatch_once():
    """The incident. Three different task strings, one session_name, one turn."""
    led = _turn()
    a = {"session_name": "voice-agent", "task": "look into the hierarchy"}
    b = {"session_name": "voice-agent", "task": "check how the hierarchy is configured"}
    c = {"session_name": "voice-agent", "task": "tell me who delegates to whom"}
    assert led.reserve("spawn_agent", a) is None
    led.record("spawn_agent", a, "Seat voice-agent is up.")
    assert led.reserve("spawn_agent", b) is not None, "reworded task, same seat — must not dispatch"
    assert led.reserve("spawn_agent", c) is not None


def test_the_suppression_says_the_later_task_was_not_delivered():
    """G. Without this Arturo cheerfully reports three queued tasks when one ran."""
    led = _turn()
    a = {"session_name": "voice-agent", "task": "first wording"}
    led.reserve("spawn_agent", a)
    led.record("spawn_agent", a, "Seat voice-agent is up.")
    msg = led.reserve("spawn_agent", {"session_name": "voice-agent", "task": "second wording"})
    assert "not" in msg.lower() and ("deliver" in msg.lower() or "sent" in msg.lower()), msg
    assert "Seat voice-agent is up." in msg, "the FIRST result must be returned verbatim"


def test_a_different_session_name_still_dispatches():
    """The control. Over-deduping would be worse than the bug."""
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "seat-one", "task": "x"})
    led.record("spawn_agent", {"session_name": "seat-one", "task": "x"}, "up")
    assert led.reserve("spawn_agent", {"session_name": "seat-two", "task": "x"}) is None


# --------------------------------------------------------------- B (arg defaults)

def test_an_omitted_machine_matches_an_explicit_default():
    """B. spawn_agent reads args.get("machine", "vps"), so these are the SAME seat."""
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "s", "task": "a"})
    led.record("spawn_agent", {"session_name": "s", "task": "a"}, "up")
    assert led.reserve("spawn_agent", {"session_name": "s", "machine": "vps", "task": "b"}) is not None


def test_a_different_machine_is_a_different_seat():
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "s", "machine": "vps", "task": "a"})
    led.record("spawn_agent", {"session_name": "s", "machine": "vps", "task": "a"}, "up")
    assert led.reserve("spawn_agent", {"session_name": "s", "machine": "mac", "task": "a"}) is None


# --------------------------------------------------------------- F (opposing lifecycle)

def test_a_kill_between_two_spawns_lets_the_second_spawn_through():
    """F. spawn X -> kill X -> spawn X is two legitimate spawns, not a duplicate."""
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "worker", "task": "a"})
    led.record("spawn_agent", {"session_name": "worker", "task": "a"}, "up")
    led.reserve("kill_agent", {"session_name": "worker"})
    led.record("kill_agent", {"session_name": "worker"}, "stopped")
    assert led.reserve("spawn_agent", {"session_name": "worker", "task": "b"}) is None, \
        "replaying 'running' for a seat that was just killed is worse than a duplicate spawn"


def test_kill_agent_itself_dedupes_on_identity():
    led = _turn()
    led.reserve("kill_agent", {"session_name": "worker"})
    led.record("kill_agent", {"session_name": "worker"}, "stopped")
    assert led.reserve("kill_agent", {"session_name": "worker"}) is not None


# --------------------------------------------------------------- H (scope)

def test_two_different_messages_to_one_session_both_deliver():
    """H, and gm's explicit control. inject_message must NOT get an identity key."""
    led = _turn()
    one = {"session_name": "gm", "message": "first thing"}
    two = {"session_name": "gm", "message": "second thing"}
    led.reserve("inject_message", one)
    led.record("inject_message", one, "injected")
    assert led.reserve("inject_message", two) is None, \
        "two genuinely different messages to one seat are not duplicates"


def test_two_different_shell_commands_both_run():
    led = _turn()
    led.reserve("run_command", {"command": "ls"})
    led.record("run_command", {"command": "ls"}, "ok")
    assert led.reserve("run_command", {"command": "whoami"}) is None


def test_the_exact_args_rule_still_holds_for_non_lifecycle_tools():
    """The pre-existing behaviour test_tool_dedup.py relies on: identical args still suppress."""
    led = _turn()
    same = {"session_name": "gm", "message": "same thing"}
    led.reserve("inject_message", same)
    led.record("inject_message", same, "injected")
    assert led.reserve("inject_message", same) is not None


def test_read_only_tools_are_never_suppressed():
    led = _turn()
    led.reserve("knowledge", {"query": "tiers"})
    led.record("knowledge", {"query": "tiers"}, "...")
    assert led.reserve("knowledge", {"query": "tiers"}) is None


# --------------------------------------------------------------- the indirect path

def test_async_task_wrapping_spawn_agent_dedupes_on_the_inner_identity():
    """The path the incident actually took: async_task -> spawn_agent."""
    led = _turn()
    a = {"tool_name": "spawn_agent", "tool_args": {"session_name": "voice-agent", "task": "one"},
         "summary": "spawning"}
    b = {"tool_name": "spawn_agent", "tool_args": {"session_name": "voice-agent", "task": "two"},
         "summary": "spawning again"}
    assert led.reserve("async_task", a) is None
    led.record("async_task", a, "Task queued: spawning.")
    assert led.reserve("async_task", b) is not None


def test_a_direct_spawn_and_an_async_wrapped_spawn_are_the_same_seat():
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "voice-agent", "task": "one"})
    led.record("spawn_agent", {"session_name": "voice-agent", "task": "one"}, "up")
    wrapped = {"tool_name": "spawn_agent", "tool_args": {"session_name": "voice-agent", "task": "two"},
               "summary": "s"}
    assert led.reserve("async_task", wrapped) is not None


def test_async_task_wrapping_a_non_lifecycle_tool_keeps_exact_args_semantics():
    led = _turn()
    a = {"tool_name": "run_command", "tool_args": {"command": "ls"}, "summary": "s"}
    b = {"tool_name": "run_command", "tool_args": {"command": "whoami"}, "summary": "s"}
    led.reserve("async_task", a)
    led.record("async_task", a, "queued")
    assert led.reserve("async_task", b) is None


# --------------------------------------------------------------- robustness

def test_malformed_tool_args_do_not_raise():
    """A model can emit anything. This runs inside a live voice turn."""
    led = _turn()
    for bad in ({"tool_name": "spawn_agent", "tool_args": "not-a-dict"},
                {"tool_name": "spawn_agent"},
                {"tool_args": {"session_name": "s"}},
                {}):
        led.reserve("async_task", bad)
        led.record("async_task", bad, "r")


def test_unhashable_and_nested_args_do_not_raise():
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "s", "task": {"nested": [1, 2, {"deep": True}]}})
    led.record("spawn_agent", {"session_name": "s", "task": object()}, "r")


def test_a_missing_identity_field_falls_back_to_exact_args():
    """A spawn_agent call with no session_name cannot be identity-keyed; it must still be
    deduped on exact args rather than silently bypassing the ledger."""
    led = _turn()
    led.reserve("spawn_agent", {"task": "no name given"})
    led.record("spawn_agent", {"task": "no name given"}, "r")
    assert led.reserve("spawn_agent", {"task": "no name given"}) is not None
    assert led.reserve("spawn_agent", {"task": "different"}) is None


# --- Congruence round 2 NITs of DEC-1790369372448894 -------------------------------------------
# Both peers APPROVED Change A; these two are the defects their review turned up in it.

def test_recording_a_replay_cannot_clobber_the_first_result():
    """The loop used to record EVERY entry including suppressed ones, whose "result" is the replay
    message itself. Three reworded calls is the shape of the real incident, and by the fourth the
    original result had been pushed past the 300-char cap — so gm's "a retry returns the first
    result" quietly stopped holding. The ledger now refuses to absorb its own replay, so the
    invariant survives even a caller that hands one back."""
    led = _turn()
    first = "CONFIRMED: seat app-dev-v4 is running on vps (pid 4131)"
    led.reserve("spawn_agent", {"session_name": "app-dev-v4", "task": "build the thing"})
    led.record("spawn_agent", {"session_name": "app-dev-v4", "task": "build the thing"}, first)

    for reworded in ("actually could you also make it fast",
                     "and please have it check the tests too",
                     "one more thing, run the linter"):
        replay = led.reserve("spawn_agent", {"session_name": "app-dev-v4", "task": reworded})
        assert replay is not None and "NOT delivered" in replay
        # simulate the old loop handing the replay straight back to record()
        led.record("spawn_agent", {"session_name": "app-dev-v4", "task": reworded}, replay)

    final = led.reserve("spawn_agent", {"session_name": "app-dev-v4", "task": "and finally, deploy"})
    assert first in final, (
        "the first result must still be the thing replayed after three reworded calls; got: %r"
        % final)


def test_suppressed_call_does_not_overwrite_the_first_result():
    """A retry must return the FIRST result (gm). Recording a suppressed call stored the replay
    message as the new 'first result', so each further reworded call quoted the previous message and
    the real result was pushed past the 300-char cap. Three calls is the shape of the real incident.
    """
    led = _turn()
    first = "CONFIRMED: seat app-dev-v4 is running on vps (pid 4131)"
    assert led.reserve("spawn_agent", {"session_name": "app-dev-v4", "task": "build the thing"}) is None
    led.record("spawn_agent", {"session_name": "app-dev-v4", "task": "build the thing"}, first)

    # Second and third reworded requests for the SAME seat: suppressed, and NOT recorded.
    for reworded in ("actually could you also make it fast",
                     "and please have it check the tests too"):
        msg = led.reserve("spawn_agent", {"session_name": "app-dev-v4", "task": reworded})
        assert msg is not None, "reworded request for the same seat must be suppressed"
        assert "NOT delivered" in msg
        assert first in msg, (
            "the first result must survive verbatim in every replay; got: %r" % msg)


def test_kill_then_respawn_on_a_non_default_machine_is_not_suppressed():
    """kill_agent names no machine, spawn_agent keys on it. Normalising the absent machine to the
    default cleared only the vps key, so spawn-on-mac -> kill -> spawn-on-mac wrongly suppressed a
    legitimate re-spawn. Task prose differs so the EXACT-args rule is not what is under test."""
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "voice-box", "machine": "mac", "task": "a"})
    led.record("spawn_agent", {"session_name": "voice-box", "machine": "mac", "task": "a"},
               "CONFIRMED: voice-box running on mac")
    led.reserve("kill_agent", {"session_name": "voice-box"})
    led.record("kill_agent", {"session_name": "voice-box"}, "killed voice-box")
    assert led.reserve("spawn_agent",
                       {"session_name": "voice-box", "machine": "mac", "task": "b"}) is None, \
        "a spawn after a kill is a second LEGITIMATE spawn on any machine, not a duplicate"


def test_kill_then_respawn_still_dedupes_the_second_respawn():
    """Control for the wildcard invalidation: it must clear the claim, not disable the guard."""
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "voice-box", "machine": "mac", "task": "a"})
    led.record("spawn_agent", {"session_name": "voice-box", "machine": "mac", "task": "a"}, "up")
    led.reserve("kill_agent", {"session_name": "voice-box"})
    led.record("kill_agent", {"session_name": "voice-box"}, "killed")
    led.reserve("spawn_agent", {"session_name": "voice-box", "machine": "mac", "task": "b"})
    led.record("spawn_agent", {"session_name": "voice-box", "machine": "mac", "task": "b"}, "up again")
    assert led.reserve("spawn_agent",
                       {"session_name": "voice-box", "machine": "mac", "task": "c"}) is not None, \
        "the re-spawn must itself be guarded"


def test_invalidation_does_not_clear_a_different_seat():
    """Wildcarding the machine must not wildcard the session_name."""
    led = _turn()
    led.reserve("spawn_agent", {"session_name": "seat-a", "machine": "mac", "task": "a"})
    led.record("spawn_agent", {"session_name": "seat-a", "machine": "mac", "task": "a"}, "seat-a up")
    led.reserve("kill_agent", {"session_name": "seat-b"})
    led.record("kill_agent", {"session_name": "seat-b"}, "killed seat-b")
    assert led.reserve("spawn_agent",
                       {"session_name": "seat-a", "machine": "mac", "task": "b"}) is not None, \
        "killing seat-b must not release the guard on seat-a"
