"""RED-first — hume reply-text streaming to the watch (ios msg_0864f940 contract, closed
(a) in msg_57c21e6c; the operator vc_7b22821477e940fe: 'not seeing anything Arturo says written').
Flag ARTURO_STREAM_AGENT_TEXT (default OFF => hume events byte-identical to today, so a
watchdog crash-restart cannot arm the contract before watch >=218 is on the wrist).
ON: each assistant_message emits agent_response {text: cumulative, agent_turn, partial:true};
assistant_end and a FORWARDED user_interruption emit the partial:false final (then
assistant_end / barge_in as today); replay_log stays end-only. EL path untouched
(test_stream_relay.py 25 baseline)."""
import base64
import json
import math
import struct
import threading
import time

import numpy as np

from services.arturo import stream_relay as sr


class FakeHumeSocket:
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False
        self.resumed = None

    def encode_audio(self, pcm):
        return json.dumps({"type": "audio_input", "data": base64.b64encode(pcm).decode()})

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


def _manager(**kw):
    socks = []

    def hume_factory(cid, resumed_chat_group_id=None):
        s = FakeHumeSocket()
        s.resumed = resumed_chat_group_id
        socks.append(s)
        return s
    m = sr.RelayManager(factories={"hume": hume_factory}, vendor_fn=lambda: "hume",
                        vendor_check=lambda v: "", **kw)
    m._t = socks
    return m


def _wait(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _wav48(seconds=0.03):
    n = int(48000 * seconds)
    t = np.arange(n) / 48000
    frames = (np.sin(2 * math.pi * 440 * t) * 10000).astype("<i2").tobytes()
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16)
    body = b"fmt " + fmt + b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _events(m, cid):
    return m.events(cid, 0)[0]


def _agent_events(m, cid):
    return [e for e in _events(m, cid) if e["type"] == "agent_response"]


def _seg(s, text):
    s.push({"type": "assistant_message", "from_text": False,
            "message": {"role": "assistant", "content": text}})


def test_flag_on_streams_cumulative_partials_then_final():
    m = _manager(agent_text_enabled=True)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _seg(s, "Hey")
        assert _wait(lambda: len(_agent_events(m, "c1")) == 1)
        _seg(s, "the operator.")
        assert _wait(lambda: len(_agent_events(m, "c1")) == 2)
        s.push({"type": "assistant_end"})
        assert _wait(lambda: len(_agent_events(m, "c1")) == 3)
        a1, a2, fin = _agent_events(m, "c1")
        assert a1 == {"type": "agent_response", "text": "Hey", "agent_turn": 1, "partial": True}
        assert a2 == {"type": "agent_response", "text": "Hey the operator.", "agent_turn": 1, "partial": True}
        assert fin == {"type": "agent_response", "text": "Hey the operator.", "agent_turn": 1, "partial": False}
        # assistant_end still follows the final, as today
        types = [e["type"] for e in _events(m, "c1")]
        assert types.index("assistant_end") > types.index("agent_response")
        # replay_log is end-only: exactly ONE agent entry despite 3 emissions
        agent_rows = [r for r in m.replay_log("c1").recent() if r[0] == "agent"]
        assert agent_rows == [("agent", "Hey the operator.")]
    finally:
        m.shutdown()


def test_flag_on_second_turn_increments_agent_turn():
    m = _manager(agent_text_enabled=True)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _seg(s, "one")
        s.push({"type": "assistant_end"})
        assert _wait(lambda: len(_agent_events(m, "c1")) == 2)
        _seg(s, "two")
        assert _wait(lambda: len(_agent_events(m, "c1")) == 3)
        assert _agent_events(m, "c1")[2] == {"type": "agent_response", "text": "two",
                                             "agent_turn": 2, "partial": True}
    finally:
        m.shutdown()


def test_flag_on_forwarded_interruption_emits_final_before_barge_in():
    m = _manager(agent_text_enabled=True)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        # unlock the greeting-clip guard with forwarded audio first
        s.push({"type": "audio_output", "id": "a0", "index": 0,
                "data": base64.b64encode(_wav48()).decode()})
        assert _wait(lambda: any(e["type"] == "audio" for e in _events(m, "c1")))
        _seg(s, "I was saying")
        assert _wait(lambda: len(_agent_events(m, "c1")) == 1)
        s.push({"type": "user_interruption", "time": 1})
        assert _wait(lambda: any(e["type"] == "barge_in" for e in _events(m, "c1")))
        evs = _events(m, "c1")
        fin = [e for e in evs if e["type"] == "agent_response" and e.get("partial") is False]
        assert fin == [{"type": "agent_response", "text": "I was saying",
                        "agent_turn": 1, "partial": False}], \
            "forwarded interruption must close the turn with a partial:false final"
        assert evs.index(fin[0]) < evs.index(next(e for e in evs if e["type"] == "barge_in"))
    finally:
        m.shutdown()


def test_flag_on_suppressed_interruption_emits_nothing():
    m = _manager(agent_text_enabled=True)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _seg(s, "greeting text")          # text does NOT unlock the guard (64d93b107e)
        assert _wait(lambda: len(_agent_events(m, "c1")) == 1)
        s.push({"type": "user_interruption", "time": 1})
        s.push({"type": "assistant_end"})  # turn still closes normally afterwards
        assert _wait(lambda: any(e.get("partial") is False for e in _agent_events(m, "c1")))
        evs = _events(m, "c1")
        assert not any(e["type"] == "barge_in" for e in evs)
        finals = [e for e in _agent_events(m, "c1") if e.get("partial") is False]
        assert len(finals) == 1 and finals[0]["text"] == "greeting text"
    finally:
        m.shutdown()


def test_flag_off_is_byte_identical_to_today():
    m = _manager()                        # default: ARTURO_STREAM_AGENT_TEXT unset => off
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _seg(s, "Hey")
        _seg(s, "the operator.")
        time.sleep(0.15)
        assert _agent_events(m, "c1") == [], "flag off: segments must not emit"
        s.push({"type": "assistant_end"})
        assert _wait(lambda: len(_agent_events(m, "c1")) == 1)
        assert _agent_events(m, "c1") == [{"type": "agent_response", "text": "Hey the operator."}], \
            "flag off: legacy event shape exactly — no agent_turn/partial keys"
    finally:
        m.shutdown()


def test_end_logs_partial_final_counters(caplog):
    # ios msg_3a5b4d4b: the proxy access log carries no event bodies — end() surfaces
    # per-cid agent_response partial/final counters for by-effect grading.
    m = _manager(agent_text_enabled=True)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[0]
        _seg(s, "Hey")
        _seg(s, "the operator.")
        s.push({"type": "assistant_end"})
        assert _wait(lambda: len(_agent_events(m, "c1")) == 3)
        with caplog.at_level("INFO", logger="arturo-stream-relay"):
            assert m.end("c1")
        assert any("agent_response counters — partials=2 finals=1" in r.message
                   for r in caplog.records), [r.message for r in caplog.records]
    finally:
        m.shutdown()
