"""Rotation adapters (scripts/rotation_adapters/) and the switch rules in headless_rotation.py."""
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)
import headless_rotation as HR  # noqa: E402
import headless_switch as HS  # noqa: E402
from rotation_adapters import RotationUnsupported, adapter_for, capability_diff, refusal  # noqa: E402
from rotation_adapters.claude import ClaudeAdapter, fenced_env, fresh_argv, normalise  # noqa: E402


@pytest.mark.parametrize("rt", ["codex", "gemini", "grok", "something-else"])
def test_every_other_runtime_refuses_with_a_plain_reason(rt):
    with pytest.raises(RotationUnsupported) as e:
        adapter_for(rt)
    assert e.value.reason == f"{rt} rotation is not supported yet: its adapter isn't built and tested"
    assert refusal(rt) == e.value.reason and refusal("claude") is None
    for mod in ("codex", "gemini", "grok"):
        m = __import__(f"rotation_adapters.{mod}", fromlist=["x"])
        cls = getattr(m, mod.capitalize() + "Adapter")
        assert cls.startup_slot is None
        with pytest.raises(RotationUnsupported):
            cls()


def test_the_claude_adapter_declares_its_slot_and_builds_the_identical_command_line():
    a = adapter_for("claude", argv_for=lambda s, m=None: ["claude", "-p", "--model", m or "sample-model",
                                                         "--settings", "s.json", "--resume", s],
                    env={}, cwd="/w", model_arg="sample-model", session_id="old-sid")
    assert isinstance(a, ClaudeAdapter) and a.startup_slot == "append-system-prompt-file" and a.seed_mode == "briefing"
    assert a.command_line() == ["claude", "-p", "--model", "sample-model", "--settings", "s.json"]
    assert a.command_line("/d/packet.txt")[-4:] == ["--append-system-prompt-file", "/d/packet.txt",
                                                    "--system-prompt-snapshot", "on"]
    assert fresh_argv(["c", "--resume", "x", "--session-id", "y", "-p"]) == ["c", "-p"]
    e = fenced_env({"TMUX": "t", "TMUX_PANE": "%1", "A": "1"}, "qa-demo")
    assert "TMUX" not in e and "TMUX_PANE" not in e and e["A"] == "1" and e["ORCH_SELF_ROTATE"] == "off"


def test_capabilities_are_compared_normalised():
    init = {"model": "m", "tools": ["Read", "Bash"], "permissionMode": "bypassPermissions", "cwd": "/w",
            "mcp_servers": [{"name": "x", "status": "connected"}, {"name": "y", "status": "needs-auth"}],
            "slash_commands": ["b", "a"], "skills": [], "agents": [], "output_style": "default"}
    same = dict(init, tools=["Bash", "Read"], slash_commands=["a", "b"],
                mcp_servers=[{"name": "y", "status": "failed"}, {"name": "x", "status": "connected"}])
    assert capability_diff(normalise(init), normalise(same)) == {}
    lost = dict(init, mcp_servers=[{"name": "x", "status": "failed"}, {"name": "y", "status": "needs-auth"}])
    assert set(capability_diff(normalise(init), normalise(lost))) == {"mcp_servers"}
    assert set(capability_diff(normalise(init), normalise(dict(init, model="other")))) == {"model"}


def test_the_trigger_only_with_auto_compact_on_or_the_persons_compact():
    off = {"auto_compact": False, "at": 65.0, "ceiling": 90.0}
    on = dict(off, auto_compact=True)
    assert [HR.trigger(p, 0, 1000, off) for p in (10, 70, 95)] == [None, None, None], "off: never, guard included"
    assert HR.trigger(10, 0, 1000, off, person=True) == "person"
    assert [HR.trigger(p, 0, 1000, on) for p in (64.9, 65, 89, 90)] == [None, "threshold", "threshold", "guard"]
    # after a switch, only once the context has grown by max(1,024 tokens, 5% of the window)
    assert HR.growth_needed(1_000_000) == 50_000 and HR.growth_needed(10_000) == 1024
    assert HR.trigger(70, 700_000, 1_000_000, on, baseline=680_000) is None
    assert HR.trigger(73, 730_000, 1_000_000, on, baseline=680_000) == "threshold"


