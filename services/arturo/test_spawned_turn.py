"""Shaw, 2026-09-22: when Arturo spawns an agent, that turn must carry a way INTO the agent.

The seat id is known only to the tool call, so the /text envelope gains an additive `spawned`
list and the surfaces render a "go to agent" card from it. A FAILED spawn must never produce a
link, so only the verified success path records.
"""
import importlib.util
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _wire(mod, tmp_path, brain):
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = tmp_path / "arturo"
    mod.build_context = lambda **k: "BASECTX"
    mod._brain_reply = brain


def test_text_envelope_carries_the_seats_this_turn_created(tmp_path):
    mod = _load_proxy()
    _wire(mod, tmp_path, lambda m, c: ("Scout is up.", ["spawn_agent"], ["scout"]))
    code, body = mod.text_turn("commission a scout", "c1")
    assert code == 200 and body["ok"]
    assert body["spawned"] == ["scout"]
    assert body["tools_called"] == ["spawn_agent"]


def test_an_ordinary_turn_carries_an_empty_list_not_a_missing_key(tmp_path):
    mod = _load_proxy()
    _wire(mod, tmp_path, lambda m, c: ("Hello.", []))          # the older 2-tuple shape still works
    code, body = mod.text_turn("hi", "c1")
    assert code == 200 and body["spawned"] == []


def test_only_a_verified_spawn_is_recorded(tmp_path):
    mod = _load_proxy()
    token = mod._SPAWNED_THIS_TURN.set([])
    try:
        mod._record_spawned_this_turn("scout")
        mod._record_spawned_this_turn("scout")                  # idempotent within a turn
        mod._record_spawned_this_turn("")                       # a failed spawn has no name
        assert mod._SPAWNED_THIS_TURN.get() == ["scout"]
    finally:
        mod._SPAWNED_THIS_TURN.reset(token)
    # outside a turn it is a no-op, never an exception
    mod._record_spawned_this_turn("orphan")
