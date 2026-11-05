"""Provider-agnostic rotation, the store side (scripts/episodic_memory.py): provider sessions, seen-only entries,
seed boundaries, compaction notices, the automatic ingest tick and what a briefing reads. Neutral fixtures, a temp
ORCHESTRA_DIR; no real transcript is read."""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import episodic_memory as EM

BLOCK = "<earlier-conversation>\nEarlier: the Sample plan, step 2 is next.\n</earlier-conversation>\n"


def line(uuid, typ, content, sid, ts="2026-10-31T07:00:00Z", **extra):
    return json.dumps({"type": typ, "uuid": uuid, "sessionId": sid, "timestamp": ts,
                       "message": {"role": typ, "content": content}, **extra}) + "\n"


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    return tmp_path


def write(tmp_path, sid, text):
    p = tmp_path / f"{sid}.jsonl"
    p.write_text(text)
    return p


# ── provider sessions ────────────────────────────────────────────────────────────────────────────────────────────

def test_sessions_one_active_per_seat_states_and_reregister(box):
    a = EM.register_session("qa-demo", "claude", "sample-model", "sess-1", 1, "briefing")
    assert EM.active_session("qa-demo")["provider_session_id"] == "sess-1"
    b = EM.register_session("qa-demo", "claude", "sample-model", "sess-2", 2, "briefing")
    assert a != b
    s = {r["provider_session_id"]: r for r in EM.sessions_of("qa-demo")}
    assert s["sess-1"]["state"] == "inactive" and s["sess-1"]["reason"] == "replaced" and s["sess-1"]["ended_at"]
    assert s["sess-2"]["state"] == "active" and s["sess-2"]["generation"] == 2 and s["sess-2"]["seed_mode"] == "briefing"
    assert EM.register_session("qa-demo", "claude", "sample-model", "sess-2", 2, "briefing") == b, "same id again"
    EM.register_session("dev-demo", "claude", None, "sess-d", 1, "briefing")
    assert EM.active_session("qa-demo")["provider_session_id"] == "sess-2", "another seat's session is its own"
    EM.set_session_state("sess-2", "failed", reason="sample failure")
    assert EM.active_session("qa-demo") is None
    EM.set_session_state("sess-1", "active")
    assert EM.active_session("qa-demo")["provider_session_id"] == "sess-1"
    with pytest.raises(ValueError):
        EM.set_session_state("sess-1", "sleeping")
    with pytest.raises(KeyError):
        EM.set_session_state("no-such", "inactive")
    # the database itself refuses a second active row for a seat
    conn = sqlite3.connect(EM.default_db_path())
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE provider_sessions SET state = 'active' WHERE provider_session_id = 'sess-2'")
    conn.close()


# ── seen-only and seed rows ──────────────────────────────────────────────────────────────────────────────────────

def test_strip_seen_only_for_the_registered_entry(box, tmp_path):
    EM.mark_seen_only("sess-1", "u-brief")
    text = BLOCK + "What is next?"
    assert EM.strip_seen("sess-1", "u-brief", text) == "What is next?"
    assert EM.strip_seen("sess-1", "u-other", text) == text, "the same words in another entry stay"
    assert EM.strip_seen("sess-2", "u-brief", text) == text, "another session's entry with that uuid stays"
    assert EM.strip_block("a <earlier-conversation>x") == "a ", "an unclosed block runs to the end"
    assert EM.strip_block("plain") == "plain"


def test_ingest_strips_seen_blocks_and_marking_later_strips_rows_already_indexed(box, tmp_path):
    sid = "sess-1"
    log = write(tmp_path, sid,
                line("u1", "user", BLOCK + "What is next?", sid)
                + line("u2", "user", BLOCK + "I pasted this myself", sid)
                + line("u3", "user", BLOCK.strip(), sid))
    EM.mark_seen_only(sid, "u1")
    EM.mark_seen_only(sid, "u3")
    EM.ingest_session("qa-demo", sid, 1, log)
    assert EM.verify("qa-demo", sid, "What is next?")[0]
    assert EM.verify("qa-demo", sid, "Sample plan")[1]["uuid"] == "u2", "only the person's own entry keeps the words"
    assert [r["uuid"] for r in EM.query("Sample plan")] == ["u2"]
    assert not any(r["uuid"] == "u3" for r in EM.query("Sample")), "an entry that was only the block is not indexed"
    # an entry indexed before it was marked is stripped when it is marked
    sid2 = "sess-2"
    log2 = write(tmp_path, sid2, line("v1", "user", BLOCK + "Carry on", sid2))
    EM.ingest_session("qa-demo", sid2, 2, log2)
    assert EM.verify("qa-demo", sid2, "Sample plan")[0]
    EM.mark_seen_only(sid2, "v1")
    assert not EM.verify("qa-demo", sid2, "Sample plan")[0]
    assert EM.verify("qa-demo", sid2, "Carry on")[0]
    assert EM.query("Carry on")[0]["uuid"] == "v1", "the index follows the update"
    # idempotent: ingesting again adds nothing
    assert EM.ingest_session("qa-demo", sid, 1, log)["added"] == 0


