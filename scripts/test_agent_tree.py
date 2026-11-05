"""agent_tree.py: seat -> subagent -> helper from Claude's files.

Fixture mirrors the shapes measured on a live seat 2026-11-04 (Claude Code 2.1.284): depth-1 meta
has no parentAgentId, depth-2 meta names its parent; one record per content block, each repeating
the message's usage under the same message id.
"""
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_tree as at  # noqa: E402

NOW = 1_800_000_000.0
SID = "11111111-2222-3333-4444-555555555555"


def _ts(t):
    return at._iso(t)


def _asst(mid, t, blocks, stop=None, usage=None):
    u = usage or {"input_tokens": 10, "cache_creation_input_tokens": 100,
                  "cache_read_input_tokens": 1000, "output_tokens": 5}
    return [{"type": "assistant", "timestamp": _ts(t), "uuid": f"{mid}-{i}",
             "message": {"id": mid, "role": "assistant", "stop_reason": stop if i == len(blocks) - 1 else None,
                         "usage": u, "content": [b]}} for i, b in enumerate(blocks)]


def _user(t, text="go", result_for=None):
    content = [{"type": "tool_result", "tool_use_id": result_for, "content": "ok"}] if result_for else text
    return [{"type": "user", "timestamp": _ts(t), "message": {"role": "user", "content": content}}]


def _tool(tid, name):
    return {"type": "tool_use", "id": tid, "name": name, "input": {}}


def _write(path, recs, mtime):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))


def _agent(sub, aid, recs, mtime, depth, desc, parent=None, kind="general-purpose"):
    _write(os.path.join(sub, f"agent-{aid}.jsonl"), recs, mtime)
    meta = {"agentType": kind, "description": desc, "toolUseId": "toolu_x", "spawnDepth": depth}
    if parent:
        meta["parentAgentId"] = parent
    with open(os.path.join(sub, f"agent-{aid}.meta.json"), "w") as f:
        json.dump(meta, f)


def _fixture(tmp_path):
    """seat (idle, ended its turn) ->
         parent P   depth 1: blocked inside a pending Agent call, file quiet 20 min
           helper H depth 2: pending Bash, wrote 30 s ago                      -> running
         done D     depth 1: finished 2 h ago                                  -> pruned by default
         recent R   depth 1: finished 5 min ago                                -> kept
         orphan O   depth 2: parent 'gone' has no files, running"""
    proj = tmp_path / "projects" / "-home-x"
    main = str(proj / f"{SID}.jsonl")
    _write(main, _user(NOW - 4000) + _asst("m1", NOW - 3990, [_tool("t0", "Agent")])
           + _user(NOW - 3980, result_for="t0")
           + _asst("m2", NOW - 3970, [{"type": "text", "text": "x"}], stop="end_turn"), NOW - 3970)
    sub = str(proj / SID / "subagents")
    _agent(sub, "P", _user(NOW - 3000) + _asst("p1", NOW - 1200, [{"type": "text", "text": "t"},
                                                                   _tool("tp", "Agent")]),
           NOW - 1200, 1, "Build part 2a")
    _agent(sub, "H", _user(NOW - 600) + _asst("h1", NOW - 30, [_tool("th", "Bash")]),
           NOW - 30, 2, "Task 5 gate hook", parent="P")
    _agent(sub, "D", _user(NOW - 9000) + _asst("d1", NOW - 7200, [{"type": "text", "text": "a"}],
                                                stop="end_turn"), NOW - 7200, 1, "old done")
    _agent(sub, "R", _user(NOW - 900) + _asst("r1", NOW - 300, [_tool("tr", "Read")])
           + _user(NOW - 299, result_for="tr")
           + _asst("r2", NOW - 290, [{"type": "text", "text": "a"}], stop="end_turn"), NOW - 290, 1,
           "recent done", kind="Explore")
    _agent(sub, "O", _user(NOW - 100) + _asst("o1", NOW - 10, [_tool("to", "Grep")]),
           NOW - 10, 2, "orphan", parent="gone")
    return main


def _by_id(node, out=None):
    out = {} if out is None else out
    out[node["agent_id"]] = node
    for c in node["children"]:
        _by_id(c, out)
    return out


def setup_function(_):
    at._SCANS.clear()


