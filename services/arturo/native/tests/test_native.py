"""Native GPT-Live service: tools boundary, claim gate, spend, and the WebSocket server (fakes for the provider and the
fleet; one test imports the REAL arturo-proxy into a temp ORCHESTRA_DIR to pin the tool list and the principal path)."""
import asyncio
import base64
import json
import os
import threading
import time

import numpy as np
import pytest

from services.arturo.native import claim_gate, native_config, protocol, server as srv, spend
from services.arturo.native.session import TransportDown
from services.arturo.native.tool_ledger import ToolLedger
from services.arturo.native.tools import ArturoTools, NEEDS_A_CARD, SessionBinding, read_back

LIVE_TREE = os.path.realpath(os.path.expanduser("~/scripts/agent-orchestra"))


@pytest.fixture(autouse=True)
def _fence(tmp_path, monkeypatch):
    """No test here may touch the live tree: ORCHESTRA_DIR and the native state dir are temp, asserted first."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "orch"))
    for k in ("ARTURO_STREAM_RELAY", "ARTURO_WARM_VOICES_CACHE", "ARTURO_FACTS_RECALL"):
        monkeypatch.delenv(k, raising=False)
    assert not os.path.realpath(str(tmp_path)).startswith(LIVE_TREE)
    yield


# ---- fakes ---------------------------------------------------------------------------------------------------
FLEET_SPECS = {
    "list_agents": ("List agents", {"type": "object", "properties": {}}),
    "spawn_agent": ("Spawn a seat", {"type": "object", "properties": {"session_name": {"type": "string"},
                                                                    "machine": {"type": "string"},
                                                                    "task": {"type": "string"}}}),
    "inject_message": ("Message a seat", {"type": "object", "properties": {}}),
    "kill_agent": ("Kill a seat", {"type": "object", "properties": {}}),
    "remember_note": ("Save a note", {"type": "object", "properties": {}}),
    "knowledge": ("Look up", {"type": "object", "properties": {}}),
    "run_command": ("Run a shell command", {"type": "object", "properties": {}}),
    "async_task": ("Run a tool in the background", {"type": "object", "properties": {}}),
    "send_telegram": ("Text Shaw", {"type": "object", "properties": {}}),
    "research": ("Start a research seat", {"type": "object", "properties": {"query": {"type": "string"}}}),
    "answer_menu": ("Answer a seat's menu", {"type": "object", "properties": {}}),
    "gm_command": ("Ask gm", {"type": "object", "properties": {}}),
    "agent_message": ("Message a seat", {"type": "object", "properties": {}}),
    "focus_entity": ("Focus", {"type": "object", "properties": {}}),
    "get_agent_output": ("Read a seat's pane", {"type": "object", "properties": {}}),
}
NON_FLEET = {"knowledge", "list_agents"}


def _real_success(name, args):
    """What the proxy really returns when each action works (tools.action_ok accepts only these shapes)."""
    return {
        "spawn_agent": f"CONFIRMED: Agent '{args.get('session_name', 'x')}' is running on vps (runtime claude).",
        "research": f"Task queued: Web research: {str(args.get('query', ''))[:40]}.",
        "gm_command": "GM response:\nok",
        "inject_message": f"Message injected into '{args.get('session_name', 'x')}' on vps (verified landed).",
        "agent_message": "Message sent to gm: [msg_1] hi. Router will deliver when gm is idle.",
        "send_telegram": "Message sent to Shaw on Telegram.",
        "remember_note": "Noted and saved: x",
        "answer_menu": (f"Sent option {args.get('option')} to {args.get('session')}." if args.get("confirm")
                        else f"CONFIRM_NEEDED: I'll send option {args.get('option')} to {args.get('session')}."),
    }.get(name, "ok")


class FakeExecutor:
    def __init__(self, reply=_real_success, delay=0.0):
        self.calls, self.reply, self.delay = [], reply, delay

    def specs(self, fleet):
        return {n: s for n, s in FLEET_SPECS.items() if fleet or n in NON_FLEET}

    non_fleet = NON_FLEET

    def __call__(self, name, args, principal, fenced=False, user_turns=None, call=None):
        self.call_seen = call
        self.calls.append((name, dict(args), principal))
        self.fenced_seen = fenced
        self.user_turns_seen = user_turns
        if self.delay:
            time.sleep(self.delay)
        return self.reply(name, args) if callable(self.reply) else self.reply


def _tools(tmp_path, spoken="", **kw):
    ex = FakeExecutor(**kw)
    t = ArturoTools(ToolLedger(tmp_path / "led"), executor=ex, spoken_fn=lambda conv: spoken)
    return t, ex


def _fleet(t, conv="c1"):
    return t.bind("fleet", "iphone", conv)


# ---- tools boundary --------------------------------------------------------------------------------------------
def test_fleet_gets_arturos_tools_minus_kill_and_non_fleet_gets_the_allowlist(tmp_path):
    t, _ = _tools(tmp_path)
    names = {d["name"] for d in t.declared_tools(_fleet(t))}
    assert "spawn_agent" in names and "remember_note" in names and "kill_agent" not in names
    assert {d["name"] for d in t.declared_tools(t.bind("device:x", "quest"))} == NON_FLEET


def test_a_kill_or_restart_is_refused_as_needing_a_card_and_never_runs(tmp_path):
    t, ex = _tools(tmp_path)
    b = _fleet(t)
    for name in ("kill_agent", "restart_seat"):
        out = t.execute(b, f"call_{name}", name, "{}")
        assert out["error"] == "NEEDS_A_CARD" and out["detail"] == NEEDS_A_CARD
    assert ex.calls == []


def test_the_model_cannot_choose_its_principal(tmp_path):
    t, ex = _tools(tmp_path)
    out = t.execute(_fleet(t), "call_1", "list_agents", json.dumps({"principal": "owner:someone"}))
    assert out["error"] == "PRINCIPAL_IN_ARGS" and ex.calls == []


def test_the_executor_runs_as_the_sessions_principal(tmp_path):
    t, ex = _tools(tmp_path)
    t.execute(t.bind("owner:abc", "ipad", "c9"), "call_1", "list_agents", "{}")
    assert ex.calls == [("list_agents", {}, "owner:abc")]


def test_a_tool_outside_the_callers_set_is_refused(tmp_path):
    t, ex = _tools(tmp_path)
    out = t.execute(t.bind("device:x", "quest"), "call_1", "remember_note", "{}")
    assert out["error"] == "TOOL_NOT_ALLOWED" and ex.calls == []


def test_a_duplicate_call_id_never_runs_twice_and_a_reused_id_is_refused(tmp_path):
    t, ex = _tools(tmp_path)
    b = _fleet(t)
    a = t.execute(b, "call_1", "list_agents", "{}")
    again = t.execute(b, "call_1", "list_agents", "{}")
    assert a == again and len(ex.calls) == 1
    assert t.execute(b, "call_1", "knowledge", "{}")["error"] == "TOOL_CALL_ID_REUSED"
    assert len(ex.calls) == 1


def test_a_duplicate_while_the_first_still_runs_is_in_progress_not_a_second_run(tmp_path):
    t, ex = _tools(tmp_path, delay=0.5)
    b = _fleet(t)
    th = threading.Thread(target=t.execute, args=(b, "call_1", "list_agents", "{}"))
    th.start()
    time.sleep(0.1)
    assert t.execute(b, "call_1", "list_agents", "{}")["error"] == "IN_PROGRESS"
    th.join()
    assert len(ex.calls) == 1


SPAWN = {"session_name": "research-bot", "machine": "vps", "task": "compare the three vendors on price and latency"}


def test_spawn_needs_a_spoken_read_back_first(tmp_path):
    t, ex = _tools(tmp_path, spoken="Sure.")
    out = t.execute(_fleet(t), "call_1", "spawn_agent", json.dumps(SPAWN))
    assert out["error"] == "READ_BACK_FIRST" and ex.calls == []


@pytest.mark.parametrize("said", ["I'll start a seat called research bot to compare vendors.",
                                  "Starting one to compare the vendors on price and latency."])
def test_a_read_back_of_the_name_or_the_task_lets_the_spawn_run_with_a_receipt(tmp_path, said):
    t, ex = _tools(tmp_path, spoken=said)
    out = t.execute(_fleet(t), "call_1", "spawn_agent", json.dumps(SPAWN))
    assert out["ok"] and out["seat"] == "research-bot" and out["receipt"] == "accepted"
    assert t.ledger.receipt(out["task_id"])["state"] == "accepted" and out["task_id"].startswith("research-bot#")
    assert len(ex.calls) == 1


def test_read_back_matching():
    assert read_back("I'll start research bot now", {"session_name": "research-bot"})
    assert not read_back("I'll start something", {"session_name": "research-bot", "task": "fix the build"})


def test_spawn_caps_three_per_call_and_ten_per_day(tmp_path):
    said = "I'll start a seat called research bot"
    t, ex = _tools(tmp_path, spoken=said)
    b = _fleet(t)
    for i in range(3):
        t.new_turn(b.conversation_id)                      # each spawn on its own turn (the G1' fence)
        assert t.execute(b, f"c{i}", "spawn_agent", json.dumps(SPAWN))["ok"]
    t.new_turn(b.conversation_id)
    assert t.execute(b, "c3", "spawn_agent", json.dumps(SPAWN))["error"] == "SPAWN_CAP"
    for i in range(7):                                     # other calls, same principal: 10 in the day
        bi = _fleet(t, conv=f"conv{i}")
        assert t.execute(bi, f"d{i}", "spawn_agent", json.dumps(SPAWN))["ok"]
    out = t.execute(_fleet(t, conv="conv-last"), "d-last", "spawn_agent", json.dumps(SPAWN))
    assert out["error"] == "SPAWN_CAP" and "per day" in out["detail"]
    assert len(ex.calls) == 10


def test_a_failed_spawn_is_not_a_success_and_opens_no_receipt(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start research bot", reply="FAILED to spawn 'research-bot' on vps: boom")
    out = t.execute(_fleet(t), "call_1", "spawn_agent", json.dumps(SPAWN))
    assert out["ok"] is False and "task_id" not in out and t.ledger.tasks() == [] and t.ledger.calls()[0]["task_id"] is None


# ---- the REAL proxy: tool list + the principal reaches execute_tool's allowlist ---------------------------------
def test_the_real_proxy_tool_list_and_principal_path(tmp_path):
    before = {t.name for t in threading.enumerate()}
    from services.arturo.native.tools import ProxyExecutor, _load_proxy
    mod = _load_proxy()
    started = {t.name for t in threading.enumerate()} - before
    assert not started, f"importing the proxy started threads: {started}"
    assert str(mod.ORCHESTRA_DIR).startswith(str(tmp_path))
    ex = ProxyExecutor(mod)
    fleet = set(ex.specs(True))
    assert {"spawn_agent", "inject_message", "list_agents", "kill_agent"} <= fleet
    assert set(ex.specs(False)) == set(mod._NON_FLEET_ALLOWED) & fleet
    t = ArturoTools(ToolLedger(tmp_path / "led"), executor=ex)
    assert "kill_agent" not in {d["name"] for d in t.declared_tools(_fleet(t))}
    # a non-fleet principal reaches execute_tool's own fail-closed allowlist (no side effect: refused before any run)
    refused = ex("spawn_agent", {"session_name": "x-y", "machine": "vps"}, "device:abc")
    assert "spawn" in str(refused).lower() or "can't" in str(refused).lower() or "not" in str(refused).lower()


# ---- claim gate -------------------------------------------------------------------------------------------------
def _pcm(sec=0.1, rms=3000, seed=0):
    n = int(16000 * sec)
    x = np.random.default_rng(seed).standard_normal(n)
    return np.clip(np.rint(x / np.sqrt((x * x).mean()) * rms), -32768, 32767).astype("<i2").tobytes()


def _gate(backed=False, **kw):
    return claim_gate.ClaimGate(is_backed=lambda kind: backed(kind) if callable(backed) else backed, **kw)


def test_audio_is_held_behind_the_words_then_released():
    g = _gate()
    g.feed_transcript("Sure, what should the task be?")
    g.feed_audio(_pcm(), now=10.0)
    assert g.release(10.1) == []
    assert len(g.release(10.31)) == 1


def test_test_drive_3_capability_question_passes():
    g = _gate()
    g.feed_transcript("Yeah, I can start one on a task. What would you like it to do?")
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(1.0)) == 1 and not g.blocks


def test_an_unbacked_action_claim_is_never_played_and_a_correction_is_owed():
    """gm msg_0aec5c71 + design msg_b4cb2363: Arturo cannot SAY he started something with no receipt."""
    g = _gate(backed=False)
    g.feed_audio(_pcm(), now=0.0)                  # the utterance's first audio, still held
    g.feed_transcript("Done, I've started the research seat.")
    g.feed_audio(_pcm(), now=0.1)
    assert g.release(5.0) == [] and g.end_utterance(5.0) == []
    assert g.take_correction() == claim_gate.CORRECTION and g.blocks


def test_a_backed_action_claim_is_played():
    g = _gate(backed=lambda kind: kind == ("action", "start"))
    g.feed_transcript("I've started research-bot on the vendor comparison.")
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(1.0)) == 1 and g.take_correction() is None


def test_a_state_claim_needs_any_tool_result_an_action_claim_a_side_effect():
    status_read = lambda kind: kind[0] == "state"      # noqa: E731 — only a read tool ran this turn
    g = _gate(backed=status_read)
    g.feed_transcript("The build is finished.")
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(1.0)) == 1
    g2 = _gate(backed=status_read)
    g2.feed_transcript("I've restarted the build.")
    g2.feed_audio(_pcm(), now=0.0)
    assert g2.release(1.0) == []


def test_the_read_back_announcement_is_not_a_claim():
    g = _gate()
    g.feed_transcript("I'll start a seat called research-bot to compare the vendors. I'm starting it now.")
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(1.0)) == 1


def test_claim_gate_off_plays_everything():
    g = _gate(enabled=False)
    g.feed_transcript("I've started it.")
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(0.0)) == 1


# ---- spend --------------------------------------------------------------------------------------------------------
class FakeUsage:
    def __init__(self, minutes=0.0, cap_min=60):
        self.minutes, self.cap_min, self.added = minutes, cap_min, []

    def over_cap(self, vendor):
        return self.minutes >= self.cap_min

    def today_minutes(self, vendor):
        return self.minutes

    def add_seconds(self, vendor, s):
        self.added.append((vendor, s))


def test_spend_refuses_over_the_daily_cap_and_an_unpriced_backend():
    with pytest.raises(spend.SpendStop):
        spend.CallSpend(FakeUsage(minutes=60), "gpt-6-luna").authorize()
    with pytest.raises(spend.SpendStop):
        spend.CallSpend(FakeUsage(), "gpt-unpriced").authorize()


def test_spend_caps_and_counts_into_the_daily_cap_once():
    u = FakeUsage()
    s = spend.CallSpend(u, "gpt-6-luna", clock=lambda: 0.0)
    s.start()
    assert s.check(10) is None
    assert "cost cap" in s.check(10_000)
    s.close(42.0)
    s.close(42.0)
    assert u.added == [("openai", 42.0)]
    bad = {"type": "response.event", "event": {"type": "response.completed", "response": {"model": "gpt-6-luna"}}}
    assert "unmeasurable" in s.on_frame(bad)


# ---- server -----------------------------------------------------------------------------------------------------------
class FakeProvider:
    instances = []

    def __init__(self):
        self.sent, self.inbox, self.down = [], [], False
        FakeProvider.instances.append(self)

    def push(self, frame):
        self.inbox.append(json.dumps(frame))

    def send(self, payload):
        if self.down:
            raise TransportDown("down")
        self.sent.append(json.loads(payload))

    def recv(self):
        if self.down:
            raise TransportDown("down")
        return self.inbox.pop(0) if self.inbox else None

    def close(self):
        self.down = True

    def types(self):
        return [f["type"] for f in self.sent]


def _server(tmp_path, monkeypatch, enabled=True, verified=True, usage=None, executor=None):
    if enabled:
        monkeypatch.setenv(native_config.FLAG, "1")
    else:
        monkeypatch.delenv(native_config.FLAG, raising=False)
    FakeProvider.instances = []
    s = srv.NativeServer(tmp_path / "native", executor=executor or FakeExecutor(),
                         transport_factory=FakeProvider, usage_store=usage or FakeUsage(),
                         verify=lambda headers: verified)
    s.thread_loader = lambda agent: ""                  # never the live API or the live call journals
    s.chat_feed_factory = lambda agent: None
    return s


async def _client(server):
    from aiohttp.test_utils import TestClient, TestServer
    c = TestClient(TestServer(server.app()))
    await c.start_server()
    return c


HDRS = {"X-Arturo-Principal": "fleet", "X-Arturo-Stamp": "s", "X-Arturo-Device": "iphone"}


def _run(coro):
    return asyncio.run(coro)


def test_flag_off_refuses_every_call(tmp_path, monkeypatch):
    async def go():
        c = await _client(_server(tmp_path, monkeypatch, enabled=False))
        try:
            r = await c.get("/native/ws", headers=HDRS)
            assert r.status == 503
        finally:
            await c.close()
    _run(go())


@pytest.mark.parametrize("hdrs,verified", [
    (HDRS, False),                                                  # stamp does not verify
    ({**HDRS, "X-Forwarded-Host": "srv.ts.net"}, True),             # came through the Funnel / a proxy
    ({"X-Arturo-Principal": "device:abc"}, True),                   # not a full-tools caller
])
def test_an_unverified_or_forwarded_caller_is_refused(tmp_path, monkeypatch, hdrs, verified):
    async def go():
        c = await _client(_server(tmp_path, monkeypatch, verified=verified))
        try:
            r = await c.get("/native/ws", headers=hdrs)
            assert r.status == 403
        finally:
            await c.close()
    _run(go())


async def _call(c, script, settle="fast", start=None):
    """Open a call, run script(ws, provider), collect every message the client got."""
    got = []
    ws = await c.ws_connect("/native/ws", headers=HDRS)

    async def reader():
        async for m in ws:
            got.append(m.data if m.type.name == "BINARY" else json.loads(m.data))
    rt = asyncio.ensure_future(reader())
    await ws.send_str(json.dumps({"settle": settle, **(start or {"type": "start"})}))
    for _ in range(40):
        if FakeProvider.instances:
            break
        await asyncio.sleep(0.02)
    p = FakeProvider.instances[0] if FakeProvider.instances else None
    if p is not None:
        await script(ws, p)
    if not ws.closed:
        try:
            await ws.send_str(json.dumps({"type": "disconnect"}))
        except Exception:  # noqa: BLE001 — the server already ended the call (refused / capped)
            pass
    await asyncio.wait_for(rt, 10)
    return got, p


def _audio_frame(rms=3000, sec=0.1):
    return {"type": "session.output_audio.delta", "delta": base64.b64encode(_pcm(sec, rms)).decode()}


def test_a_call_starts_with_arturos_tools_and_streams_audio_both_ways(tmp_path, monkeypatch):
    async def script(ws, p):
        p.push({"type": "session.started"})
        await ws.send_bytes(_pcm(0.2))
        p.push({"type": "session.output_transcript.delta", "delta": "Hey Shaw, what's up?"})
        for _ in range(3):
            p.push(_audio_frame())
        for _ in range(10):
            p.push(_audio_frame(rms=0))
        await asyncio.sleep(0.8)
        p.push({"type": "session.usage.updated", "usage": {"seconds": 12}})
        await asyncio.sleep(0.2)

    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        try:
            got, p = await _call(c, script)
        finally:
            await c.close()
        start = p.sent[0]
        assert start["type"] == "session.start"
        tools = {t["name"] for t in start["session"]["delegation"]["responses"]["tools"]}
        assert "spawn_agent" in tools and "kill_agent" not in tools
        assert "session.input_audio.append" in p.types() and p.types()[-1] == "session.close"
        assert any(isinstance(m, bytes) for m in got), "Arturo's audio never reached the client"
        assert any(isinstance(m, dict) and m.get("type") == "transcript" and m["who"] == "arturo" for m in got)
        assert s.usage_store.added == [("openai", 12.0)]
        reps = list((tmp_path / "native" / "calls").glob("*.json"))
        assert len(reps) == 1 and oct(reps[0].stat().st_mode & 0o777) == "0o600"
    _run(go())


def test_an_unbacked_claim_never_reaches_the_phone_and_arturo_corrects_himself(tmp_path, monkeypatch):
    """Test drive 3 shape, with a real claim: no tool ran, Arturo says he started something."""
    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.input_transcript.delta", "delta": "can you spawn an agent"})
        p.push({"type": "session.output_transcript.delta", "delta": "Done, I've started a research seat."})
        for _ in range(4):
            p.push(_audio_frame())
        for _ in range(10):
            p.push(_audio_frame(rms=0))
        await asyncio.sleep(1.0)

    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        try:
            got, p = await _call(c, script)
        finally:
            await c.close()
        speech = [m for m in got if isinstance(m, bytes) and np.abs(np.frombuffer(m, "<i2")).max() > 500]
        assert speech == [], "the unbacked claim was played"
        assert any(isinstance(m, dict) and m.get("withdrawn") for m in got)
        corr = [f for f in p.sent if f["type"] == "session.commentary.append"]
        assert corr and corr[0]["content"] == claim_gate.CORRECTION
    _run(go())


def test_a_real_tool_call_runs_as_the_caller_and_its_result_goes_back(tmp_path, monkeypatch):
    ex = FakeExecutor(reply="seats: gm (working), research-bot (idle)")

    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.input_transcript.delta", "delta": "what are my agents doing"})
        p.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_A", "name": "list_agents", "arguments": "{}"}}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                       "usage": {"input_tokens": 100, "output_tokens": 20}}}})
        await asyncio.sleep(0.6)

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        c = await _client(s)
        try:
            got, p = await _call(c, script)
        finally:
            await c.close()
        assert ex.calls == [("list_agents", {}, "fleet")]
        outs = [f for f in p.sent if f["type"] == "response.item.create"]
        assert outs and outs[0]["item"]["call_id"] == "call_A" and "research-bot" in outs[0]["item"]["output"]
        assert "response.create" in p.types()
        snaps = [m for m in got if isinstance(m, dict) and m.get("type") == "tools" and m["calls"]]
        assert snaps and snaps[-1]["calls"][0]["name"] == "list_agents"
    _run(go())


def test_over_the_daily_cap_the_call_is_refused_before_any_provider_socket(tmp_path, monkeypatch):
    async def go():
        s = _server(tmp_path, monkeypatch, usage=FakeUsage(minutes=60))
        c = await _client(s)
        try:
            got, p = await _call(c, None)
        finally:
            await c.close()
        assert p is None and FakeProvider.instances == []
        assert any(isinstance(m, dict) and m.get("type") == "refused" and "cap" in m["reason"] for m in got)
    _run(go())


def test_the_native_prompts_share_no_text_with_the_relays_f1_prompt():
    from services.arturo import openai_live
    p = native_config.prompts()
    for line in openai_live.INSTRUCTIONS.split("\n"):
        line = line.strip()
        if len(line) > 30:
            assert line not in p["live"] and line not in p["backend"]
    assert "say out loud which seat you will start" in p["live"]
    assert "READ_BACK_FIRST" in p["backend"]


def test_the_live_prompt_says_gm_is_answered_in_its_own_time_and_never_still_on_it():
    # gm on Shaw's 18:3x call: keep answering Shaw while a question to gm is out; never fill the air with "still on it"
    live = native_config.prompts()["live"]
    assert "say once that gm hasn't answered\nyet" in live and 'Never say "I\'m still on it".' in live


def test_a_slow_tool_never_stalls_the_audio(tmp_path, monkeypatch):
    """spawn_agent can take up to 120 s. Tool calls ran inside the event loop that moves the audio, so one slow tool
    froze every call (GPT-Live heard nothing, Shaw heard nothing). Audio must keep flowing while a tool runs, and the
    result must still go back to the provider when it is ready."""
    ex = FakeExecutor(reply="seats: gm", delay=1.0)
    arrivals = {}

    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.input_transcript.delta", "delta": "what are my agents doing"})
        p.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_S", "name": "list_agents", "arguments": "{}"}}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                       "usage": {"input_tokens": 10, "output_tokens": 2}}}})
        def provider_speaks():                           # the provider is another machine: its own clock/thread
            time.sleep(0.4)                              # the tool (1.0 s) is running by now
            arrivals["pushed"] = time.monotonic()
            p.push({"type": "session.output_transcript.delta", "delta": "Let me look."})
            for _ in range(3):
                p.push(_audio_frame())
        threading.Thread(target=provider_speaks, daemon=True).start()
        await asyncio.sleep(2.0)

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        c = await _client(s)
        got_t = []
        orig = srv.NativeCall.emit

        def emit(self, msg):
            if isinstance(msg, (bytes, bytearray)):
                got_t.append(time.monotonic())
            return orig(self, msg)
        monkeypatch.setattr(srv.NativeCall, "emit", emit)
        try:
            got, p = await _call(c, script)
        finally:
            await c.close()
        first = min(t for t in got_t if t >= arrivals["pushed"])
        assert first - arrivals["pushed"] < 0.7, f"audio waited {first - arrivals['pushed']:.2f} s behind a running tool"
        outs = [f for f in p.sent if f["type"] == "response.item.create"]
        assert len(outs) == 1 and outs[0]["item"]["call_id"] == "call_S", "the slow tool's result never went back"
        assert "seats: gm" in outs[0]["item"]["output"], "a placeholder went back instead of the tool's result"
        assert len(ex.calls) == 1
    _run(go())


# ---- gm msg_04c7d9bd: claim-gate false positives, late transcripts, counts ----------------------------------------
@pytest.mark.parametrize("said", [
    "Yeah, I can start one on a task. What would you like it to do?",     # test drive 3 (gm msg_6e917289)
    "You started that one yesterday.",
    "It was created yesterday by gm.",
    "Your agents are already running.",
    "I can explain how that works.",
])
def test_claim_words_that_are_not_his_own_action_pass(said):
    g = _gate(backed=False)
    g.feed_transcript(said)
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(1.0)) == 1 and not g.blocks, said


@pytest.mark.parametrize("said", ["I've started it.", "Done, I sent the message to gm.", "It's done."])
def test_a_backed_claim_is_never_dropped(said):
    g = _gate(backed=True)
    g.feed_transcript(said)
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(1.0)) == 1 and not g.blocks


def test_a_late_transcript_fails_open_and_is_counted():
    """Audio before any words: never stalled (released after the hold), and CLAIM-GATE-LATE is counted."""
    g = _gate(backed=False)
    g.feed_audio(_pcm(), now=0.0)
    assert len(g.release(0.31)) == 1                     # played without words: fail open
    g.feed_transcript("Sure, here it is.")
    assert g.late == 1
    g.end_utterance(1.0)
    g.feed_transcript("Hi.")                             # a normal utterance afterwards is not late
    g.feed_audio(_pcm(), now=2.0)
    g.release(2.4)
    assert g.late == 1


# ---- independent review round 1 (sonnet, REJECT) --------------------------------------------------------------------
def test_review_b1_b2_no_shell_and_no_tool_wrapper_on_native_voice(tmp_path):
    """async_task wraps ANY tool (async_task(kill_agent) killed a seat past every native check); run_command is a raw
    shell (tmux kill-server). Neither is offered, and asking for them is refused without running anything."""
    t, ex = _tools(tmp_path)
    b = _fleet(t)
    names = {d["name"] for d in t.declared_tools(b)}
    assert not names & {"async_task", "run_command", "kill_agent"}
    for name, args in (("async_task", {"tool_name": "kill_agent", "tool_args": {"session_name": "gm"}}),
                       ("run_command", {"command": "tmux kill-server"})):
        assert t.execute(b, f"call_{name}", name, json.dumps(args))["ok"] is False
    assert ex.calls == []


@pytest.mark.parametrize("text", ["/exit", "  /clear", "!tmux kill-server", "/compact now"])
def test_review_b2_inject_message_cannot_type_a_command_into_a_seat(tmp_path, text):
    t, ex = _tools(tmp_path)
    out = t.execute(_fleet(t), "call_1", "inject_message", json.dumps({"session_name": "gm", "message": text}))
    assert out["ok"] is False and ex.calls == []


def test_review_m3_after_a_tool_result_the_turn_is_fenced_to_lookups(tmp_path):
    """The relay's G1' fence: text that came back in a tool result cannot steer an action in the same turn."""
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    b = _fleet(t)
    assert t.execute(b, "c1", "list_agents", "{}")["ok"]
    out = t.execute(b, "c2", "spawn_agent", json.dumps(SPAWN))
    assert out["error"] == "FENCED" and [c[0] for c in ex.calls] == ["list_agents"]
    assert t.execute(b, "c3", "knowledge", "{}")["ok"]           # lookups still run, and are run fenced
    assert ex.fenced_seen is True
    t.new_turn(b.conversation_id)                                 # Shaw speaks again: a new turn
    assert t.execute(b, "c4", "spawn_agent", json.dumps(SPAWN))["ok"]


def test_review_m4_spawn_cap_counts_spawns_still_running(tmp_path):
    """A spawn takes up to 120 s; the cap counted only finished ones, so overlapping spawns overshot it."""
    t, ex = _tools(tmp_path, spoken="I'll start research bot", delay=0.6)
    b = _fleet(t)
    ths = [threading.Thread(target=lambda i=i: t.execute(_fleet(t, conv="cX"), f"s{i}", "spawn_agent",
                                                         json.dumps(SPAWN))) for i in range(5)]
    for th in ths:
        th.start()
        time.sleep(0.02)
    for th in ths:
        th.join()
    assert len(ex.calls) == 3


def test_review_m9_a_tool_call_id_from_another_conversation_is_refused_not_replayed(tmp_path):
    t, ex = _tools(tmp_path)
    assert t.execute(_fleet(t, "conv-a"), "same_id", "list_agents", "{}")["ok"]
    assert t.execute(_fleet(t, "conv-b"), "same_id", "list_agents", "{}")["error"] == "TOOL_CALL_ID_REUSED"


def test_review_m11_send_telegram_gets_the_callers_words(tmp_path):
    t, ex = _tools(tmp_path)
    t.user_fn = lambda conv: "text me the summary"
    t.execute(_fleet(t), "c1", "send_telegram", json.dumps({"text": "x"}))
    assert ex.user_turns_seen == ["text me the summary"]


@pytest.mark.parametrize("said,cls", [
    ("I sent the message to gm.", "message"), ("I saved that note.", "save"), ("I started research-bot.", "start"),
    ("Consider it done.", None), ("I went ahead and did it.", "fix"), ("I got that going.", "start"),
    ("Started.", "start"), ("The seat is up.", None), ("I've got it started.", "start"),
])
def test_review_m6_more_claim_phrasings_are_caught(said, cls):
    kind = claim_gate.claim_kind(said)
    assert kind is not None, said
    if cls:
        assert kind == ("action", cls), (said, kind)


@pytest.mark.parametrize("said", ["As I told you earlier, it's the same seat.", "I asked you about that yesterday."])
def test_review_m6_narration_is_not_a_claim(said):
    assert claim_gate.claim_kind(said) is None


def test_review_m6_a_claim_needs_a_receipt_of_ITS_kind():
    """'I sent the text' must not be backed by an unrelated spawn."""
    seen = []
    g = claim_gate.ClaimGate(is_backed=lambda kind: seen.append(kind) or kind == ("action", "start"))
    g.feed_transcript("I sent it to gm.")
    g.feed_audio(_pcm(), now=0.0)
    assert g.release(1.0) == [] and seen == [("action", "message")]


def test_review_m7_daily_cap_is_charged_during_the_call_not_only_at_the_end():
    u = FakeUsage()
    s = spend.CallSpend(u, "gpt-6-luna", clock=lambda: 0.0)
    s.start()
    s.checkpoint(30.0)
    s.checkpoint(45.0)
    s.close(50.0)
    assert [x[1] for x in u.added] == [30.0, 15.0, 5.0]


def test_review_real_proxy_kill_paths_never_reach_execute_tool_and_the_principal_crosses_the_pool(tmp_path):
    """Against the REAL arturo-proxy module (temp ORCHESTRA_DIR), with execute_tool replaced by a recorder so nothing
    runs: (1) kill_agent / async_task(kill_agent) / run_command never reach it; (2) a lookup does, through the SAME
    thread pool the server uses, and inside the worker execute_tool sees the caller's principal and the G1' fence."""
    from concurrent.futures import ThreadPoolExecutor
    from services.arturo.native.server import ToolRunner
    from services.arturo.native.session import RUNNING
    from services.arturo.native.tools import ProxyExecutor, _load_proxy
    mod = _load_proxy()
    seen = []
    mod.execute_tool = lambda name, args, user_turns=None: seen.append((name, dict(mod._TEAM_TURN.get() or {}))) or "ok"
    t = ArturoTools(ToolLedger(tmp_path / "led"), executor=ProxyExecutor(mod))
    b = t.bind("owner:shawphone", "iphone", "conv-real")
    assert not {"kill_agent", "async_task", "run_command"} & {d["name"] for d in t.declared_tools(b)}
    for i, (name, args) in enumerate((("kill_agent", {"session_name": "gm"}),
                                       ("async_task", {"tool_name": "kill_agent", "tool_args": {"session_name": "gm"}}),
                                       ("run_command", {"command": "tmux kill-server"}))):
        assert t.execute(b, f"k{i}", name, json.dumps(args))["ok"] is False
    assert seen == []
    runner = ToolRunner(t, ThreadPoolExecutor(max_workers=2))
    deadline = time.time() + 5
    out = runner.execute(b, "look1", "list_agents", "{}")
    while out is RUNNING and time.time() < deadline:
        time.sleep(0.02)
        out = runner.execute(b, "look1", "list_agents", "{}")
    assert out["ok"] and seen[0][0] == "list_agents"
    assert seen[0][1]["principal"] == "owner:shawphone" and seen[0][1]["voice"] is True and seen[0][1]["fenced"] is False
    out = runner.execute(b, "look2", "list_agents", "{}")
    while out is RUNNING and time.time() < deadline:
        time.sleep(0.02)
        out = runner.execute(b, "look2", "list_agents", "{}")
    assert seen[1][1]["fenced"] is True, "the second tool of the turn must run fenced (G1')"


# ---- gm msg_07a44324: F-TD3-2, every barge silent within 1.0 s (test drive 3: one barge talked on 5.4 s) ----------
def _barge_call(tmp_path, monkeypatch, caller_speech_s):
    """GPT-Live talks for 3 s (its own thread, real time) and ignores the caller; the caller speaks for
    caller_speech_s starting 0.6 s in. Returns (emit times of Arturo speech audio, barge emit times, caller start)."""
    speech_t, barge_t, marks = [], [], {}
    orig = srv.NativeCall.emit

    def emit(self, msg):
        now = time.monotonic()
        if isinstance(msg, (bytes, bytearray)) and np.abs(np.frombuffer(bytes(msg), "<i2")).max() > 500:
            speech_t.append(now)
        elif isinstance(msg, dict) and msg.get("type") == "barge":
            barge_t.append(now)
        return orig(self, msg)
    monkeypatch.setattr(srv.NativeCall, "emit", emit)

    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.output_transcript.delta", "delta": "Here is a long answer about the fleet."})

        def provider_talks():
            for _ in range(30):
                p.push(_audio_frame())
                time.sleep(0.1)
            for _ in range(10):
                p.push(_audio_frame(rms=0))
        threading.Thread(target=provider_talks, daemon=True).start()
        await asyncio.sleep(0.6)
        marks["caller"] = time.monotonic()
        t_end = time.monotonic() + caller_speech_s
        while time.monotonic() < t_end:
            await ws.send_bytes(_pcm(0.1))
            await asyncio.sleep(0.1)
        while time.monotonic() < marks["caller"] + 3.2:
            await ws.send_bytes(_pcm(0.1, rms=0))
            await asyncio.sleep(0.1)

    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        try:
            await _call(c, script)
        finally:
            await c.close()
    _run(go())
    return speech_t, barge_t, marks["caller"]


def test_f_td3_2_a_sustained_barge_silences_arturo_within_one_second(tmp_path, monkeypatch):
    """LEGACY, behind ARTURO_NATIVE_FORCED_BARGE=1 only: Shaw 10-16 overruled it (full interjections while he talks)."""
    monkeypatch.setattr(srv, "FORCED_BARGE", True)
    speech_t, barge_t, caller = _barge_call(tmp_path, monkeypatch, caller_speech_s=1.5)
    assert barge_t, "no barge was sent"
    after = [t for t in speech_t if t > caller]
    assert after and max(after) - caller <= 1.3, f"Arturo audible {max(after) - caller:.2f} s after the caller began"


def test_f_td3_2_a_short_noise_does_not_stop_arturo(tmp_path, monkeypatch):
    """Shaw 10-14: 'a small noise did NOT stop Arturo. That was ideal.'"""
    speech_t, barge_t, caller = _barge_call(tmp_path, monkeypatch, caller_speech_s=0.35)
    assert not barge_t
    assert any(t > caller + 1.5 for t in speech_t), "Arturo stopped for a noise"


def test_ios_audio_end_follows_each_utterance(tmp_path, monkeypatch):
    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.output_transcript.delta", "delta": "Yes."})
        p.push(_audio_frame())
        for _ in range(10):
            p.push(_audio_frame(rms=0))
        await asyncio.sleep(0.8)

    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        try:
            got, p = await _call(c, script)
        finally:
            await c.close()
        kinds = ["audio" if isinstance(m, bytes) else m.get("type") for m in got]
        assert "audio_end" in kinds and kinds.index("audio_end") > kinds.index("audio")
    _run(go())


def test_review_m7_concurrent_calls_are_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "MAX_CALLS", 1)

    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        try:
            ws1 = await c.ws_connect("/native/ws", headers=HDRS)
            await ws1.send_str(json.dumps({"type": "start"}))
            for _ in range(50):
                if s.calls:
                    break
                await asyncio.sleep(0.02)
            ws2 = await c.ws_connect("/native/ws", headers=HDRS)
            await ws2.send_str(json.dumps({"type": "start"}))
            got = []

            async def read2():
                async for m in ws2:
                    got.append(json.loads(m.data))
            try:
                await asyncio.wait_for(read2(), 5)                    # a 2nd call that is NOT refused never ends
            except asyncio.TimeoutError:
                pass
            assert any(g.get("type") == "refused" and "busy" in g["reason"] for g in got), "a 2nd call was let in"
            await ws1.send_str(json.dumps({"type": "disconnect"}))
            async for _ in ws1:
                pass
        finally:
            await c.close()
        reps = [json.loads(p.read_text()) for p in (tmp_path / "native" / "calls").glob("*.json")]
        assert any("claim_late" in r and "forced_barges" in r for r in reps)
    _run(go())


def test_cancelling_a_ticker_waiting_on_close_still_releases_slot_and_writes_report(tmp_path, monkeypatch):
    """A websocket unwind cancels the ticker; that must not cancel its in-flight teardown.

    Native call ``native-9a5420f81d31`` demonstrated the former failure shape:
    aiohttp returned 101 with neither END nor a report, leaving one MAX_CALLS
    slot in memory.  Keep the transport close blocked long enough to cancel the
    original close waiter, then prove its shared finalizer finishes.
    """
    class SlowCloseTransport:
        def __init__(self):
            self.started, self.release = threading.Event(), threading.Event()

        def close(self):
            self.started.set()
            self.release.wait(timeout=2)

    class ClosedDriver:
        disconnected = True

        def send(self, payload):
            pass

    async def go():
        s = _server(tmp_path, monkeypatch)
        call = srv.NativeCall(s, None, "fleet", "iphone")
        call.t0 = time.monotonic()
        call.spend.start()
        call.driver = ClosedDriver()
        slow = call.transport = SlowCloseTransport()
        cid = call.binding.conversation_id
        s.calls[cid] = call

        ticker = asyncio.create_task(call.close("call cost cap"))
        while not slow.started.is_set():
            await asyncio.sleep(0.01)
        ticker.cancel()  # exactly what NativeCall.run() does while unwinding
        with pytest.raises(asyncio.CancelledError):
            await ticker
        slow.release.set()
        for _ in range(100):
            if cid not in s.calls:
                break
            await asyncio.sleep(0.01)
        assert cid not in s.calls
        assert (tmp_path / "native" / "calls" / f"{cid}.json").exists()

    _run(go())


def test_downlink_sender_paces_a_burst_and_keeps_audio_end_fifo(tmp_path, monkeypatch):
    """A provider backlog must not turn into a faster-than-real-time phone burst."""
    class Socket:
        def __init__(self):
            self.sent = []

        async def send_bytes(self, pcm):
            self.sent.append(("audio", time.monotonic(), bytes(pcm)))

        async def send_str(self, text):
            self.sent.append(("text", time.monotonic(), json.loads(text)))

    async def go():
        s = _server(tmp_path, monkeypatch)
        sock = Socket()
        call = srv.NativeCall(s, sock, "fleet", "iphone")
        sender = asyncio.create_task(call._sender())
        frame = _pcm(0.1)
        for _ in range(20):
            call.emit(frame)                 # Driver.pump's entire provider backlog, at once
        call.emit({"type": "audio_end"})
        call.out.put_nowait(None)
        await asyncio.wait_for(sender, 4)
        return sock.sent, call.downlink.report()

    sent, report = _run(go())
    audio = [x for x in sent if x[0] == "audio"]
    assert len(audio) == 20 and all(x[2] == _pcm(0.1) for x in audio)
    assert audio[-1][1] - audio[0][1] >= 1.7, "20 x 100ms frames must not flush as a burst"
    assert [x[0] for x in sent] == ["audio"] * 20 + ["text"]
    assert sent[-1][2]["type"] == "audio_end", "control events stay behind paced PCM"
    assert report["queued_audio_s"] == 0.0 and report["max_queued_audio_s"] >= 2.0
    assert report["paced_wait_s"] >= 1.7 and report["frames"] == 20


def test_downlink_pacer_never_catches_up_after_a_late_send():
    """A late scheduler tick resets the clock; it never repays lateness as a burst."""
    class Clock:
        now = 0.0

        async def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    pacer = srv.DownlinkPacer("native-test", warn_s=0, clock=lambda: clock.now, sleeper=clock.sleep)
    frame = _pcm(0.1)

    pacer.enqueue(frame)
    _run(pacer.await_turn(frame))             # sent at 0.0; next due at 0.1
    clock.now = 0.4                           # event loop / socket was late by 300ms
    pacer.enqueue(frame)
    _run(pacer.await_turn(frame))             # sends at 0.4, not immediately followed by a catch-up burst
    assert clock.now == 0.4 and pacer.next_due == pytest.approx(0.5)
    pacer.enqueue(frame)
    _run(pacer.await_turn(frame))
    assert clock.now == pytest.approx(0.5)


# ---- ios item 5: the $0 OFFLINE fixture on the production endpoint --------------------------------------------------
def test_offline_mode_needs_no_provider_runs_no_real_tool_and_spends_nothing(tmp_path, monkeypatch):
    ex = FakeExecutor()

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        c = await _client(s)
        got = []
        try:
            ws = await c.ws_connect("/native/ws", headers=HDRS)

            async def reader():
                async for m in ws:
                    got.append(m.data if m.type.name == "BINARY" else json.loads(m.data))
            rt = asyncio.ensure_future(reader())
            await ws.send_str(json.dumps({"type": "start", "mode": "offline", "settle": "fast"}))
            await asyncio.sleep(2.5)                                  # the scripted greeting plays
            await ws.send_str(json.dumps({"type": "say", "text": "what are my agents doing"}))
            await asyncio.sleep(1.2)
            for _ in range(8):                                        # Shaw talks over the reply: a barge
                await ws.send_bytes(_pcm(0.1))
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.5)
            await ws.send_str(json.dumps({"type": "disconnect"}))
            await asyncio.wait_for(rt, 10)
        finally:
            await c.close()
        assert FakeProvider.instances == [], "offline opened a provider socket"
        assert ex.calls == [], "offline ran a real tool"
        assert s.usage_store.added == [], "offline charged the daily cap"
        state = next(m for m in got if isinstance(m, dict) and m.get("type") == "state")
        assert state["mode"] == "offline"
        assert any(isinstance(m, bytes) for m in got)
        tools = [m for m in got if isinstance(m, dict) and m.get("type") == "tools" and m["calls"]]
        assert tools and tools[-1]["calls"][0]["name"] == "fleet_status"
        kinds = [m.get("type") for m in got if isinstance(m, dict)]
        assert "audio_end" in kinds and "barge" in kinds
    _run(go())


def test_review_m6_the_real_backing_check_ties_a_claim_to_its_tool_class(tmp_path, monkeypatch):
    """R5 survived: the claim-kind test used a stand-in is_backed. This pins ArturoTools.backed itself."""
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    b = _fleet(t)
    since = time.time() - 1
    assert t.execute(b, "c1", "spawn_agent", json.dumps(SPAWN))["ok"]
    assert t.backed(b.conversation_id, since, ("action", "start")) is True
    assert t.backed(b.conversation_id, since, ("action", "message")) is False
    assert t.backed(b.conversation_id, since, ("action", "stop")) is False
    assert t.backed(b.conversation_id, since, ("state", None)) is True
    from services.arturo.native import tools as ntools
    monkeypatch.setattr(ntools, "RECENT_RECEIPT_S", 0.0)     # 10-16: a receipt < 60 s old crosses a turn (cea7bb2a33fc)
    assert t.backed(b.conversation_id, time.time() + 5, ("state", None)) is False      # an earlier turn does not count


# ---- review round 2 (independent re-review of 881bd25a5b + 0bd800477f) -----------------------------------------
RESEARCH = {"query": "compare vendor latency and pricing"}


def test_review2_n1_research_starts_a_seat_so_it_needs_a_read_back(tmp_path):
    """research spawns a research-NNNN seat through async_task(spawn_agent): it skipped the read-back and the caps."""
    t, ex = _tools(tmp_path, spoken="Sure.")
    out = t.execute(_fleet(t), "r1", "research", json.dumps(RESEARCH))
    assert out["error"] == "READ_BACK_FIRST" and ex.calls == []


def test_review2_n1_research_counts_against_the_spawn_caps(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start research bot to compare vendor latency and pricing")
    b = _fleet(t)
    for i, (name, args) in enumerate([("spawn_agent", SPAWN), ("research", RESEARCH), ("research", RESEARCH)]):
        t.new_turn(b.conversation_id)
        assert t.execute(b, f"m{i}", name, json.dumps(args))["ok"]
    t.new_turn(b.conversation_id)
    assert t.execute(b, "m3", "research", json.dumps(RESEARCH))["error"] == "SPAWN_CAP"
    t.new_turn(b.conversation_id)
    assert t.execute(b, "m4", "spawn_agent", json.dumps(SPAWN))["error"] == "SPAWN_CAP"
    assert len(ex.calls) == 3


def test_review2_n1_a_research_receipt_backs_i_started(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start one to compare vendor latency and pricing")
    b = _fleet(t)
    since = time.time() - 1
    assert t.execute(b, "r1", "research", json.dumps(RESEARCH))["ok"]
    assert t.backed(b.conversation_id, since, ("action", "start")) is True


@pytest.mark.parametrize("reply", [
    "GM error: bridge down", "GM timed out after 60s. The query may be too complex for voice.",
    "Blocked: message looks like an internal context summary, not a real task. Not injected into 'gm'.",
    "Failed to inject on vps: no server", "Couldn't answer the menu on gm: gone",
    "Injected into 'gm' on vps but COULD NOT VERIFY it landed — check the pane", "Unknown tool: foo",
    "Session 'gm' not found on vps: no such session"])
def test_review2_n3_a_failed_action_is_never_a_receipt(tmp_path, reply):
    t, ex = _tools(tmp_path, reply=reply)
    b = _fleet(t)
    since = time.time() - 1
    out = t.execute(b, "i1", "inject_message", json.dumps({"session_name": "gm", "message": "hello"}))
    assert out["ok"] is False
    assert t.backed(b.conversation_id, since, ("action", "message")) is False


@pytest.mark.parametrize("name,reply", [
    ("inject_message", "Message injected into 'gm' on vps (verified landed)."),
    ("inject_message", "Message injected into 'gm' on vps (verified on retry)."),
    ("knowledge", "The build error from Tuesday is fixed; nothing failed since."),     # a lookup that mentions errors
    # re-review N3b: success lines echo model text, which may hold failure words
    ("inject_message", "Message injected into 'error-fixer' on vps (verified landed)."),
    ("gm_command", "GM response:\nI can't see an issue; nothing failed and no errors were found."),
    ("remember_note", "Noted and saved: the nightly job failed twice"),
    ("agent_message", "Message sent to gm: [msg_1] Re: build failed"),
    ("research", "Task queued: Web research: why do builds fail. I'll text you the result on Telegram when it's done."),
])
def test_review2_n3_real_successes_stay_successes(tmp_path, name, reply):
    t, ex = _tools(tmp_path, reply=reply, spoken="I'll research why do builds fail")
    args = {"query": "why do builds fail"} if name == "research" else {"session_name": "gm", "message": "hello"}
    out = t.execute(_fleet(t), "i1", name, json.dumps(args))
    assert out["ok"] is True


def _session(t, conv="c1"):
    from services.arturo.native.session import NativeSession
    from services.arturo.native import fixtures
    sess = NativeSession(_fleet(t, conv), t, speech_fn=lambda pcm: pcm[:1] == b"S", settle=None,
                         turn_speech_fn=lambda pcm: pcm[:1] == b"S")
    return sess, (lambda f: sess.on_server_frame(f[0])), fixtures


SPEECH_100MS = b"S" * 3200                   # 0.1 s of 16 kHz s16 audio the fake detector calls speech
QUIET_100MS = b"\0" * 3200


def _speak(sess, t, sec=0.4):
    """Caller speech from t for sec (0.1 s frames)."""
    for i in range(int(round(sec / 0.1))):
        sess.on_uplink(SPEECH_100MS, now=t + 0.1 * i)


def test_review2_n4_a_late_transcript_of_the_same_utterance_does_not_lift_the_fence(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    sess, feed, fx = _session(t)
    b = sess.binding
    _speak(sess, 1.0)
    feed(fx.input_transcript(" what are my agents doing"))
    assert t.execute(b, "l1", "list_agents", "{}")["ok"]                # a lookup result: the turn is fenced
    feed(fx.output_transcript("I found three agents."))
    feed(fx.input_transcript(" right now"))                               # the tail of the SAME utterance, late
    assert t.execute(b, "s1", "spawn_agent", json.dumps(SPAWN))["error"] == "FENCED"
    _speak(sess, 5.0)                                                     # Shaw really speaks again
    feed(fx.input_transcript(" ok start research bot"))
    assert t.execute(b, "s2", "spawn_agent", json.dumps(SPAWN))["ok"]


def test_review2_n4_speech_while_arturo_talks_is_a_new_turn(tmp_path):
    """Control: a barge (caller speech after Arturo began) is a real new turn."""
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    sess, feed, fx = _session(t)
    b = sess.binding
    _speak(sess, 1.0)
    feed(fx.input_transcript(" what are my agents doing"))
    assert t.execute(b, "l1", "list_agents", "{}")["ok"]
    feed(fx.output_transcript("I found three"))
    _speak(sess, 3.0)
    feed(fx.input_transcript(" start research bot"))
    assert t.execute(b, "s1", "spawn_agent", json.dumps(SPAWN))["ok"]


MENU = {"session": "dev-seat", "option": "1"}


def test_review2_n5_answer_menu_confirm_needs_a_staged_call_in_an_earlier_turn(tmp_path):
    """confirm=true selects a seat's menu option at once; nothing checked that Shaw said yes to a staged choice."""
    t, ex = _tools(tmp_path)
    b = _fleet(t)
    out = t.execute(b, "a1", "answer_menu", json.dumps({**MENU, "confirm": True}))
    assert out["error"] == "CONFIRM_FIRST" and ex.calls == []
    staged = t.execute(b, "a2", "answer_menu", json.dumps({**MENU, "confirm": False}))     # staged, read back
    assert staged["ok"] is False and staged["result"].startswith("CONFIRM_NEEDED")             # nothing sent yet
    t.new_turn(b.conversation_id)                                                             # Shaw: "yes"
    other = t.execute(b, "a3", "answer_menu", json.dumps({**MENU, "option": "2", "confirm": True}))
    assert other["error"] == "CONFIRM_FIRST", "a different option was never staged"
    assert t.execute(b, "a4", "answer_menu", json.dumps({**MENU, "confirm": True}))["ok"]
    assert [c[1].get("confirm") for c in ex.calls] == [False, True]


