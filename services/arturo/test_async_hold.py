"""ASYNC-HOLD (gm msg_2c161950 (iii)): a FRAGMENT final must not dispatch background work.
Replay relay E58F077A / Hume chat dabf1bd3 (10-09): Hume's first final "Turn there." (the operator's
garbled opening words, 380.1) dispatched async gm_command("Turn there.") at once, and at call
end the operator got a Telegram result for it. the operator was still talking (uplink RMS ~1400 until ~401.5);
the request for the grown turn began at 391.9, 11.8 s later, so a fixed 2 s hold misses it.
Rule: hold async dispatch until the operator has been quiet (relay speech/finals) for QUIET_S, at least
MIN_S, at most MAX_S; cancel if a newer DIFFERENT request began. Sync lookups never wait."""
from services.arturo.test_hume_recovery import _Q1, _Q2, _live
from services.arturo.test_stream_relay_hume import _manager


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _settle(m, cid, t0, clk, script):
    """Run wait_turn_settled on a fake clock; `script(t)` mutates state as time advances."""
    def sleep(dt):
        clk.t += dt
        script(clk.t)
    return m.wait_turn_settled(cid, t0, clock=clk, sleep=sleep)


def test_replay_fragment_is_cancelled_when_the_turn_grows():
    m = _manager()
    try:
        s, h = _live(m, "H1")
        T = 1_000_000.0
        clk = _Clock(T)
        m.note_clm_request("H1", T, "Turn there.")
        h._last_user_ts = T                        # the operator talking

        def script(t):
            if t < T + 21.5:
                h._last_user_ts = t                # speech keeps refreshing until ~401.5
            if abs(t - (T + 11.8)) < 0.13:
                m.note_clm_request("H1", T + 11.8, _Q1)
        assert _settle(m, "H1", T, clk, script) == "superseded"
    finally:
        m.shutdown()


def test_a_complete_question_dispatches_after_the_quiet_gap():
    m = _manager()
    try:
        s, h = _live(m, "H2")
        T = 1_000_000.0
        clk = _Clock(T)
        m.note_clm_request("H2", T, "what is gm doing right now")
        h._last_user_ts = T - 0.8                  # final came 0.8 s after he stopped
        assert _settle(m, "H2", T, clk, lambda t: None) == "settled"
        assert clk.t - T < 2.6, "a finished question must not wait much past MIN_S"
    finally:
        m.shutdown()


def test_identical_re_ask_does_not_cancel():
    m = _manager()
    try:
        s, h = _live(m, "H3")
        T = 1_000_000.0
        clk = _Clock(T)
        m.note_clm_request("H3", T, _Q1)
        h._last_user_ts = T - 1

        def script(t):
            if abs(t - (T + 1.0)) < 0.13:
                m.note_clm_request("H3", T + 1.0, _Q1 + " {calm}")   # vendor retry, same words
        assert _settle(m, "H3", T, clk, script) == "settled"
    finally:
        m.shutdown()


def test_endless_talk_dispatches_at_the_cap():
    m = _manager()
    try:
        s, h = _live(m, "H4")
        T = 1_000_000.0
        clk = _Clock(T)
        m.note_clm_request("H4", T, _Q2)

        def script(t):
            h._last_user_ts = t                    # never quiet, never superseded
        assert _settle(m, "H4", T, clk, script) == "timeout"
        assert clk.t - T <= m.ASYNC_HOLD_MAX_S + 0.5
    finally:
        m.shutdown()


def test_no_live_call_means_no_hold():
    m = _manager()
    try:
        clk = _Clock(1_000_000.0)
        assert _settle(m, "NOPE", clk.t, clk, lambda t: None) == "gone"
        assert clk.t == 1_000_000.0
    finally:
        m.shutdown()


