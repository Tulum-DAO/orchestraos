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

    class _AS:
        def get_agent_status(self, session):
            return {"state": "idle", "context_pct": readings[session], "pending_menu": None}

    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_PERM_INSTANCE_LEDGER", str(tmp_path / "ledger.json"), raising=False)
    monkeypatch.setattr(G, "_live_voice_call", lambda: None)
    monkeypatch.setattr(G, "_agent_status", lambda: _AS())
    monkeypatch.setattr(G, "_tmux_session_names", lambda: list(readings))

    got = {row["id"]: row["context_pct"] for row in G.compute_agents()}
    assert got == {"a": 49, "b": None, "c": None, "d": 97, "e": None}
    assert all(v is None or type(v) is int for v in got.values())
