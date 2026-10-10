"""VOICE-RESULTS gates (gm msg_b0b4228c; the operator apr_4ec0ca94 "can't walk and chew gum").
(a) never over the operator or over Arturo; (b) lead-in names the question; (c) ended call or stale
result -> Telegram; (d) one result at a time per call. Exactly one channel per result."""
import base64
import json

from services.arturo import stream_relay as sr
from services.arturo import voice_delivery as vd
from services.arturo.test_stream_relay_hume import _manager, _wait, _wav48


class FakeRelay:
    def __init__(self, script):
        self.script = list(script)       # outcomes returned in order; last one repeats
        self.calls = []

    def try_speak(self, cid, text, user_quiet_s=2.5, now=None):
        self.calls.append((cid, text, now))
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]


def _d(relay, **kw):
    return vd.VoiceResultDelivery(relay, start_thread=False, clock=lambda: 100.0, **kw)


def _tg():
    sent = []
    return sent, (lambda: sent.append(1))


# ---- delivery queue (c), (d), one-channel ----

def test_no_call_goes_straight_to_telegram():
    sent, tg = _tg()
    assert _d(FakeRelay(["spoken"])).submit(None, "q", "answer", tg) == "telegram:no-call"
    assert sent == [1]


def test_spoken_short_result_never_also_texted():
    sent, tg = _tg()
    r = FakeRelay(["spoken"])
    d = _d(r)
    d.submit("c1", "Deep dive: why is chat view broken", "Because build 256 changed it.", tg)
    assert d.tick(now=100.0) is False
    assert sent == [], "spoken in full -> no Telegram copy (never both)"
    line = r.calls[0][1]
    assert line.startswith("About your question on why is chat view broken:"), line   # (b)


def test_waits_while_gated_then_speaks():
    sent, tg = _tg()
    r = FakeRelay(["wait:user-speaking", "wait:agent-speaking", "spoken"])
    d = _d(r)
    d.submit("c1", "q one two", "answer", tg)
    assert d.tick(now=100.5) is True
    assert d.tick(now=101.0) is True
    assert d.tick(now=101.5) is False
    assert sent == [] and len(r.calls) == 3


def test_call_ended_falls_back_to_telegram_once():
    sent, tg = _tg()
    d = _d(FakeRelay(["gone"]))
    d.submit("c1", "q", "answer", tg)
    d.tick(now=100.5)
    d.tick(now=101.0)
    assert sent == [1]


def test_stale_result_goes_to_telegram_not_voice():
    sent, tg = _tg()
    r = FakeRelay(["wait:user-speaking"])
    d = _d(r, max_age_s=180)
    d.submit("c1", "q", "answer", tg)
    d.tick(now=200.0)
    assert sent == []
    d.tick(now=281.0)                                   # 181 s old
    assert sent == [1]
    assert all(now < 281.0 for _, _, now in r.calls), "a stale result must never be offered to voice"


def test_one_result_at_a_time_per_call():
    sent, tg = _tg()
    r = FakeRelay(["wait:agent-speaking"])
    d = _d(r)
    d.submit("c1", "first question", "a1", tg)
    d.submit("c1", "second question", "a2", tg)
    d.tick(now=100.5)
    assert len(r.calls) == 1 and "first question" in r.calls[0][1], "(d) only the head is offered"


def test_long_result_spoken_short_and_full_text_texted():
    sent, tg = _tg()
    r = FakeRelay(["spoken"])
    d = _d(r)
    d.submit("c1", "q", "Sentence one is here. " * 60, tg)
    d.tick(now=100.5)
    assert sent == [1]
    assert len(r.calls[0][1]) < 700 and r.calls[0][1].endswith("I've texted you the full answer.")


# ---- relay gates (a), on the real _Holder ----

def _live(m, cid="h1"):
    m.feed_audio(cid, b"\x00" * 10)
    assert _wait(lambda: m._t)
    return m._t[0], m._holders[cid]


