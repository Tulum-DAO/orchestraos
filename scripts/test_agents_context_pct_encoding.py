"""/agents sends context_pct as an integer or null, never a string.

The iOS app declares `AgentInfo.context_pct` as `Int?` and decodes the whole /agents payload in
ONE non-tolerant pass. A Swift optional absorbs `null` and an absent key, but not a wrong type:
one row carrying "" or "49%" throws, and the operator loses the ENTIRE agent list, not one
number (ios-watch-dev, 2026-10-09). The detector reports '' whenever a seat's status bar is not
drawn (scripts/context_meter.py), so the int-or-null coercion in compute_agents is load-bearing.
/agent-screen is typed `String?` on the client and keeps sending the string.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import watch_gateway as G  # noqa: E402


def test_agents_context_pct_is_an_int_or_null(monkeypatch, tmp_path):
    readings = {"a": "49%", "b": "", "c": None, "d": "97", "e": "n/a"}
    pair = {"a": (39, 49), "b": (None, None), "c": ("39", 49.0), "d": (True, 0), "e": (0, None)}

    class _AS:
        def get_agent_status(self, session):
            return {"state": "idle", "context_pct": readings[session], "pending_menu": None,
                    "context_pct_of_window": pair[session][0], "context_pct_of_budget": pair[session][1]}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: list(readings))

    rows = G.compute_agents()
    got = {row["id"]: row["context_pct"] for row in rows}
    assert got == {"a": 49, "b": None, "c": None, "d": 97, "e": None}
    pairs = {row["id"]: (row["context_pct_of_window"], row["context_pct_of_budget"]) for row in rows}
    assert pairs == {"a": (39, 49), "b": (None, None), "c": (None, None), "d": (None, 0), "e": (0, None)}
    for row in rows:
        for k in ("context_pct", "context_pct_of_window", "context_pct_of_budget"):
            assert row[k] is None or type(row[k]) is int, (row["id"], k, row[k])


def test_agent_screen_sends_the_pair_as_int_or_null(monkeypatch, tmp_path):
    """/agent-screen keeps context_pct a string (the app's String?), and sends the new pair Int?."""
    import asyncio
    import json

    class _AS:
        def get_agent_status(self, session):
            return {"state": "idle", "context_pct": "", "pending_menu": None,
                    "context_pct_of_window": 39, "context_pct_of_budget": "49"}

    class _Req:
        headers = {"Authorization": "Bearer fleet-tok", "User-Agent": "OrchestraOS/271 CFNetwork Darwin iOS"}
        query = {"session": "seat"}
        match_info = {}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-tok")
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["seat"])
    monkeypatch.setattr(G, "_capture_pane", lambda *a, **k: "")
    resp = asyncio.run(G.handle_agent_screen(_Req()))
    body = json.loads(resp.body)
    assert body["context_pct"] == ""
    assert (body["context_pct_of_window"], body["context_pct_of_budget"]) == (39, None)


def _screen_body(monkeypatch, tmp_path, proc):
    import asyncio
    import json

    class _AS:
        def get_agent_status(self, session):
            return {"state": "idle", "context_pct": "", "process": proc}

    class _Req:
        headers = {"Authorization": "Bearer fleet-tok", "User-Agent": "OrchestraOS/271 CFNetwork Darwin iOS"}
        query = {"session": "seat"}
        match_info = {}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-tok")
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: ["seat"])
    monkeypatch.setattr(G, "_capture_pane", lambda *a, **k: "")
    return json.loads(asyncio.run(G.handle_agent_screen(_Req())).body)


def test_agent_screen_names_the_runtime_as_a_string_or_null(monkeypatch, tmp_path):
    """The conversation screen colours a seat by its CLI, and the app decodes `provider` as
    String? in one pass: a non-string there is the same hazard _int_or_none guards."""
    assert _screen_body(monkeypatch, tmp_path, {"runtime": "codex"})["provider"] == "codex"
    assert _screen_body(monkeypatch, tmp_path, None)["provider"] is None
    assert _screen_body(monkeypatch, tmp_path, {"runtime": 7})["provider"] is None
    assert _screen_body(monkeypatch, tmp_path, {"runtime": ""})["provider"] is None


def test_agents_names_the_runtime_as_a_string_or_null(monkeypatch, tmp_path):
    """/agents and /agent-screen are one contract for `provider`: a string, or null."""
    procs = {"a": {"runtime": "claude"}, "b": None, "c": {"runtime": 7}, "d": {"runtime": ""}}

    class _AS:
        def get_agent_status(self, session):
            return {"state": "idle", "context_pct": "", "process": procs[session]}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: list(procs))
    got = {row["id"]: row["provider"] for row in G.compute_agents()}
    assert got == {"a": "claude", "b": None, "c": None, "d": None}