def test_review2_n5_a_staged_choice_in_the_same_turn_or_another_call_does_not_count(tmp_path):
    t, ex = _tools(tmp_path)
    b = _fleet(t)
    assert t.execute(_fleet(t, conv="other"), "o1", "answer_menu",
                     json.dumps({**MENU, "confirm": False}))["result"].startswith("CONFIRM_NEEDED")
    t.new_turn(b.conversation_id)
    assert t.execute(b, "a1", "answer_menu", json.dumps({**MENU, "confirm": True}))["error"] == "CONFIRM_FIRST"
    t2, ex2 = _tools(tmp_path / "two")
    b2 = _fleet(t2)
    t2.new_turn(b2.conversation_id)                                  # staged INSIDE the current turn (fence bypassed)
    t2.ledger.run_once_external("x0", b2.conversation_id, "fleet", "answer_menu", {**MENU, "confirm": False},
                                lambda: ({"ok": False, "result": "CONFIRM_NEEDED: I'll send option 1."}, None))
    assert t2.execute(b2, "a2", "answer_menu", json.dumps({**MENU, "confirm": True}))["error"] == "CONFIRM_FIRST"


def test_review2_n6_an_offline_call_still_hangs_up_when_idle(tmp_path, monkeypatch):
    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        got = []
        try:
            ws = await c.ws_connect("/native/ws", headers=HDRS)

            async def reader():
                async for m in ws:
                    if m.type.name != "BINARY":
                        got.append(json.loads(m.data))
            rt = asyncio.ensure_future(reader())
            await ws.send_str(json.dumps({"type": "start", "mode": "offline", "settle": "fast"}))
            for _ in range(40):
                if s.calls:
                    break
                await asyncio.sleep(0.05)
            call = next(iter(s.calls.values()))
            call.spend.idle_s = 0.5
            for _ in range(80):
                if not s.calls:
                    break
                await asyncio.sleep(0.05)
            assert not s.calls, "an idle offline call held its slot"
            await asyncio.sleep(0.2)
            await ws.send_str(json.dumps({"type": "disconnect"}))            # the client drops, as after any hang-up
            await asyncio.wait_for(rt, 5)
        finally:
            await c.close()
        assert any("idle" in json.dumps(m) for m in got)
    _run(go())