def test_relay_gate_user_speaking_reply_due_agent_speaking_then_spoken():
    m = _manager()
    try:
        s, h = _live(m)
        s.push({"type": "user_message", "interim": False, "message": {"content": "what's up with the build"}})
        assert _wait(lambda: h._awaiting_reply)
        t = sr.time.time()
        assert m.try_speak("h1", "About x: y", now=t + 0.5) == "wait:user-speaking"
        assert m.try_speak("h1", "About x: y", now=t + 5.0) == "wait:reply-due"
        s.push({"type": "audio_output", "id": "a", "index": 0, "data": base64.b64encode(_wav48()).decode()})
        s.push({"type": "assistant_end"})
        assert _wait(lambda: not h._awaiting_reply)
        h._play_until = t + 8.0                         # a long reply still playing on the watch
        assert m.try_speak("h1", "About x: y", now=t + 6.0) == "wait:agent-speaking"
        with h._speak_lock:
            h._speaking = False
        assert m.try_speak("h1", "About x: y", now=t + 9.0) == "spoken"
        frames = [f for f in s.sent if f.get("type") == "assistant_input"]
        assert frames and frames[-1]["text"] == "About x: y"
        assert m.try_speak("h1", "About z: w", now=t + 9.5) == "wait:agent-speaking", \
            "(d) the spoken result reserves its own playback window"
    finally:
        m.shutdown()


def test_relay_unknown_or_ended_call_is_gone():
    m = _manager()
    try:
        _live(m)
        assert m.try_speak("nope", "x") == "gone"
        m.end("h1")
        assert m.try_speak("h1", "x") == "gone"
    finally:
        m.shutdown()


# ---- replay: vc_97f296f6dc024d1b (phone, 2026-10-06 23:18 ET, the deep_query call) ----

def test_replay_deep_query_call_result_never_lands_over_shaw_or_arturo():
    """Journal timeline (s from start): 0.0 the operator asks; deep_query; 64.3 the operator: "Are you gonna
    tell me now?"; 66.0 Arturo replies (~6 s of audio). The gm_command result landing time
    is not logged; replay it landing at 62.0, inside the operator's second question. Gates must hold
    it through the operator's turn AND Arturo's reply, then speak it in the first quiet window."""
    m = _manager()
    try:
        _s, h = _live(m)
        T0 = 1_000_000.0
        user_speech = [(0.0, 15.0), (61.5, 64.3)]       # uplink speech spans (s)
        arturo_audio = [(66.0, 72.0)]                    # reply playback window
        r = vd.VoiceResultDelivery(m, start_thread=False, clock=lambda: T0 + 62.0)
        sent, tg = _tg()
        r.submit("h1", "Deep dive: why chats land in terminal view", "Build 256 routes them there.", tg)
        spoken_at = None
        t = 62.0
        while t < 120.0 and spoken_at is None:
            last = max([e for s0, e in user_speech if s0 <= t] or [-99])
            h._last_user_ts = T0 + min(last, t)
            h._awaiting_reply = 64.3 <= t < 66.0
            h._play_until = max(T0 + e for _s0, e in arturo_audio) if t >= 66.0 else 0.0
            with h._speak_lock:
                h._speaking = any(s0 <= t < e for s0, e in arturo_audio)
            r.tick(now=T0 + t)
            if not r._q:
                spoken_at = t
            t += 0.5
        assert spoken_at is not None and sent == [], "spoken, not texted"
        assert not any(s0 <= spoken_at < e + 2.5 for s0, e in user_speech), spoken_at
        assert not any(s0 <= spoken_at < e + 0.8 for s0, e in arturo_audio), spoken_at
        assert spoken_at <= 75.0, f"first quiet window was ~72.8 s; spoke at {spoken_at}"
    finally:
        m.shutdown()


# ---- proxy seam: flag OFF byte-identical, flag ON routes to voice ----

def _proxy(monkeypatch):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_vr", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from services.arturo import dispatcher as dp
    monkeypatch.setattr(dp, "run_analyst", lambda *a, **k: (False, ""))
    delivered, submitted = [], []
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: delivered.append(cmd[-1]) or type("R", (), {"returncode": 0})())
    monkeypatch.setattr(mod._TG_OUTBOX, "allow", lambda text: (True, ""))
    orig = mod.execute_tool
    def ex(name, args, user_turns=None):
        if name == "gm_command":
            return "Build 256 routes conversational chats to terminal view."
        return orig(name, args, user_turns)
    monkeypatch.setattr(mod, "execute_tool", ex)

    class _VR:
        def submit(self, cid, q, text, tg):
            submitted.append((cid, q, text))

        def started(self, cid, summary):
            return object()

        def finished(self, cid, tok):
            pass

        def context_note(self, cid, now=None):
            return ""
    monkeypatch.setattr(mod, "_voice_results", lambda: _VR())
    # public: actions need a fleet turn (#278); these tests are about routing, not the principal
    from services.arturo.conftest import as_fleet
    return as_fleet(mod), delivered, submitted


