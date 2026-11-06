"""PATCH /threads/<id> (rename a chat) and DELETE /threads/<id> (delete it, with every verbatim trace), on :5071.

Rename follows the rule for reading a thread: the dashboard renames any chat, a paired device only its own, and
another principal's chat reads as one that does not exist. Delete is the dashboard's (its API lets only an admin
ask) or the owner device's, and removes the chat everywhere it was copied: threads.db, the unified log, the recall
query log, call journals, jobs, and the in-memory history a next turn would read. Facts learned in the chat go
only when asked (?forget_facts=1); the onboarding chat's operator facts never do.
"""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from services.arturo import operator_store
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture
from services.arturo.thread_store import TITLE_MAX

ENV = {"REMOTE_ADDR": "127.0.0.1"}
DEV_A = {"X-Arturo-Principal": "device:dev_a"}
OPENER = "(first run: the operator just opened the page)"


@pytest.fixture()
def fleet(P, fleet_stamp, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ARTURO_LOGS", tmp_path / "logs")
    monkeypatch.setattr(P, "VOICE_MEMORY_FILE", tmp_path / "arturo" / "voice-memory.json")
    monkeypatch.setattr(P, "VOICE_TRANSCRIPTS_DIR", tmp_path / "transcripts")
    monkeypatch.setattr(P, "VOICE_CALLS_DIR", tmp_path / "calls")
    return fleet_stamp(P)


def _patch(P, headers, cid, body, env=ENV):
    with P.app.test_client() as c:
        return c.patch(f"/threads/{cid}", json=body, headers=headers, environ_base=env)


def _delete(P, headers, cid, q="", env=ENV):
    with P.app.test_client() as c:
        return c.delete(f"/threads/{cid}{q}", headers=headers, environ_base=env)


def _get(P, headers, path):
    with P.app.test_client() as c:
        return c.get(path, headers=headers, environ_base=ENV)


# --- rename ----------------------------------------------------------------------------------------------------

def test_rename_is_loopback_only(P, fleet):
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    assert _patch(P, fleet, "c1", {"title": "x"}, env={"REMOTE_ADDR": "10.1.2.3"}).status_code == 403
    assert _delete(P, fleet, "c1", env={"REMOTE_ADDR": "10.1.2.3"}).status_code == 403


def test_the_dashboard_renames_a_chat(P, fleet):
    P._THREADS.record_turn("c1", "How many agents are up?", "Nine.", principal="fleet")
    r = _patch(P, fleet, "c1", {"title": "  Agent   count "})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "thread": {"id": "c1", "title": "Agent count", "title_custom": True}}
    listed = _get(P, fleet, "/threads").get_json()["threads"]
    assert listed[0]["title"] == "Agent count" and listed[0]["title_custom"] is True
    assert _get(P, fleet, "/threads/c1").get_json()["thread"]["title"] == "Agent count"


def test_an_empty_title_puts_the_auto_title_back(P, fleet):
    P._THREADS.record_turn("c1", "How many agents are up?", "Nine.", principal="fleet")
    _patch(P, fleet, "c1", {"title": "Agent count"})
    assert _patch(P, fleet, "c1", {"title": ""}).get_json()["thread"] == {
        "id": "c1", "title": "How many agents are up?", "title_custom": False}


def test_rename_validates_the_title(P, fleet):
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    assert _patch(P, fleet, "c1", {"title": "x" * (TITLE_MAX + 1)}).status_code == 400
    assert _patch(P, fleet, "c1", {"title": 5}).status_code == 400
    assert _patch(P, fleet, "c1", {}).status_code == 400
    assert _patch(P, fleet, "c1", {"title": "x" * TITLE_MAX}).status_code == 200


def test_rename_of_an_unknown_chat_is_404(P, fleet):
    assert _patch(P, fleet, "nope", {"title": "x"}).status_code == 404


def test_a_device_renames_only_its_own_chats(P, fleet):
    P._THREADS.record_turn("web_1", "fleet plans", "ok", principal="fleet")
    P._THREADS.record_turn("dev_1", "phone question", "ok", principal="device:dev_a")
    assert _patch(P, DEV_A, "web_1", {"title": "mine now"}).status_code == 404
    assert P._THREADS.get_thread("web_1")["title"] == "fleet plans"
    assert _patch(P, DEV_A, "dev_1", {"title": "From the phone"}).status_code == 200


