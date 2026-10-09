"""One operator send runs at most once, and the reply shown is THAT send's (#319 review; DEC-1791518421640932).

The client mints a turn id per send and sends the same id on every attempt. Before it, the page matched a
dropped stream's reply by TEXT: a repeated "yes" read back the previous "yes"'s reply (item 1), and an error
the server reported after it had recorded the turn re-asked it (item 2).
"""
import json
import threading
import time

import pytest

from services.arturo import turn_ids
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture

ENV = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture()
def brain(P):
    """A brain that counts its calls and answers reply 1, reply 2, ..."""
    calls = []

    def reply(messages, cid):
        calls.append(messages[-1]["content"])
        return (f"reply {len(calls)}", [])
    P._brain_reply = reply
    return calls


def _post(P, route, text, cid="c1", tid="t_000000000001", principal=None):
    headers = {"X-Arturo-Principal": principal} if principal else {}
    body = {"text": text, "conversation_id": cid}
    if tid is not None:
        body["turn_id"] = tid
    with P.app.test_client() as c:
        r = c.post(route, json=body, headers=headers, environ_base=ENV)
        r.get_data()                                        # a stream runs to its end
    return r


def _turn(P, tid, cid="c1", principal=None):
    headers = {"X-Arturo-Principal": principal} if principal else {}
    with P.app.test_client() as c:
        return c.get(f"/threads/{cid}?turn={tid}", headers=headers, environ_base=ENV).get_json()["turn"]


def _settled(P, cid="c1"):
    for _ in range(100):
        if not P._TURN_LOCKS.busy(cid):
            return
        time.sleep(0.02)


def _stream_end(resp):
    name = None
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name == "turn.end":
            return json.loads(line[6:])
    return None


# ---- replay: the same send twice runs once ----------------------------------------------------------
def test_the_same_turn_id_twice_runs_once_and_the_second_is_a_replay(P, brain):
    a = _post(P, "/text", "hello")
    b = _post(P, "/text", "hello")
    assert a.status_code == b.status_code == 200
    assert brain == ["hello"]
    assert b.get_json()["replayed"] is True and b.get_json()["reply_text"] == a.get_json()["reply_text"] == "reply 1"
    assert P._THREADS.turn_count("c1") == 2                 # recorded once


@pytest.mark.parametrize("first,second", [("/text/stream", "/text"), ("/text", "/text/stream")])
def test_a_send_reasked_on_the_other_endpoint_is_replayed_not_rerun(P, brain, first, second):
    _post(P, first, "hello")
    _settled(P)
    r = _post(P, second, "hello")
    assert brain == ["hello"]
    assert r.status_code == 200 and r.is_json and r.get_json()["replayed"] is True   # JSON, never a second stream


def test_a_different_send_with_the_same_id_is_refused_and_runs_nothing(P, brain):
    _post(P, "/text", "hello")
    r = _post(P, "/text", "something else")
    assert r.status_code == 409 and r.get_json()["error"] == "turn_id_conflict"
    assert brain == ["hello"]


# ---- item 1: a repeated message reads back ITS reply, never the previous one --------------------------
def test_a_repeated_message_reads_back_its_own_reply_not_the_previous_ones(P, brain):
    _post(P, "/text/stream", "yes", tid="t_first_yes_01")
    _settled(P)
    assert P._THREADS.get_thread("c1")["turns"][-1]["content"] == "reply 1"      # thread ends ["yes", "reply 1"]
    gate = threading.Event()

    def slow(messages, cid):
        brain.append(messages[-1]["content"])
        gate.wait(5)
        return ("reply 2", [])
    P._brain_reply = slow
    t = threading.Thread(target=_post, args=(P, "/text/stream", "yes"), kwargs={"tid": "t_second_yes_2"})
    t.start()
    for _ in range(100):
        if len(brain) == 2:
            break
        time.sleep(0.02)
    assert _turn(P, "t_second_yes_2")["state"] == "running"      # NOT the previous yes's "reply 1"
    gate.set()
    t.join(5)
    _settled(P)
    got = _turn(P, "t_second_yes_2")
    assert got["state"] == "done" and got["result"]["reply_text"] == "reply 2"
    assert "recorded_only" not in got["result"]                  # the whole body, cards included (nit 7)


