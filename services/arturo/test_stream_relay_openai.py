"""GPT-Live ("openai") vendor path of the relay (DEC-1791605997016955 rev 1; gm conditions 1-5).
FakeOpenAISocket replays the event SHAPES measured in the 2026-10-10 scratch session. No network."""
import base64
import json
import logging
import threading
import time

import numpy as np

from services.arturo import stream_relay as sr
from services.arturo.test_stream_relay_hume import _wait


class FakeOpenAISocket:
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False

    def send(self, payload):
        if self.closed:
            raise RuntimeError("down")
        self.sent.append(json.loads(payload))

    def push(self, ev):
        with self._cv:
            self._incoming.append(json.dumps(ev))
            self._cv.notify()

    def recv(self):
        with self._cv:
            while not self._incoming and not self.closed:
                self._cv.wait(timeout=0.1)
            if self.closed and not self._incoming:
                raise RuntimeError("closed")
            return self._incoming.pop(0)

    def close(self):
        self.closed = True
        with self._cv:
            self._cv.notify_all()


def _manager(delegate=None, journal=None, **kw):
    socks = []

    def factory(cid, **_):
        s = FakeOpenAISocket()
        socks.append(s)
        return s
    m = sr.RelayManager(factories={"openai": factory}, vendor_fn=lambda: "openai", vendor_check=lambda v: "",
                        speaking_enabled=True, agent_text_enabled=True, speak_idle_ms=150, **kw)
    m.openai_delegate = delegate
    m.journal_hook = journal
    m._t = socks
    return m


def _frame(rms, n=1600, seed=1):
    if not rms:
        return base64.b64encode(b"\x00" * (n * 2)).decode()
    x = np.random.default_rng(seed).standard_normal(n)
    x = np.clip(np.rint(x / np.sqrt((x * x).mean()) * rms), -32768, 32767).astype("<i2")
    return base64.b64encode(x.tobytes()).decode()


def _audio(rms):
    return {"type": "session.output_audio.delta", "delta": _frame(rms)}


def _live(m, cid="o1"):
    m.feed_audio(cid, b"\x00" * 3200)
    assert _wait(lambda: m._t)
    return m._t[0], m._holders[cid]


def _ev(m, cid="o1", typ=None):
    evs = m.events(cid, 0)[0]
    return [e for e in evs if typ is None or e["type"] == typ]


def test_uplink_is_session_input_audio_append():
    m = _manager()
    try:
        s, h = _live(m)
        assert _wait(lambda: any(f.get("type") == "session.input_audio.append" for f in s.sent))
    finally:
        m.shutdown()


def test_silence_and_dither_are_never_speech_or_forwarded():
    m = _manager()
    try:
        s, h = _live(m)
        for _ in range(10):
            s.push(_audio(0))
            s.push(_audio(3))
        time.sleep(0.4)
        assert _ev(m, typ="audio") == [], "silence frames must not reach the app (the watch would gate its mic forever)"
        assert not any(e.get("speaking") for e in _ev(m, typ="agent_speaking"))
        assert h._last_agent_audio_ts == 0.0
    finally:
        m.shutdown()


def test_speech_opens_a_span_and_the_quiet_tail_closes_it_with_the_reply_text():
    m = _manager()
    try:
        s, h = _live(m)
        s.push({"type": "session.output_transcript.delta", "delta": " Hey!"})
        s.push({"type": "session.output_transcript.delta", "delta": " How's your night?"})
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        assert any(e.get("speaking") for e in _ev(m, typ="agent_speaking"))
        assert h._last_agent_audio_ts > 0
        for _ in range(15):                          # 1.5 s of exact zero: past the span tail
            s.push(_audio(0))
        assert _wait(lambda: _ev(m, typ="assistant_end"))
        finals = [e for e in _ev(m, typ="agent_response") if e.get("partial") is False]
        assert finals and finals[-1]["text"] == "Hey! How's your night?"
        n_audio = len(_ev(m, typ="audio"))
        for _ in range(5):
            s.push(_audio(0))
        time.sleep(0.3)
        assert len(_ev(m, typ="audio")) == n_audio, "zero frames after the span are not forwarded"
    finally:
        m.shutdown()


def test_user_speech_during_arturo_speech_is_a_barge_in():
    m = _manager()
    try:
        s, h = _live(m)
        for _ in range(3):
            s.push(_audio(640))
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " Hey"})
        s.push({"type": "session.input_transcript.delta", "delta": " wait"})
        assert _wait(lambda: _ev(m, typ="barge_in"))
        assert len(_ev(m, typ="barge_in")) == 1, "one barge_in per interrupted span"
        evs = _ev(m)
        i_b = max(i for i, e in enumerate(evs) if e["type"] == "barge_in")
        assert any(e["type"] == "agent_speaking" and e["speaking"] is False for e in evs[:i_b])
    finally:
        m.shutdown()