def test_the_recent_chats_block_shows_a_chosen_title(P, fleet):
    P._THREADS.record_turn("c1", "some first question", "ok", principal="fleet")
    P._THREADS.record_turn("c2", "the current chat", "ok", principal="fleet")
    _patch(P, fleet, "c1", {"title": "Budget planning"})
    block = P._recent_threads_block("c2")
    assert "Budget planning" in block and "some first question" not in block


def test_an_onboarding_chat_is_listed_as_getting_started(P, fleet):
    P._THREADS.record_turn("c1", OPENER, "Hi, what should I call you?", principal="fleet")
    assert _get(P, fleet, "/threads").get_json()["threads"][0]["title"] == "Getting started"


# --- delete ----------------------------------------------------------------------------------------------------

def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_only_the_dashboard_deletes(P, fleet):
    P._THREADS.record_turn("dev_1", "phone question", "ok", principal="device:dev_a")
    assert _delete(P, DEV_A, "dev_1").status_code == 403
    assert _delete(P, {}, "dev_1").status_code == 403
    assert _delete(P, {"X-Arturo-Principal": "owner:dev_a"}, "dev_1").status_code == 403
    assert P._THREADS.get_thread("dev_1") is not None


def test_delete_of_an_unknown_chat_is_404(P, fleet):
    assert _delete(P, fleet, "nope").status_code == 404


def test_delete_removes_every_verbatim_trace(P, fleet, tmp_path, monkeypatch):
    now = 1_800_000_000.0
    P._THREADS.record_turn("c1", "the zebra code is 1234", "noted the zebra code", ts=now, principal="fleet")
    P._THREADS.append_message("c1", "assistant", "Call ended (call_abc).", meta={"kind": "call_end", "call_id": "call_abc"},
                              ts=now + 5)
    P._THREADS.set_summary("c1", 2, "a zebra code")
    P._THREADS.record_turn("c2", "keep this chat", "kept", ts=now, principal="fleet")
    log = P.ARTURO_STATE / "unified-conversation.jsonl"
    _jsonl(log, [
        {"ts": _iso(now), "channel": "jarvis-voice", "role": "operator", "content": "the zebra code is 1234"},  # legacy
        {"ts": _iso(now), "channel": "jarvis-voice", "role": "system", "content": "stamped line", "conversation_id": "c1"},
        {"ts": _iso(now), "channel": "jarvis-voice", "role": "operator", "content": "keep this chat"},
        {"ts": _iso(now - 86400), "channel": "jarvis-voice", "role": "operator", "content": "the zebra code is 1234"},
        {"ts": _iso(now), "channel": "jarvis-voice", "role": "system", "content": "other chat", "conversation_id": "c2"},
    ])
    data = tmp_path / "data"
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    (data / "state").mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(data / "state" / "semantic-memory.db") as c:
        c.execute("CREATE TABLE queries_log (query_hash TEXT PRIMARY KEY, query_text TEXT NOT NULL, scope TEXT,"
                  " agent TEXT, ts REAL NOT NULL, returned_doc_ids TEXT)")
        c.execute("INSERT INTO queries_log VALUES ('a', 'the zebra code is 1234', NULL, 'arturo-voice', 1, '')")
        c.execute("INSERT INTO queries_log VALUES ('b', 'keep this chat', NULL, 'arturo-voice', 1, '')")
    P.VOICE_CALLS_DIR.mkdir(parents=True)
    (P.VOICE_CALLS_DIR / "call_abc.json").write_text("{}")
    (P.VOICE_CALLS_DIR / "call_other.json").write_text("{}")
    P._TEXT_HISTORY.append("c1", "user", "the zebra code is 1234")

    r = _delete(P, fleet, "c1")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["deleted"] == "c1" and "not_cleared" not in r.get_json()
    assert P._THREADS.get_thread("c1") is None and P._THREADS.get_thread("c2") is not None
    kept = [json.loads(x)["content"] for x in log.read_text().splitlines()]
    assert kept == ["keep this chat", "the zebra code is 1234", "other chat"]   # the day-old line is not this chat's
    with sqlite3.connect(data / "state" / "semantic-memory.db") as c:
        assert [r[0] for r in c.execute("SELECT query_text FROM queries_log")] == ["keep this chat"]
    assert not (P.VOICE_CALLS_DIR / "call_abc.json").exists() and (P.VOICE_CALLS_DIR / "call_other.json").exists()
    assert P._TEXT_HISTORY.get("c1") == []
    # logged by id, time and who; never the words
    entries = [json.loads(x) for x in (P.ARTURO_LOGS / "thread-deletes.jsonl").read_text().splitlines()]
    assert entries[0]["thread"] == "c1" and entries[0]["by"] == "fleet" and entries[0]["ts"]
    assert "zebra" not in (P.ARTURO_LOGS / "thread-deletes.jsonl").read_text()