def test_review2_n4_an_offline_say_is_the_callers_speech_so_each_opens_a_turn(tmp_path, monkeypatch):
    """Offline has no uplink audio: a typed 'say' must count as the caller speaking, or after Arturo's greeting no
    later turn ever opens (stale per-turn words for the Telegram gate and the claim gate)."""
    async def go():
        s = _server(tmp_path, monkeypatch)
        c = await _client(s)
        try:
            ws = await c.ws_connect("/native/ws", headers=HDRS)

            async def reader():
                async for m in ws:
                    pass
            rt = asyncio.ensure_future(reader())
            await ws.send_str(json.dumps({"type": "start", "mode": "offline", "settle": "fast"}))
            await asyncio.sleep(2.5)                                  # the scripted greeting: Arturo has spoken
            call = next(iter(s.calls.values()))
            await ws.send_str(json.dumps({"type": "say", "text": "hello there"}))
            await asyncio.sleep(1.5)
            await ws.send_str(json.dumps({"type": "say", "text": "thanks"}))
            await asyncio.sleep(0.5)
            words = call.session.user_turn_text
            await ws.send_str(json.dumps({"type": "disconnect"}))
            await asyncio.wait_for(rt, 10)
        finally:
            await c.close()
        assert words.strip() == "thanks"
    _run(go())