# ---- item 2: an error after the record, or after a tool, is never re-run ------------------------------
def test_an_error_after_the_turn_was_recorded_is_replayed_never_rerun(P, brain, monkeypatch):
    from services.arturo import operator_store
    monkeypatch.setattr(operator_store, "public", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        P.app.testing = True
        _post(P, "/text", "hello")
    P.app.testing = False
    assert P._THREADS.turn_count("c1") == 2                 # it WAS recorded before the error
    assert _turn(P, "t_000000000001")["state"] == "done"
    monkeypatch.undo()
    r = _post(P, "/text", "hello")
    assert r.status_code == 200 and r.get_json()["replayed"] is True and r.get_json()["reply_text"] == "reply 1"
    assert brain == ["hello"]


def test_a_brain_failure_after_a_tool_ran_is_never_rerun(P, brain):
    def tool_then_fail(messages, cid):
        brain.append(messages[-1]["content"])
        led = P._turn_ledger()
        led.reserve("send_telegram", {"text": "hi"})
        led.record("send_telegram", {"text": "hi"}, "sent")
        raise P._BrainHttpError(503, "upstream")
    P._brain_reply = tool_then_fail
    a = _post(P, "/text", "tell them")
    assert a.status_code == 502
    b = _post(P, "/text", "tell them")
    assert brain == ["tell them"]                            # the tool never runs twice
    assert b.status_code == 502 and b.get_json()["replayed"] is True


def test_a_failure_that_ran_nothing_may_be_sent_again_and_then_runs(P, brain):
    def fail_once(messages, cid):
        brain.append(messages[-1]["content"])
        if len(brain) == 1:
            raise P._BrainHttpError(503, "upstream")
        return ("reply 2", [])
    P._brain_reply = fail_once
    assert _post(P, "/text", "hello").status_code == 502
    assert _turn(P, "t_000000000001")["state"] == "unknown"  # nothing ran: the client may re-ask
    r = _post(P, "/text", "hello")
    assert r.status_code == 200 and r.get_json()["reply_text"] == "reply 2" and "replayed" not in r.get_json()


# ---- a turn that started and never finished is never run again --------------------------------------
def test_a_turn_started_before_a_restart_is_lost_and_never_rerun(P, brain):
    P._THREADS.mark_started("c1", "t_000000000001", turn_ids.owner(None, "hello"), "oldboot")
    r = _post(P, "/text", "hello")
    assert r.status_code == 409 and r.get_json()["error"] == "turn_lost"
    assert brain == []
    assert _turn(P, "t_000000000001")["state"] == "lost"


def test_a_turn_that_cannot_be_marked_does_not_run(P, brain):
    P._THREADS.usable = False
    r = _post(P, "/text", "hello")
    assert r.status_code == 503 and r.get_json()["error"] == "turn_mark_unavailable"
    assert brain == []


# ---- seen from the first moment, and only by its sender -----------------------------------------------
def test_a_send_waiting_for_the_lock_already_reads_running_and_a_busy_one_does_not_linger(P, brain, monkeypatch):
    held = P._TURN_LOCKS.acquire("c1")
    monkeypatch.setattr(P._TURN_LOCKS, "acquire", lambda key, timeout=None, _a=P._TURN_LOCKS.acquire: _a(key, timeout=0.6))
    out = []
    t = threading.Thread(target=lambda: out.append(_post(P, "/text", "hello")))
    t.start()
    time.sleep(0.2)
    assert _turn(P, "t_000000000001")["state"] == "running"  # registered before the lock wait
    t.join(5)
    held.release()
    assert out[0].status_code == 409 and brain == []
    assert _turn(P, "t_000000000001")["state"] == "unknown"  # the busy request left; it never ran


def test_the_result_goes_only_to_the_principal_that_sent_it(P, brain):
    _post(P, "/text", "hello", principal="device:a")
    mine, theirs = _turn(P, "t_000000000001", principal="device:a"), _turn(P, "t_000000000001", principal="device:b")
    assert mine["state"] == theirs["state"] == "done"
    assert mine["result"]["reply_text"] == "reply 1" and "result" not in theirs


def test_a_duplicate_request_never_clears_the_running_turns_entry(P, brain, monkeypatch):
    gate = threading.Event()

    def slow(messages, cid):
        brain.append(1)
        gate.wait(5)
        return ("reply", [])
    P._brain_reply = slow
    t = threading.Thread(target=_post, args=(P, "/text", "hello"))
    t.start()
    for _ in range(100):
        if brain:
            break
        time.sleep(0.02)
    monkeypatch.setattr(P._TURN_LOCKS, "acquire", lambda key, timeout=None, _a=P._TURN_LOCKS.acquire: _a(key, timeout=0.1))
    assert _post(P, "/text", "hello").status_code == 409    # the duplicate: busy, and gone again
    assert _turn(P, "t_000000000001")["state"] == "running"  # the live turn is still seen as running
    gate.set()
    t.join(5)
    assert brain == [1]


# ---- the contract's edges ----------------------------------------------------------------------------
def test_a_bad_turn_id_or_one_without_a_conversation_is_refused(P, brain):
    assert _post(P, "/text", "hello", tid="no spaces!").status_code == 400
    assert _post(P, "/text", "hello", cid="").get_json()["error"] == "turn_id_needs_conversation"
    assert _post(P, "/text/stream", "hello", cid="").get_json()["error"] == "turn_id_needs_conversation"
    assert brain == []


def test_without_a_turn_id_nothing_changes(P, brain):
    _post(P, "/text", "hello", tid=None)
    _post(P, "/text", "hello", tid=None)
    assert brain == ["hello", "hello"]


def test_a_streamed_turns_events_carry_the_clients_turn_id(P, brain):
    r = _post(P, "/text/stream", "hello", tid="t_client_minted")
    assert _stream_end(r)["turn_id"] == "t_client_minted"


def test_after_a_restart_a_different_send_with_a_recorded_id_is_refused_not_given_the_reply(P, brain):
    _post(P, "/text", "hello")
    P._TURN_IDS = turn_ids.Registry()                        # memory gone: only the recorded mark is left
    r = _post(P, "/text", "something else")
    assert r.status_code == 409 and r.get_json()["error"] == "turn_id_conflict"
    assert "reply_text" not in r.get_json()


def test_a_turn_served_whole_carries_the_clients_turn_id(P):
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "check the agents", "conversation_id": "c9", "turn_id": "t_whole_codex1",
                                         "brain": {"provider": "codex", "model": "gpt-5.6-terra"}}, environ_base=ENV)
    assert _stream_end(r)["turn_id"] == "t_whole_codex1"


def test_a_turn_the_archive_failed_to_store_is_not_counted_as_recorded(P, monkeypatch):
    # #319 delta SF2: record_turn swallows its errors and answers False; such a turn has no row to replay from.
    monkeypatch.setattr(P._THREADS, "record_turn", lambda *a, **k: False)
    P._record_text_turn(conversation_id="c1", text="hi", reply="hello", turn_id="t_000000000001")
    assert not P._TURN_IDS.was_recorded("c1", "t_000000000001")
    monkeypatch.setattr(P._THREADS, "record_turn", lambda *a, **k: True)
    P._record_text_turn(conversation_id="c1", text="hi", reply="hello", turn_id="t_000000000002")
    assert P._TURN_IDS.was_recorded("c1", "t_000000000002")