def test_seed_rows_are_skipped_on_ingest_and_removed_when_the_boundary_comes_later(box, tmp_path):
    sid = "sess-s"
    EM.set_seed_boundary(sid, "seed-2")
    log = write(tmp_path, sid,
                line("seed-1", "user", "SEEDWORD one", sid) + line("seed-2", "assistant", [{"type": "text", "text": "SEEDWORD two"}], sid)
                + line("real-1", "user", "the real question", sid))
    assert EM.ingest_session("qa-demo", sid, 1, log)["added"] == 1
    assert EM.query("SEEDWORD") == [] and EM.query("real question")[0]["uuid"] == "real-1"
    with open(log, "a") as f:
        f.write(line("real-2", "user", "and another", sid))
    assert EM.ingest_session("qa-demo", sid, 1, log)["added"] == 1, "past the boundary, later reads keep everything"
    # a seed still being written (boundary not reached) indexes nothing
    sid2 = "sess-t"
    EM.set_seed_boundary(sid2, "seed-9")
    log2 = write(tmp_path, sid2, line("seed-8", "user", "SEEDWORD partial", sid2))
    assert EM.ingest_session("qa-demo", sid2, 1, log2)["added"] == 0
    # a boundary recorded after rows were indexed removes them
    sid3 = "sess-u"
    log3 = write(tmp_path, sid3, line("s1", "user", "LATESEED x", sid3) + line("s2", "user", "kept words", sid3))
    EM.ingest_session("qa-demo", sid3, 1, log3)
    EM.set_seed_boundary(sid3, "s1")
    assert EM.query("LATESEED") == [] and EM.query("kept words")


def test_ingest_tick_reads_the_active_session_and_the_previous_tail_skipping_seed_rows(box, tmp_path):
    files = {}
    def locate(sid):
        return files.get(sid)
    old = "sess-old"
    files[old] = write(tmp_path, old, line("o1", "user", "OLDWORD early", old))
    EM.register_session("qa-demo", "claude", None, old, 1, "briefing")
    t1 = EM.ingest_tick("qa-demo", locate=locate)
    assert t1["added"] == 1 and t1["lag_bytes"] == 0
    with open(files[old], "a") as f:
        f.write(line("o2", "user", "OLDWORD tail written at the switch", old))
    new = "sess-new"
    EM.set_seed_boundary(new, "n-seed")
    files[new] = write(tmp_path, new, line("n-seed", "user", "SEEDWORD", new) + line("n1", "user", "NEWWORD first", new))
    EM.register_session("qa-demo", "claude", None, new, 2, "seeded_file")
    t2 = EM.ingest_tick("qa-demo", locate=locate)
    assert t2["added"] == 2, "the new session's real row and the old session's tail"
    assert [s["provider_session_id"] for s in t2["sessions"]] == [new, old]
    assert EM.query("SEEDWORD") == []
    assert EM.query("NEWWORD")[0]["generation"] == 2
    # lag: a half-written line is not yet indexed, and stats says so
    with open(files[new], "a") as f:
        f.write('{"type": "user", "uuid": "n2"')
    t3 = EM.ingest_tick("qa-demo", locate=locate)
    assert t3["lag_bytes"] == len('{"type": "user", "uuid": "n2"')
    lag = EM.stats()["ingest_lag"]["qa-demo"]
    assert lag["lag_bytes"] == t3["lag_bytes"] and lag["last_tick"] == t3["at"] and lag["last_error"] is None
    # a missing file is reported, not raised
    files.pop(new)
    t4 = EM.ingest_tick("qa-demo", locate=locate)
    assert "transcript not found" in t4["sessions"][0]["error"]
    assert "transcript not found" in EM.stats()["ingest_lag"]["qa-demo"]["last_error"]
    assert EM.ingest_tick("pm-demo", locate=locate)["sessions"] == [], "a seat with no sessions: nothing to read"


