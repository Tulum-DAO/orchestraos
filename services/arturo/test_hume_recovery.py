"""HUME-RECOVERY (gm msg_8ce087b5): Hume sometimes keeps NONE of our replies (5 of 41 recent
chats; replay chat 95ada595 / vc_0ee7a77dbbf2a3f3: we streamed 5 answers, Hume spoke none,
the operator heard 30 s of nothing). When we streamed a non-empty reply and the relay saw NO Arturo
audio since that request began, and the operator is quiet, re-send ONLY the latest reply as Hume
assistant_input, once. A merely slow Hume (audio arrives late) must NOT double."""
import os

from services.arturo.test_stream_relay_hume import _manager, _wait


def _live(m, cid="h1"):
    m.feed_audio(cid, b"\x00" * 10)
    assert _wait(lambda: m._t)
    return m._t[0], m._holders[cid]


def _ai(s):
    return [f["text"] for f in s.sent if f.get("type") == "assistant_input"]


def test_replay_stuck_chat_recovers_latest_reply_once(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="arturo-stream-relay")
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        h._last_agent_audio_ts = T - 30          # greeting only
        h._last_user_ts = T - 1                  # the operator finished asking just before
        m.note_clm_reply("h1", "You're on the approvals page.", T, streamed_at=T + 1.5)
        assert h.recovery_check(now=T + 2.0) == "wait"            # too soon after streaming
        assert h.recovery_check(now=T + 4.2) == "recovered"
        assert _ai(s) == ["You're on the approvals page."]
        assert h.recovery_check(now=T + 9.0) == "none", "at most once"
        assert _ai(s) == ["You're on the approvals page."]
        assert "HUME-RECOVERY" in caplog.text
    finally:
        m.shutdown()


def test_hume_merely_slow_does_not_double():
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        h._last_user_ts = T - 1
        m.note_clm_reply("h1", "It is green.", T, streamed_at=T + 1.0)
        h._last_agent_audio_ts = T + 3.0         # Hume started speaking, just late
        assert h.recovery_check(now=T + 4.0) == "spoken-by-hume"
        assert _ai(s) == []
    finally:
        m.shutdown()


def test_waits_while_the_operator_speaks_and_only_latest_reply_is_recovered():
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        m.note_clm_reply("h1", "old answer", T, streamed_at=T + 1)
        h._last_user_ts = T + 3.5                # the operator talking over the gap
        assert h.recovery_check(now=T + 4.0) == "user-speaking"
        m.note_clm_reply("h1", "new answer", T + 5, streamed_at=T + 6)
        h._last_user_ts = T + 5
        assert h.recovery_check(now=T + 9.0) == "recovered"
        assert _ai(s) == ["new answer"]
    finally:
        m.shutdown()


def test_stale_reply_is_dropped_not_spoken():
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        m.note_clm_reply("h1", "old", T, streamed_at=T + 1)
        h._last_user_ts = T + 30                 # the operator kept talking past the window
        assert h.recovery_check(now=T + 30.5) == "stale"
        assert _ai(s) == []
    finally:
        m.shutdown()


def test_flag_off_registers_nothing(monkeypatch):
    monkeypatch.setenv("ARTURO_HUME_RECOVERY", "0")
    m = _manager()
    try:
        s, h = _live(m)
        m.note_clm_reply("h1", "x", 1.0, streamed_at=2.0)
        assert h.recovery_check(now=100.0) == "none"
    finally:
        m.shutdown()


