"""RED-first — orphaned-conversation reaper (ios msg_3107d68b: watch app DIED mid-call,
Hume socket + live journal burned for ~60s until a manual /end; the operator: 'make sure we're not
wasting tokens'). Distinct from idle-close (a quiet-but-ALIVE client still POSTs silence and
polls events; idle-close retires the socket but keeps the conversation). An ORPHAN = no
uplink POST and no events poll at all for orphan_end_s -> full end(): socket retired without
reconnect, journal finalized, usage recorded, tombstoned."""
import threading
import time

from services.arturo import stream_relay as sr


class QuietSocket:
    def __init__(self):
        self._cv = threading.Condition()
        self.closed = False

    def send(self, payload):
        pass

    def recv(self):
        with self._cv:
            self._cv.wait(timeout=0.1)
            if self.closed:
                raise RuntimeError("closed")
            return '{"type": "ping"}'

    def close(self):
        self.closed = True
        with self._cv:
            self._cv.notify_all()


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.03)
    return False


def _manager(**kw):
    kw.setdefault("orphan_end", True)
    kw.setdefault("orphan_end_s", 0.4)
    kw.setdefault("orphan_sweep_s", 0.1)
    return sr.RelayManager(socket_factory=lambda cid: QuietSocket(), **kw)


def test_orphan_is_fully_ended():
    finalized = []

    class U:
        calls = []

        def add_seconds(self, vendor, seconds):
            U.calls.append((vendor, seconds))

        def over_cap(self, vendor):
            return False
    U.calls = []
    m = _manager(on_finalize=finalized.append, usage=U())
    try:
        m.feed_audio("c1", b"\x00" * 10)
        # dead client: no more feeds, no events polls
        assert _wait(lambda: "c1" not in m._holders), "orphan was never ended"
        assert finalized == ["c1"]                    # journal finalize fired
        assert m.is_ended("c1")                       # tombstoned
        assert len(U.calls) == 1                      # usage recorded exactly once
    finally:
        m.shutdown()


def test_events_polling_keeps_conversation_alive():
    m = _manager()
    try:
        m.feed_audio("c1", b"\x00" * 10)
        end = time.time() + 1.2
        while time.time() < end:
            m.events("c1", 0)                         # alive client polling, no audio
            time.sleep(0.08)
        assert "c1" in m._holders, "a polling client must NEVER be orphan-ended"
    finally:
        m.shutdown()


def test_feeding_keeps_conversation_alive():
    m = _manager()
    try:
        end = time.time() + 1.2
        while time.time() < end:
            m.feed_audio("c1", b"\x00" * 10)
            time.sleep(0.08)
        assert "c1" in m._holders
    finally:
        m.shutdown()


def test_orphan_end_disabled_is_inert():
    m = _manager(orphan_end=False)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        time.sleep(1.0)
        assert "c1" in m._holders                     # nothing reaped when off
    finally:
        m.shutdown()
