"""RED-first: a streamed turn reports what it DID, the way /text does (DEC-1790747010535469).

The home page decides onboarding by effect: the name step advances when `set_operator_fact`
is in tools_called, a spawn step when a seat is in `spawned`. /text returns both,
plus `operator`. A streamed turn returned none of them, and the stream endpoint never applied
the onboarding marker at all — so onboarding over streaming could not advance its name step
and read every answer to a spawn offer as "declined".
"""
import json
import types

from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture


def _events(resp):
    out, name = [], None
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            out.append((name, json.loads(line[6:])))
    return out


def _turn_end(evs):
    ends = [d for n, d in evs if n == "turn.end"]
    assert ends, f"no turn.end in {[n for n, _ in evs]}"
    return ends[-1]


def test_an_onboarding_turn_goes_down_the_whole_turn_path_with_its_marker_applied(P, monkeypatch):
    seen = {}

    def whole_turn(text, conversation_id, brain=None, context=None):
        seen["text"] = text
        return 200, {"ok": True, "reply_text": "Nice to meet you, Mo.", "conversation_id": conversation_id,
                     "tools_called": ["set_operator_fact"], "spawned": [], "operator": {"name": "Mo"},
                     "brain": {"kind": "test"}}

    monkeypatch.setattr(P, "text_turn", whole_turn)
    monkeypatch.setattr(P._text_stream, "stream_turn",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("an onboarding turn must not stream")))
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "[Onboarding: step=onboarding]\nI'm Mo", "conversation_id": "c1"})
        end = _turn_end(_events(r))
    assert seen["text"].startswith("[Onboarding: step=onboarding]"), "text_turn applies the marker itself"
    assert end["tools_called"] == ["set_operator_fact"]
    assert end["operator"] == {"name": "Mo"}


def test_a_streamed_turn_end_carries_what_the_turn_spawned_and_the_operator(P, monkeypatch):
    def fake_execute(name, args, user_turns=None):
        P._SPAWNED_THIS_TURN.get().append("mgr-1")       # what spawn_agent does on a verified seat
        return "spawned mgr-1"

    def fake_stream_turn(**kw):
        kw["run_tools"]([{"name": "spawn_agent", "arguments": {}}])
        yield {"event": "turn.end", "data": {"turn_id": "t", "conversation_id": "c2", "reply_text": "Done.",
                                             "tools_called": ["spawn_agent"], "brain": {}}}

    monkeypatch.setattr(P, "execute_tool", fake_execute)
    monkeypatch.setattr(P._text_stream, "stream_turn", fake_stream_turn)
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "make me a manager", "conversation_id": "c2"})
        end = _turn_end(_events(r))
    assert end["spawned"] == ["mgr-1"]
    assert "operator" in end


def test_the_marker_is_found_on_the_message_even_with_page_context(P, monkeypatch):
    monkeypatch.setattr(P, "text_turn", lambda text, conversation_id, brain=None, context=None: (
        200, {"ok": True, "reply_text": "ok", "tools_called": [], "spawned": [], "operator": {}}))
    monkeypatch.setattr(P._text_stream, "stream_turn",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("an onboarding turn must not stream")))
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "[Onboarding: step=onboarding]\nno thanks",
                                         "conversation_id": "c3", "context": {"route": "/arturo"}})
        _turn_end(_events(r))


def test_the_whole_reply_turn_end_keeps_its_own_spawned(P, monkeypatch):
    # The fallback's turn.end already carries text_turn's spawned; the stream's (empty) list
    # is merged into it, never written over it.
    monkeypatch.setattr(P._text_stream, "stream_turn", lambda **kw: iter([
        {"event": "turn.end", "data": {"reply_text": "Up.", "tools_called": ["spawn_agent"],
                                       "spawned": ["mgr-9"], "brain": {}}}]))
    with P.app.test_client() as c:
        end = _turn_end(_events(c.post("/text/stream", json={"text": "manager please", "conversation_id": "c4"})))
    assert end["spawned"] == ["mgr-9"]