def test_the_guard_card_is_for_a_runtime_without_its_own_compaction_once_per_crossing():
    off = {"auto_compact": False, "ceiling": 90.0}
    assert HR.guard_card_due(False, 91, off, already=False)
    assert not HR.guard_card_due(False, 91, off, already=True)
    assert not HR.guard_card_due(True, 99, off, already=False), "Claude compacts on its own while ours is off"
    assert not HR.guard_card_due(False, 91, dict(off, auto_compact=True), already=False)
    assert "session" not in HR.GUARD_CARD_TEXT.lower() and "/compact" in HR.GUARD_CARD_TEXT


def test_settings_come_from_rotation_settings_and_the_old_switch_migrates_once(tmp_path):
    (tmp_path / "state").mkdir()
    conf = HR.rotation_conf("qa-demo", tmp_path, env={}, reader=lambda seat: {})
    assert conf["auto_compact"] is False and conf["at"] == 65.0 and conf["ceiling"] == 90.0
    conf = HR.rotation_conf("qa-demo", tmp_path, env={"HEADLESS_ROTATE_IDLE_S": "0"},
                            reader=lambda seat: {"auto_compact": True, "compact_at": 50, "guard_pct": 90})
    assert conf["auto_compact"] and conf["at"] == 50.0 and conf["idle_s"] == 0.0
    low = {"HEADLESS_ROTATE_AT": "1"}
    assert HR.rotation_conf("qa-demo", tmp_path, env=low, reader=lambda s: {})["at"] == 65.0, "not on a real seat"
    assert HR.rotation_conf("qa-demo", tmp_path, env={**low, "HEADLESS_ROTATION_TEST": "1"},
                            reader=lambda s: {})["at"] == 1.0, "a proof seat's low trigger"
    HS.set_option("qa-demo", "rotation", True, orch_dir=tmp_path)
    assert HR.migrate_legacy_rotation("qa-demo", tmp_path) is True
    doc = json.loads((tmp_path / "state" / "seat-settings.json").read_text())
    assert doc["seats"]["qa-demo"]["auto_compact"] is True and doc["history"][0]["field"] == "auto_compact"
    assert oct((tmp_path / "state" / "seat-settings.json").stat().st_mode & 0o777) == "0o600"
    assert HR.migrate_legacy_rotation("qa-demo", tmp_path) is False, "once"
    assert json.loads(HS.switch_path(tmp_path).read_text())["seats"]["qa-demo"].get("rotation") is None


def test_bound_is_checked_in_the_database_and_its_json(tmp_path):
    db = tmp_path / "state" / "orchestra-registry.db"
    db.parent.mkdir(parents=True)
    c = sqlite3.connect(db)
    c.executescript("""CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT);
                       CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT);
                       INSERT INTO generations VALUES (1, 'qa-demo', 4, 'new-sid');
                       INSERT INTO canonical VALUES ('qa-demo', 1, 'qa-demo');""")
    c.commit()
    c.close()
    (tmp_path / "registry.json").write_text(json.dumps({"agents": {"qa-demo": {"session_id": "new-sid"}}}))
    (tmp_path / "state" / "agent-sessions.json").write_text(json.dumps({"qa-demo": {"session_id": "old-sid"}}))
    got = HR.verify_bound(tmp_path, "qa-demo", "new-sid")
    assert got["db"] is True and got["json"]["registry.json"] is True
    assert got["json"]["agent-sessions.json"] is False and got["ok"] is False, "a lagging projection is caught"
    assert HR.verify_bound(tmp_path, "qa-demo", "other")["db"] is False


def test_the_block_file_is_written_owner_only(tmp_path):
    p = HR.write_packet_file(tmp_path, "sid-1", "text")
    assert p.name == "packet-sid-1.txt" and oct(p.stat().st_mode & 0o777) == "0o600" and p.read_text() == "text"


