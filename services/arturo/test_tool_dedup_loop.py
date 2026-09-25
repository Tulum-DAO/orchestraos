"""The SEAM test the unit tests cannot reach: the proxy's tool loop itself.

test_tool_dedup_identity.py proves the ledger's rules. This proves the LOOP uses them — which is
where the original defect lived. The loop decided for every tool_call in a round BEFORE recording
any result, so a same-round twin dispatched twice no matter how good the ledger was
(DEC-1790305211739337, blocker A, the load-bearing change).

Same lesson as the onboarding seam: a guard that passes its own tests and is not consulted at the
decision point protects nothing.
"""
import importlib.util
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_loop_reserves_rather_than_checks():
    """Pins the fix in the source: a check-then-record loop is the defect. If someone reverts
    reserve() to check() here, the same-round hole silently reopens."""
    src = pathlib.Path("services/arturo/arturo-proxy.py").read_text()
    assert "_dedup.reserve(fn_name, fn_args)" in src, \
        "the tool loop must RESERVE at decision time, not check-then-record"


def test_handler_level_dispatches_go_through_the_guard():
    """ask_gm / deep_query / research dispatch a nested async_task from inside a tool handler; the
    loop never sees those, so they must route through _dispatch_guarded."""
    src = pathlib.Path("services/arturo/arturo-proxy.py").read_text()
    assert src.count('_dispatch_guarded("async_task"') >= 3, \
        "the indirect dispatch sites bypassed the ledger entirely"


def test_the_turn_ledger_is_published_for_indirect_dispatch():
    src = pathlib.Path("services/arturo/arturo-proxy.py").read_text()
    assert "_TURN_DEDUP.set(_dedup)" in src, \
        "_dispatch_guarded cannot find the turn's ledger unless generate() publishes it"


def test_dispatch_guarded_suppresses_a_repeat_without_calling_the_tool():
    mod = _load_proxy()
    led = mod._voice_guards.ToolDedupLedger()
    mod._TURN_DEDUP.set(led)
    calls = []
    mod.execute_tool = lambda name, args: (calls.append((name, args)) or "dispatched")

    a = {"tool_name": "spawn_agent", "tool_args": {"session_name": "voice-agent", "task": "one"},
         "summary": "s"}
    b = {"tool_name": "spawn_agent", "tool_args": {"session_name": "voice-agent", "task": "two"},
         "summary": "s"}

    first = mod._dispatch_guarded("async_task", a)
    second = mod._dispatch_guarded("async_task", b)

    assert len(calls) == 1, "the reworded second dispatch must not reach execute_tool"
    assert first == "dispatched"
    assert "NOT delivered" in second, second


def test_dispatch_guarded_still_dispatches_a_genuinely_different_seat():
    mod = _load_proxy()
    led = mod._voice_guards.ToolDedupLedger()
    mod._TURN_DEDUP.set(led)
    calls = []
    mod.execute_tool = lambda name, args: (calls.append((name, args)) or "dispatched")

    mod._dispatch_guarded("async_task", {"tool_name": "spawn_agent",
                                         "tool_args": {"session_name": "seat-one"}, "summary": "s"})
    mod._dispatch_guarded("async_task", {"tool_name": "spawn_agent",
                                         "tool_args": {"session_name": "seat-two"}, "summary": "s"})
    assert len(calls) == 2, "over-deduping would be worse than the bug"


def test_dispatch_guarded_works_with_no_ledger_present():
    """A handler can run outside a turn (a direct call, a test, a script). No ledger must mean
    'dispatch normally', never a crash."""
    mod = _load_proxy()
    mod._TURN_DEDUP.set(None)
    calls = []
    mod.execute_tool = lambda name, args: (calls.append(name) or "dispatched")
    assert mod._dispatch_guarded("async_task", {"tool_name": "spawn_agent",
                                                "tool_args": {"session_name": "s"}, "summary": "s"}) == "dispatched"
    assert calls == ["async_task"]


def test_the_suppression_log_truncates_arg_values():
    """The suppression line renders tool args. Until secrets travel by reference, those args can
    carry a token (gm 03:1xZ: the bot token is in 227 transcripts), so values are truncated."""
    mod = _load_proxy()
    long_secret = "x" * 500
    line = mod._dedup_argsum({"prompt": long_secret})
    assert long_secret not in line
    assert len(line) < 120


def test_argsum_never_raises_on_odd_args():
    mod = _load_proxy()
    for bad in (None, "string", 42, [1, 2], {"k": object()}):
        mod._dedup_argsum(bad)