def _proxy_with_async_model(monkeypatch, tmp_path, m):
    """The real CLM endpoint; the model asks for async_task(gm_command) once, then speaks.
    Telegram is NEUTERED (outbox refuses, tg-notify stubbed): a test must never text the operator."""
    import importlib.util, json as _j, pathlib, threading
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    monkeypatch.setenv("ARTURO_VOICE_RESULTS", "0")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))   # call records: never the host's
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_hold", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    mod.build_context = lambda **k: "BASECTX"
    monkeypatch.setattr(mod._TG_OUTBOX, "allow", lambda *a, **k: (False, "test"))

    def _no_tg(argv, *a, **k):
        raise AssertionError(f"test tried to run {argv[:2]}")
    monkeypatch.setattr(mod.subprocess, "run", _no_tg)
    ran = []
    done = threading.Event()
    real = mod.execute_tool

    def spy(name, args=None, *a, **k):
        if name == "gm_command":
            ran.append(args)
            done.set()
            return "gm says hi"
        return real(name, args, *a, **k)
    monkeypatch.setattr(mod, "execute_tool", spy)

    class _Fn:
        name = "async_task"
        arguments = _j.dumps({"tool_name": "gm_command", "tool_args": {"prompt": "Turn there."},
                              "summary": "Deep dive: Turn there."})

    class _Call:
        id = "c1"
        type = "function"
        function = _Fn()

    class _Msg:
        def __init__(self, calls, content):
            self.tool_calls, self.content = calls, content

    class _Choice:
        def __init__(self, msg, fr):
            self.message, self.finish_reason = msg, fr

    class _Resp:
        def __init__(self, choice):
            self.choices = [choice]
    n = {"i": 0}

    class _C:
        def create(self, **kw):
            n["i"] += 1
            if n["i"] == 1:
                return _Resp(_Choice(_Msg([_Call()], ""), "tool_calls"))
            return _Resp(_Choice(_Msg(None, "On it, I'll text you."), "stop"))

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    _cmp = _C()
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _cmp.create(**kw))
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    monkeypatch.setattr(mod, "_STREAM_RELAY", m)
    assert str(mod.ARTURO_STATE).startswith(str(tmp_path)), "fence: test state must be in tmp"
    return mod, ran, done


CID1 = "11111111-2222-4333-8444-5555555555a1"     # visibly synthetic, valid v4 (call records)
CID2 = "11111111-2222-4333-8444-5555555555a2"


def _post(mod, cid, q):
    mod._record_call(cid, "fleet")                   # the operator's own device: full tools
    _r = mod.app.test_client().post(
        f"/v1/chat/completions?custom_session_id={cid}",
        json={"messages": [{"role": "assistant", "content": "Hey the operator, Arturo here."},
                           {"role": "user", "content": q}]},
        headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"})
    return _r.status_code, _r.get_data(as_text=True)[:300]


def test_clm_endpoint_fragment_async_is_never_run(monkeypatch, tmp_path):
    import time as _t
    m = _manager()
    try:
        s, h = _live(m, CID1)
        monkeypatch.setattr(m, "ASYNC_HOLD_MIN_S", 0.3)
        monkeypatch.setattr(m, "ASYNC_HOLD_QUIET_S", 0.3)
        mod, ran, done = _proxy_with_async_model(monkeypatch, tmp_path, m)
        h._last_user_ts = _t.time() + 0.5                 # the operator still talking
        assert _post(mod, CID1, "Turn there.")[0] == 200
        m.note_clm_request(CID1, _t.time(), _Q1)       # Hume's grown final arrives
        assert not done.wait(1.5), f"fragment work ran: {ran}"
    finally:
        m.shutdown()


def test_clm_endpoint_settled_async_runs(monkeypatch, tmp_path):
    m = _manager()
    try:
        s, h = _live(m, CID2)
        monkeypatch.setattr(m, "ASYNC_HOLD_MIN_S", 0.3)
        monkeypatch.setattr(m, "ASYNC_HOLD_QUIET_S", 0.3)
        mod, ran, done = _proxy_with_async_model(monkeypatch, tmp_path, m)
        h._last_user_ts = 0.0
        assert _post(mod, CID2, "what is gm doing right now")[0] == 200
        assert done.wait(3.0), "a settled turn's async work must run"
        assert ran and ran[0]["prompt"] == "Turn there."
    finally:
        m.shutdown()


def test_follow_up_after_arturo_spoke_is_a_new_turn_not_a_supersede():
    """Hume merges a growing utterance into one turn ONLY while it has not spoken in between.
    Once Arturo's audio (an ack, a reply) has played, a newer request is a NEW turn: the work
    the earlier, complete question asked for must keep running (test_voice_delivery e2e shape)."""
    m = _manager()
    try:
        s, h = _live(m, "H5")
        T = 1_000_000.0
        clk = _Clock(T)
        m.note_clm_request("H5", T, "why are chats in terminal view")
        h._last_user_ts = T - 0.5

        def script(t):
            if abs(t - (T + 0.75)) < 0.13:
                h._last_agent_audio_ts = t                 # the ack is spoken
            if abs(t - (T + 1.5)) < 0.13:
                m.note_clm_request("H5", T + 1.5, "are you gonna tell me now")
        assert _settle(m, "H5", T, clk, script) == "settled"
    finally:
        m.shutdown()
