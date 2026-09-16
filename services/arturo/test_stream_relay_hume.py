"""Hume vendor path of the relay (spec §4.2, Hume Task 6, frozen artifact d3cbafa9).
FakeHumeSocket mirrors FakeELSocket; the manager is built with factories={"hume": ...} and
vendor_fn=lambda: "hume". test_stream_relay.py (25 EL tests) must stay green + untouched."""
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
                        vendor_check=lambda v: "", partials_enabled=True, **kw)   # partials ON to prove forced-off for hume
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


def test_audio_framed_as_hume_audio_input():
    m = _manager()
    try:
        assert m.feed_audio("h1", b"\x01\x02" * 50)["ok"]
        assert _wait(lambda: m._t and m._t[0].sent)
        msg = m._t[0].sent[0]
        assert msg["type"] == "audio_input" and base64.b64decode(msg["data"]) == b"\x01\x02" * 50
        assert m._holders["h1"].partials is None          # scribe fork forced off for hume
    finally:
        m.shutdown()


def test_interim_then_final_user_message():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "user_message", "interim": True, "from_text": False, "message": {"role": "user", "content": "what's"}})
        s.push({"type": "user_message", "interim": True, "from_text": False, "message": {"role": "user", "content": "what's on my"}})
        s.push({"type": "user_message", "interim": False, "from_text": False, "message": {"role": "user", "content": "what's on my plate"}})
        assert _wait(lambda: any(e["type"] == "user_transcript" for e in m.events("h1", 0)[0]))
        ev = m.events("h1", 0)[0]
        partials = [e for e in ev if e["type"] == "user_partial"]
        assert [p["revision"] for p in partials] == [1, 2] and all(p["turn"] == 1 for p in partials)
        final = [e for e in ev if e["type"] == "user_transcript"][0]
        assert final["text"] == "what's on my plate" and final["turn"] == 1
        s.push({"type": "user_message", "interim": True, "from_text": False, "message": {"role": "user", "content": "and"}})
        assert _wait(lambda: any(e["type"] == "user_partial" and e["turn"] == 2 and e["revision"] == 1 for e in m.events("h1", 0)[0]))
    finally:
        m.shutdown()


def test_from_text_user_message_ignored():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "user_message", "interim": False, "from_text": True, "message": {"role": "user", "content": "injected"}})
        s.push({"type": "assistant_end"})
        assert _wait(lambda: any(e["type"] == "assistant_end" for e in m.events("h1", 0)[0]))
        assert not any(e["type"] == "user_transcript" for e in m.events("h1", 0)[0])
    finally:
        m.shutdown()


def test_assistant_segments_coalesce_on_assistant_end():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "assistant_message", "from_text": False, "message": {"role": "assistant", "content": "Sure —"}})
        s.push({"type": "assistant_message", "from_text": False, "message": {"role": "assistant", "content": "two decisions are pending."}})
        s.push({"type": "assistant_end"})
        assert _wait(lambda: any(e["type"] == "agent_response" for e in m.events("h1", 0)[0]))
        ar = [e for e in m.events("h1", 0)[0] if e["type"] == "agent_response"]
        assert len(ar) == 1 and ar[0]["text"] == "Sure — two decisions are pending."
    finally:
        m.shutdown()


def test_audio_output_wav_becomes_pcm16k():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "audio_output", "id": "a", "index": 0, "data": base64.b64encode(_wav48()).decode()})
        assert _wait(lambda: any(e["type"] == "audio" for e in m.events("h1", 0)[0]))
        a = [e for e in m.events("h1", 0)[0] if e["type"] == "audio"][0]
        assert len(base64.b64decode(a["audio"])) == (int(48000 * 0.03) // 3) * 2
    finally:
        m.shutdown()


def test_user_interruption_is_barge_in_and_tool_call_gets_tool_error():
    # barge_in requires PRIOR agent output on this socket (greeting-clip guard,
    # msg_59d6dcd5): interruptions during a real agent turn still forward.
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "audio_output", "id": "a0", "index": 0,
                "data": base64.b64encode(_wav48()).decode()})
        s.push({"type": "user_interruption", "time": 1})
        s.push({"type": "tool_call", "tool_call_id": "tc1", "name": "x", "parameters": "{}"})
        assert _wait(lambda: any(e["type"] == "barge_in" for e in m.events("h1", 0)[0]))
        assert _wait(lambda: any(x.get("type") == "tool_error" and x.get("tool_call_id") == "tc1" for x in s.sent))
    finally:
        m.shutdown()


