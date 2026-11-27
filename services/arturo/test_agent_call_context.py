"""Every takes-calls agent's call opens with the same thread as Arturo's (Shaw 2026-11-25, gm msg_caaa3dc6): its own
chat plus its own previous calls, every line stamped, through ONE contract (GET /api/agents/:id/call-context). The
order of fallbacks is the route, then the Python twin (thread_context.load), then the agent's plain recent
conversation (voice_context); which one answered goes on the call's END line. The opening's size is per seat
(agent-calls.json seats.<id>.context_tokens), else the install default."""
import json
import logging
import urllib.error

import pytest

from services.arturo import agent_call as AC
from services.arturo import thread_context as tc
from services.arturo.test_agent_call import _switch, data  # noqa: F401 — the fixture


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body.encode()


def _route(monkeypatch, body=None, fail=False):
    seen = []

    def urlopen(url, timeout):
        seen.append((url, timeout))
        if fail:
            raise urllib.error.URLError("down")
        return _Resp(body)
    monkeypatch.setattr(AC.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("ORCH_API_URL", "http://127.0.0.1:8888")
    return seen


def test_the_route_answers_first_for_the_agents_own_chat_and_calls(data, monkeypatch):
    seen = _route(monkeypatch, body=f"{tc.PAST_OPEN}\nTHREAD ...\n{tc.PAST_CLOSE}")
    text, source = AC.call_context("dev-demo")
    assert source == "route" and text.endswith(tc.PAST_CLOSE)
    url, timeout = seen[0]
    assert url == "http://127.0.0.1:8888/api/agents/dev-demo/call-context?calls=dev-demo&budget=1500"
    assert timeout <= 5


def test_the_opening_size_is_a_seat_setting_else_the_install_default(data, monkeypatch):
    monkeypatch.delenv("ARTURO_AGENT_CONTEXT_TOKENS", raising=False)
    assert AC.context_tokens("dev-demo") == AC.CONTEXT_TOKENS == 1500           # no switch file
    _switch(data, {"dev-demo": {"on": True, "context_tokens": 3000}, "pm-demo": {"on": True, "context_tokens": "junk"},
                   "big": {"context_tokens": 99999}, "tiny": {"context_tokens": 10}})
    assert AC.context_tokens("dev-demo") == 3000
    assert AC.context_tokens("pm-demo") == 1500                                 # unreadable: the default
    assert (AC.context_tokens("big"), AC.context_tokens("tiny")) == (20000, 500)   # the route's clamp
    monkeypatch.setenv("ARTURO_AGENT_CONTEXT_TOKENS", "2500")
    assert AC.context_tokens("pm-demo") == 2500                                 # the install default, by env
    seen = _route(monkeypatch, body="x")
    AC.call_context("dev-demo")
    assert seen[0][0].endswith("&budget=3000")


def test_the_python_twin_answers_when_the_route_cannot(data, monkeypatch):
    _route(monkeypatch, fail=True)
    asked = []
    monkeypatch.setattr(AC.thread_context, "fetch_transcript", lambda agent, limit=300, **k: [])
    monkeypatch.setattr(AC.thread_context, "load", lambda **kw: asked.append(kw) or "TWIN THREAD")
    text, source = AC.call_context("dev-demo", exclude="vc_now")
    assert (text, source) == ("TWIN THREAD", "twin")
    kw = asked[0]
    assert kw["chat_agent"] == "dev-demo" and kw["call_owner"] == "dev-demo" and kw["exclude"] == "vc_now"
    assert kw["budget_tokens"] == 1500


def test_the_plain_conversation_answers_when_the_chat_cannot_be_read_at_all(data, monkeypatch):
    _route(monkeypatch, fail=True)

    def down(agent, limit=300, **k):
        raise OSError("api down")
    monkeypatch.setattr(AC.thread_context, "fetch_transcript", down)
    monkeypatch.setattr(AC, "voice_context", lambda seat, orch_dir=None: "Operator: status?")
    assert AC.call_context("dev-demo") == ("Operator: status?", "voice_context")
    # nothing in the session log either: the twin's calls-only thread, said as partial
    monkeypatch.setattr(AC, "voice_context", lambda seat, orch_dir=None: "")
    text, source = AC.call_context("dev-demo")
    assert source == "twin_partial" and "chat could not be read" in text


def test_nothing_readable_is_no_context_and_never_raises(data, monkeypatch):
    _route(monkeypatch, fail=True)
    monkeypatch.setattr(AC.thread_context, "fetch_transcript", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(AC.thread_context, "load", lambda **kw: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(AC, "voice_context", lambda seat, orch_dir=None: "")
    assert AC.call_context("dev-demo") == ("", "none")


def test_a_typed_note_is_stamped_when_the_flag_is_on(monkeypatch):
    monkeypatch.delenv(AC.STAMP_NOTES_FLAG, raising=False)
    plain = AC.typed_note("Sample", "did it land?", at=1764090000)
    assert plain.startswith("The operator just SENT")                           # off: unchanged
    monkeypatch.setenv(AC.STAMP_NOTES_FLAG, "1")
    stamped = AC.typed_note("Sample", "did it land?", at=1764090000, tz="UTC", tz_label="UTC")
    assert stamped.startswith("[2025-11-25T17:00:00Z 17:00 UTC] The operator just SENT")


# --- the relay: a voice-mode call reads it once and says which path answered on its END line ------------------------

def test_the_relay_opens_the_call_with_the_agents_thread_and_logs_the_source(caplog):
    from services.arturo.test_agent_call_voice import _VoiceAC, _call

    class _CtxAC(_VoiceAC):
        def call_context(self, a, exclude=None):
            self.cc_reads = getattr(self, "cc_reads", 0) + 1
            return f"{tc.PAST_OPEN}\nTHREAD sk-ant-SECRET\n{tc.PAST_CLOSE}", "route"

    import services.arturo.test_agent_call_voice as V
    orig = V._VoiceAC
    V._VoiceAC = _CtxAC
    try:
        with caplog.at_level(logging.INFO):
            m, s, h, ac, kws, _ = _call()
            try:
                assert kws[0]["agent_context"] == f"{tc.PAST_OPEN}\nTHREAD [redacted]\n{tc.PAST_CLOSE}"
                assert ac.cc_reads == 1 and not getattr(ac, "ctx_reads", 0)
            finally:
                m.shutdown()
    finally:
        V._VoiceAC = orig
    end = [r.getMessage() for r in caplog.records if ": END agent=" in r.getMessage()]
    assert end and "agent=dev-demo" in end[0] and "context_source=route" in end[0]
    assert f"opening_tokens={tc.estimate_tokens(kws[0]['agent_context'])}" in end[0]
