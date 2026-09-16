"""RED-first — server-authoritative agent_speaking boundary (arturo-agent-speaking-spec.md
FROZEN @642abdd9 (commit a4477e118f), DEC-1788854280517526 CONSENSUS_REACHED honest: both
slots verified on 642abdd9). Flag ARTURO_STREAM_SPEAKING default OFF => downlink
byte-identical. Gen-token guard per the claude COUNTER_PROPOSE: Timer.cancel() is a no-op
once the callback began, so every state change bumps _speak_gen and a fired-while-blocked
stale timer must no-op. Kept out of test_stream_relay.py (25 EL baseline untouched)."""
import json
import threading
import time

import pytest

from services.arturo import stream_relay as sr


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    monkeypatch.delenv("ARTURO_STREAM_PARTIALS", raising=False)
    monkeypatch.delenv("ARTURO_STREAM_SPEAKING", raising=False)


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


def _manager(**kw):
    socks = []

    def factory(cid):
        s = FakeELSocket()
        socks.append(s)
        return s
    kw.setdefault("speaking_enabled", True)
    kw.setdefault("speak_idle_ms", 150)
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


def _speaks(m, cid):
    return [e for e in m.events(cid, 0)[0] if e["type"] == "agent_speaking"]


def _sock(m, cid="c1"):
    m.feed_audio(cid, b"\x00" * 10)
    assert _wait(lambda: m._t)
    return m._t[-1]


AUDIO = {"type": "audio", "audio_event": {"audio_base_64": "QUJD"}}


def test_speaking_true_on_first_agent_audio():
    m = _manager()
    try:
        s = _sock(m)
        s.push(AUDIO)
        s.push(AUDIO)
        assert _wait(lambda: _speaks(m, "c1"))
        time.sleep(0.05)
        trues = [e for e in _speaks(m, "c1") if e["speaking"]]
        assert len(trues) == 1                      # not re-emitted on the second chunk
    finally:
        m.shutdown()


def test_speaking_false_on_idle_timeout():
    m = _manager()
    try:
        s = _sock(m)
        s.push(AUDIO)
        assert _wait(lambda: any(not e["speaking"] for e in _speaks(m, "c1")))
        ev = _speaks(m, "c1")
        assert ev[0]["speaking"] and not ev[-1]["speaking"]
        assert ev[-1]["epoch_ms"] >= ev[0]["epoch_ms"]
    finally:
        m.shutdown()


def test_speaking_false_on_interruption():
    m = _manager(speak_idle_ms=5000)
    try:
        s = _sock(m)
        s.push(AUDIO)
        assert _wait(lambda: _speaks(m, "c1"))
        s.push({"type": "interruption"})
        assert _wait(lambda: any(not e["speaking"] for e in _speaks(m, "c1")))
        evs = m.events("c1", 0)[0]
        types = [e["type"] for e in evs]
        assert "barge_in" in types                  # still forwarded
        # speaking:false lands before/with barge_in
        false_i = next(i for i, e in enumerate(evs)
                       if e["type"] == "agent_speaking" and not e["speaking"])
        assert false_i <= types.index("barge_in")
    finally:
        m.shutdown()


def test_speaking_false_on_next_user_transcript():
    m = _manager(speak_idle_ms=5000)
    try:
        s = _sock(m)
        s.push(AUDIO)
        assert _wait(lambda: _speaks(m, "c1"))
        s.push({"type": "user_transcript", "user_transcription_event": {"user_transcript": "hey"}})
        assert _wait(lambda: any(not e["speaking"] for e in _speaks(m, "c1")))
        time.sleep(0.2)
        falses = [e for e in _speaks(m, "c1") if not e["speaking"]]
        assert len(falses) == 1                     # timer canceled: no duplicate false
    finally:
        m.shutdown()


def test_speaking_monotonic_across_turn():
    m = _manager(speak_idle_ms=120)
    try:
        s = _sock(m)
        s.push(AUDIO)
        s.push(AUDIO)
        s.push({"type": "user_transcript", "user_transcription_event": {"user_transcript": "q"}})
        assert _wait(lambda: len(_speaks(m, "c1")) == 2)
        s.push(AUDIO)
        s.push(AUDIO)
        assert _wait(lambda: len(_speaks(m, "c1")) == 4, timeout=3.0)   # idle closes span 2
        flags = [e["speaking"] for e in _speaks(m, "c1")]
        assert flags == [True, False, True, False]
    finally:
        m.shutdown()


def test_speaking_flag_off_no_events():
    m = _manager(speaking_enabled=None)             # env unset by fixture => OFF
    try:
        s = _sock(m)
        s.push(AUDIO)
        s.push({"type": "interruption"})
        assert _wait(lambda: any(e["type"] == "barge_in" for e in m.events("c1", 0)[0]))
        assert _speaks(m, "c1") == []               # downlink byte-identical
    finally:
        m.shutdown()


def test_speaking_epoch_ms_present_and_monotonic():
    m = _manager()
    try:
        s = _sock(m)
        s.push(AUDIO)
        assert _wait(lambda: len(_speaks(m, "c1")) == 2)
        t, f = _speaks(m, "c1")
        assert isinstance(t["epoch_ms"], int) and isinstance(f["epoch_ms"], int)
        assert f["epoch_ms"] >= t["epoch_ms"]
    finally:
        m.shutdown()