# ── the compaction notice ────────────────────────────────────────────────────────────────────────────────────────

def test_notices_start_finish_tokens_and_order(box):
    a = EM.notice_start("qa-demo", "sess-1", 150_000)
    b = EM.notice_start("qa-demo", "sess-2", 160_000)
    EM.notice_start("dev-demo", "sess-d", 1)
    n = EM.notices("qa-demo")
    assert [x["id"] for x in n] == [a, b], "oldest first, this seat only"
    assert n[0]["state"] == "compacting" and n[0]["finished_at"] is None and n[0]["before_tokens"] == 150_000
    EM.notice_finish(a, "compacted", to_session="sess-2")
    got = EM.notices("qa-demo")[0]
    assert got["state"] == "compacted" and got["finished_at"] and got["to_session"] == "sess-2" and got["after_tokens"] is None
    EM.notice_set_tokens(a, 40_000)
    assert EM.notices("qa-demo")[0]["after_tokens"] == 40_000 and EM.notices("qa-demo")[0]["state"] == "compacted"
    EM.notice_finish(b, "failed")
    assert EM.notices("qa-demo")[1]["state"] == "failed"
    with pytest.raises(ValueError):
        EM.notice_finish(b, "compacting")
    with pytest.raises(KeyError):
        EM.notice_finish(999, "failed")
    with pytest.raises(KeyError):
        EM.notice_set_tokens(999, 1)


# ── what a briefing reads ────────────────────────────────────────────────────────────────────────────────────────

def test_recent_window_operator_messages_open_records_and_wakeups(box, tmp_path):
    sid = "sess-1"
    todo1 = {"todos": [{"content": "Write the Sample note", "status": "completed"}, {"content": "Check the Example page", "status": "pending"}]}
    log = write(tmp_path, sid,
                line("u1", "user", "Always answer in short lines.", sid, ts="2026-10-31T07:00:00Z")
                + line("m1", "user", "[MSG from pm-demo | low] mail, not typed", sid, ts="2026-10-31T07:00:01Z")
                + line("a1", "assistant", [{"type": "tool_use", "id": "t-todo", "name": "TodoWrite", "input": todo1},
                                           {"type": "tool_use", "id": "t-task", "name": "TaskCreate", "input": {"subject": "Draft the Example reply"}},
                                           {"type": "tool_use", "id": "t-cron", "name": "CronCreate", "input": {"cron": "*/30 * * * *", "prompt": "check the Sample feed"}},
                                           {"type": "tool_use", "id": "t-wake", "name": "ScheduleWakeup", "input": {"delaySeconds": 600, "prompt": "look again"}},
                                           {"type": "tool_use", "id": "t-mon", "name": "Monitor", "input": {"command": "tail -f sample.log"}}],
                       sid, ts="2026-10-31T07:00:02Z")
                + line("r1", "user", [{"type": "tool_result", "tool_use_id": "t-cron", "content": "Scheduled job id cron-abc123"}], sid, ts="2026-10-31T07:00:03Z")
                + line("a2", "assistant", [{"type": "tool_use", "id": "t-del", "name": "CronDelete", "input": {"id": "cron-abc123"}}], sid, ts="2026-10-31T07:00:04Z")
                + line("u2", "user", "Now do the second option.", sid, ts="2026-10-31T07:00:05Z")
                + line("a3", "assistant", [{"type": "text", "text": "Doing the second option now."}], sid, ts="2026-10-31T07:00:06Z"))
    EM.register_session("qa-demo", "claude", None, sid, 1, "briefing")
    EM.ingest_session("qa-demo", sid, 1, log)
    ops = EM.operator_messages("qa-demo")
    assert [o["uuid"] for o in ops] == ["u1", "u2"], "typed by the operator, oldest first; mail is not"
    w = EM.recent_window("qa-demo", 60)
    assert w[-1]["uuid"] == "a3" and all(x["ts"] <= w[-1]["ts"] for x in w), "oldest first, newest last"
    assert sum(len(x["text"]) for x in w) <= 60 or len(w) == 1
    assert EM.recent_window("qa-demo", 1)[-1]["uuid"] == "a3", "the newest entry always, even over budget"
    rec = EM.open_records("qa-demo", approvals_db=tmp_path / "none.db")
    assert [t["text"] for t in rec["tasks"]] == ["Check the Example page", "Draft the Example reply"]
    assert all(t["id"] for t in rec["tasks"])
    wk = EM.wakeups("qa-demo")
    assert [(x["tool"], x["tool_id"]) for x in wk] == [("ScheduleWakeup", "t-wake"), ("Monitor", "t-mon")], "the deleted cron is gone"
    assert wk[0]["schedule"] == 600 and wk[0]["prompt"] == "look again" and wk[0]["created_at"] == "2026-10-31T07:00:02Z"
    assert EM.wakeups("dev-demo") == [], "no active session: nothing"


