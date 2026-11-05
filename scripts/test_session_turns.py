"""Each CLI's saved conversation reads as the same turns, and a briefing is built from any of them."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import briefing, session_turns as ST  # noqa: E402

FIXTURES = ROOT / "contract" / "transcript" / "fixtures"


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_codex_rollout_turns_in_ordinal_order_without_its_own_context():
    turns = ST.turns("codex-rollout", FIXTURES / "codex-basic.input.jsonl")
    assert turns[0][:1] == ("user",) and turns[0][2] == "Please check the status."
    assert any("[ran exec_command git status]" in t[2] for t in turns if t[0] == "assistant")
    assert all(t[0] != "developer" for t in turns)


def test_codex_envelopes_are_not_the_work(tmp_path):
    p = _jsonl(tmp_path / "rollout.jsonl", [
        {"ordinal": 2, "timestamp": "t2", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Fix the test"}]}},
        {"ordinal": 1, "timestamp": "t1", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
    ])
    assert ST.turns("codex-rollout", p) == [("user", "t2", "Fix the test")]


def test_antigravity_steps_unwrap_the_request_and_show_tools():
    turns = ST.turns("antigravity-steps", FIXTURES / "antigravity-basic.input.jsonl")
    assert turns[0] == ("user", "2026-08-27T11:00:00Z", "Check the queue")
    assert turns[1][0] == "assistant"
    assert "[ran run_command ls queue]" in turns[1][2] and "Queue is empty." in turns[1][2]


def test_grok_chunks_join_into_one_turn(tmp_path):
    def upd(su, **kw):
        return {"params": {"update": {"sessionUpdate": su, **kw}, "_meta": {"agentTimestampMs": 1793217620000}}}
    p = _jsonl(tmp_path / "updates.jsonl", [
        upd("user_message_chunk", content={"text": "<system-reminder>x</system-reminder>"}),
        upd("user_message_chunk", content={"text": "List the files"}),
        upd("agent_message_chunk", content={"text": "Sure, "}),
        upd("agent_message_chunk", content={"text": "listing."}),
        upd("tool_call", title="bash", rawInput={"command": "ls"}),
    ])
    turns = ST.turns("grok-updates", p)
    assert [t[0] for t in turns] == ["user", "assistant"]
    assert turns[0][2] == "List the files"
    assert turns[1][2] == "Sure, listing. [ran bash ls]"
    assert turns[0][1].startswith("2026-")


def _pi_session(path):
    return _jsonl(path, [
        {"type": "session", "version": 3, "id": "s-1", "timestamp": "2026-10-29T10:00:00.000Z", "cwd": "/srv/example"},
        {"type": "message", "id": "a1", "parentId": None, "timestamp": "2026-10-29T10:00:01.000Z",
         "message": {"role": "user", "content": "Add a test", "timestamp": 1}},
        # an abandoned branch: /tree went back to a1 and continued from there
        {"type": "message", "id": "b1", "parentId": "a1", "timestamp": "2026-10-29T10:00:02.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Old approach"}]}},
        {"type": "message", "id": "c1", "parentId": "a1", "timestamp": "2026-10-29T10:00:03.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "hmm"},
             {"type": "text", "text": "Writing it."},
             {"type": "toolCall", "id": "t1", "name": "write", "arguments": {"path": "test_x.py"}}]}},
        {"type": "message", "id": "c2", "parentId": "c1", "timestamp": "2026-10-29T10:00:04.000Z",
         "message": {"role": "toolResult", "toolCallId": "t1", "toolName": "write", "content": [], "isError": False}},
        {"type": "model_change", "id": "c3", "parentId": "c2", "timestamp": "2026-10-29T10:00:05.000Z",
         "provider": "example", "modelId": "sample-1"},
        {"type": "message", "id": "c4", "parentId": "c3", "timestamp": "2026-10-29T10:00:06.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ])


def test_pi_reads_the_current_branch_only(tmp_path):
    turns = ST.turns("pi-jsonl", _pi_session(tmp_path / "s.jsonl"))
    assert turns == [
        ("user", "2026-10-29T10:00:01.000Z", "Add a test"),
        ("assistant", "2026-10-29T10:00:03.000Z", "Writing it. [ran write test_x.py] Done."),
    ]


def test_sniff_tells_the_formats_apart_and_leaves_claude_alone(tmp_path):
    assert ST.sniff(FIXTURES / "codex-basic.input.jsonl") == "codex-rollout"
    assert ST.sniff(FIXTURES / "antigravity-basic.input.jsonl") == "antigravity-steps"
    assert ST.sniff(_pi_session(tmp_path / "pi.jsonl")) == "pi-jsonl"
    assert ST.sniff(_jsonl(tmp_path / "g.jsonl", [{"params": {"update": {"sessionUpdate": "x"}}}])) == "grok-updates"
    assert ST.sniff(FIXTURES / "claude-basic.input.jsonl") is None
    assert ST.sniff(tmp_path / "missing.jsonl") is None


def test_unreadable_input_is_no_turns(tmp_path):
    (tmp_path / "torn.jsonl").write_text('{"type": "message", "id"\n')
    assert ST.turns("pi-jsonl", tmp_path / "torn.jsonl") == []
    assert ST.turns("nope", tmp_path / "torn.jsonl") == []
    assert ST.turns("pi-jsonl", None) == []


# ---- briefings from any agent ---------------------------------------------------------------------

def test_briefing_reads_a_pi_session_and_skips_the_boot_prompt(tmp_path):
    p = _pi_session(tmp_path / "s.jsonl")
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    rows.insert(1, {"type": "message", "id": "a0", "parentId": None, "timestamp": "2026-10-29T09:59:59.000Z",
                    "message": {"role": "user", "content": "You are dev-demo. Read /srv/prompts/dev-demo.md first."}})
    rows[2]["parentId"] = "a0"
    _jsonl(p, rows)
    turns = briefing.text_turns(p)
    assert turns[0][2] == "Add a test"
    assert turns[-1][2].endswith("Done.")


def test_briefing_still_reads_claude_sessions_its_own_way():
    turns = briefing.text_turns(FIXTURES / "claude-basic.input.jsonl")
    assert turns and all(r in ("user", "assistant") for r, _, _ in turns)


def test_briefing_finds_a_codex_seats_log_through_its_profile(tmp_path):
    sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    data = tmp_path / "data"
    data.mkdir()
    (data / "registry.json").write_text(json.dumps(
        {"agents": {"dev-demo": {"runtime": "codex", "session_id": sid, "tmux_session": "dev-demo"}}}))
    day = tmp_path / ".codex" / "sessions" / "2026" / "10" / "29"
    day.mkdir(parents=True)
    log = day / f"rollout-2026-10-29T10-00-00-{sid}.jsonl"
    log.write_text((FIXTURES / "codex-basic.input.jsonl").read_text())
    assert briefing.transcript_path("dev-demo", orchestra_dir=data, home=tmp_path) == log
    assert "Please check the status." in briefing.last_line("dev-demo", orchestra_dir=data, home=tmp_path)


def test_briefing_for_a_claude_seat_is_unchanged(tmp_path):
    sid = "11111111-2222-4333-8444-555555555555"
    data = tmp_path / "data"
    data.mkdir()
    (data / "registry.json").write_text(json.dumps(
        {"agents": {"pm-demo": {"session_id": sid, "cwd": "/srv/example"}}}))
    proj = tmp_path / ".claude" / "projects" / "-srv-example"
    proj.mkdir(parents=True)
    (proj / f"{sid}.jsonl").write_text("{}\n")
    assert briefing.transcript_path("pm-demo", orchestra_dir=data, home=tmp_path) == proj / f"{sid}.jsonl"
