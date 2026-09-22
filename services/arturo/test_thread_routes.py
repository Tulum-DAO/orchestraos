"""G20 route/wiring tests on :5071 — the thread list is served BY THE SERVICE.

thread_store.py carries the storage logic (test_thread_store.py); these pin the seam:
every text turn is archived, the list/detail routes are loopback-only like the rest of
:5071, and continuing an OLD thread rehydrates its context from the archive rather than
answering with none (the case a restart creates).
"""
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod_and_tmp(tmp_path):
    return _load_proxy(), tmp_path


def _store(mod, tmp_path):
    from services.arturo.thread_store import ThreadStore
    s = ThreadStore(tmp_path / "threads.db")
    mod._THREADS = s
    return s


def test_a_text_turn_is_archived_as_a_thread(mod_and_tmp):
    mod, tmp_path = mod_and_tmp
    s = _store(mod, tmp_path)
    mod.build_context = lambda **k: "ctx"
    mod._brain_reply = lambda messages, conversation_id: ("Nine seats are up.", [])
    code, body = mod.text_turn("How many agents are up?", "c1")
    assert code == 200 and body["ok"]
    assert [t["id"] for t in s.list_threads()] == ["c1"]
    assert s.get_thread("c1")["title"] == "How many agents are up?"


def test_threads_routes_are_loopback_only(mod_and_tmp):
    mod, tmp_path = mod_and_tmp
    _store(mod, tmp_path)
    c = mod.app.test_client()
    for path in ("/threads", "/threads/c1"):
        r = c.get(path, environ_base={"REMOTE_ADDR": "10.1.2.3"})
        assert r.status_code == 403, path


def test_threads_list_route_returns_summaries_newest_first(mod_and_tmp):
    mod, tmp_path = mod_and_tmp
    s = _store(mod, tmp_path)
    s.record_turn("c1", "older question", "older answer", ts=100)
    s.record_turn("c2", "newer question", "newer answer", ts=200)
    r = mod.app.test_client().get("/threads", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert [t["id"] for t in body["threads"]] == ["c2", "c1"]
    assert body["threads"][0]["title"] == "newer question"
    assert "turns" in body["threads"][0] and isinstance(body["threads"][0]["turns"], int)


def test_thread_detail_route_returns_turns_and_a_null_thread_for_an_unknown_id(mod_and_tmp):
    mod, tmp_path = mod_and_tmp
    s = _store(mod, tmp_path)
    s.record_turn("c1", "q1", "a1")
    c = mod.app.test_client()
    r = c.get("/threads/c1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    assert [(t["role"], t["content"]) for t in r.get_json()["thread"]["turns"]] == [
        ("user", "q1"), ("assistant", "a1")]
    # an id with no turns yet is the pill's normal first-open state, not a failed request
    rnew = c.get("/threads/nope", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert rnew.status_code == 200 and rnew.get_json() == {"ok": True, "thread": None}


def test_continuing_an_old_thread_rehydrates_its_context_after_a_restart(mod_and_tmp):
    """The restart case: in-memory history is empty, but the thread is not new. The brain
    must be handed the archived turns, or reopening yesterday's thread answers blind."""
    mod, tmp_path = mod_and_tmp
    s = _store(mod, tmp_path)
    s.record_turn("c1", "my name is Sam", "Nice to meet you, Sam.")
    mod._TEXT_HISTORY = mod._ptt.PttHistory()          # the restart: nothing in memory
    seen = {}
    mod.build_context = lambda **k: "ctx"
    def fake_reply(messages, conversation_id):
        seen["messages"] = messages
        return ("You said Sam.", [])
    mod._brain_reply = fake_reply
    mod.text_turn("what is my name?", "c1")
    flat = " ".join(m.get("content") or "" for m in seen["messages"])
    assert "my name is Sam" in flat


def test_an_unusable_archive_does_not_fail_the_turn(mod_and_tmp):
    mod, tmp_path = mod_and_tmp
    bad = tmp_path / "blocked"
    bad.write_text("a file where the directory should be")
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(bad / "threads.db")
    assert mod._THREADS.usable is False
    mod.build_context = lambda **k: "ctx"
    mod._brain_reply = lambda messages, conversation_id: ("still answered", [])
    code, body = mod.text_turn("does this still work?", "c1")
    assert code == 200 and body["reply_text"] == "still answered"
    r = mod.app.test_client().get("/threads", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.get_json()["threads"] == []
