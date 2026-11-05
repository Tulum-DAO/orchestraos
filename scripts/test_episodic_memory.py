"""The conversation index (scripts/episodic_memory.py): fixture transcripts with neutral names, a temp data dir."""
from __future__ import annotations

import json
import os
import sqlite3
import stat

import pytest

from scripts import episodic_memory as EM

SID = "11111111-2222-3333-4444-555555555555"


def line(uuid, typ, content, ts="2026-10-31T02:00:00Z", **extra):
    return json.dumps({"type": typ, "uuid": uuid, "sessionId": SID, "timestamp": ts,
                       "message": {"role": typ, "content": content}, **extra}) + "\n"


TURN = (
    line("u1", "user", "Please check the Sample report for the Example token")
    + line("a1", "assistant", [{"type": "text", "text": "Reading it now."},
                                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/tmp/sample.md"}}])
    + line("r1", "user", [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "the Sample report says ZEBRA-7731 twice"}])
    + line("a2", "assistant", [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": "The report names ZEBRA-7731."}])
)


@pytest.fixture
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    log = tmp_path / f"{SID}.jsonl"
    log.write_text(TURN)
    return tmp_path, log


def test_ingest_tags_round_trip_and_kinds(box):
    _, log = box
    rep = EM.ingest_session("qa-demo", SID, 3, log)
    assert rep["added"] == 5                                     # thinking is not indexed
    rows = EM.query("ZEBRA-7731")
    assert {r["kind"] for r in rows} == {"tool_result", "message"}
    res = next(r for r in rows if r["kind"] == "tool_result")
    assert (res["agent"], res["session_id"], res["generation"], res["uuid"], res["part"], res["tool_id"], res["role"]) == \
        ("qa-demo", SID, 3, "r1", 0, "toolu_1", "user")
    call = EM.query("sample.md")[0]
    assert call["kind"] == "tool_call" and call["tool_id"] == "toolu_1" and call["uuid"] == "a1" and call["part"] == 1
    assert "[" in res["snippet"] and "]" in res["snippet"], "the match is marked in the snippet"


def test_reingest_is_idempotent_and_append_is_incremental(box):
    _, log = box
    assert EM.ingest_session("qa-demo", SID, 3, log)["added"] == 5
    again = EM.ingest_session("qa-demo", SID, 3, log)
    assert again["added"] == 0 and again["lines"] == 0, "nothing new: nothing read"
    size = log.stat().st_size
    with open(log, "a") as f:
        f.write(line("u2", "user", "One more: the Example checklist"))
        f.write('{"type": "assistant", "uuid": "partial"')           # a line still being written
    rep = EM.ingest_session("qa-demo", SID, 3, log)
    assert rep["added"] == 1 and rep["lines"] == 1
    assert rep["offset"] > size and rep["offset"] < log.stat().st_size, "the partial line waits for its newline"
    assert EM.stats()["entries"] == 6
    # a file replaced (another inode) is read from its start; rows already there are not doubled
    log.unlink(); log.write_text(TURN)
    assert EM.ingest_session("qa-demo", SID, 3, log)["added"] == 0
    assert EM.stats()["entries"] == 6


def test_verify_finds_a_planted_token_in_that_session_only(box):
    _, log = box
    EM.ingest_session("qa-demo", SID, 3, log)
    ok, row = EM.verify("qa-demo", SID, "ZEBRA-7731")
    assert ok and row["uuid"] == "a2", "the newest row holding it"
    assert EM.verify("qa-demo", SID, "NOT-THERE-0000") == (False, None)
    assert EM.verify("dev-demo", SID, "ZEBRA-7731") == (False, None), "another agent's session does not count"
    assert EM.verify("qa-demo", "other-session", "ZEBRA-7731") == (False, None)
    assert EM.verify("qa-demo", SID, "") == (False, None)


def test_query_filters_by_agent_session_and_generation(box, tmp_path):
    _, log = box
    EM.ingest_session("qa-demo", SID, 3, log)
    other = tmp_path / "other.jsonl"
    other.write_text(line("x1", "user", "ZEBRA-7731 is in the Example plan too").replace(SID, "sess-2"))
    EM.ingest_session("dev-demo", "sess-2", 1, other)
    assert {r["agent"] for r in EM.query("ZEBRA-7731")} == {"qa-demo", "dev-demo"}
    assert {r["agent"] for r in EM.query("ZEBRA-7731", agent="dev-demo")} == {"dev-demo"}
    assert {r["session_id"] for r in EM.query("ZEBRA-7731", session=SID)} == {SID}
    assert {r["generation"] for r in EM.query("ZEBRA-7731", generation=1)} == {1}
    assert EM.query("ZEBRA-7731", agent="pm-demo") == []
    assert len(EM.query("ZEBRA-7731", limit=1)) == 1
    assert EM.query("") == []
    # FTS syntax in a question is read as words, not operators
    assert EM.query('"Sample" report:'), 'quotes and a colon are read as part of the words'
    assert EM.query('-dash: OR (paren') == [], 'operators are words: no syntax error'
    assert EM.query("sample report")[0]["uuid"] in ("u1", "r1")


def test_decisions_are_their_own_kind(box, tmp_path):
    _, log = box
    with open(log, "a") as f:
        f.write(line("d1", "user", "[DECISION ANSWERED apr_sample_1] 'Which size?' -> Small"))
    EM.ingest_session("qa-demo", SID, 3, log)
    r = EM.query("Which size")[0]
    assert r["kind"] == "decision" and r["uuid"] == "d1"


def test_store_is_private_and_ready_for_embeddings(box, tmp_path):
    _, log = box
    EM.ingest_session("qa-demo", SID, 3, log)
    db = EM.default_db_path()
    assert db == tmp_path / "data" / "state" / "episodic-memory.db"
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(embeddings)")}
    assert {"entry_id", "model", "dim", "vector"} <= cols
    assert conn.execute("SELECT count(*) FROM embeddings").fetchone()[0] == 0
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "1"
    conn.close()
    assert EM.stats()["embeddings"] == 0


def test_codex_and_gemini_logs_are_refused_clearly(box, tmp_path):
    codex = tmp_path / "rollout.jsonl"
    codex.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
    with pytest.raises(EM.UnsupportedTranscript, match="Codex"):
        EM.ingest_session("dev-demo", "sess-c", 1, codex)
    other = tmp_path / "transcript.jsonl"
    other.write_text(json.dumps({"role": "model", "parts": [{"text": "hi"}]}) + "\n")
    with pytest.raises(EM.UnsupportedTranscript, match="not a Claude Code transcript"):
        EM.ingest_session("dev-demo", "sess-g", 1, other)
    assert EM.stats()["entries"] == 0, "a refused file leaves nothing behind"
    with pytest.raises(FileNotFoundError):
        EM.ingest_session("dev-demo", "sess-x", 1, tmp_path / "missing.jsonl")


def test_a_huge_tool_result_is_kept_to_its_start(box, tmp_path):
    big = tmp_path / "big.jsonl"
    big.write_text(line("r9", "user", [{"type": "tool_result", "tool_use_id": "toolu_9", "content": "MARKER " + "x" * 100_000}]).replace(SID, "sess-b"))
    EM.ingest_session("qa-demo", "sess-b", 1, big)
    ok, row = EM.verify("qa-demo", "sess-b", "MARKER")
    assert ok and len(row["text"]) < 40_000 and row["text"].endswith("[cut: the rest was not indexed]")


def test_cli_ingest_and_search(box, tmp_path, monkeypatch, capsys):
    _, log = box
    from orchestra_cli.__main__ import main
    assert main(["memory", "ingest", "qa-demo", "--session", SID, "--generation", "3", "--path", str(log), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"added": 5, "offset": log.stat().st_size, "lines": 4, "agent": "qa-demo", "session": SID, "generation": 3}
    assert main(["memory", "search", "ZEBRA-7731", "--agent", "qa-demo", "--generation", "3", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all(r["agent"] == "qa-demo" and r["generation"] == 3 for r in rows)
    assert main(["memory", "search", "ZEBRA-7731"]) == 0
    text = capsys.readouterr().out
    assert "qa-demo · session" in text and "tool toolu_1" in text
    assert main(["memory", "search", "nothing-like-this"]) == 0
    assert capsys.readouterr().out.strip() == "no matches"
    # the transcript found from the session id (a fixture config dir), the generation from the identity store
    proj = tmp_path / "claude" / "projects" / "-sample"
    proj.mkdir(parents=True)
    (proj / "sess-cli.jsonl").write_text(line("c1", "user", "the Example handoff").replace(SID, "sess-cli"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    reg = tmp_path / "data" / "state" / "orchestra-registry.db"
    c = sqlite3.connect(reg)
    c.execute("CREATE TABLE generations (id INTEGER PRIMARY KEY, generation INTEGER, session_id TEXT)")
    c.execute("INSERT INTO generations(generation, session_id) VALUES (7, 'sess-cli')")
    c.commit(); c.close()
    assert main(["memory", "ingest", "pm-demo", "--session", "sess-cli"]) == 0
    assert "generation 7" in capsys.readouterr().out
    assert EM.query("handoff")[0]["generation"] == 7
    # refusals exit 2 with a reason
    assert main(["memory", "ingest", "pm-demo", "--session", "no-such-session"]) == 2
    assert "no transcript found" in capsys.readouterr().err