def test_open_records_reads_pending_cards_for_this_seat_only(box, tmp_path):
    db = tmp_path / "tasks.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE approval_requests (id TEXT PRIMARY KEY, from_agent TEXT, question TEXT, status TEXT, created_at TEXT)")
    c.executemany("INSERT INTO approval_requests VALUES (?, ?, ?, ?, ?)", [
        ("apr_1", "qa-demo", "Which size?", "pending", "2026-10-31T06:00:00Z"),
        ("apr_2", "qa-demo", "Old one", "answered", "2026-10-31T05:00:00Z"),
        ("apr_3", "dev-demo", "Not mine", "pending", "2026-10-31T06:00:00Z")])
    c.commit(); c.close()
    rec = EM.open_records("qa-demo", approvals_db=db)
    assert rec["approvals"] == [{"id": "apr_1", "question": "Which size?", "status": "pending", "created_at": "2026-10-31T06:00:00Z"}]
    assert rec["tasks"] == []


def test_tables_are_created_idempotently(box):
    EM.connect().close()
    EM.connect().close()
    conn = sqlite3.connect(EM.default_db_path())
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()
    assert {"provider_sessions", "seen_only", "seed_boundary", "compaction_notices", "ingest_state",
            "provider_sessions_one_active"} <= names


def test_a_check_copy_is_recorded_never_active_and_never_read_as_the_seats(box, tmp_path):
    files = {}
    real = "sess-real"
    files[real] = write(tmp_path, real, line("r1", "user", "REALWORD from the seat", real))
    EM.register_session("qa-demo", "claude", None, real, 1, "briefing")
    copy = "sess-copy"
    files[copy] = write(tmp_path, copy, line("c1", "user", "COPYWORD from the check", copy))
    cid = EM.register_session("qa-demo", "claude", None, copy, 2, "system_prompt", state="failed", reason="check copy",
                              prompt_chars=180_000, cost_tokens=52_000)
    assert EM.active_session("qa-demo")["provider_session_id"] == real, "a check copy never displaces the active one"
    row = next(r for r in EM.sessions_of("qa-demo") if r["provider_session_id"] == copy)
    assert (row["id"], row["state"], row["reason"], row["was_active"], row["prompt_chars"], row["cost_tokens"]) == \
        (cid, "failed", "check copy", 0, 180_000, 52_000)
    with pytest.raises(EM.NeverActive):
        EM.ingest_session("qa-demo", copy, 2, files[copy])
    t = EM.ingest_tick("qa-demo", locate=files.get)
    assert [s["provider_session_id"] for s in t["sessions"]] == [real], "the tick never reads a check copy"
    # even rows that got in some other way are ignored by every reader
    conn = sqlite3.connect(EM.default_db_path())
    conn.execute("INSERT INTO entries(agent, session_id, generation, uuid, part, role, kind, text) "
                 "VALUES ('qa-demo', 'sess-copy', 2, 'c1', 0, 'user', 'message', 'COPYWORD stray')")
    conn.commit(); conn.close()
    assert EM.query("COPYWORD") == []
    assert EM.verify("qa-demo", copy, "COPYWORD") == (False, None)
    assert not any("COPYWORD" in r["text"] for r in EM.recent_window("qa-demo", 10_000))
    assert not any("COPYWORD" in r["text"] for r in EM.operator_messages("qa-demo"))
    assert EM.query("REALWORD")[0]["session_id"] == real
    # costs filled in later; a session made active once is the seat's from then on
    EM.set_session_cost(real, prompt_chars=1000, cost_tokens=300)
    r = next(x for x in EM.sessions_of("qa-demo") if x["provider_session_id"] == real)
    assert (r["prompt_chars"], r["cost_tokens"], r["was_active"]) == (1000, 300, 1)
    with pytest.raises(KeyError):
        EM.set_session_cost("no-such", cost_tokens=1)