def test_review3_n4b_a_noise_after_arturo_spoke_does_not_open_a_turn(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    sess, feed, fx = _session(t)
    b = sess.binding
    _speak(sess, 1.0)
    feed(fx.input_transcript(" what are my agents doing"))
    assert t.execute(b, "l1", "list_agents", "{}")["ok"]
    feed(fx.output_transcript("I found three agents."))
    sess.on_uplink(SPEECH_100MS, now=4.0)                                 # a cough / a door: 0.1 s
    sess.on_uplink(QUIET_100MS, now=4.1)
    feed(fx.input_transcript(" right now"))                               # the late tail of the old utterance
    assert t.execute(b, "s1", "spawn_agent", json.dumps(SPAWN))["error"] == "FENCED"


def test_review3_n4b_the_turn_rule_uses_the_barge_floor_not_the_activity_floor():
    from services.arturo.native import session as sm
    assert sm.TURN_SPEECH_FLOOR == 500
    assert sm._turn_speech(_pcm(0.1, rms=3000)) >= 0.08                  # speech: (nearly) every 20 ms window
    assert sm._turn_speech(_pcm(0.1, rms=200)) == 0.0                     # room noise / echo: above 60, below 500


def test_review3_n3b_every_spawn_attempt_counts_against_the_cap(tmp_path):
    """A failed or half-started spawn still counts: counting only successes let a result that LOOKED failed escape."""
    t, ex = _tools(tmp_path, spoken="I'll start research bot", reply="FAILED to spawn 'research-bot' on vps: boom")
    b = _fleet(t)
    for i in range(3):
        t.new_turn(b.conversation_id)
        assert t.execute(b, f"f{i}", "spawn_agent", json.dumps(SPAWN))["ok"] is False
    t.new_turn(b.conversation_id)
    assert t.execute(b, "f3", "spawn_agent", json.dumps(SPAWN))["error"] == "SPAWN_CAP" and len(ex.calls) == 3


def test_review3_n3b_a_task_with_failure_words_is_a_real_spawn_with_a_receipt(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start research bot to fix the failed build",
                   reply="CONFIRMED: Agent 'research-bot' is live on vps. Initial task injected: fix the failed build")
    out = t.execute(_fleet(t), "s1", "spawn_agent", json.dumps({**SPAWN, "task": "fix the failed build"}))
    assert out["ok"] is True and out["receipt"] == "accepted"


# ---- review round 4 ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,reply", [
    ("send_telegram", "Not sent \u2014 Shaw has not asked for a text. Say it instead."),
    ("send_telegram", "Message already handled (dup) \u2014 not resent (anti-spam guard)."),
    ("answer_menu", "answer_menu needs a session and an option number."),
    ("answer_menu", "Option 2 on dev is a 'Chat about this' option \u2014 that's not a decision, keep talking."),
    ("answer_menu", "That menu on dev is gone \u2014 it was answered already or the agent moved on. Nothing sent."),
    ("answer_menu", "That key isn't allowed on dev: nope. Nothing sent."),
    ("answer_menu", "I sent option 1 to dev, but its menu is STILL showing \u2014 it may not have taken."),
    ("answer_menu", "CONFIRM_NEEDED: I'll send option 1 to dev. Say yes to confirm."),       # staged, nothing sent
    ("remember_note", "No note provided."),
    ("agent_message", "to_agent and subject are required."),
    ("gm_command", "ERROR: gm_command called with empty prompt."),
    ("spawn_agent", "CONFIRMED: Agent 'research-bot' is running on vps. Warning: task injection failed."),
])
def test_review4_an_action_succeeds_only_in_a_known_success_shape(tmp_path, name, reply):
    """Round 4: failure prefixes kept drifting (a DENIED Telegram send backed "I texted you"); an action is a success
    only when its result has one of the proxy's success shapes."""
    t, ex = _tools(tmp_path, reply=reply, spoken="I'll start research bot")
    b = _fleet(t)
    args = SPAWN if name == "spawn_agent" else {"session": "dev", "option": "1", "confirm": False, "message": "hi"}
    out = t.execute(b, "x1", name, json.dumps(args))
    assert out.get("ok") is False


