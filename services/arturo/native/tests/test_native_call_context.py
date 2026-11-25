"""A native call opens with the thread above it and hears the chat that arrives during it (Shaw 2026-11-25: "you
can't read your own messages, you can't read what the GM is saying ... you can't even remember the last call").

The thread (the page's chat + Arturo's earlier calls, every line stamped) goes into BOTH prompts of session.start,
after the rules; it loads while the provider connects and never holds a call. The chat that lands during the call
reaches the session as stamped appends, one per tick, only after session.started. ARTURO_NATIVE_THREAD_CONTEXT=0
turns both off.
"""
import asyncio
import json

from services.arturo.native import call_context
from services.arturo.native.tests.test_native import FakeProvider, HDRS, _client, _run, _server


class _Feed:
    def __init__(self, agent):
        self.agent, self.polls = agent, 0

    def poll(self):
        self.polls += 1
        return ["[2026-11-25T17:40:00Z 12:40 Tulum] chat gm (typed, during this call): the dream fix merged"] \
            if self.polls == 1 else []


async def _start(c, start):
    ws = await c.ws_connect("/native/ws", headers=HDRS)
    await ws.send_str(json.dumps(start))
    for _ in range(100):
        if FakeProvider.instances and FakeProvider.instances[0].sent:
            break
        await asyncio.sleep(0.02)
    return ws, FakeProvider.instances[0]


def _go(tmp_path, monkeypatch, start, after_start, loader=None, flag=None):
    if flag is not None:
        monkeypatch.setenv(call_context.FLAG, flag)
    feeds, asked = [], []

    async def go():
        s = _server(tmp_path, monkeypatch)
        s.thread_loader = loader or (lambda agent: asked.append(agent) or f"THREAD for {agent}: last call said hi")
        s.chat_feed_factory = lambda agent: feeds.append(_Feed(agent)) or feeds[-1]
        c = await _client(s)
        try:
            ws, p = await _start(c, start)
            await after_start(ws, p)
            await ws.send_str(json.dumps({"type": "disconnect"}))
            await ws.close()
        finally:
            await c.close()
        return p
    return _run(go()), feeds, asked


async def _started(ws, p):
    p.push({"type": "session.started"})
    await asyncio.sleep(0.5)


def test_the_thread_opens_the_call_in_both_prompts(tmp_path, monkeypatch):
    p, _, asked = _go(tmp_path, monkeypatch, {"type": "start"}, _started)
    start = p.sent[0]
    assert start["type"] == "session.start"
    assert asked == ["gm"]                                          # the client page's chat is gm's (10-20 ruling)
    live, backend = start["session"]["instructions"], start["session"]["delegation"]["responses"]["instructions"]
    for text in (live, backend):
        assert text.rstrip().endswith("THREAD for gm: last call said hi")   # after the rules, never before


def test_the_page_names_whose_chat_it_is(tmp_path, monkeypatch):
    _, feeds, asked = _go(tmp_path, monkeypatch, {"type": "start", "agent": "pm-demo"}, _started)
    assert asked == ["pm-demo"] and feeds[0].agent == "pm-demo"
    _, _, asked = _go(tmp_path, monkeypatch, {"type": "start", "agent": "../etc"}, _started)
    assert asked == ["gm"]                                          # a malformed id is ignored


def test_chat_during_the_call_reaches_the_session_stamped_after_it_started(tmp_path, monkeypatch):
    async def before_and_after(ws, p):
        await asyncio.sleep(0.4)                                    # not started yet: nothing appended
        assert not [f for f in p.sent if f["type"].endswith(".append") and "dream fix" in f.get("content", "")]
        await _started(ws, p)
    p, _, _ = _go(tmp_path, monkeypatch, {"type": "start"}, before_and_after)
    notes = [f for f in p.sent if f["type"] == "session.instructions.append" and "dream fix" in f["content"]]
    assert len(notes) == 1 and notes[0]["content"].startswith("[2026-11-25T17:40:00Z 12:40 Tulum] chat gm")
    rep = json.loads(next((tmp_path / "native" / "calls").glob("*.json")).read_text())
    assert rep["thread"]["chat"] == "gm" and rep["thread"]["feed_sent"] == 1 and rep["thread"]["opening_tokens"] > 0


def test_a_slow_or_broken_thread_never_holds_the_call(tmp_path, monkeypatch):
    def boom(agent):
        raise OSError("api down")
    p, _, _ = _go(tmp_path, monkeypatch, {"type": "start"}, _started, loader=boom)
    assert p.sent[0]["type"] == "session.start"
    assert "THREAD" not in p.sent[0]["session"]["instructions"]


def test_the_escape_hatch_turns_both_off(tmp_path, monkeypatch):
    p, feeds, asked = _go(tmp_path, monkeypatch, {"type": "start"}, _started, flag="0")
    assert asked == [] and feeds == []
    assert "THREAD" not in p.sent[0]["session"]["instructions"]