def _join_async():
    import threading, time
    end = time.time() + 3
    while time.time() < end and any(t.daemon and t.name.startswith("Thread") and t.is_alive()
                                    for t in threading.enumerate()):
        time.sleep(0.02)


def test_proxy_flag_off_is_telegram_only(monkeypatch):
    monkeypatch.delenv("ARTURO_VOICE_RESULTS", raising=False)
    mod, delivered, submitted = _proxy(monkeypatch)
    mod._RELAY_CID_THIS_TURN.set("CID1")
    from services.arturo import dispatcher as dp
    out = mod.execute_tool("deep_query", {"question": "why are chats in terminal view"})
    _join_async()
    assert out == dp.SPOKEN_DEEP_FALLBACK
    assert submitted == [] and delivered and "Build 256" in delivered[0]


def test_proxy_flag_on_live_call_routes_result_to_voice(monkeypatch):
    monkeypatch.setenv("ARTURO_VOICE_RESULTS", "1")
    mod, delivered, submitted = _proxy(monkeypatch)
    mod._RELAY_CID_THIS_TURN.set("CID1")
    out = mod.execute_tool("deep_query", {"question": "why are chats in terminal view"})
    _join_async()
    assert "tell you" in out and "text you if we've hung up" in out
    assert submitted and submitted[0][0] == "CID1" and "Build 256" in submitted[0][2]
    assert delivered == [], "the queue owns the Telegram fallback; nothing sent directly"


def test_proxy_flag_on_but_no_live_call_stays_telegram(monkeypatch):
    monkeypatch.setenv("ARTURO_VOICE_RESULTS", "1")
    mod, delivered, submitted = _proxy(monkeypatch)
    mod._RELAY_CID_THIS_TURN.set(None)
    from services.arturo import dispatcher as dp
    out = mod.execute_tool("ask_gm", {"request": "status of the iOS build"})
    _join_async()
    assert out == dp.SPOKEN_ASK_GM_ACK and submitted == [] and delivered