@pytest.mark.parametrize("name,reply", [
    ("spawn_agent", "CONFIRMED: Agent 'research-bot' is running on vps (runtime claude) and is registered."),
    ("spawn_agent", "PARTIAL: Agent 'research-bot' is running on vps (runtime claude) but it is NOT registered."),
    ("send_telegram", "Message sent to Shaw on Telegram."),
    ("answer_menu", "Sent option 1 to dev."),
    ("answer_menu", "Sent option 4 to dev and typed your answer: \u201cship it\u201d."),
])
def test_review4_the_success_shapes_are_successes(tmp_path, name, reply):
    t, ex = _tools(tmp_path, reply=reply, spoken="I'll start research bot")
    b = _fleet(t)
    if name == "answer_menu":                                  # a confirm needs its staged choice first
        t.ledger.run_once_external("st", b.conversation_id, "fleet", "answer_menu",
                                   {"session": "dev", "option": "1", "confirm": False},
                                   lambda: ({"ok": False, "result": "CONFIRM_NEEDED: I'll send option 1 to dev."}, None))
        t.new_turn(b.conversation_id)
        args = {"session": "dev", "option": "1", "confirm": True}
    else:
        args = SPAWN if name == "spawn_agent" else {"message": "hi"}
    assert t.execute(b, "x1", name, json.dumps(args))["ok"] is True


