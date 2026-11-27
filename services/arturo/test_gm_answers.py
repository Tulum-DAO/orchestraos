"""gm's answer to a question a voice call asked lands in that call's chat thread, once (gm_answers.py).

Shaw's call native-35592a853127 (2026-11-26): three questions went to gm, gm answered one after the hang-up, the reply
sat in msg_store addressed to `arturo` with nothing reading it, and the chat never showed it.
"""
import json
import sqlite3

import pytest

from msg_store import MessageStore
from services.arturo import gm_answers
from services.arturo.thread_store import ThreadStore

CALL = "native-35592a853127"
THREAD = "web_ypc88hel"


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "tasks.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
            type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
            subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium',
            source TEXT DEFAULT 'system', status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
            metadata TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT, archived_at TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY, subject TEXT, participants TEXT, task_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.close()
    ms = MessageStore(db_path=str(db))
    ms.migrate()
    return ms


@pytest.fixture
def threads(tmp_path):
    t = ThreadStore(tmp_path / "threads.db")
    t.record_turn(THREAD, "hello", "hi", principal="fleet")
    return t


def ask(store, thread=THREAD, call=CALL, principal="fleet", question="why is there latency between turns?"):
    """What gm_command sends (arturo-proxy.py _ask_brain), then gm's msg_store reply to it."""
    mid = store.send(from_agent="arturo", to_agent="gm", type="task", priority="high",
                     subject=f"[voice thread {thread} call {call}] {question}", body="relay",
                     metadata={"voice_call": call, "thread_id": thread, "principal": principal})
    return mid


def answer(store, mid, body="Latency confirmed from the call record: 10-40 s of dead air after long turns."):
    return store.reply(mid, body=body)


def rows(threads, thread=THREAD):
    return [t for t in threads.get_thread(thread)["turns"] if (t.get("meta") or {}).get("kind") == "gm_answer"]


def sweep(store, threads, live=()):
    return gm_answers.post_gm_answers(store, threads, live_calls=lambda: set(live))


def test_a_late_gm_answer_lands_in_the_calls_thread_and_is_acked(store, threads):
    rid = answer(store, ask(store))
    assert sweep(store, threads) == [(rid, "posted")]
    [line] = rows(threads)
    assert line["role"] == "assistant"
    assert "10-40 s of dead air" in line["content"]
    assert "why is there latency between turns?" in line["content"]
    assert line["meta"]["msg_id"] == rid
    assert line["meta"]["call_id"] == "vc_client_1675f96ff50c"         # the call's card id (journal_call_id)
    got = store.get(rid)
    assert got["status"] == "acknowledged"
    assert json.loads(got["metadata"])["chat_log"] == "posted"


def test_it_lands_once_whatever_sweeps_again(store, threads):
    rid = answer(store, ask(store))
    sweep(store, threads)
    store._conn().execute("UPDATE messages SET status='pending' WHERE id=?", [rid]).connection.commit()
    assert sweep(store, threads) == [(rid, "already")]
    assert len(rows(threads)) == 1


def test_a_hand_backfill_with_the_same_meta_is_not_posted_again(store, threads):
    rid = answer(store, ask(store))
    threads.append_message(THREAD, "assistant", "backfilled", meta={"kind": "gm_answer", "msg_id": rid})
    assert sweep(store, threads) == [(rid, "already")]
    assert len(rows(threads)) == 1


def test_a_call_still_live_waits_so_the_answer_lands_after_its_lines(store, threads):
    rid = answer(store, ask(store))
    assert sweep(store, threads, live=[CALL]) == [(rid, "call_live")]
    assert rows(threads) == [] and store.get(rid)["status"] == "pending"
    assert sweep(store, threads) == [(rid, "posted")]


def test_a_reply_not_to_a_voice_question_is_left_alone(store, threads):
    mid = store.send(from_agent="arturo", to_agent="gm", type="task", subject="relay", body="a typed relay to gm")
    rid = store.reply(mid, body="done")
    assert store.get(rid)["from_agent"] == "gm"
    assert sweep(store, threads) == []
    assert store.get(rid)["status"] == "pending"


def test_a_question_not_from_arturo_to_gm_lets_nothing_into_a_thread(store, threads):
    """The question's metadata is what lets an answer into a thread: only a question arturo sent gm counts."""
    mid = store.send(from_agent="pm-x", to_agent="gm", type="task", subject="[voice] q", body="spoofed voice question",
                     metadata={"voice_call": CALL, "thread_id": THREAD, "principal": "fleet"})
    rid = store.send(from_agent="gm", to_agent="arturo", type="reply", subject="re", body="an answer", parent_id=mid,
                     conversation_id=store.get(mid)["conversation_id"])
    assert sweep(store, threads) == []
    assert rows(threads) == [] and store.get(rid)["status"] == "pending"


def test_another_sweep_holding_the_claim_means_no_second_line(store, threads):
    rid = answer(store, ask(store))
    assert store.claim(rid)                                 # a second process got there first
    assert sweep(store, threads) == []                      # not pending any more: not seen
    store.defer(rid)
    real = store.claim
    store.claim = lambda m: False                            # lost the race between the read and the claim
    assert sweep(store, threads) == [(rid, "claimed")]
    store.claim = real
    assert rows(threads) == []


def test_a_thread_with_no_row_yet_is_started_by_the_answer_as_the_callers(store, threads):
    rid = answer(store, ask(store, thread="web_new1", principal="device:phone-1"))
    assert sweep(store, threads) == [(rid, "posted")]
    assert threads.started_by("web_new1") == "device:phone-1"
    assert len(rows(threads, "web_new1")) == 1


