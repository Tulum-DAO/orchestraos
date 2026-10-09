"""RED-first: a turn that runs WHOLE still shows its tool cards (DEC-1790750869457756).

Codex has no streaming tool loop, so its turns run whole through the ordinary chat-completions
tool loop — which is what gives them Arturo's tools — and the page showed "ran knowledge"
instead of cards, because that loop emitted no events. It now reports each call and result to
a sink, when one is set; voice and plain /text set none and are unchanged.
"""
import json
import types

from services.arturo import text_stream as TS
from services.arturo.brain import make_response, _tool_call
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture


def _collect():
    got = []
    return got, TS.ToolEvents(lambda event, **data: got.append((event, data)))


def _brain_that_calls(P, name, args, answer):
    calls = {"n": 0}

    def complete(model=None, messages=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return make_response(None, [_tool_call(name, args)])
        return make_response(answer, None, "stop")

    P.brain = types.SimpleNamespace(kind="test", model="default", complete=complete,
                                    describe=lambda: {"kind": "test", "model": "default"})


# ---- the real text_turn -> chat-completions loop reports to the sink -------------------

def test_the_tool_loop_reports_each_call_and_result_to_the_sink(P, monkeypatch):
    _brain_that_calls(P, "list_agents", {}, "Four agents are up.")
    monkeypatch.setattr(P, "execute_tool", lambda name, args, user_turns=None: "4 agents: a, b, c, d")
    got, sink = _collect()
    tok = P._TOOL_EVENTS.set(sink)
    try:
        code, body = P.text_turn("who's up?", "c1")
    finally:
        P._TOOL_EVENTS.reset(tok)
    assert code == 200 and body["reply_text"] == "Four agents are up."
    assert [n for n, _ in got] == ["tool.call", "tool.result"]
    call, result = got[0][1], got[1][1]
    assert call["name"] == "list_agents" and call["call_id"] == result["call_id"]
    assert result["ok"] is True and result["summary"].startswith("4 agents")


def test_an_error_result_is_reported_as_failed(P, monkeypatch):
    _brain_that_calls(P, "read_file", {"path": "/nope"}, "That file doesn't exist.")
    monkeypatch.setattr(P, "execute_tool", lambda name, args, user_turns=None: "Error: no such file /nope")
    got, sink = _collect()
    tok = P._TOOL_EVENTS.set(sink)
    try:
        P.text_turn("read /nope", "c2")
    finally:
        P._TOOL_EVENTS.reset(tok)
    assert got[1][1]["ok"] is False


def test_with_no_sink_the_loop_is_unchanged(P, monkeypatch):
    _brain_that_calls(P, "list_agents", {}, "Four agents are up.")
    monkeypatch.setattr(P, "execute_tool", lambda name, args, user_turns=None: "4 agents")
    assert P._TOOL_EVENTS.get() is None
    code, body = P.text_turn("who's up?", "c3")
    assert code == 200 and body["reply_text"] == "Four agents are up."


# ---- whole_turn streams the sink's events live, then the whole reply -------------------

def test_whole_turn_yields_tool_events_then_the_reply():
    def run(sink):
        cid = sink.call("list_agents", {})
        sink.result(cid, "list_agents", True, "4 agents")
        return 200, {"ok": True, "reply_text": "Four.", "tools_called": ["list_agents"], "spawned": []}

    brain = types.SimpleNamespace(describe=lambda: {"kind": "runtime"})
    evs = list(TS.whole_turn("c4", brain, fallback=None, run_with_sink=run))
    assert [e["event"] for e in evs] == ["turn.start", "tool.call", "tool.result", "text.delta", "turn.end"]
    assert evs[1]["data"]["call_id"] == evs[2]["data"]["call_id"] == "c1"


def test_a_failing_whole_turn_ends_in_an_error_event():
    def run(sink):
        raise RuntimeError("brain exploded")

    brain = types.SimpleNamespace(describe=lambda: {"kind": "runtime"})
    evs = list(TS.whole_turn("c5", brain, fallback=None, run_with_sink=run))
    assert evs[-1]["event"] == "error"


# ---- the endpoint wires it for codex ---------------------------------------------------

def _events(resp):
    out, name = [], None
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            out.append((name, json.loads(line[6:])))
    return out


def test_a_codex_turn_on_the_stream_shows_its_tool_cards(P, monkeypatch):
    def whole(text, conversation_id, brain=None, context=None, principal=None):
        sink = P._TOOL_EVENTS.get()
        cid = sink.call("knowledge", {"query": "agents"})
        sink.result(cid, "knowledge", True, "4 on VPS")
        return 200, {"ok": True, "reply_text": "Four.", "tools_called": ["knowledge"], "spawned": []}

    monkeypatch.setattr(P, "text_turn", whole)
    with P.app.test_client() as c:
        evs = _events(c.post("/text/stream", json={"text": "who's up", "conversation_id": "c6",
                                                   "brain": {"provider": "codex", "model": "gpt-5.6-terra"}}))
    names = [n for n, _ in evs]
    assert names.index("tool.call") < names.index("tool.result") < names.index("text.delta")