def test_review4_focus_backs_no_fix_claim(tmp_path):
    t, ex = _tools(tmp_path, reply="I'd focus the adaptiv approval.")
    b = _fleet(t)
    since = time.time() - 1
    t.execute(b, "f1", "focus_entity", json.dumps({"spoken_ref": "that one"}))
    assert t.backed(b.conversation_id, since, ("action", "fix")) is False


def _fenced_after_lookup(tmp_path):
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    sess, feed, fx = _session(t)
    _speak(sess, 1.0)
    feed(fx.input_transcript(" what are my agents doing"))
    assert t.execute(sess.binding, "l1", "list_agents", "{}")["ok"]
    feed(fx.output_transcript("I found three. Want me to start research bot?"))
    return t, sess, feed, fx


def test_review4_n4c_a_short_yes_in_one_big_frame_opens_the_turn(tmp_path):
    """Round 4: wall-clock timing never saw a 250 ms client frame as sustained; audio time does."""
    t, sess, feed, fx = _fenced_after_lookup(tmp_path)
    sess.on_uplink(b"S" * 8000, now=5.0)                                   # "yes": 0.25 s in ONE frame
    feed(fx.input_transcript(" yes"))
    assert t.execute(sess.binding, "s1", "spawn_agent", json.dumps(SPAWN))["ok"]


