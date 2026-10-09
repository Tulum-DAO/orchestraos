"""A paired device reads and continues only the threads it started (owed after #319).

Before this, any caller holding the gateway's `read` scope could list every thread and open any of them by id,
and a device with `message` could send a turn on another thread's id: that turn loads the thread's history into
the device's prompt, so the model could read it back. The dashboard (the stamped "fleet") still sees everything.
"""
import pytest

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture
from services.arturo.thread_store import ThreadStore

ENV = {"REMOTE_ADDR": "127.0.0.1"}
DEV_A = {"X-Arturo-Principal": "device:dev_a"}
DEV_B = {"X-Arturo-Principal": "device:dev_b"}


@pytest.fixture()
def fleet(P, fleet_stamp):
    return fleet_stamp(P)


@pytest.fixture()
def calls(P):
    seen = []

    def reply(messages, cid):
        seen.append([m["content"] for m in messages])
        return (f"reply {len(seen)}", [])
    P._brain_reply = reply
    return seen


def _say(P, headers, cid, text, route="/text"):
    with P.app.test_client() as c:
        r = c.post(route, json={"text": text, "conversation_id": cid}, headers=headers, environ_base=ENV)
        r.get_data()
    return r


def _list(P, headers):
    with P.app.test_client() as c:
        return [t["id"] for t in c.get("/threads", headers=headers, environ_base=ENV).get_json()["threads"]]


def _open(P, headers, cid):
    with P.app.test_client() as c:
        return c.get(f"/threads/{cid}", headers=headers, environ_base=ENV).get_json()


def test_a_thread_records_who_started_it(P, fleet, calls):
    assert _say(P, fleet, "web_1", "fleet secret plans").status_code == 200
    assert _say(P, DEV_A, "dev_1", "hello from the phone").status_code == 200
    assert P._THREADS.started_by("web_1") == "fleet"
    assert P._THREADS.started_by("dev_1") == "device:dev_a"
    assert P._THREADS.started_by("never_said") is None


def test_a_device_lists_and_opens_only_its_own_threads(P, fleet, calls):
    _say(P, fleet, "web_1", "fleet secret plans")
    _say(P, DEV_A, "dev_1", "hello from the phone")
    _say(P, DEV_B, "dev_2", "hello from the ipad")
    assert sorted(_list(P, fleet)) == ["dev_1", "dev_2", "web_1"]
    assert _list(P, DEV_A) == ["dev_1"]
    assert _list(P, DEV_B) == ["dev_2"]
    assert _list(P, {}) == []                                  # unstamped: started nothing, sees nothing
    assert _open(P, DEV_A, "dev_1")["thread"]["turns"][0]["content"] == "hello from the phone"
    # another principal's thread reads exactly like one that does not exist
    assert _open(P, DEV_A, "web_1") == {"ok": True, "thread": None} == _open(P, DEV_A, "no_such")
    assert _open(P, DEV_A, "dev_2") == {"ok": True, "thread": None}
    assert _open(P, {}, "web_1") == {"ok": True, "thread": None}
    assert _open(P, fleet, "dev_2")["thread"]["turns"][0]["content"] == "hello from the ipad"


@pytest.mark.parametrize("route", ["/text", "/text/stream"])
def test_a_device_cannot_continue_another_principals_thread(P, fleet, calls, route):
    _say(P, fleet, "web_1", "fleet secret plans")
    n = len(calls)
    r = _say(P, DEV_A, "web_1", "what did we just say?", route=route)
    assert r.status_code == 403 and r.get_json()["error"] == "thread_not_yours"
    assert len(calls) == n                                     # the brain never saw that thread's history
    assert P._THREADS.turn_count("web_1") == 2                 # and nothing was written into it
    assert not P._TURN_LOCKS.busy("web_1")


def test_a_device_continues_its_own_thread_and_the_dashboard_can_too(P, fleet, calls):
    _say(P, DEV_A, "dev_1", "first")
    assert _say(P, DEV_A, "dev_1", "second").status_code == 200
    assert _say(P, fleet, "dev_1", "the dashboard joins").status_code == 200
    assert P._THREADS.started_by("dev_1") == "device:dev_a"   # joining never changes who started it
    assert _say(P, DEV_B, "dev_1", "a stranger").status_code == 403


