# Journal tool-events regression (gm msg_467bf15c, v14 characterization msg_080078d7):
# tool-turns present in call journals through 02:25 Aug-18, ZERO from 05:05 onward.
# Mechanism: the handler thread arms _TOOLS_THIS_TURN.set([]) (a ContextVar), but
# leg-3 (58422c031) moved the execute_tool call into a daemon threading.Thread —
# and threads do NOT inherit contextvars, so the worker read bucket=None and the
# per-turn flush journaled an empty list. Window matches the 05:00 respawn that
# picked up leg-3 exactly.
#
# Fix under test: _spawn_tool_worker() captures contextvars.copy_context() in the
# CALLING thread and runs the tool under it — copy_context shares the SAME list
# object, so worker appends are visible to the handler's flush.
import pathlib
import importlib.util


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_worker_records_into_callers_tools_bucket():
    mod = _load_proxy()
    token = mod._TOOLS_THIS_TURN.set([])   # what the handler does when a journal is live
    try:
        worker, holder = mod._spawn_tool_worker("nonexistent_tool_for_ctx_test", {"x": 1})
        worker.join(10)
        assert not worker.is_alive()
        assert "r" in holder                      # normal return, no exception
        bucket = mod._TOOLS_THIS_TURN.get()
        assert bucket, "worker appended nothing — the ContextVar did not cross the thread"
        assert bucket[0]["tool"] == "nonexistent_tool_for_ctx_test"
        assert bucket[0]["args"] == {"x": 1}
    finally:
        mod._TOOLS_THIS_TURN.reset(token)


def test_tool_worker_exception_lands_in_holder_e():
    # error semantics of the leg-3 wrap preserved: exceptions surface via holder['e']
    mod = _load_proxy()

    def _boom(*a, **kw):
        raise RuntimeError("boom")

    orig = mod.execute_tool
    mod.execute_tool = _boom
    try:
        worker, holder = mod._spawn_tool_worker("any", {})
        worker.join(10)
        assert "e" in holder and "r" not in holder
        assert isinstance(holder["e"], RuntimeError)
    finally:
        mod.execute_tool = orig


def test_tool_worker_threads_user_turns_to_gate():
    # the Q0 user_turns kwarg must survive the worker extraction (deny path is
    # network-free by construction: no URL, no texting intent => teaching deny)
    mod = _load_proxy()
    worker, holder = mod._spawn_tool_worker(
        "send_telegram", {"message": "internal chatter"}, user_turns=[])
    worker.join(10)
    assert "Not sent" in holder["r"]


def test_tool_worker_no_bucket_is_still_safe():
    # journal not armed (no ContextVar set) — worker must run fine with bucket=None
    mod = _load_proxy()
    worker, holder = mod._spawn_tool_worker("nonexistent_tool_for_ctx_test", {})
    worker.join(10)
    assert "r" in holder