def test_delete_keeps_learned_facts_unless_asked(P, fleet):
    P._THREADS.record_turn("c1", "call me Sam, I'm in Lisbon", "Hi Sam.", principal="fleet")
    P._THREADS.record_turn("c2", "another", "ok", principal="fleet")
    operator_store.set_fact(P.ARTURO_STATE, "timezone", "Europe/Lisbon", source_thread="c1")
    operator_store.set_fact(P.ARTURO_STATE, "role", "founder")                    # source unknown
    P.VOICE_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    P.VOICE_MEMORY_FILE.write_text(json.dumps({"notes": [
        {"note": "likes short answers", "created": "t1", "source_thread": "c1"},
        {"note": "an older note", "created": "t0"}]}))
    facts = _get(P, fleet, "/threads/c1?facts=1").get_json()
    assert facts["ok"] and facts["onboarding"] is False
    assert [(f["store"], f["value"]) for f in facts["facts"]] == [("operator", "Europe/Lisbon"),
                                                                  ("note", "likes short answers")]
    assert _delete(P, fleet, "c1").get_json()["facts_forgotten"] == 0
    assert operator_store.get(P.ARTURO_STATE, "timezone") == "Europe/Lisbon"
    # a second chat, deleted with forget_facts=1
    operator_store.set_fact(P.ARTURO_STATE, "pronouns", "they", source_thread="c2")
    assert _delete(P, fleet, "c2", "?forget_facts=1").get_json()["facts_forgotten"] == 1
    assert operator_store.get(P.ARTURO_STATE, "pronouns") is None
    assert operator_store.get(P.ARTURO_STATE, "role") == "founder"                # never auto-deleted


def test_forgetting_removes_the_chats_notes_only(P, fleet):
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    P.VOICE_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    P.VOICE_MEMORY_FILE.write_text(json.dumps({"notes": [
        {"note": "from c1", "created": "t1", "source_thread": "c1"}, {"note": "unknown source", "created": "t0"}]}))
    assert _delete(P, fleet, "c1", "?forget_facts=1").get_json()["facts_forgotten"] == 1
    assert [n["note"] for n in json.loads(P.VOICE_MEMORY_FILE.read_text())["notes"]] == ["unknown source"]


def test_deleting_the_onboarding_chat_never_resets_onboarding(P, fleet):
    P._THREADS.record_turn("c1", OPENER, "Hi, what should I call you?", principal="fleet")
    P._THREADS.record_turn("c1", "call me Sam", "Hi Sam.", principal="fleet")
    operator_store.set_fact(P.ARTURO_STATE, "name", "Sam", source_thread="c1")
    onb = P.ARTURO_STATE / "onboarding.json"
    onb.write_text('{"done": true}')
    facts = _get(P, fleet, "/threads/c1?facts=1").get_json()
    assert facts["onboarding"] is True and facts["facts"] == []
    assert _delete(P, fleet, "c1", "?forget_facts=1").status_code == 200
    assert operator_store.get(P.ARTURO_STATE, "name") == "Sam"
    assert onb.read_text() == '{"done": true}'


def test_facts_of_an_unknown_or_hidden_chat_is_404(P, fleet):
    P._THREADS.record_turn("web_1", "fleet plans", "ok", principal="fleet")
    assert _get(P, fleet, "/threads/nope?facts=1").status_code == 404
    assert _get(P, DEV_A, "/threads/web_1?facts=1").get_json()["thread"] is None   # reads as not there


def test_the_log_names_the_admin_who_asked(P, fleet):
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    assert _delete(P, fleet, "c1", "?by=ops@example.com").status_code == 200
    entry = json.loads((P.ARTURO_LOGS / "thread-deletes.jsonl").read_text().splitlines()[0])
    assert entry["by"] == "ops@example.com" and entry["thread"] == "c1"


def test_a_job_finishing_after_the_delete_does_not_bring_the_chat_back(P, fleet, tmp_path):
    from services.arturo.jobs import JobBoard
    runs = []
    P._JOBS = JobBoard(tmp_path / "jobs.json", on_done=P._post_job_result, spawn=runs.append)
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    P._JOBS.start("c1", "research", "look it up", lambda step: ("the findings", True))
    assert _delete(P, fleet, "c1").status_code == 200
    runs[0]()                                   # the job ends after the delete
    assert P._THREADS.get_thread("c1") is None
    # and a result posted by a job that slipped past the cancel is refused too
    P._post_job_result({"id": "job_x", "thread": "c1", "title": "t", "kind": "research", "status": "done",
                        "result": "late findings", "steps": []})
    assert P._THREADS.get_thread("c1") is None


