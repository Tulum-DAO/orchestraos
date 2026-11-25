"""The activity collector records who really wrote a CLI turn, and cites the turn by a stable id.

Synthetic transcripts only. A pane's "user" turn is not always a person: hooks, harness notices, headless
runs, subagents, accepted model suggestions and text typed in by other processes all arrive as user turns.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect_activity as C  # noqa: E402

PROJECT = "-work-example-seat"
SESSION = "11111111-2222-3333-4444-555555555555.jsonl"


def turn(uuid, text, **meta):
    o = {"type": "user", "uuid": uuid, "timestamp": "2026-11-01T10:00:00.000Z",
         "message": {"role": "user", "content": text}}
    o.update(meta)
    return o


TYPED = {"entrypoint": "cli", "promptSource": "typed", "origin": {"kind": "human"}}
TURNS = [
    turn("u-typed", "keep the dark theme on every page", **TYPED),
    turn("u-hook", "the build is red, fix it first", isMeta=True, entrypoint="cli"),
    turn("u-headless", "summarise these notes into three bullets", entrypoint="sdk-cli", promptSource="sdk"),
    turn("u-sidechain", "search the repo for the config loader", isSidechain=True),
    turn("u-notify", "background task finished", entrypoint="cli", promptSource="system",
         origin={"kind": "task-notification"}),
    turn("u-suggest", "run the tests again", entrypoint="cli", promptSource="suggestion_accepted",
         origin={"kind": "human"}),
    turn("u-init", "You are example-seat. Read /tmp/agent-init-example-seat.md and follow it.", **TYPED),
    turn("u-stop", "Stop hook feedback:\ncontinue", **TYPED),
    turn("u-old", "an older transcript line with no origin fields"),
    turn("u-longmsg", "[LONG-MSG chip-dodge] Voice notes for the client call: lead with pricing", **TYPED),
    turn("u-hydrate", "ingest your hydrate digest at /tmp/x.md", **TYPED),
    turn("u-approval", "[APPROVAL RESOLVED apr_1] approved: ship it", **TYPED),
    turn("u-voice", "Voice call ended: the caller asked for the status of the build", **TYPED),
    turn("u-inbox", "Check your inbox: 2 new messages", **TYPED),
    turn("u-resumed", "You are example-seat, resumed (session 0000). Carry on.", **TYPED),
    turn("u-continue", "continue", **TYPED),
]
EXPECT = {"u-typed": "shaw", "u-hook": "hook", "u-headless": "headless", "u-sidechain": "subagent",
          "u-notify": "system", "u-suggest": "model-suggestion", "u-init": "injected", "u-stop": "injected",
          "u-old": "unknown", "u-longmsg": "shaw", "u-hydrate": "injected", "u-approval": "injected",
          "u-voice": "injected", "u-inbox": "injected", "u-resumed": "injected", "u-continue": "shaw"}


def write_transcript(root, turns, lead=()):
    d = root / PROJECT
    d.mkdir(parents=True, exist_ok=True)
    p = d / SESSION
    p.write_text("".join(json.dumps(t) + "\n" for t in list(lead) + list(turns)))
    return str(p)


def by_uuid(facts):
    return {f["citation"]["uuid"]: f for f in facts}


def test_full_read_records_the_real_author(tmp_path):
    facts = by_uuid(C.extract_transcript_facts(write_transcript(tmp_path, TURNS), "example-seat"))
    assert {u: f["actor"] for u, f in facts.items()} == EXPECT


def test_incremental_read_records_the_same_author(tmp_path):
    path = write_transcript(tmp_path, TURNS)
    facts, _entry, mode = C.read_file_incremental(path, "example-seat", None)
    assert mode == "from0"
    assert {u: f["actor"] for u, f in by_uuid(facts).items()} == EXPECT
    full = C.extract_transcript_facts(path, "example-seat")
    assert [(f["citation"], f["source_key"]) for f in facts] == [(f["citation"], f["source_key"]) for f in full]


def test_citation_is_transcript_and_message_uuid(tmp_path):
    f = by_uuid(C.extract_transcript_facts(write_transcript(tmp_path, TURNS), "example-seat"))["u-typed"]
    assert f["citation"] == {"transcript": f"{PROJECT}/{SESSION}", "uuid": "u-typed", "part": 0}
    assert f["source_key"] == "cli:u-typed:0"
    assert str(tmp_path) not in json.dumps(f)          # no path above the project dir in the row


def test_citation_survives_lines_added_before_the_turn(tmp_path):
    a = by_uuid(C.extract_transcript_facts(write_transcript(tmp_path / "a", TURNS), "s"))
    lead = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}] * 5
    b = by_uuid(C.extract_transcript_facts(write_transcript(tmp_path / "b", TURNS, lead=lead), "s"))
    assert {u: (f["citation"], f["source_key"]) for u, f in a.items()} == \
           {u: (f["citation"], f["source_key"]) for u, f in b.items()}


def test_each_text_block_of_one_message_has_its_own_part(tmp_path):
    t = turn("u-multi", "", **TYPED)
    t["message"]["content"] = [{"type": "text", "text": "first block of the message"},
                               {"type": "tool_result", "content": "ignored"},
                               {"type": "text", "text": "second block of the message"}]
    facts = C.extract_transcript_facts(write_transcript(tmp_path, [t]), "s")
    assert [(f["citation"]["part"], f["source_key"]) for f in facts] == \
           [(0, "cli:u-multi:0"), (1, "cli:u-multi:1")]


def test_a_turn_without_uuid_has_no_source_key(tmp_path):
    t = turn(None, "a line written before turns carried ids", **TYPED)
    del t["uuid"]
    (f,) = C.extract_transcript_facts(write_transcript(tmp_path, [t]), "s")
    assert f["citation"]["uuid"] is None
    assert f["source_key"] is None


def test_other_surfaces_keep_their_row_shape(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text(json.dumps({"dir": "inbound", "ts": "2026-11-01T10:00:00Z", "text": "ship it tonight"}) + "\n")
    (f,) = C.collect_jsonl(str(p), "telegram", lambda o: o.get("dir") == "inbound", "ts", "text", "bot")
    assert f == {"ts": "2026-11-01T10:00:00Z", "surface": "telegram", "agent": "bot", "actor": "shaw",
                 "kind": "message", "text": "ship it tonight"}


def test_data_dir_comes_from_orchestra_dir(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    try:
        mod = importlib.reload(C)
        assert mod.FACTS_DIR == os.path.join(str(tmp_path), "dream", "facts")
        assert mod.CHECKPOINT_PATH == os.path.join(str(tmp_path), "dream", ".collect-checkpoint.json")
    finally:
        monkeypatch.delenv("ORCHESTRA_DIR")
        importlib.reload(C)