def test_tree_shape_states_and_tools(tmp_path):
    t = at.build_tree(_fixture(tmp_path), seat="pm-x", now=NOW)
    n = _by_id(t)
    assert t["depth"] == 0 and t["type"] == "seat" and t["label"] == "pm-x" and t["agent_id"] == SID
    assert [c["agent_id"] for c in t["children"]] == ["P", "R", "O"]     # by started_at; D pruned
    assert [c["agent_id"] for c in n["P"]["children"]] == ["H"]
    assert n["H"]["parent_agent_id"] == "P" and n["H"]["depth"] == 2
    assert n["P"]["parent_agent_id"] == SID and n["P"]["depth"] == 1
    assert n["H"]["state"] == "running" and n["H"]["current_tool"] == "Bash"
    # P's own file is 20 min quiet (would be 'stale'), but its helper is live: waiting, not dead
    assert n["P"]["state"] == "waiting" and n["P"]["current_tool"] == "Agent"
    # the seat ended its turn, a background descendant is still working: waiting, not finished
    assert t["state"] == "waiting"
    assert n["R"]["state"] == "finished" and n["R"]["current_tool"] is None
    assert n["R"]["last_tool"] == "Read" and n["R"]["type"] == "Explore"
    # orphan: named parent has no files -> hangs off the seat, keeps the missing id visible
    assert n["O"]["parent_agent_id"] == "gone" and n["O"] in t["children"]
    assert n["H"]["started_at"] == _ts(NOW - 600) and n["H"]["last_activity_at"] == _ts(NOW - 30)
    assert n["H"]["elapsed_s"] == 600.0 and n["R"]["elapsed_s"] == 610.0
    keys = {"agent_id", "parent_agent_id", "depth", "type", "label", "state", "started_at",
            "last_activity_at", "current_tool", "tokens_own", "tokens_rollup", "elapsed_s",
            "transcript_path", "children"}
    assert keys <= set(n["H"])


def test_finished_and_stale_stay_put_without_live_children(tmp_path):
    """Opposite direction of the waiting promotion: nothing alive below -> no promotion."""
    main = _fixture(tmp_path)
    t = at.build_tree(main, now=NOW, all_finished=True)
    n = _by_id(t)
    assert n["D"]["state"] == "finished"
    sub = os.path.join(main[:-6], "subagents")
    _agent(sub, "S", _user(NOW - 5000) + _asst("s1", NOW - 4000, [_tool("ts", "Bash")]),
           NOW - 4000, 1, "silent stale")
    at._SCANS.clear()
    n = _by_id(at.build_tree(main, now=NOW, all_finished=True))
    assert n["S"]["state"] == "stale" and n["S"]["current_tool"] == "Bash"
    # a finished parent whose only child is finished stays finished
    _agent(sub, "F", _user(NOW - 800) + _asst("f1", NOW - 700, [{"type": "text", "text": "a"}],
                                               stop="end_turn"), NOW - 700, 1, "done parent")
    _agent(sub, "FC", _user(NOW - 790) + _asst("fc1", NOW - 750, [{"type": "text", "text": "a"}],
                                                stop="end_turn"), NOW - 750, 2, "done child", parent="F")
    at._SCANS.clear()
    n = _by_id(at.build_tree(main, now=NOW, all_finished=True))
    assert n["FC"] in n["F"]["children"]
    assert n["F"]["state"] == "finished" and n["FC"]["state"] == "finished"


def test_tokens_count_each_message_once_and_roll_up(tmp_path):
    n = _by_id(at.build_tree(_fixture(tmp_path), now=NOW, all_finished=True))
    # P: one message id written as 2 records (text + tool_use) -> counted once
    assert n["P"]["tokens_own"] == 115 and n["P"]["tokens_cache_read"] == 1000
    assert n["P"]["ctx_tokens"] == 1110
    assert n["P"]["tokens_rollup"] == 115 + n["H"]["tokens_rollup"] == 230
    t = n[SID]
    assert t["tokens_own"] == 230                         # m1 + m2
    assert t["tokens_rollup"] == 230 + 230 + 115 + 230 + 115   # seat + P-subtree + D + R + O


def test_pruned_nodes_still_count_and_ancestors_are_kept(tmp_path):
    main = _fixture(tmp_path)
    full = at.build_tree(main, now=NOW, all_finished=True)
    at._SCANS.clear()
    pruned = at.build_tree(main, now=NOW)
    assert "D" in _by_id(full) and "D" not in _by_id(pruned)
    assert pruned["tokens_rollup"] == full["tokens_rollup"]
    # a 2 h old parent stays when its child is live
    at._SCANS.clear()
    n = _by_id(at.build_tree(main, now=NOW, recent_s=1))
    assert "P" in n and "H" in n and "R" not in n
    # an old finished parent is kept for a recently finished background child
    sub = os.path.join(main[:-6], "subagents")
    _agent(sub, "OP", _user(NOW - 9000) + _asst("op1", NOW - 7000, [{"type": "text", "text": "a"}],
                                                 stop="end_turn"), NOW - 7000, 1, "old parent")
    _agent(sub, "OC", _user(NOW - 8000) + _asst("oc1", NOW - 60, [{"type": "text", "text": "a"}],
                                                 stop="end_turn"), NOW - 60, 2, "late child", parent="OP")
    at._SCANS.clear()
    n = _by_id(at.build_tree(main, now=NOW))
    assert n["OP"]["state"] == "finished" and [c["agent_id"] for c in n["OP"]["children"]] == ["OC"]