def test_hume_barge_in_suppressed_until_first_agent_output():
    # ios msg_59d6dcd5 + msg_b8f46733: Hume emits user_interruption on silence chunks while
    # generating the greeting. The unlock must be FORWARDED AUDIO ONLY — assistant_message
    # TEXT arrives before the interruption on real calls (it unlocked v1's guard and barge_in
    # still preceded the greeting on the wire). The watch flushes AUDIO playback; barge_in
    # is meaningless until audio has been forwarded.
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "user_interruption", "time": 0})     # pre-greeting: suppressed
        s.push({"type": "assistant_message", "from_text": False,
                "message": {"role": "assistant", "content": "Hey the operator"}})
        s.push({"type": "user_interruption", "time": 1})     # TEXT only: still suppressed
        s.push({"type": "audio_output", "id": "a", "index": 0,
                "data": base64.b64encode(_wav48()).decode()})
        assert _wait(lambda: any(e["type"] == "audio" for e in m.events("h1", 0)[0]))
        assert not any(e["type"] == "barge_in" for e in m.events("h1", 0)[0])
        s.push({"type": "user_interruption", "time": 2})     # audio forwarded: genuine
        assert _wait(lambda: any(e["type"] == "barge_in" for e in m.events("h1", 0)[0]))
    finally:
        m.shutdown()


def test_chat_metadata_records_group_and_no_ping_reply():
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        s = m._t[0]
        s.push({"type": "chat_metadata", "chat_id": "c", "chat_group_id": "g1"})
        assert _wait(lambda: m._holders["h1"].chat_group_id == "g1")
        s.push({"type": "ping"})
        time.sleep(0.2)
        assert not any(x.get("type") == "pong" for x in s.sent)
    finally:
        m.shutdown()


def test_unavailable_vendor_is_refused_visibly_no_socket():
    calls = []
    m = sr.RelayManager(factories={"hume": lambda cid, resumed_chat_group_id=None: calls.append(cid)},
                        vendor_fn=lambda: "hume", vendor_check=lambda v: "missing HUME_API_KEY")
    try:
        r = m.feed_audio("h1", b"\x00" * 10)
        assert r == {"ok": False, "error": "vendor_unavailable", "message": "missing HUME_API_KEY"}
        time.sleep(0.1)
        assert calls == [] and "h1" not in m._holders
    finally:
        m.shutdown()


def test_stale_resume_timer_never_reopens_after_end():
    m = _manager(hume_session_s=60.4)            # timer armed for 0.4 s
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        assert m.end("h1")                       # end BEFORE the timer fires -> gen bumped, timers cancelled
        time.sleep(0.8)
        assert len(m._t) == 1                    # no second socket ever opened
    finally:
        m.shutdown()


def test_idle_close_retires_without_reconnect():
    # C1 (srw-dev): idle-close retires WITHOUT reconnect — zero new sockets until a real chunk.
    m = _manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        h = m._holders["h1"]
        h._retire_socket("idle", reconnect=False)
        time.sleep(0.5)
        assert len(m._t) == 1, "idle-close must not spawn a reconnect"
        # a fresh uplink reopens exactly one new socket (user-driven reopen)
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: len(m._t) == 2)
    finally:
        m.shutdown()


class FakeUsage:
    def __init__(self, over=False):
        self.calls = []
        self.over = over

    def add_seconds(self, vendor, seconds):
        self.calls.append((vendor, seconds))

    def over_cap(self, vendor):
        return self.over