def test_a_summary_finishing_after_the_delete_leaves_nothing(P, fleet, tmp_path):
    """The summarizer reads the messages, then asks the brain (seconds): a delete in between must win."""
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")

    def complete(**k):
        assert _delete(P, fleet, "c1").status_code == 200        # the chat is deleted while the summary is written
        return P._brain.make_response("a summary of a deleted chat", None, "stop")
    P.brain.complete = complete
    assert P._summarize_now("c1", 0, 2, "") is False
    with sqlite3.connect(tmp_path / "threads.db") as c:
        assert c.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 0


def test_the_live_call_file_goes_when_it_is_this_chats(P, fleet):
    P._THREADS.record_turn("c1", "the zebra code is 1234", "noted", principal="fleet")
    P._THREADS.record_turn("c2", "other", "ok", principal="fleet")
    live = P.ARTURO_STATE / "voice-live-call.json"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(json.dumps({"turns": [{"role": "user", "text": "other"}], "conversation_id": "c2"}))
    _delete(P, fleet, "c1")
    assert live.exists()                                                       # another chat's call
    live.write_text(json.dumps({"turns": [{"role": "user", "text": "other"}]}))   # unstamped, holds c2's line
    _delete(P, fleet, "c2")
    assert not live.exists()


def test_a_fact_said_again_in_another_chat_keeps_where_it_was_learned(P, fleet):
    operator_store.set_fact(P.ARTURO_STATE, "role", "founder")                    # source unknown
    operator_store.set_fact(P.ARTURO_STATE, "role", "founder", source_thread="c2")
    operator_store.set_fact(P.ARTURO_STATE, "timezone", "UTC", source_thread="c1")
    operator_store.set_fact(P.ARTURO_STATE, "timezone", "UTC", source_thread="c2")
    facts = operator_store.load(P.ARTURO_STATE)["facts"]
    assert "source_thread" not in facts["role"] and facts["timezone"]["source_thread"] == "c1"
    operator_store.set_fact(P.ARTURO_STATE, "timezone", "Europe/Lisbon", source_thread="c2")   # a new value
    assert operator_store.load(P.ARTURO_STATE)["facts"]["timezone"]["source_thread"] == "c2"


def test_delete_waits_for_no_turn(P, fleet):
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    token = P._TURN_LOCKS.acquire("c1")
    try:
        assert _delete(P, fleet, "c1").status_code == 409
    finally:
        token.release()
    assert P._THREADS.get_thread("c1") is not None


def test_delete_drops_the_chats_jobs(P, fleet, tmp_path):
    from services.arturo.jobs import JobBoard
    P._JOBS = JobBoard(tmp_path / "jobs.json", spawn=lambda fn: None)
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")
    P._JOBS.start("c1", "research", "look it up", lambda step: ("x", True))
    P._JOBS.start("c2", "research", "other", lambda step: ("x", True))
    assert _delete(P, fleet, "c1").status_code == 200
    assert P._JOBS.for_thread("c1") == [] and len(P._JOBS.for_thread("c2")) == 1


def test_a_fact_set_in_a_chat_records_the_chat(P, fleet, monkeypatch):
    monkeypatch.setattr(P, "_non_fleet_refusal", lambda name, turn: None)   # the tool itself, not who may call it
    token = P._CALLER_CONV.set("c9")
    try:
        assert P.execute_tool("set_operator_fact", {"field": "timezone", "value": "UTC"}).startswith("Recorded")
    finally:
        P._CALLER_CONV.reset(token)
    assert operator_store.load(P.ARTURO_STATE)["facts"]["timezone"]["source_thread"] == "c9"


def test_a_failed_delete_does_not_hold_the_chat_as_deleted(P, fleet, monkeypatch):
    P._THREADS.record_turn("c1", "q", "a", principal="fleet")

    def boom(cid):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(P._THREADS, "delete_thread", boom)
    assert _delete(P, fleet, "c1").status_code == 500
    assert not P._RECENTLY_DELETED.has("c1")
    assert P._THREADS.get_thread("c1") is not None


def test_recently_deleted_unmark():
    from services.arturo.thread_delete import RecentlyDeleted
    r = RecentlyDeleted()
    r.mark("c1")
    assert r.has("c1")
    r.unmark("c1")
    r.unmark("never")
    assert not r.has("c1")