def test_incremental_scan_reads_only_appended_lines(tmp_path):
    main = _fixture(tmp_path)
    h = os.path.join(main[:-6], "subagents", "agent-H.jsonl")
    n = _by_id(at.build_tree(main, now=NOW))
    assert n["H"]["current_tool"] == "Bash" and n["H"]["tokens_own"] == 115
    off = at._SCANS[h].offset
    with open(h, "a") as f:
        for r in _user(NOW - 5, result_for="th") + _asst("h2", NOW - 4, [_tool("th2", "Edit")]):
            f.write(json.dumps(r) + "\n")
        f.write('{"type": "assistant", "partial')           # half-written line: not consumed yet
    os.utime(h, (NOW - 4, NOW - 4))
    n = _by_id(at.build_tree(main, now=NOW))
    assert at._SCANS[h].offset > off
    assert n["H"]["current_tool"] == "Edit" and n["H"]["tokens_own"] == 230
    with open(h, "rb") as f:
        assert f.read()[at._SCANS[h].offset:].startswith(b'{"type": "assistant", "partial')


def test_seat_resolution_from_registry(tmp_path, monkeypatch):
    main = _fixture(tmp_path)
    orch = tmp_path / "orch"
    (orch / "state").mkdir(parents=True)
    db = sqlite3.connect(orch / "state" / "orchestra-registry.db")
    db.executescript("CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, session_id TEXT);"
                     "CREATE TABLE canonical (root TEXT, generation_id INTEGER, tmux_session TEXT, status TEXT);")
    db.execute("INSERT INTO generations VALUES (1, 'pm-x', ?)", (SID,))
    db.execute("INSERT INTO generations VALUES (2, 'codex-y', 'not-a-claude-sid')")
    db.execute("INSERT INTO canonical VALUES ('pm-x', 1, 'pm-x', 'online')")
    db.execute("INSERT INTO canonical VALUES ('codex-y', 2, 'codex-y', 'online')")
    db.commit()
    db.close()
    monkeypatch.setattr(at, "ORCH_DIR", str(orch))
    monkeypatch.setattr(at, "CLAUDE_PROJECTS", str(tmp_path / "projects"))
    assert at.seat_sessions("pm-x") == [("pm-x", main)]
    assert at.seat_sessions("all") == [("pm-x", main)]          # codex seat skipped
    assert at.seat_sessions(SID) == [(None, main)]               # raw session id
    assert at.seat_sessions(main) == [(None, main)]              # raw path
    assert at.seat_sessions("nope") == []


def test_cli_json_is_the_contract(tmp_path, capsys, monkeypatch):
    main = _fixture(tmp_path)
    monkeypatch.setattr(at.time, "time", lambda: NOW)
    assert at.main([main, "--json"]) == 0
    t = json.loads(capsys.readouterr().out)
    assert t["agent_id"] == SID and t["children"][0]["children"][0]["agent_id"] == "H"
    assert at.main([main]) == 0
    text = capsys.readouterr().out
    assert "general-purpose: Task 5 gate hook  [running]  Bash" in text


def test_registry_lives_in_the_orchestra_data_dir(tmp_path, monkeypatch):
    """OrchestraOS keeps data apart from code: the registry is $ORCHESTRA_DIR/state/
    orchestra-registry.db, default ~/.orchestra (never the checkout)."""
    import importlib
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("ORCH_DIR", raising=False)
    assert importlib.reload(at).ORCH_DIR == str(tmp_path / "data")
    monkeypatch.delenv("ORCHESTRA_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert importlib.reload(at).ORCH_DIR == str(tmp_path / "home" / ".orchestra")
    importlib.reload(at)


def test_seat_resolution_with_a_registry_in_ORCHESTRA_DIR(tmp_path, monkeypatch):
    import importlib
    main = _fixture(tmp_path)
    data = tmp_path / "data"
    (data / "state").mkdir(parents=True)
    db = sqlite3.connect(data / "state" / "orchestra-registry.db")
    db.executescript("CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, session_id TEXT);"
                     "CREATE TABLE canonical (root TEXT, generation_id INTEGER, tmux_session TEXT, status TEXT);")
    db.execute("INSERT INTO generations VALUES (1, 'pm-x', ?)", (SID,))
    db.execute("INSERT INTO canonical VALUES ('pm-x', 1, 'pm-x', 'online')")
    db.commit()
    db.close()
    monkeypatch.setenv("ORCHESTRA_DIR", str(data))
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(tmp_path / "projects"))
    mod = importlib.reload(at)
    try:
        assert mod.seat_sessions("pm-x") == [("pm-x", main)]
    finally:
        monkeypatch.delenv("ORCHESTRA_DIR")
        monkeypatch.delenv("CLAUDE_PROJECTS_DIR")
        importlib.reload(at)


def test_the_gateway_counts_subagents_with_the_same_rule():
    """One running/finished rule: /agents (watch_gateway) and the tree both use subagent_files."""
    import subagent_files
    import watch_gateway as wg
    assert wg._subagent_finished is subagent_files.subagent_finished
    assert wg.SUBAGENT_STALE_S == subagent_files.SUBAGENT_STALE_S