def test_a_conversation_the_index_does_not_hold_as_active_is_refused_not_exported(tmp_path, monkeypatch):
    """The index's active conversation must be the one running: otherwise its saved history is another one's, and
    nothing is changed (the supervisor registers its own again at the next start)."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    import episodic_memory as em
    seat, sid = "qa-demo", "s-takeover-1"
    path = tmp_path / f"{sid}.jsonl"
    rows = [{"type": "user", "uuid": "u1", "timestamp": "2026-10-31T07:40:00Z", "sessionId": sid,
             "message": {"role": "user", "content": "Please tidy the Example report's appendix."}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-31T07:40:05Z", "sessionId": sid,
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Tidied the appendix-zebra table."}]}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    steps = HR.BriefingSteps(tmp_path, em)
    assert em.active_session(seat) is None
    out = steps.export(seat, sid, str(path))
    assert out["ok"] is False and out["foreign"] and out["active"] is None
    em.register_session(seat, "claude", "sample-model", sid, None, None)
    em.ingest_session(seat, sid, None, path)              # what the tick does with the live file
    again = steps.export(seat, sid, str(path))
    assert again["ok"] and "foreign" not in again, "registered: the index's own tick covers it"


def test_mcp_is_compared_like_for_like_and_connector_tools_follow_their_server():
    old = {"model": "m", "tools": ["Bash", "mcp__docs__read", "ListMcpResourcesTool"],
           "mcp_servers": [{"name": "docs", "status": "connected"}, {"name": "mail", "status": "needs-auth"}]}
    pending = {"model": "m", "tools": ["Bash"],
               "mcp_servers": [{"name": "docs", "status": "pending"}, {"name": "mail", "status": "needs-auth"}]}
    up = {"model": "m", "tools": ["Bash", "mcp__docs__read", "ReadMcpResourceTool"],
          "mcp_servers": [{"name": "docs", "status": "connected"}, {"name": "mail", "status": "failed"}]}
    assert set(capability_diff(normalise(old), normalise(pending))) == {"mcp_servers"}, "only the server, not its tools"
    assert capability_diff(normalise(old), normalise(up)) == {}, "a server not connected before is not required"
    from rotation_adapters.claude import mcp_statuses, settle_seconds
    assert settle_seconds({}) == 30.0 and settle_seconds({"HEADLESS_ROTATE_MCP_SETTLE_S": "500"}) == 60.0
    assert mcp_statuses({"subtype": "success", "response": {"mcpServers": [{"name": "a", "status": "connected"}]}}) \
        == {"a": "connected"}
    assert mcp_statuses({"subtype": "error"}) is None


def test_a_promoted_conversation_with_no_turn_passes_as_saved_and_one_with_a_turn_is_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    import episodic_memory as em
    seat, sid = "qa-demo", "s-promoted-1"
    em.register_session(seat, "claude", "sample-model", sid, 2, "briefing")
    steps = HR.BriefingSteps(tmp_path, em)
    assert steps.export(seat, sid, None)["ok"] is False, "not one we promoted: the full check, which fails"
    got = steps.export(seat, sid, None, assume_saved=True)
    assert got["ok"] and got["saved"].startswith("promoted here")
    path = tmp_path / f"{sid}.jsonl"
    path.write_text(json.dumps({"type": "user", "uuid": "u1", "timestamp": "2026-10-31T18:00:00Z", "sessionId": sid,
                                "message": {"role": "user", "content": "Sample zebra-crossing question"}}) + "\n")
    em.ingest_session(seat, sid, 2, path)
    full = steps.export(seat, sid, str(path), assume_saved=True)
    assert full["ok"] and "saved" not in full and full["token"] == "zebra-crossing", "a turn: exported and checked"
    em.register_session(seat, "claude", "m", "s-other", 3, "briefing")
    assert steps.export(seat, sid, None, assume_saved=True)["foreign"], "still only the active conversation"