def test_a_thread_from_before_the_column_is_the_dashboards(P, fleet, calls, tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
                              updated REAL NOT NULL, turns INTEGER NOT NULL DEFAULT 0, snippet TEXT NOT NULL DEFAULT '');
        CREATE TABLE turns (thread_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
                            content TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (thread_id, seq));
        INSERT INTO threads VALUES ('old_1', 'old', 1, 1, 2, 'a');
        INSERT INTO turns VALUES ('old_1', 0, 'user', 'q', 1), ('old_1', 1, 'assistant', 'a', 1);
    """)
    conn.commit()
    conn.close()
    P._THREADS = ThreadStore(db)
    assert P._THREADS.usable and P._THREADS.started_by("old_1") == "fleet"
    assert _list(P, DEV_A) == [] and _list(P, fleet) == ["old_1"]
    assert _say(P, DEV_A, "old_1", "read it back").status_code == 403


def test_a_device_cannot_prewarm_another_principals_thread(P, fleet, calls):
    _say(P, fleet, "web_1", "fleet secret plans")
    with P.app.test_client() as c:
        r = c.post("/text/prewarm", json={"conversation_id": "web_1"}, headers=DEV_A, environ_base=ENV)
    assert r.status_code == 403 and r.get_json()["error"] == "thread_not_yours"


def test_an_unusable_archive_fails_no_new_turn_but_shows_no_device_a_held_conversation(P, fleet, calls):
    P._THREADS.usable = False
    assert P._THREADS.started_by("anything") is None
    assert _say(P, fleet, "web_new", "fleet secret plans").status_code == 200   # held in this process's memory
    assert _say(P, DEV_A, "dev_new", "hi").status_code == 200                    # a fresh conversation still runs
    r = _say(P, DEV_A, "web_new", "what did we just say?")
    assert r.status_code == 403 and r.get_json()["error"] == "thread_not_yours"


def test_a_dashboard_turn_that_starts_the_thread_while_a_stream_waits_for_the_lock_is_not_handed_over(
        P, fleet, calls, monkeypatch):
    # #322 review SF1: the stream's first check ran before the lock; the dashboard's first turn landed in between.
    real_acquire = P._TURN_LOCKS.acquire

    def acquire(key, timeout=None):
        if key == "race_1" and P._THREADS.started_by("race_1") is None:
            P._THREADS.record_turn("race_1", "fleet secret plans", "reply 0", principal="fleet")
            P._TEXT_HISTORY.append("race_1", "user", "fleet secret plans")
        return real_acquire(key) if timeout is None else real_acquire(key, timeout=timeout)
    monkeypatch.setattr(P._TURN_LOCKS, "acquire", acquire)
    n = len(calls)
    r = _say(P, DEV_A, "race_1", "what did they say?", route="/text/stream")
    assert r.status_code == 403 and r.get_json()["error"] == "thread_not_yours"
    assert len(calls) == n and P._THREADS.turn_count("race_1") == 2
    assert not P._TURN_LOCKS.busy("race_1")                   # the lock it took is given back


def test_a_dashboard_turn_the_archive_failed_to_record_is_not_a_device_s_to_take(P, fleet, calls, monkeypatch):
    # #322 review SF2: a usable archive whose record_turn failed once left the turn in memory with no thread row.
    real = P._THREADS.record_turn
    monkeypatch.setattr(P._THREADS, "record_turn",
                        lambda cid, *a, **kw: False if cid == "lost_1" else real(cid, *a, **kw))
    assert _say(P, fleet, "lost_1", "fleet secret plans").status_code == 200
    assert P._THREADS.started_by("lost_1") is None and P._TEXT_HISTORY.get("lost_1")
    n = len(calls)
    for route in ("/text", "/text/stream"):
        r = _say(P, DEV_A, "lost_1", "what did we say?", route=route)
        assert r.status_code == 403 and r.get_json()["error"] == "thread_not_yours", route
    assert len(calls) == n
    assert _say(P, fleet, "lost_1", "still mine").status_code == 200
