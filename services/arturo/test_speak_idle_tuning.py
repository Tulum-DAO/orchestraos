"""RED-first — agent_speaking flap fix (ios 221 grade msg_cb3a8769, vc_2b106dfe06dbec74):
SPEAK_IDLE_MS=400 was shorter than Hume's real inter-chunk gap (~1s), so the machine
flapped true/false on nearly every audio_output chunk (94 true edges in 6 min). Default
goes to 1500ms (covers observed pacing; a ~1.5s trailing 'speaking' after a reply is the
right trade for an echo gate — over-hold beats flap) and the constant becomes
env-overridable (ARTURO_SPEAK_IDLE_MS) like the storm constants. The gen-guarded machine
itself is untouched. Kept out of test_stream_relay.py (25 EL baseline)."""
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


def _wav48(seconds=0.02):
    n = int(48000 * seconds)
    t = np.arange(n) / 48000
    frames = (np.sin(2 * math.pi * 440 * t) * 10000).astype("<i2").tobytes()
    fmt = struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16)
    body = b"fmt " + fmt + b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_default_is_1500_and_env_overridable(monkeypatch):
    assert sr.SPEAK_IDLE_MS == 1500, "default must cover Hume's ~1s inter-chunk pacing"
    import importlib
    monkeypatch.setenv("ARTURO_SPEAK_IDLE_MS", "900")
    mod = importlib.reload(sr)
    try:
        assert mod.SPEAK_IDLE_MS == 900
    finally:
        monkeypatch.delenv("ARTURO_SPEAK_IDLE_MS")
        importlib.reload(sr)


def test_no_flap_across_sub_idle_chunk_gaps():
    # chunks 300ms apart with a 1000ms idle: ONE true edge, zero false edges mid-reply,
    # then exactly one false edge after the last chunk's idle expiry.
    socks = []

    def factory(cid, resumed_chat_group_id=None):
        s = FakeHumeSocket()
        socks.append(s)
        return s

    m = sr.RelayManager(factories={"hume": factory}, vendor_fn=lambda: "hume",
                        vendor_check=lambda v: "", speaking_enabled=True,
                        speak_idle_ms=1000)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: socks)
        s = socks[0]
        wav = base64.b64encode(_wav48()).decode()
        for i in range(4):
            s.push({"type": "audio_output", "id": "a0", "index": i, "data": wav})
            time.sleep(0.3)
        def speak_events():
            return [e for e in m.events("c1", 0)[0] if e["type"] == "agent_speaking"]
        assert _wait(lambda: any(e["speaking"] for e in speak_events()))
        trues = [e for e in speak_events() if e["speaking"]]
        falses = [e for e in speak_events() if not e["speaking"]]
        assert len(trues) == 1, f"flapping: {len(trues)} true edges for one reply"
        assert len(falses) == 0, "span closed mid-reply despite sub-idle gaps"
        assert _wait(lambda: [e for e in speak_events() if not e["speaking"]], timeout=2.5)
        assert len([e for e in speak_events() if not e["speaking"]]) == 1
    finally:
        m.shutdown()
