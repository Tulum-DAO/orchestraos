"""One turn at a time per conversation (DEC-1791511578959986 W1): the operator typed while the
page's opener was still running, and two turns ran on one conversation at once."""
import importlib.util
import pathlib
import threading
import time

from services.arturo import text_stream
from services.arturo.turn_lock import TurnLocks


def test_a_second_turn_in_the_same_conversation_waits_for_the_first():
    locks = TurnLocks()
    first = locks.acquire("c1")
    got = []
    t = threading.Thread(target=lambda: got.append(locks.acquire("c1", timeout=5)))
    t.start()
    time.sleep(0.2)
    assert got == []                                        # still waiting
    assert locks.acquire("c2", timeout=0.1) is not None     # another conversation does not wait
    first.release()
    t.join(2)
    assert got and got[0] is not None


def test_release_is_idempotent_and_works_from_another_thread():
    locks = TurnLocks()
    tok = locks.acquire("c1")
    threading.Thread(target=tok.release).start()
    time.sleep(0.1)
    tok.release()                                           # a second release is a no-op
    again = locks.acquire("c1", timeout=0.5)
    assert again is not None
    again.release()
    assert not locks.busy("c1") and locks._locks == {}      # nothing left behind


def test_a_wait_that_runs_out_answers_none():
    locks = TurnLocks()
    locks.acquire("c1")
    assert locks.acquire("c1", timeout=0.1) is None


def test_the_heartbeat_releases_when_the_turn_ends_not_when_the_client_leaves():
    done, started = [], []
    gate = threading.Event()

    def turn():
        yield {"event": "a", "data": {}}
        gate.wait(2)                                        # the turn is still running...
        yield {"event": "b", "data": {}}
    frames = text_stream.with_heartbeat(turn(), interval_s=5, on_start=lambda: started.append(1),
                                        on_done=lambda: done.append(1))
    next(frames)
    frames.close()                                          # ...when the client goes away
    assert started == [1] and done == []
    gate.set()
    for _ in range(50):
        if done:
            break
        time.sleep(0.02)
    assert done == [1]                                      # released once the turn itself ended


def test_the_heartbeat_releases_after_a_failed_turn():
    done = []

    def turn():
        raise RuntimeError("boom")
        yield  # noqa
    list(text_stream.with_heartbeat(turn(), interval_s=5, on_done=lambda: done.append(1)))
    assert done == [1]


# ---- the endpoints -------------------------------------------------------------------------------
def _proxy(tmp_path):
    spec = importlib.util.spec_from_file_location("arturo_proxy_lock", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = tmp_path / "arturo"
    mod.build_context = lambda **k: "BASECTX"
    return mod


def test_two_text_turns_on_one_conversation_run_one_after_the_other(tmp_path):
    P = _proxy(tmp_path)
    inside, overlap = [], []

    def brain(messages, cid):
        inside.append(1)
        if len(inside) > 1:
            overlap.append(1)
        time.sleep(0.3)
        inside.pop()
        return ("ok", [])
    P._brain_reply = brain
    results = []

    def post(text):
        with P.app.test_client() as c:
            results.append(c.post("/text", json={"text": text, "conversation_id": "c1"},
                                  environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code)
    ts = [threading.Thread(target=post, args=(f"m{i}",)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    assert results == [200, 200] and overlap == []
    assert P._THREADS.turn_count("c1") == 4
    assert not P._TURN_LOCKS.busy("c1")


def test_a_busy_conversation_answers_409_instead_of_hanging(tmp_path, monkeypatch):
    P = _proxy(tmp_path)
    held = P._TURN_LOCKS.acquire("c1")
    monkeypatch.setattr(P._TURN_LOCKS, "acquire", lambda key, timeout=None, _a=P._TURN_LOCKS.acquire: _a(key, timeout=0.1))
    with P.app.test_client() as c:
        r = c.post("/text", json={"text": "hi", "conversation_id": "c1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        s = c.post("/text/stream", json={"text": "hi", "conversation_id": "c1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 409 and r.get_json()["error"] == "busy"
    assert s.status_code == 409
    held.release()


def test_a_stream_releases_its_conversation_when_the_turn_ends(tmp_path):
    P = _proxy(tmp_path)
    P._brain_reply = lambda m, c: ("ok", [])
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "[Onboarding: step=onboarding]\nhi", "conversation_id": "c1"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
        r.get_data()
    for _ in range(50):
        if not P._TURN_LOCKS.busy("c1"):
            break
        time.sleep(0.02)
    assert not P._TURN_LOCKS.busy("c1")


def test_a_stream_that_fails_before_it_starts_releases_its_conversation(tmp_path, monkeypatch):
    P = _proxy(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("history store down")
    monkeypatch.setattr(P, "_conversation_history", broken)
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "hi", "conversation_id": "c1"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 500
    assert not P._TURN_LOCKS.busy("c1")


# ---- #312 review B1: a send while the conversation is mid-turn must not run twice ----------------
def test_a_send_during_a_turn_answers_busy_at_once_and_runs_nothing(tmp_path):
    P = _proxy(tmp_path)
    calls, gate = [], threading.Event()

    def brain(messages, cid):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            gate.wait(10)                                   # turn A is slow
        return ("ok", [])
    P._brain_reply = brain
    env = {"REMOTE_ADDR": "127.0.0.1"}

    def turn_a():
        with P.app.test_client() as c:
            c.post("/text", json={"text": "A slow", "conversation_id": "c1"}, environ_base=env)
    a = threading.Thread(target=turn_a)
    a.start()
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    with P.app.test_client() as c:
        t0 = time.time()
        s = c.post("/text/stream", json={"text": "B says hi", "conversation_id": "c1"}, environ_base=env)
        t = c.post("/text", json={"text": "B says hi", "conversation_id": "c1"}, environ_base=env)
        waited = time.time() - t0
    gate.set()
    a.join(10)
    assert s.status_code == 409 and t.status_code == 409 and s.get_json()["error"] == "busy"
    assert waited < 5                                       # long before any client deadline (20 s)
    assert calls == ["A slow"]                              # B never ran, let alone twice


def test_calls_without_a_conversation_id_never_share_a_lock(tmp_path):
    P = _proxy(tmp_path)
    P._brain_reply = lambda m, c: ("ok", [])
    held = P._TURN_LOCKS.acquire("")
    with P.app.test_client() as c:
        r = c.post("/text", json={"text": "hi"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    held.release()
    assert r.status_code == 200
