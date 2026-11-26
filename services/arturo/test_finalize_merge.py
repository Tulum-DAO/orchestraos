# test_finalize_merge.py — Piece A merge logic (DEC-1786426323).
from services.arturo import finalize_merge as fm


# ------------------------------------------------------- extract_surface_server_ids
def test_extract_surface_ids_from_overlay():
    lines = [
        '{"ts":1.0,"stack":[{"role":"base","entity":null},{"role":"overlay","entity":{"kind":"voice","id":"vc_e91877ed787b1316"}}]}',
        '{"ts":2.0,"stack":[{"role":"overlay","entity":{"kind":"voice","id":"vc_e91877ed787b1316"}}]}',
        'garbage not json',
        '',
    ]
    assert fm.extract_surface_server_ids(lines) == {"vc_e91877ed787b1316"}


def test_extract_ignores_client_ids():
    lines = ['{"ts":1,"stack":[{"entity":{"id":"vc_client_2f140442c137"}}]}']
    assert fm.extract_surface_server_ids(lines) == set()


# ------------------------------------------------------- pick_server_journal
def _client(**kw):
    d = {"source": "client", "call_id": "vc_client_x", "started_at": 100.0, "ended_at": 200.0}
    d.update(kw)
    return d


def test_pick_by_conv_id_primary():
    client = _client(conv_id="conv_abc")
    cands = [
        ("vc_srv1", {"origin": "funnel", "conv_id": "conv_zzz", "turns": []}),
        ("vc_srv2", {"origin": "funnel", "conv_id": "conv_abc", "turns": []}),
    ]
    assert fm.pick_server_journal(client, cands) == "vc_srv2"


def test_pick_by_surface_overlay_when_no_conv_id():
    # server has NO conv_id (predates stamping) — surface overlay id bridges it
    client = _client()   # no conv_id
    cands = [
        ("vc_srv1", {"origin": "funnel", "started_at": 100, "ended_at": 200, "turns": []}),
        ("vc_e91877ed787b1316", {"origin": "funnel", "started_at": 100, "ended_at": 200, "turns": []}),
    ]
    got = fm.pick_server_journal(client, cands, surface_server_ids={"vc_e91877ed787b1316"})
    assert got == "vc_e91877ed787b1316"


def test_pick_by_time_window_fallback_richest():
    client = _client(started_at=100, ended_at=200)
    cands = [
        ("vc_thin", {"origin": "funnel", "started_at": 150, "ended_at": 260,
                     "turns": [{"role": "tool"}]}),
        ("vc_rich", {"origin": "funnel", "started_at": 90, "ended_at": 210,
                     "turns": [{"role": "tool"}, {"role": "tool"}, {"role": "tool"}]}),
    ]
    assert fm.pick_server_journal(client, cands) == "vc_rich"


def test_pick_skips_client_and_superseded():
    client = _client(conv_id="conv_abc")
    cands = [
        ("vc_clientdupe", {"source": "client", "conv_id": "conv_abc", "turns": []}),
        ("vc_done", {"origin": "funnel", "conv_id": "conv_abc", "superseded_by": "x", "turns": []}),
    ]
    assert fm.pick_server_journal(client, cands) is None


def test_pick_none_when_no_overlap():
    client = _client(started_at=100, ended_at=200)
    cands = [("vc_far", {"origin": "funnel", "started_at": 500, "ended_at": 600, "turns": []})]
    assert fm.pick_server_journal(client, cands) is None


# ------------------------------------------------------- merge_timeline
def test_merge_folds_tools_and_surface_by_ts():
    client_turns = [
        {"role": "user", "text": "hi", "ts": 1.0},
        {"role": "arturo", "text": "hey", "ts": 2.0},
        {"role": "user", "text": "status?", "ts": 5.0},
        {"role": "arturo", "text": "all good", "ts": 6.0},
    ]
    server_turns = [
        {"role": "user", "text": "hi (truncated)", "ts": 1.1},      # dropped — server user dupe
        {"role": "tool", "tool": "list_agents", "ts": 3.0},
        {"role": "tool", "tool": "get_agent_output", "ts": 4.0},
        {"role": "arturo", "text": "server arturo dupe", "ts": 2.1},  # dropped
    ]
    surface = [{"ts": 3.5, "focused": None, "stack": [{"hint": "Arturo call live"}]}]
    merged = fm.merge_timeline(client_turns, server_turns, surface)
    roles = [t["role"] for t in merged]
    # client 4 + 2 tools + 1 surface = 7; server user/arturo dupes dropped
    assert roles == ["user", "arturo", "tool", "surface", "tool", "user", "arturo"]
    assert [t["ts"] for t in merged] == [1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 6.0]
    assert merged[3]["hint"] == "Arturo call live"


def test_merge_stable_for_equal_ts():
    client_turns = [{"role": "user", "text": "a", "ts": 1.0}]
    server_turns = [{"role": "tool", "tool": "t1", "ts": 1.0}, {"role": "tool", "tool": "t2", "ts": 1.0}]
    merged = fm.merge_timeline(client_turns, server_turns, [])
    # equal ts → client spine first (added first), then tools in order
    assert [t.get("tool", t["role"]) for t in merged] == ["user", "t1", "t2"]


# ------------------------------------------------------- summarize
def test_summarize_counts_turns_and_tools():
    merged = [
        {"role": "user", "text": "q", "ts": 1},
        {"role": "tool", "tool": "x", "ts": 2},
        {"role": "arturo", "text": "the answer", "ts": 3},
        {"role": "surface", "ts": 2.5},
    ]
    s = fm.summarize(merged)
    assert "2 turns" in s and "1 tool runs" in s and "the answer" in s


def _native_ledger(tmp_path):
    from services.arturo.native.tool_ledger import ToolLedger
    led = ToolLedger(tmp_path, clock=iter([100.0, 101.0, 150.0, 151.0]).__next__)
    for cid, name, conv in (("c1", "gm_command", "native-a"), ("c2", "list_agents", "native-a")):
        led.run_once_external(cid, conv, "fleet", name, {"prompt": "why the lag"} if name == "gm_command" else {},
                              lambda: ({"ok": True, "result": "done"}, None))
    led.run_once_external("c9", "native-b", "fleet", "list_agents", {}, lambda: ({"ok": True, "result": "x"}, None))
    return tmp_path / "tool_ledger.sqlite3"


def test_a_native_calls_tool_runs_come_from_its_ledger(tmp_path):
    # Shaw's 18:3x call: three gm_command runs, yet the summary said "0 tool runs" (the native writes no journal of
    # its own, so finalize had no tool turns to fold in)
    turns = fm.native_tool_turns("native-a", _native_ledger(tmp_path))
    assert [(t["role"], t["tool"], t["source"]) for t in turns] == [("tool", "gm_command", "native"),
                                                                    ("tool", "list_agents", "native")]
    assert turns[0]["input"] == {"prompt": "why the lag"} and turns[0]["ts"] < turns[1]["ts"]
    merged = fm.merge_timeline([{"role": "user", "text": "hi", "ts": 99.0}], turns, [])
    assert fm.summarize(merged).startswith("Voice call: 1 turns, 2 tool runs.")


def test_no_native_tool_turns_for_another_call_a_relay_call_or_no_ledger(tmp_path):
    led = _native_ledger(tmp_path)
    assert fm.native_tool_turns("native-zzz", led) == []
    assert fm.native_tool_turns("vc_relay_1", led) == []
    assert fm.native_tool_turns("", led) == []
    assert fm.native_tool_turns("native-a", tmp_path / "missing.sqlite3") == []
