"""A voice call's tool runs reach the caller's screen live: start + done events into the relay downlink."""
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture
from services.arturo.test_non_fleet_allowlist import tripwires  # noqa: F401
from services.arturo import relay_live_bridge as B


class _Buf:
    def __init__(self):
        self.events = []

    def put(self, cid, ev):
        self.events.append((cid, ev))


class _Relay:
    def __init__(self):
        self.buffer = _Buf()

    def resolve(self, conv):
        return None


def _voice(P, monkeypatch, surface="web"):
    relay = _Relay()
    monkeypatch.setattr(P, "_STREAM_RELAY", relay)
    return relay, P._TEAM_TURN.set(P._voice_turn("rid-1", "fleet", surface))


def test_a_tool_in_a_voice_call_sends_start_then_done(P, monkeypatch):
    monkeypatch.setattr(P, "_execute_tool_impl", lambda name, args, user_turns=None: "3 agents running")
    relay, tok = _voice(P, monkeypatch)
    try:
        assert P.execute_tool("list_agents", {"filter": "all"}) == "3 agents running"
    finally:
        P._TEAM_TURN.reset(tok)
    (c1, start), (c2, done) = relay.buffer.events
    assert c1 == c2 == "rid-1"
    assert start["type"] == "tool" and start["phase"] == "start" and start["name"] == "list_agents"
    assert start["args_summary"] == '{"filter": "all"}'
    assert done["phase"] == "done" and done["call_id"] == start["call_id"] and done["ok"] is True
    assert done["summary"] == "3 agents running"


def test_a_refused_tool_is_shown_failed(P, monkeypatch, tripwires):
    relay, tok = _voice(P, monkeypatch)
    try:
        out = P.execute_tool("run_command", {"command": "id"})        # off the browser-voice list
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT RUN")
    assert relay.buffer.events[-1][1]["ok"] is False


def test_a_nested_dispatch_and_a_text_turn_show_nothing(P, monkeypatch):
    monkeypatch.setattr(P, "_execute_tool_impl", lambda name, args, user_turns=None: "ok")
    relay, tok = _voice(P, monkeypatch)
    try:
        ntok = P._NESTED_DISPATCH.set(True)
        try:
            P.execute_tool("async_task", {"tool_name": "gm_command"})
        finally:
            P._NESTED_DISPATCH.reset(ntok)
    finally:
        P._TEAM_TURN.reset(tok)
    P.execute_tool("knowledge", {"query": "x"})                         # no voice turn at all
    assert relay.buffer.events == []


def test_the_bridge_passes_tool_events_to_the_browser():
    assert B.translate({"type": "tool", "phase": "start", "call_id": "v1", "name": "list_agents",
                        "args_summary": "{}"}) == \
        [("json", {"event": "tool_start", "call_id": "v1", "name": "list_agents", "args_summary": "{}"})]
    assert B.translate({"type": "tool", "phase": "done", "call_id": "v1", "name": "list_agents", "ok": True,
                        "summary": "3 running"}) == \
        [("json", {"event": "tool_done", "call_id": "v1", "name": "list_agents", "ok": True, "summary": "3 running"})]
