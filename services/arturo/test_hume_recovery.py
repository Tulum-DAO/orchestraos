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


def test_waits_while_shaw_speaks_and_only_latest_reply_is_recovered():
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