def test_the_streams_fallback_inherits_the_streams_dedupe_ledger(P, monkeypatch):
    # A side-effecting tool the stream already ran must not run again in the whole-reply
    # fallback (it made a fresh ledger: send_telegram twice).
    seen = {}

    def whole_turn(text, conversation_id, brain=None, context=None):
        seen["ledger"] = P._turn_ledger()
        return 200, {"ok": True, "reply_text": "ok", "tools_called": [], "spawned": []}

    def fake_stream_turn(**kw):
        kw["run_tools"]([{"name": "list_agents", "arguments": {}}])
        seen["stream"] = P._TURN_DEDUP.get()
        yield from P._text_stream._whole_reply("t", "c5", kw["fallback"], kw["brain"])

    monkeypatch.setattr(P, "execute_tool", lambda name, args, user_turns=None: "4")
    monkeypatch.setattr(P, "text_turn", whole_turn)
    monkeypatch.setattr(P._text_stream, "stream_turn", fake_stream_turn)
    with P.app.test_client() as c:
        _turn_end(_events(c.post("/text/stream", json={"text": "who is up", "conversation_id": "c5"})))
    assert seen["ledger"] is seen["stream"]


def test_outside_a_streams_fallback_every_turn_gets_a_fresh_ledger(P):
    a, b = P._turn_ledger(), P._turn_ledger()
    assert a is not b


def test_merged_spawned_keeps_order_and_never_duplicates_a_seat(P, monkeypatch):
    def fake_execute(name, args, user_turns=None):
        P._SPAWNED_THIS_TURN.get().extend(["mgr-1", "dev-2"])
        return "ok"

    def fake_stream_turn(**kw):
        kw["run_tools"]([{"name": "spawn_agent", "arguments": {}}])
        yield {"event": "turn.end", "data": {"reply_text": "Up.", "spawned": ["dev-2", "qa-3"],
                                             "tools_called": ["spawn_agent"], "brain": {}}}

    monkeypatch.setattr(P, "execute_tool", fake_execute)
    monkeypatch.setattr(P._text_stream, "stream_turn", fake_stream_turn)
    with P.app.test_client() as c:
        end = _turn_end(_events(c.post("/text/stream", json={"text": "team up", "conversation_id": "c6"})))
    assert end["spawned"] == ["dev-2", "qa-3", "mgr-1"]


# ---- a runtime with no streaming tool loop runs whole, with Arturo's tools ----------------
# Found on staging, 2026-09-30: a streamed Codex turn opened with prose ("I'm checking the live
# agent sessions now."), so it streamed — and then reached for codex's OWN sandboxed shell
# instead of Arturo's tools: "I couldn't access the live session list from this environment."
# Only Claude has a streaming tool loop; codex's CLI hands over whole messages anyway, so
# streaming it bought nothing and cost the tools.

def test_a_codex_turn_runs_whole_through_the_tool_loop(P, monkeypatch):
    monkeypatch.setattr(P, "text_turn", lambda text, conversation_id, brain=None, context=None: (
        200, {"ok": True, "reply_text": "Four sessions.", "tools_called": ["list_agents"], "spawned": []}))
    monkeypatch.setattr(P._text_stream, "stream_turn",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("codex must not take the cold stream")))
    with P.app.test_client() as c:
        r = c.post("/text/stream", json={"text": "check which agents are running", "conversation_id": "c7",
                                         "brain": {"provider": "codex", "model": "gpt-5.6-terra"}})
        end = _turn_end(_events(r))
    assert end["tools_called"] == ["list_agents"]


def test_a_claude_turn_still_streams(P, monkeypatch):
    called = {}

    def fake_stream_turn(**kw):
        called["yes"] = True
        yield {"event": "turn.end", "data": {"reply_text": "hi", "tools_called": [], "brain": {}}}

    monkeypatch.setattr(P._text_stream, "stream_turn", fake_stream_turn)
    with P.app.test_client() as c:
        _turn_end(_events(c.post("/text/stream", json={"text": "hi", "conversation_id": "c8",
                                                       "brain": {"provider": "claude", "model": "claude-sonnet-5"}})))
    assert called.get("yes")