def test_review4_n4c_a_dip_inside_a_short_word_does_not_reset_it(tmp_path):
    t, sess, feed, fx = _fenced_after_lookup(tmp_path)
    for i, pcm in enumerate([SPEECH_100MS, QUIET_100MS, SPEECH_100MS]):   # "do it": speech, a dip, speech
        sess.on_uplink(pcm, now=5.0 + 0.1 * i)
    feed(fx.input_transcript(" do it"))
    assert t.execute(sess.binding, "s1", "spawn_agent", json.dumps(SPAWN))["ok"]


def test_review4_n4c_two_noises_seconds_apart_are_not_a_turn(tmp_path):
    t, sess, feed, fx = _fenced_after_lookup(tmp_path)
    sess.on_uplink(SPEECH_100MS, now=5.0)
    sess.on_uplink(SPEECH_100MS, now=9.0)                                  # the stream paused between them
    feed(fx.input_transcript(" right now"))
    assert t.execute(sess.binding, "s1", "spawn_agent", json.dumps(SPAWN))["error"] == "FENCED"


@pytest.mark.parametrize("reply", ["Session 'gm' not found on vps: no such session", "GM error: bridge down",
                                   "Couldn't resolve what you're referring to: x", "Unknown tool: foo"])
def test_review4_a_failed_lookup_backs_no_state_claim(tmp_path, reply):
    """Lookups have no success shape, so their failure prefix is what keeps "it's running" from being backed."""
    t, ex = _tools(tmp_path, reply=reply)
    b = _fleet(t)
    since = time.time() - 1
    assert t.execute(b, "g1", "get_agent_output", json.dumps({"session_name": "gm"}))["ok"] is False
    assert t.backed(b.conversation_id, since, ("state", None)) is False


# ---- review round 5 (APPROVE WITH FIXES): minor hardening ----------------------------------------------------------
def test_review5_a_refused_lookup_backs_no_state_claim(tmp_path):
    t, ex = _tools(tmp_path, reply="Refused: ~/.ssh/id_ed25519 is a secret and is never read into a conversation.")
    b = _fleet(t)
    since = time.time() - 1
    assert t.execute(b, "r1", "get_agent_output", json.dumps({"session_name": "gm"}))["ok"] is False
    assert t.backed(b.conversation_id, since, ("state", None)) is False


def _clicks(n_loud_windows, rms=4000):
    """0.1 s of 16 kHz audio: silence with n loud 20 ms windows."""
    x = np.zeros(1600, dtype="<i2")
    for k in range(n_loud_windows):
        x[k * 320:(k + 1) * 320] = rms
    return x.tobytes()