def test_the_feed_stamps_new_chat_and_moves_its_cursor():
    items = [{"kind": "text", "role": "user", "text": "before", "ts": "2026-11-25T17:00:00Z", "audience": "user"},
             {"kind": "text", "role": "assistant", "text": "after", "ts": "2026-11-25T17:40:00Z", "audience": "user"}]
    since = 1795626600.0                 # 2026-11-25T17:10:00Z
    f = call_context.ChatFeed("gm", since=since, fetch=lambda a, n: items, tz="UTC", tz_label="UTC", operator="Shaw")
    assert f.poll() == ["[2026-11-25T17:40:00Z 17:40 UTC] chat gm (typed, during this call): after"]
    assert f.poll() == []                                           # never twice


def test_the_feed_is_capped_per_call():
    items = [{"kind": "text", "role": "user", "text": f"m{i}", "ts": f"2026-11-25T17:{i:02d}:00Z", "audience": "user"}
             for i in range(10)]
    f = call_context.ChatFeed("gm", since=0, fetch=lambda a, n: items, tz="UTC", tz_label="UTC", operator="S",
                              max_notes=3)
    assert len(f.poll()) == 3 and f.poll() == []


def test_a_tool_runs_knowing_its_call_and_the_pages_thread(tmp_path, monkeypatch):
    # gm_command tags its message to gm with these, so gm's answer lands in the thread the call came from
    from services.arturo.native.tests.test_native import FakeExecutor
    ex = FakeExecutor(reply="gm has it")

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        s.thread_loader = lambda agent: ""
        s.chat_feed_factory = lambda agent: None
        c = await _client(s)
        try:
            ws, p = await _start(c, {"type": "start", "agent": "pm-demo"})
            p.push({"type": "session.started"})
            p.push({"type": "session.input_transcript.delta", "delta": "ask the gm if the fix is in"})
            p.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
            p.push({"type": "response.event", "delegation_id": "d1", "event": {
                "type": "response.output_item.done",
                "item": {"type": "function_call", "call_id": "call_G", "name": "list_agents", "arguments": "{}"}}})
            p.push({"type": "response.event", "delegation_id": "d1", "event": {
                "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                           "usage": {"input_tokens": 10, "output_tokens": 2}}}})
            for _ in range(60):
                if ex.calls:
                    break
                await asyncio.sleep(0.05)
            await ws.send_str(json.dumps({"type": "disconnect"}))
            await ws.close()
        finally:
            await c.close()
    _run(go())
    assert ex.calls and ex.call_seen["thread_id"] == "pm-demo chat"
    assert ex.call_seen["conversation_id"].startswith("native-")


def test_the_proxy_turn_carries_the_call_and_thread():
    from services.arturo.native.tools import ProxyExecutor
    seen = {}

    class _Mod:
        def _as_turn(self, turn, fn, *a, **k):
            seen.update(turn)
            return "ok"
        execute_tool = None
    ProxyExecutor(_Mod())("gm_command", {"prompt": "x"}, "fleet",
                          call={"conversation_id": "native-1", "thread_id": "gm chat"})
    assert seen["conversation_id"] == "native-1" and seen["thread_id"] == "gm chat" and seen["principal"] == "fleet"


def test_run_sh_turns_on_the_thread_and_the_knowledge_facts_but_no_proxy_daemon():
    import pathlib
    sh = (pathlib.Path(__file__).resolve().parents[1] / "run.sh").read_text()
    assert 'export ARTURO_KNOWLEDGE_FACTS="${ARTURO_KNOWLEDGE_FACTS:-1}"' in sh
    assert 'export ARTURO_NATIVE_THREAD_CONTEXT="${ARTURO_NATIVE_THREAD_CONTEXT:-1}"' in sh
    assert "unset ARTURO_STREAM_RELAY ARTURO_WARM_VOICES_CACHE ARTURO_FACTS_RECALL" in sh


def test_the_opening_comes_from_the_api_route_and_falls_back_to_the_python_twin(tmp_path, monkeypatch):
    # the route redacts with the full redactor and this install's token; the twin is the same contract, offline
    monkeypatch.delenv(call_context.FLAG, raising=False)
    asked = []
    out = call_context.opening("gm", route=lambda agent, budget: asked.append((agent, budget)) or "FROM ROUTE",
                               load=lambda **k: "FROM TWIN", directory=tmp_path)
    assert out == "FROM ROUTE" and asked == [("gm", call_context.budget_tokens())]

    def down(agent, budget):
        raise OSError("api down")
    seen = {}
    out = call_context.opening("gm", route=down, load=lambda **k: seen.update(k) or "FROM TWIN", directory=tmp_path)
    assert out == "FROM TWIN" and seen["chat_agent"] == "gm" and seen["call_owner"] is None


def test_the_route_is_asked_for_arturos_calls_with_the_budget(monkeypatch):
    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return "THREAD …".encode()
    monkeypatch.setattr(call_context.urllib.request, "urlopen",
                        lambda url, timeout: seen.update(url=url, timeout=timeout) or _Resp())
    monkeypatch.setenv("ORCH_API_URL", "http://127.0.0.1:8888")
    assert call_context.fetch_route("gm", 6000) == "THREAD …"
    assert seen["url"] == "http://127.0.0.1:8888/api/agents/gm/call-context?calls=arturo&budget=6000"
    assert seen["timeout"] <= 5