def test_clm_endpoint_registers_the_streamed_reply_with_the_relay(monkeypatch, tmp_path):
    """Call site: a real Hume CLM request on a live relay call must leave its streamed reply
    pending recovery on that call's holder."""
    import importlib.util, pathlib
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_rec", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)

    class _Msg:
        tool_calls = None
        content = "You're on the approvals page."

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _C:
        def create(self, **kw):
            return _Resp()

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    _cmp = _C()
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _cmp.create(**kw))
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    m = _manager()
    try:
        s, h = _live(m, "CIDREC")
        monkeypatch.setattr(mod, "_STREAM_RELAY", m)
        c = mod.app.test_client()
        c.post("/v1/chat/completions?custom_session_id=CIDREC",
               json={"messages": [{"role": "assistant", "content": "Hey the operator, Arturo here."},
                                  {"role": "user", "content": "what page am I on"}]},
               headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data()
        assert h._rec is not None and h._rec["text"] == "You're on the approvals page.", h._rec
    finally:
        m.shutdown()


# ---- ANSWERED-REPEAT re-speak (gm msg_c07a2389): one re-speak per reply across BOTH paths ----

def _repeat_proxy(monkeypatch, tmp_path, m):
    import importlib.util, pathlib
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_rr", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)

    class _Msg:
        tool_calls = None
        content = "It is the approvals page."

    class _Choice:
        finish_reason = "stop"
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _C:
        def create(self, **kw):
            return _Resp()

    # public: the brain seam (services/arturo/brain.py) replaced the bare openai client
    _cmp = _C()
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: _cmp.create(**kw))
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    monkeypatch.setattr(mod, "_STREAM_RELAY", m)
    return mod