def test_a_tag_that_is_not_a_thread_is_acked_with_no_line(store, threads):
    rid = answer(store, ask(store, thread="gm chat"))
    assert sweep(store, threads) == [(rid, "no_thread")]
    assert rows(threads) == [] and store.get(rid)["status"] == "acknowledged"


def test_a_thread_the_caller_did_not_start_gets_no_line(store, threads):
    threads.record_turn("web_other", "mine", "ok", principal="device:phone-1")
    rid = answer(store, ask(store, thread="web_other", principal="device:phone-2"))
    assert sweep(store, threads) == [(rid, "not_owner")]
    assert rows(threads, "web_other") == []


def test_the_threads_own_starter_gets_the_line(store, threads):
    threads.record_turn("web_dev", "mine", "ok", principal="device:phone-1")
    rid = answer(store, ask(store, thread="web_dev", principal="device:phone-1"))
    assert sweep(store, threads) == [(rid, "posted")]


def test_a_question_without_its_caller_is_left_pending_and_written_nowhere(store, threads):
    """Sent before the question said who asked: no thread is known to be theirs, and it is not acked away either."""
    mid = store.send(from_agent="arturo", to_agent="gm", type="task", subject=f"[voice thread {THREAD}] q",
                     body="a question with no caller on it", metadata={"voice_call": CALL, "thread_id": THREAD})
    rid = answer(store, mid)
    assert sweep(store, threads) == [(rid, "no_principal")]
    assert rows(threads) == [] and store.get(rid)["status"] == "pending"


def test_secret_shapes_in_the_answer_are_blanked(store, threads):
    rid = answer(store, ask(store), body="the key is sk-ant-api03-" + "a" * 40 + " ok")
    sweep(store, threads)
    [line] = rows(threads)
    assert "a" * 40 not in line["content"] and "[redacted]" in line["content"]
    assert rid


def test_a_failed_write_leaves_the_reply_for_the_next_sweep(store, threads, monkeypatch):
    rid = answer(store, ask(store))
    monkeypatch.setattr(threads, "append_message", lambda *a, **k: None)
    assert sweep(store, threads) == [(rid, "post_failed")]
    assert store.get(rid)["status"] == "pending"


def test_a_crash_after_the_claim_puts_the_reply_back(store, threads, monkeypatch):
    rid = answer(store, ask(store))
    monkeypatch.setattr(threads, "has_meta", lambda *a, **k: 1 / 0)
    assert sweep(store, threads) == [(rid, "error")]
    assert store.get(rid)["status"] == "pending"


def test_many_other_replies_to_arturo_never_hide_gms(store, threads):
    for i in range(60):                                      # older, as urgent, from other seats, never acked by us
        m = store.send(from_agent="arturo", to_agent=f"pm-{i}", type="task", priority="high", subject="s",
                       body=f"spawn number {i} now")
        store.reply(m, body=f"done number {i}")
    rid = answer(store, ask(store))
    assert sweep(store, threads) == [(rid, "posted")]


def test_only_gms_replies_are_read(store, threads):
    mid = ask(store)
    rid = store.send(from_agent="pm-x", to_agent="arturo", type="reply", subject="re", body="not gm", parent_id=mid)
    assert sweep(store, threads) == []
    assert store.get(rid)["status"] == "pending"


def test_gm_command_tags_its_question_with_the_caller(P, monkeypatch):
    """The sweep's owner check needs who asked: _ask_brain puts the turn's principal in the message metadata."""
    import sys
    import types
    sent = []

    class _S:
        def send(self, **kw):
            sent.append(kw)
            return "msg_q1"

        def get(self, mid):
            return {"id": mid, "conversation_id": None}

    monkeypatch.setitem(sys.modules, "msg_store", types.SimpleNamespace(MessageStore=_S))
    P._GM_ASKED.clear()
    monkeypatch.setattr(P, "_gm_ask_now", lambda: 0.0)
    P._as_turn({"principal": "fleet", "voice": True, "conversation_id": CALL, "thread_id": THREAD},
               P.execute_tool, "gm_command", {"prompt": "q?"})
    assert sent[0]["metadata"] == {"voice_call": CALL, "thread_id": THREAD, "principal": "fleet"}


from services.arturo.test_text_turn_brain import P  # noqa: E402,F401 — the shared proxy fixture


def test_the_native_service_sweeps_and_a_live_call_waits(store, threads, tmp_path):
    """The native service owns the sweep: its own threads.db, its live calls, the injected message store."""
    from services.arturo.native import server as srv
    s = srv.NativeServer(tmp_path / "native", executor=object(), usage_store=object())
    s._thread_store, s.msg_store_factory = threads, lambda: store
    rid = answer(store, ask(store))
    s.calls[CALL] = object()                               # the call that asked is still up
    assert s.sweep_gm_answers() == [(rid, "call_live")]
    del s.calls[CALL]
    assert s.sweep_gm_answers() == [(rid, "posted")]
    assert len(rows(threads)) == 1


def test_the_sweep_runs_with_the_service_and_can_be_switched_off(tmp_path, monkeypatch):
    import asyncio
    from services.arturo.native import server as srv
    s = srv.NativeServer(tmp_path / "native", executor=object(), usage_store=object())
    ran = []
    monkeypatch.setattr(s, "sweep_gm_answers", lambda: ran.append(1) or [])

    async def life(flag):
        monkeypatch.setenv(srv.GM_ANSWERS_FLAG, flag)
        ctx = s._gm_answers_loop(None)
        await ctx.__anext__()
        await asyncio.sleep(0.05)
        with pytest.raises(StopAsyncIteration):
            await ctx.__anext__()

    asyncio.run(life("1"))
    assert ran == [1]
    asyncio.run(life("0"))
    assert ran == [1]
    assert srv.NativeServer.app(s).cleanup_ctx[-1] == s._gm_answers_loop