def test_review5_n14_speech_is_credited_per_20ms_window_not_per_chunk(tmp_path):
    """Two clicks (one loud 20 ms window each) within 0.6 s credited 0.2 s before; they are 0.04 s of speech."""
    from services.arturo.native.session import NativeSession
    from services.arturo.native import fixtures as fx
    t, ex = _tools(tmp_path, spoken="I'll start research bot")
    sess = NativeSession(_fleet(t), t, speech_fn=lambda pcm: True, settle=None)      # the REAL turn detector
    feed = lambda f: sess.on_server_frame(f[0])
    sess.on_uplink(_clicks(5), now=1.0); sess.on_uplink(_clicks(5), now=1.1); sess.on_uplink(_clicks(5), now=1.2)
    feed(fx.input_transcript(" what are my agents doing"))
    assert t.execute(sess.binding, "l1", "list_agents", "{}")["ok"]
    feed(fx.output_transcript("I found three."))
    sess.on_uplink(_clicks(1), now=4.0); sess.on_uplink(_clicks(0), now=4.1); sess.on_uplink(_clicks(1), now=4.2)
    feed(fx.input_transcript(" right now"))
    assert t.execute(sess.binding, "s1", "spawn_agent", json.dumps(SPAWN))["error"] == "FENCED"
    sess.on_uplink(_clicks(5), now=6.0); sess.on_uplink(_clicks(5), now=6.1); sess.on_uplink(_clicks(3), now=6.2)
    feed(fx.input_transcript(" yes start it"))                                          # 0.26 s of real speech
    assert t.execute(sess.binding, "s2", "spawn_agent", json.dumps(SPAWN))["ok"]


def test_a_question_to_gm_never_holds_the_call_and_gms_answer_comes_in_as_a_note(tmp_path, monkeypatch):
    # Shaw's 18:3x call, 2026-11-26: three gm_command calls each held the voice 25 s while the proxy polled gm's reply
    ex = FakeExecutor(reply=lambda name, args: "gm says:\nIt is the backend's tool decision.", delay=3.0)
    t = {}

    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.input_transcript.delta", "delta": "ask the gm why it lags"})
        p.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_G", "name": "gm_command", "arguments": "{}"}}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                       "usage": {"input_tokens": 10, "output_tokens": 2}}}})
        t["asked"] = time.monotonic()
        for _ in range(60):
            if any(f["type"] == "response.item.create" for f in p.sent):
                t["answered"] = time.monotonic()
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(3.5)                         # gm's reply lands (3 s) while the call is still up

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        c = await _client(s)
        try:
            return await _call(c, script)
        finally:
            await c.close()
    got, p = _run(go())
    outs = [f for f in p.sent if f["type"] == "response.item.create"]
    assert len(outs) == 1 and json.loads(outs[0]["item"]["output"]) == srv.DETACHED
    assert t["answered"] - t["asked"] < srv.DETACH_AFTER_S + 0.6, "the call waited on gm's reply"
    notes = [json.dumps(f) for f in p.sent if f["type"] not in ("response.item.create", "session.start")]
    assert any("It is the backend" in n and "gm" in n for n in notes), "gm's answer never reached the call"


def test_a_tool_on_a_page_thread_call_is_tagged_with_that_thread(tmp_path, monkeypatch):
    # Shaw's 18:3x call: its three questions to gm were tagged "thread gm chat", not the page's thread web_ypc88hel
    ex = FakeExecutor(reply="seats: gm")

    async def script(ws, p):
        p.push({"type": "session.started"})
        p.push({"type": "session.input_transcript.delta", "delta": "list my agents"})
        p.push({"type": "session.delegation.created", "delegation": {"id": "d1"}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "call_id": "call_T", "name": "list_agents", "arguments": "{}"}}})
        p.push({"type": "response.event", "delegation_id": "d1", "event": {
            "type": "response.completed", "response": {"model": "gpt-6-luna",
                                                       "usage": {"input_tokens": 10, "output_tokens": 2}}}})
        await asyncio.sleep(1.0)

    async def go():
        s = _server(tmp_path, monkeypatch, executor=ex)
        c = await _client(s)
        try:
            return await _call(c, script, start={"type": "start", "thread": "web_test1"})
        finally:
            await c.close()
    _run(go())
    assert ex.call_seen["thread_id"] == "web_test1"


class _SendLog:
    disconnected = False

    def __init__(self):
        self.sent = []

    def send(self, frame):
        self.sent.append(frame)


def _rejected_backend_call(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch)
    call = srv.NativeCall(s, None, "fleet", "iphone")
    call.mode, call.driver = "live", _SendLog()
    closed = []

    async def close(reason):
        closed.append(reason)
    call.close = close
    return call, closed


REJECTED = {"type": "error", "event_id": "event_x", "error": {
    "type": "invalid_request_error", "code": "responses_validation_failed", "param": "session.delegation.responses",
    "message": "Responses configuration validation failed: Unsupported value: 'minimal' is not supported with the "
               "'gpt-6-luna' model.", "client_event_id": "evt_start_000001"}}


def test_a_rejected_backend_config_ends_the_call_with_a_spoken_and_visible_notice(tmp_path, monkeypatch):
    # live probe 2026-11-26 (reasoning 'minimal' on gpt-6-luna): the start was rejected by an error frame, the session
    # kept running with NO backend, and Arturo still said "I'm getting the GM to check on that". Nothing was checked.
    call, closed = _rejected_backend_call(tmp_path, monkeypatch)
    call.on_frame("in", REJECTED)
    out = []
    while not call.out.empty():
        out.append(call.out.get_nowait())
    notices = [m for m in out if isinstance(m, dict) and m.get("type") == "notice"]
    assert [n.get("code") for n in notices] == ["backend_rejected"]
    said = [f for f in call.driver.sent if f["type"] == "session.thinking.append"]
    assert len(said) == 1 and "end" in said[0]["content"].lower()
    assert call.backend_rejected.startswith("responses_validation_failed")
    assert not _run(_tick_fatal(call, 0.0)) and closed == []          # Arturo gets to say it first
    call._end_utterance(time.monotonic())                             # ... and once he has, the call ends
    assert _run(_tick_fatal(call, 0.0)) and closed == [srv.BACKEND_REJECTED_REASON]
    call.on_frame("in", REJECTED)                                     # a repeat never notices or speaks twice
    assert len([f for f in call.driver.sent if f["type"] == "session.thinking.append"]) == 1


def test_a_rejected_backend_ends_the_call_by_the_deadline_even_if_arturo_never_speaks(tmp_path, monkeypatch):
    call, closed = _rejected_backend_call(tmp_path, monkeypatch)
    call.on_frame("in", REJECTED)
    assert not _run(_tick_fatal(call, srv.BACKEND_REJECTED_SAY_S - 0.5))
    assert _run(_tick_fatal(call, srv.BACKEND_REJECTED_SAY_S + 0.1)) and closed == [srv.BACKEND_REJECTED_REASON]


def test_other_provider_errors_do_not_end_the_call(tmp_path, monkeypatch):
    call, closed = _rejected_backend_call(tmp_path, monkeypatch)
    call.on_frame("in", {"type": "error", "error": {"code": "rate_limit_exceeded", "param": None, "message": "slow"}})
    assert call.backend_rejected is None
    assert not _run(_tick_fatal(call, 60.0)) and closed == []


async def _tick_fatal(call, later_s):
    return await call._check_backend_rejected(time.monotonic() + later_s)


def test_the_report_says_the_backend_was_rejected(tmp_path, monkeypatch):
    async def go():
        s = _server(tmp_path, monkeypatch)
        call = srv.NativeCall(s, None, "fleet", "iphone")
        call.mode, call.t0 = "live", time.monotonic()
        call.spend.start()
        call.driver = _SendLog()
        call.driver.disconnected = True
        s.calls[call.binding.conversation_id] = call
        call.on_frame("in", REJECTED)
        call._end_utterance(time.monotonic())
        assert await call._check_backend_rejected(time.monotonic())
        return json.loads((tmp_path / "native" / "calls" / f"{call.binding.conversation_id}.json").read_text())

    rep = _run(go())
    assert rep["reason"] == srv.BACKEND_REJECTED_REASON
    assert rep["backend_rejected"].startswith("responses_validation_failed: Responses configuration validation failed")


def test_an_utterance_already_under_way_does_not_count_as_the_notice(tmp_path, monkeypatch):
    # the rejection lands ~1.4 s after start, about when the greeting begins (its first deltas may be quiet): the
    # greeting ending is not Arturo saying the tools are down, and the notice gets its full time from that end.
    call, closed = _rejected_backend_call(tmp_path, monkeypatch)
    quiet = base64.b64encode(_pcm(0.1, rms=40)).decode()             # far below speech level, but not silence
    call.on_frame("in", {"type": "session.output_audio.delta", "delta": quiet})     # the greeting is under way
    call.on_frame("in", REJECTED)
    call._utterance_t0 -= 6.0                                         # it began 6 s ago, 1 s before the rejection,
    call._backend_rejected_at -= 5.0                                  # ... and runs 5 s past the rejection
    call._end_utterance(time.monotonic())                             # the greeting ends
    assert not _run(_tick_fatal(call, srv.BACKEND_REJECTED_SAY_S - 0.5)) and closed == []
    call.on_frame("in", {"type": "session.output_audio.delta", "delta": quiet})     # the notice line
    call._end_utterance(time.monotonic())
    assert _run(_tick_fatal(call, 0.0)) and closed == [srv.BACKEND_REJECTED_REASON]


def test_the_digital_silence_between_utterances_does_not_start_one(tmp_path, monkeypatch):
    # Claude review of 47a66fa: GPT-Live streams all-zero audio between utterances; counted as an utterance start, it
    # made the notice look older than the rejection and pushed the close back by up to 8 s.
    call, closed = _rejected_backend_call(tmp_path, monkeypatch)
    zeros = base64.b64encode(b"\x00" * 3200).decode()
    call.on_frame("in", {"type": "session.output_audio.delta", "delta": zeros})     # silence before the rejection
    call.on_frame("in", REJECTED)
    call.on_frame("in", {"type": "session.output_audio.delta", "delta": base64.b64encode(_pcm(0.1)).decode()})
    call._end_utterance(time.monotonic())                             # the notice ends: the call ends now
    assert _run(_tick_fatal(call, 0.0)) and closed == [srv.BACKEND_REJECTED_REASON]