def test_no_mid_span_false_when_audio_straddles_idle():
    # The stale-timer race prover: a timer armed by chunk 1 must never emit false after
    # chunk 2 re-armed the span, even if it already fired and was blocked on the lock.
    m = _manager(speak_idle_ms=200)
    try:
        s = _sock(m)
        s.push(AUDIO)                               # t=0: arms timer for 0.2
        time.sleep(0.12)
        s.push(AUDIO)                               # t=0.12: re-arm; first timer now stale
        time.sleep(0.16)                            # t=0.28 > first expiry, < second (0.32)
        assert all(e["speaking"] for e in _speaks(m, "c1")), "mid-span false emitted"
        assert _wait(lambda: any(not e["speaking"] for e in _speaks(m, "c1")))
        falses = [e for e in _speaks(m, "c1") if not e["speaking"]]
        assert len(falses) == 1                     # exactly one false, after audio stops
    finally:
        m.shutdown()


def test_close_cancels_pending_speaking_false():
    m = _manager(speak_idle_ms=150)
    try:
        s = _sock(m)
        s.push(AUDIO)
        assert _wait(lambda: _speaks(m, "c1"))
        m._holders["c1"].close()
        time.sleep(0.4)                             # past SPEAK_IDLE — stale timer must no-op
        assert all(e["speaking"] for e in _speaks(m, "c1")), "speaking:false after close()"
    finally:
        m.shutdown()


def _hume_manager(**kw):
    socks = []

    def f(cid, resumed_chat_group_id=None):
        s = FakeELSocket()
        socks.append(s)
        return s
    kw.setdefault("speaking_enabled", True)
    kw.setdefault("speak_idle_ms", 150)
    m = sr.RelayManager(factories={"hume": f}, vendor_fn=lambda: "hume",
                        vendor_check=lambda v: "", **kw)
    m._t = socks
    return m


HUME_AUDIO = {"type": "audio_output", "id": "a", "index": 0, "data": ""}


def test_hume_speaking_true_on_first_audio_then_idle_false():
    # Wave-5 (ios msg_72c19e09): the echo-bleed client gate needs agent_speaking on the
    # HUME path too — the frozen spec scoped it to EL, but the operator's calls run on hume.
    m = _hume_manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[-1]
        import base64 as b64
        from services.arturo.test_stream_relay_hume import _wav48
        wav = b64.b64encode(_wav48()).decode()
        s.push({"type": "audio_output", "id": "a", "index": 0, "data": wav})
        s.push({"type": "audio_output", "id": "a", "index": 1, "data": wav})
        assert _wait(lambda: _speaks(m, "h1"))
        time.sleep(0.05)
        assert len([e for e in _speaks(m, "h1") if e["speaking"]]) == 1   # edge only
        assert _wait(lambda: any(not e["speaking"] for e in _speaks(m, "h1")))
    finally:
        m.shutdown()


def test_hume_speaking_false_on_user_final_and_interruption_orders_before_barge_in():
    m = _hume_manager(speak_idle_ms=5000)
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        s = m._t[-1]
        import base64 as b64
        from services.arturo.test_stream_relay_hume import _wav48
        s.push({"type": "audio_output", "id": "a", "index": 0,
                "data": b64.b64encode(_wav48()).decode()})
        assert _wait(lambda: _speaks(m, "h1"))
        s.push({"type": "user_interruption", "time": 1})
        assert _wait(lambda: any(not e["speaking"] for e in _speaks(m, "h1")))
        evs = m.events("h1", 0)[0]
        types = [e["type"] for e in evs]
        assert "barge_in" in types
        false_i = next(i for i, e in enumerate(evs)
                       if e["type"] == "agent_speaking" and not e["speaking"])
        assert false_i <= types.index("barge_in")   # false lands before/with barge_in
    finally:
        m.shutdown()


def test_hume_speaking_flag_off_no_events():
    m = _hume_manager(speaking_enabled=None)        # env unset by fixture => OFF
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        import base64 as b64
        from services.arturo.test_stream_relay_hume import _wav48
        m._t[-1].push({"type": "audio_output", "id": "a", "index": 0,
                       "data": b64.b64encode(_wav48()).decode()})
        assert _wait(lambda: any(e["type"] == "audio" for e in m.events("h1", 0)[0]))
        assert _speaks(m, "h1") == []
    finally:
        m.shutdown()


def test_interruption_and_user_final_before_any_audio_noop():
    m = _manager()
    try:
        s = _sock(m)
        s.push({"type": "interruption"})
        assert _wait(lambda: any(e["type"] == "barge_in" for e in m.events("c1", 0)[0]))
        assert _speaks(m, "c1") == []
        m2 = _manager()
        try:
            s2 = _sock(m2)
            s2.push({"type": "user_transcript", "user_transcription_event": {"user_transcript": "x"}})
            assert _wait(lambda: any(e["type"] == "user_transcript" for e in m2.events("c1", 0)[0]))
            assert _speaks(m2, "c1") == []          # no false-without-true
        finally:
            m2.shutdown()
    finally:
        m.shutdown()