def _post_same(mod, cid):
    from services.arturo.test_answered_final_extension import _deltas
    c = mod.app.test_client()
    r = c.post(f"/v1/chat/completions?custom_session_id={cid}",
               json={"messages": [{"role": "assistant", "content": "Hey the operator, Arturo here."},
                                  {"role": "user", "content": "what page am I on right now"}]},
               headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    return "".join(_deltas(r.get_data(as_text=True)))


def test_repeat_respeaks_dropped_reply_once_and_recovery_then_stays_silent(monkeypatch, tmp_path):
    m = _manager()
    try:
        s, h = _live(m, "CIDRR1")
        mod = _repeat_proxy(monkeypatch, tmp_path, m)
        assert _post_same(mod, "CIDRR1") == "It is the approvals page."    # streamed, never spoken
        again = _post_same(mod, "CIDRR1")                                    # Hume re-asks
        assert again == "It is the approvals page.", "ANSWERED-REPEAT must re-speak the dropped reply"
        h._last_user_ts = 0.0
        import time as _t
        assert h.recovery_check(now=_t.time() + 5) == "none", "already claimed by ANSWERED-REPEAT"
        assert _ai(s) == [], "the operator hears it exactly once"
    finally:
        m.shutdown()


def test_recovery_first_then_repeat_stays_empty(monkeypatch, tmp_path):
    import time as _t
    m = _manager()
    try:
        s, h = _live(m, "CIDRR2")
        mod = _repeat_proxy(monkeypatch, tmp_path, m)
        _post_same(mod, "CIDRR2")
        h._last_user_ts = 0.0
        assert h.recovery_check(now=_t.time() + 5) == "recovered"
        assert _post_same(mod, "CIDRR2") == "", "recovery already re-spoke it"
        assert len(_ai(s)) == 1
    finally:
        m.shutdown()


def test_repeat_stays_empty_when_hume_spoke_the_reply(monkeypatch, tmp_path):
    import time as _t
    m = _manager()
    try:
        s, h = _live(m, "CIDRR3")
        mod = _repeat_proxy(monkeypatch, tmp_path, m)
        _post_same(mod, "CIDRR3")
        h._last_agent_audio_ts = _t.time() + 1        # Hume DID speak it
        assert _post_same(mod, "CIDRR3") == ""
    finally:
        m.shutdown()


def test_repeat_stays_empty_for_a_stale_reply(monkeypatch, tmp_path):
    m = _manager()
    try:
        s, h = _live(m, "CIDRR4")
        mod = _repeat_proxy(monkeypatch, tmp_path, m)
        _post_same(mod, "CIDRR4")
        h._rec["t0"] -= 60                            # older than the 20 s window
        assert _post_same(mod, "CIDRR4") == ""
    finally:
        m.shutdown()


def test_audible_latency_logged_once_per_reply(caplog):
    import base64, logging, time as _t
    from services.arturo.test_stream_relay_hume import _wav48
    caplog.set_level(logging.INFO, logger="arturo-stream-relay")
    m = _manager()
    try:
        s, h = _live(m)
        m.note_clm_reply("h1", "hello", _t.time() - 1.5)
        for i in range(2):
            s.push({"type": "audio_output", "id": "a", "index": i, "data": base64.b64encode(_wav48()).decode()})
        assert _wait(lambda: "AUDIBLE-LATENCY request->first_audio=" in caplog.text)
        _t.sleep(0.2)
        assert caplog.text.count("AUDIBLE-LATENCY") == 1
        line = [r.getMessage() for r in caplog.records if "AUDIBLE-LATENCY" in r.getMessage()][0]
        assert float(line.split("=")[1].rstrip("s")) >= 1.4
    finally:
        m.shutdown()


# -- SUPERSEDE (gm msg_2c161950 (ii)): replay relay E58F077A / Hume chat dabf1bd3, 10-09. Hume
# merged the operator's 33 s turn into ONE user message and called the CLM four times as its finals grew.
# At 409.1 HUME-RECOVERY re-sent "On it..." (the reply to the 392 prefix) although a request for
# the extended turn had begun at 408.2: a stale line over the operator, then a second answer.
_Q1 = "Yeah, we need to turn back on the native ios. Audio cancellation such that when you say something."
_Q2 = _Q1[:-1] + " and it's coming through the speaker {slightly determined}"


def test_newer_different_request_supersedes_the_pending_reply(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="arturo-stream-relay")
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        h._last_agent_audio_ts = T - 15            # greeting only
        m.note_clm_request("h1", T, _Q1)
        m.note_clm_reply("h1", "On it, I'll look into it.", T, streamed_at=T + 3.3)
        h._last_user_ts = T + 14                   # quiet gate satisfied at T+17
        m.note_clm_request("h1", T + 16.3, _Q2)    # Hume asks again for the GROWN turn
        assert h.recovery_check(now=T + 17.2) in ("none", "superseded")
        assert _ai(s) == [], "a reply to a superseded fragment must never be re-sent"
        assert "superseded" in caplog.text
    finally:
        m.shutdown()


def test_late_reply_to_an_older_request_is_not_held():
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        h._last_agent_audio_ts = T - 15
        h._last_user_ts = T - 1
        m.note_clm_request("h1", T, _Q1)
        m.note_clm_request("h1", T + 2, _Q2)       # newer request starts before the old reply lands
        m.note_clm_reply("h1", "stale answer", T, streamed_at=T + 3)
        assert h.recovery_check(now=T + 6) in ("none", "superseded")
        m.note_clm_reply("h1", "full answer", T + 2, streamed_at=T + 6)
        assert h.recovery_check(now=T + 9) == "recovered"
        assert _ai(s) == ["full answer"]
    finally:
        m.shutdown()


def test_identical_re_ask_keeps_the_reply_for_answered_repeat():
    m = _manager()
    try:
        s, h = _live(m)
        T = 1_000_000.0
        h._last_agent_audio_ts = T - 15
        m.note_clm_request("h1", T, _Q1)
        m.note_clm_reply("h1", "On it.", T, streamed_at=T + 1)
        h._last_user_ts = T + 5                    # the operator re-asks; recovery waits
        m.note_clm_request("h1", T + 6, _Q1 + " {anxious}")   # same words, new prosody tag
        assert h.claim_respeak("answered-repeat", now=T + 6.1) == "On it."
    finally:
        m.shutdown()


def test_clm_endpoint_supersedes_on_a_newer_different_request(monkeypatch, tmp_path):
    """Call site (ii): a real CLM request whose user text EXTENDS the last one must clear the
    pending reply at request START. note_clm_reply is muted for the second request so only the
    request-start hook can clear it."""
    m = _manager()
    try:
        s, h = _live(m, "CIDSUP")
        mod = _repeat_proxy(monkeypatch, tmp_path, m)
        c = mod.app.test_client()

        def post(q):
            c.post("/v1/chat/completions?custom_session_id=CIDSUP",
                   json={"messages": [{"role": "assistant", "content": "Hey the operator, Arturo here."},
                                      {"role": "user", "content": q}]},
                   headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data()
        post(_Q1)
        assert h._rec is not None and not h._rec["done"]
        monkeypatch.setattr(m, "note_clm_reply", lambda *a, **k: False)
        post(_Q2)
        assert h._rec["done"], "the reply to the fragment must be superseded at request start"
    finally:
        m.shutdown()
