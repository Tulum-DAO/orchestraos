"""RED-first — EL socket idle-close driver (arturo-idle-close-spec.md,
DEC-1788855933961869 CONSENSUS_REACHED honest @bbf59693: both slots verified on the live
sha). Flag ARTURO_STREAM_IDLE_CLOSE default OFF; 45s idle timer (gen-guarded, on the T6
_retire_socket(reconnect=False) substrate); server-VAD speech-threshold reopen with the
I2 sustained-soft-speech fallback; silent el_idle_closed/el_idle_resumed beats; I1
partials pause; I3 _el_map prune per cycle. Kept out of test_stream_relay.py (25 EL
baseline untouched)."""
import json
import math
import threading
import time

import numpy as np

from services.arturo import stream_relay as sr


class FakeELSocket:
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


def _tone(amplitude, seconds=0.05):
    n = int(sr.BYTES_PER_S // 2 * seconds)
    t = np.arange(n)
    return (np.sin(2 * math.pi * 440 * t / 16000) * amplitude).astype("<i2").tobytes()


SPEECH = _tone(10000)          # rms ~7000, clearly above the floor
SOFT = _tone(200)              # rms ~140: above mute, below the speech floor
MUTE = b"\x00" * len(SPEECH)   # zero-filled gated silence, rms 0


def _manager(**kw):
    socks = []

    def factory(cid):
        s = FakeELSocket()
        socks.append(s)
        return s
    kw.setdefault("idle_close", True)
    kw.setdefault("idle_close_s", 0.25)
    kw.setdefault("vad_rms_floor", 500.0)
    kw.setdefault("soft_reopen_s", 0.12)
    m = sr.RelayManager(socket_factory=factory, **kw)
    m._t = socks
    return m


def _wait(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _events(m, cid):
    return [e["type"] for e in m.events(cid, 0)[0]]


def test_idle_close_drops_socket_after_inactivity():
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._t)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)   # idle fired
        assert m._t[0].closed
        assert "c1" in m._holders and m.surface("c1") == "watch"           # NOT end()
        assert "el_idle_closed" in _events(m, "c1")
    finally:
        m.shutdown()


def test_idle_close_reopens_on_next_real_utterance():
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: len(m._t) == 2)                               # fresh socket
        assert m.replay_block("c1") is not None                            # replay owed path alive
    finally:
        m.shutdown()


def test_activity_resets_idle_timer():
    m = _manager(idle_close_s=0.4)
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._t)
        for _ in range(4):                                                 # keep talking
            time.sleep(0.15)
            m.feed_audio("c1", SPEECH)
        assert m._holders["c1"].sock is not None                           # never idle-closed
        assert "el_idle_closed" not in _events(m, "c1")
    finally:
        m.shutdown()


def test_subthreshold_uplink_does_not_reopen():
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        for _ in range(3):
            m.feed_audio("c1", MUTE)                                       # gated silence
        m.feed_audio("c1", _tone(20))                                      # sub-mute noise
        time.sleep(0.2)
        assert len(m._t) == 1, "sub-threshold uplink must not reopen"
    finally:
        m.shutdown()


def test_speech_energy_reopens_and_resets_timer():
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: len(m._t) == 2)
        assert m._holders["c1"].sock is not None
        # activity was reset at reopen: no instant re-close
        time.sleep(0.1)
        assert m._holders["c1"].sock is not None
    finally:
        m.shutdown()


def test_agent_audio_resets_idle_timer_no_mid_speech_close():
    m = _manager(idle_close_s=0.3)
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._t)
        s = m._t[0]
        for _ in range(5):                              # long agent turn: only audio downlink
            time.sleep(0.1)
            s.push({"type": "audio", "audio_event": {"audio_base_64": "QUJD"}})
        time.sleep(0.1)
        assert m._holders["c1"].sock is not None, "idle-close fired mid-agent-speech"
    finally:
        m.shutdown()


def test_tap_force_reopens_and_holds():
    # a tap's interrupt() path resumes REAL audio uplink — speech energy reopens immediately
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        m.feed_audio("c1", SPEECH)                       # the tap-resumed feed
        assert _wait(lambda: len(m._t) == 2 and m._holders["c1"].sock is not None)
    finally:
        m.shutdown()


def test_idle_reopen_beat_is_not_reconnecting():
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: len(m._t) == 2)
        evs = _events(m, "c1")
        assert "el_idle_closed" in evs and "el_idle_resumed" in evs
        assert "reconnecting" not in evs and "reconnected" not in evs
    finally:
        m.shutdown()


def test_idle_close_flag_off_no_behavior_change():
    m = _manager(idle_close=False, idle_close_s=0.15)
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._t)
        time.sleep(0.6)
        assert m._holders["c1"].sock is not None         # lifecycle exactly as today
        assert "el_idle_closed" not in _events(m, "c1")
    finally:
        m.shutdown()


def test_reopen_idempotent_under_inflight():
    m = _manager()
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        t1 = threading.Thread(target=m.feed_audio, args=("c1", SPEECH))
        t2 = threading.Thread(target=m.feed_audio, args=("c1", SPEECH))
        t1.start(); t2.start(); t1.join(); t2.join()
        time.sleep(0.2)
        assert len(m._t) == 2, f"two speech chunks opened {len(m._t)-1} sockets, want exactly 1"
    finally:
        m.shutdown()


def test_repeated_idle_cycles_single_conv_one_journal():
    finalized = []
    m = _manager(on_finalize=finalized.append)
    try:
        for cycle in range(3):
            m.feed_audio("c1", SPEECH)
            assert _wait(lambda: len(m._t) == cycle + 1)
            el_id = f"el-{cycle}"
            m._t[cycle].push({"type": "conversation_initiation_metadata",
                              "conversation_initiation_metadata_event": {"conversation_id": el_id}})
            assert _wait(lambda: m.resolve(el_id) == "c1")   # maps WITHIN the cycle
            assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
            assert m.resolve(el_id) is None                  # I3: pruned at idle-close
        assert len(m._holders) == 1 and m.sole_live() == "c1"
        assert finalized == []                               # no end(): one journal seam intact
    finally:
        m.shutdown()


def test_stale_idle_timer_after_close_noops():
    m = _manager(idle_close_s=0.3)
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._t)
        h = m._holders["c1"]
        h.close()                                            # gen bump + timer cancel
        time.sleep(0.5)                                      # let any stale fire happen
        assert "el_idle_closed" not in _events(m, "c1")      # no action, no beat
    finally:
        m.shutdown()


def test_soft_speech_sustained_reopens():
    m = _manager(soft_reopen_s=0.08)                         # ~2 soft chunks of 0.05s
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        m.feed_audio("c1", SOFT)
        m.feed_audio("c1", SOFT)
        assert _wait(lambda: len(m._t) == 2), "sustained soft speech must reopen (deafness guard)"
    finally:
        m.shutdown()


def test_partials_paused_while_idle_closed():
    scribed = []
    m = _manager(partials_enabled=True, partials_cadence_s=0.01,
                 scribe_fn=lambda pcm: scribed.append(len(pcm)) or "t")
    try:
        m.feed_audio("c1", SPEECH)
        assert _wait(lambda: m._holders["c1"].sock is None, timeout=1.5)
        n = len(scribed)
        for _ in range(5):
            m.feed_audio("c1", MUTE)                         # idle audio must not bill Scribe
        time.sleep(0.2)
        assert len(scribed) == n, "Scribe billed on idle audio while idle-closed"
    finally:
        m.shutdown()