def test_usage_not_double_counted_across_resume():
    # Task 9: ONE add_seconds per conversation, keyed on _conv_started — a proactive
    # session-cap resume mid-call must NOT split the call into two usage records.
    u = FakeUsage()
    m = _manager(hume_session_s=60.2, usage=u)       # resume timer fires at 0.2s
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: len(m._t) == 2)         # proactive resume opened socket #2
        assert u.calls == []                         # nothing counted while the call is live
        assert m.end("h1")
        assert len(u.calls) == 1
        vendor, seconds = u.calls[0]
        assert vendor == "hume" and seconds >= 0
        assert not m.end("h1") and len(u.calls) == 1  # second end() never double-counts
    finally:
        m.shutdown()


def test_usage_over_cap_refuses_new_conversation_visibly():
    u = FakeUsage(over=True)
    m = _manager(usage=u)
    try:
        r = m.feed_audio("h1", b"\x00" * 10)
        assert not r["ok"] and r["error"] == "vendor_unavailable" and "cap" in r["message"]
        assert "h1" not in m._holders and m._t == []
    finally:
        m.shutdown()


def test_proactive_resume_reopens_with_chat_group_id():
    # Task 7: the resume socket carries resumed_chat_group_id learned from chat_metadata.
    m = _manager(hume_session_s=60.3)                # resume timer fires at 0.3s
    try:
        m.feed_audio("h1", b"\x00" * 10)
        _wait(lambda: m._t)
        m._t[0].push({"type": "chat_metadata", "chat_id": "c", "chat_group_id": "gRes"})
        assert _wait(lambda: m._holders["h1"].chat_group_id == "gRes")
        assert _wait(lambda: len(m._t) == 2)         # proactive resume, not a user reopen
        assert m._t[0].resumed is None and m._t[1].resumed == "gRes"
        # the retired socket is actually closed and the holder survives on socket #2
        assert m._t[0].closed
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: any(x.get("type") == "audio_input" for x in m._t[1].sent))
    finally:
        m.shutdown()


def test_hume_factory_sends_session_settings_before_any_audio(monkeypatch):
    # ON-WRIST RED (ios msg_b6ec3388, proven 4 ways): without session_settings Hume never
    # recognizes our 16k PCM as speech (deaf after greeting), the CLM callback loses
    # custom_session_id keying (T8 attribution) and language_model_api_key (CLM bearer).
    # The FIRST uplink frame after connect MUST be the session_settings declaration.
    import sys
    import types

    class FakeWs:
        def __init__(self):
            self.sent = []

        def send(self, payload):
            self.sent.append(json.loads(payload))

        def settimeout(self, v):
            pass

        def recv(self):
            raise RuntimeError("n/a")

        def close(self):
            pass

    captured = {}

    def fake_create_connection(url, header=None, timeout=None):
        captured["url"] = url
        ws = FakeWs()
        captured["ws"] = ws
        return ws

    monkeypatch.setitem(sys.modules, "websocket",
                        types.SimpleNamespace(create_connection=fake_create_connection))
    monkeypatch.setattr(sr, "_secret",
                        lambda k: {"HUME_CONFIG_ID": "cfg", "HUME_CONFIG_VERSION": "5",
                                   "HUME_API_KEY": "hk", "CUSTOM_LLM_BEARER": "clm-bearer"}.get(k, ""))
    sock = sr.hume_socket_factory("conv-42")
    ws = captured["ws"]
    assert ws.sent, "factory sent NOTHING after connect — Hume gets no session_settings"
    first = ws.sent[0]
    assert first["type"] == "session_settings"
    assert first["audio"] == {"encoding": "linear16", "sample_rate": 16000, "channels": 1}
    assert first["custom_session_id"] == "conv-42"
    assert first["language_model_api_key"] == "clm-bearer"
    # audio flows AFTER the declaration
    sock.send(sr._encode_uplink("hume", b"\x01\x02"))
    assert ws.sent[1]["type"] == "audio_input"


def test_elevenlabs_default_unchanged_when_vendor_fn_says_elevenlabs():
    calls = []
    m = sr.RelayManager(socket_factory=lambda cid: (calls.append(cid) or FakeHumeSocket()),
                        vendor_fn=lambda: "elevenlabs")
    try:
        m.feed_audio("e1", b"\x00" * 10)
        assert _wait(lambda: calls == ["e1"])
    finally:
        m.shutdown()