def test_delegation_goes_to_our_brain_and_comes_back_as_commentary():
    calls = []

    def delegate(cid, messages):
        calls.append((cid, messages))
        return "gm is reviewing the restart card."
    m = _manager(delegate=delegate)
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " What is GM"})
        s.push({"type": "session.input_transcript.delta", "delta": " working on right now"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_A", "type": "delegation",
                                                                     "target": "client"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        c = [f for f in s.sent if f.get("type") == "session.commentary.append"][0]
        assert c["delegation_id"] == "item_A" and c["content"] == "gm is reviewing the restart card."
        assert calls[0][0] == "o1"
        assert calls[0][1][-1] == {"role": "user", "content": "What is GM working on right now"}
        assert any(e["type"] == "user_transcript" and e["text"] == "What is GM working on right now"
                   for e in _ev(m))
    finally:
        m.shutdown()


def test_no_backend_answer_is_spoken_as_an_explicit_nothing_found():
    m = _manager(delegate=lambda cid, msgs: "")
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " anything new"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_B"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        c = [f for f in s.sent if f.get("type") == "session.commentary.append"][0]
        assert c["content"] == "I couldn't find anything on that."
    finally:
        m.shutdown()


def test_an_interrupted_answer_is_posted_to_the_transcript_never_lost():
    m = _manager(delegate=lambda cid, msgs: "orchestra-builder has three open tasks.")
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " what is OB doing"})
        s.push({"type": "session.delegation.created", "delegation": {"id": "item_C"}})
        assert _wait(lambda: any(f.get("type") == "session.commentary.append" for f in s.sent))
        for _ in range(3):
            s.push(_audio(640))                      # Arturo starts speaking the answer
        assert _wait(lambda: len(_ev(m, typ="audio")) == 3)
        s.push({"type": "session.input_transcript.delta", "delta": " hold on"})
        assert _wait(lambda: any("orchestra-builder has three open tasks." in (e.get("text") or "")
                                 and e.get("interrupted") for e in _ev(m, typ="agent_response")))
    finally:
        m.shutdown()


def test_per_call_cap_warns_then_closes(monkeypatch):
    monkeypatch.setattr(sr, "OPENAI_CALL_WARN_S", 10.0)
    monkeypatch.setattr(sr, "OPENAI_CALL_CAP_S", 20.0)
    m = _manager()
    try:
        s, h = _live(m)
        s.push({"type": "session.usage.updated", "usage": {"seconds": 11.0}})
        s.push({"type": "session.usage.updated", "usage": {"seconds": 12.0}})
        assert _wait(lambda: any("minute left" in (e.get("text") or "") for e in _ev(m, typ="agent_response")))
        assert len([e for e in _ev(m, typ="agent_response") if "minute left" in (e.get("text") or "")]) == 1
        s.push({"type": "session.usage.updated", "usage": {"seconds": 21.0}})
        assert _wait(lambda: any(f.get("type") == "session.close" for f in s.sent))
        assert any(e["type"] == "vendor_unavailable" for e in _ev(m))
    finally:
        m.shutdown()


def test_a_turn_answered_without_delegating_is_journaled_and_counted(caplog):
    caplog.set_level(logging.INFO, logger="arturo-stream-relay")
    seen = []
    m = _manager(journal=lambda cid, role, text, **meta: seen.append((cid, role, text, meta)))
    try:
        s, h = _live(m)
        s.push({"type": "session.input_transcript.delta", "delta": " how's it going"})
        s.push({"type": "session.output_transcript.delta", "delta": " I'm on it."})
        for _ in range(2):
            s.push(_audio(640))
        for _ in range(15):
            s.push(_audio(0))
        assert _wait(lambda: any(r == "arturo" for _, r, _, _ in seen))
        assert ("o1", "user", "how's it going", {"delegated": False}) in seen
        assert any(r == "arturo" and t == "I'm on it." and meta == {"delegated": False} for _, r, t, meta in seen)
        assert h.oa_nondelegated == 1
        assert "OPENAI-ACK" in caplog.text
    finally:
        m.shutdown()


def test_supersede_and_async_hold_gates_are_open_for_openai():
    m = _manager()
    try:
        s, h = _live(m)
        assert m.note_clm_request("o1", time.time(), "what is gm doing") is True
        t = [1e9]

        def clock():
            return t[0]

        def sleep(d):
            t[0] += d
        assert m.wait_turn_settled("o1", time.time(), clock=clock, sleep=sleep) != "gone"
    finally:
        m.shutdown()


def test_user_speech_while_arturo_is_silent_is_not_a_barge_in():
    m = _manager()
    try:
        s, h = _live(m)
        for _ in range(5):
            s.push(_audio(0))                        # GPT-Live streaming its silent frames
        s.push({"type": "session.input_transcript.delta", "delta": " What is GM working on"})
        assert _wait(lambda: _ev(m, typ="user_partial"))
        time.sleep(0.2)
        assert _ev(m, typ="barge_in") == [], "a normal turn must never flush the client's playback"
    finally:
        m.shutdown()