def test_clm_endpoint_carries_live_relay_cid_into_async_result(monkeypatch, tmp_path):
    """Call-site proof: the relay cid is set in the CLM view and must still be visible to
    execute_tool when the model calls deep_query INSIDE the streaming generator."""
    monkeypatch.setenv("ARTURO_VOICE_RESULTS", "1")
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    mod, delivered, submitted = _proxy(monkeypatch)
    m = _manager()
    try:
        m.feed_audio("CIDE2E", b"\x00" * 10)
        assert _wait(lambda: m._t)
        monkeypatch.setattr(mod, "_STREAM_RELAY", m)
        monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)

        class _Fn:
            name = "deep_query"
            arguments = json.dumps({"question": "why are chats in terminal view"})

        class _TC:
            id = "tc1"
            type = "function"
            function = _Fn()

        calls = []

        class _Completions:
            def create(self, **kw):
                calls.append(1)
                first = len(calls) == 1

                class _Msg:
                    tool_calls = [_TC()] if first else None
                    content = None if first else "On it."

                class _Choice:
                    finish_reason = "tool_calls" if first else "stop"
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]
                return _Resp()

        # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
        monkeypatch.setattr(mod.brain, "complete", lambda **kw: _Completions().create(**kw))
        monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
        # public #278/G1': a voice call gets full tools only when the gateway stamped its caller
        # (test_call_principal.py proves that path). Here the call is one the gateway stamped as
        # the fleet, so this test stays about carrying the relay cid into the async result.
        monkeypatch.setattr(mod, "_call_principal", lambda cid: "fleet")
        c = mod.app.test_client()
        r = c.post("/v1/chat/completions?custom_session_id=CIDE2E",
                   json={"messages": [{"role": "assistant", "content": "Hey the operator, Arturo here."},
                                      {"role": "user", "content": "why are chats in terminal view"}]},
                   headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
        r.get_data()
        _join_async()
        assert submitted and submitted[0][0] == "CIDE2E", (submitted, delivered)
        assert delivered == []
    finally:
        m.shutdown()


# ---- the operator apr_e18bde55: acknowledge at once, especially when work is already running ----

def test_context_note_lists_running_and_ready_work_per_call():
    d = _d(FakeRelay(["wait:user-speaking"]))
    assert d.context_note("c1") == ""
    tok = d.started("c1", "Deep dive: why chats land in terminal view")
    note = d.context_note("c1", now=130.0)
    assert "RUNNING for 30s: why chats land in terminal view" in note
    assert "Do NOT start the same work again" in note
    assert d.context_note("c2") == "", "per call"
    d.finished("c1", tok)
    sent, tg = _tg()
    d.submit("c1", "Deep dive: build status", "green", tg)
    note = d.context_note("c1")
    assert "RUNNING" not in note and "ANSWER READY" in note and "build status" in note


def _e2e(monkeypatch, tmp_path, flag, gm_block=None):
    if flag:
        monkeypatch.setenv("ARTURO_VOICE_RESULTS", "1")
    else:
        monkeypatch.delenv("ARTURO_VOICE_RESULTS", raising=False)
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    mod, delivered, submitted = _proxy(monkeypatch)
    if gm_block is not None:
        orig = mod.execute_tool
        def ex(name, args, user_turns=None):
            if name == "gm_command":
                gm_block.wait(5)
                return "done"
            return orig(name, args, user_turns)
        monkeypatch.setattr(mod, "execute_tool", ex)
    m = _manager()
    m.feed_audio("CIDACK", b"\x00" * 10)
    assert _wait(lambda: m._t)
    monkeypatch.setattr(mod, "_STREAM_RELAY", m)
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    if flag:
        from services.arturo import voice_delivery as _vdm
        real = _vdm.VoiceResultDelivery(m, start_thread=False)
        monkeypatch.setattr(mod, "_voice_results", lambda: real)
    seen = []

    class _Fn:
        name = "deep_query"
        arguments = json.dumps({"question": "why are chats in terminal view"})

    class _TC:
        id = "tc1"
        type = "function"
        function = _Fn()

    class _Completions:
        def create(self, **kw):
            seen.append(kw["messages"])
            first = len(seen) == 1

            class _Msg:
                tool_calls = [_TC()] if first else None
                content = None if first else "Model follow-up text."

            class _Choice:
                finish_reason = "tool_calls" if first else "stop"
                message = _Msg()

            class _Resp:
                choices = [_Choice()]
            return _Resp()

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    _cmp = _Completions()
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _cmp.create(**kw))
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    monkeypatch.setattr(mod, "_call_principal", lambda cid: "fleet")   # a gateway-stamped call (#278/G1')
    return mod, m, seen


def _post(mod, text):
    c = mod.app.test_client()
    r = c.post("/v1/chat/completions?custom_session_id=CIDACK",
               json={"messages": [{"role": "assistant", "content": "Hey the operator, Arturo here."},
                                  {"role": "user", "content": text}]},
               headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    from services.arturo.test_answered_final_extension import _deltas
    return "".join(_deltas(r.get_data(as_text=True)))


def test_fast_ack_speaks_dispatch_line_without_a_second_model_round(monkeypatch, tmp_path):
    mod, m, seen = _e2e(monkeypatch, tmp_path, flag=True)
    try:
        spoken = _post(mod, "why are chats in terminal view")
        assert len(seen) == 1, "fast-ack must not spend a follow-up model round"
        assert "I'll tell you as soon as it's back" in spoken
        assert "Model follow-up text" not in spoken
    finally:
        _join_async()
        m.shutdown()


def test_flag_off_keeps_the_follow_up_round(monkeypatch, tmp_path):
    mod, m, seen = _e2e(monkeypatch, tmp_path, flag=False)
    try:
        spoken = _post(mod, "why are chats in terminal view")
        assert len(seen) == 2 and "Model follow-up text" in spoken
    finally:
        _join_async()
        m.shutdown()


def test_follow_up_turn_sees_the_running_work(monkeypatch, tmp_path):
    import threading
    gate = threading.Event()
    mod, m, seen = _e2e(monkeypatch, tmp_path, flag=True, gm_block=gate)
    try:
        _post(mod, "why are chats in terminal view")
        n = len(seen)
        _post(mod, "are you gonna tell me now")
        sys2 = seen[n][0]["content"]
        assert "BACKGROUND WORK ON THIS CALL" in sys2 and "RUNNING for" in sys2, sys2[-400:]
    finally:
        gate.set()
        _join_async()
        m.shutdown()
