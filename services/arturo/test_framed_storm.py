"""RED-first — FRAMED instant-death storm gate (ios msg_33a85910, conv 6A4ADC85 at
07:21Z: Hume delivered an I0100 error FRAME on each fresh socket before dropping it, so
_got_frame exempted every death from the frameless storm-breaker and the reconnect chain
looped ~1/s). A streak of FRAMED_STORM_STREAK consecutive socket_down deaths inside the
grace window trips the same cooldown gate; a single framed blip mid-call must NOT gate,
and a socket that lives past the grace window resets the streak. Kept out of
test_stream_relay.py (25 EL baseline untouched)."""
import threading
import time

from services.arturo import stream_relay as sr


class FramedThenDeadSocket:
    """Delivers exactly ONE frame (like Hume's I0100 error event), then dies."""

    def __init__(self):
        self.closed = False
        self._served = False

    def send(self, payload):
        if self.closed:
            raise RuntimeError("down")

    def recv(self):
        if not self._served:
            self._served = True
            return '{"type": "ping"}'
        time.sleep(0.01)
        raise RuntimeError("connection lost after one frame")

    def close(self):
        self.closed = True


class HealthySocket:
    def __init__(self):
        self._cv = threading.Condition()
        self._incoming = ['{"type": "ping"}']
        self.closed = False

    def send(self, payload):
        pass

    def recv(self):
        with self._cv:
            while not self._incoming and not self.closed:
                self._cv.wait(timeout=0.1)
            if self.closed:
                raise RuntimeError("closed")
            return self._incoming.pop(0)

    def close(self):
        self.closed = True
        with self._cv:
            self._cv.notify_all()


def test_framed_instant_death_storm_is_bounded_by_cooldown():
    socks = []
    m = sr.RelayManager(socket_factory=lambda cid: socks.append(FramedThenDeadSocket()) or socks[-1],
                        connect_cooldown_s=5.0)
    try:
        for _ in range(12):
            m.feed_audio("c1", b"\x00" * 10)
            time.sleep(0.06)
        time.sleep(0.5)
        assert len(socks) <= 4, (          # literal: streak(3) + at most one more connect
            f"{len(socks)} sockets in ~1s: framed one-frame-then-die deaths are storming — "
            "a streak must trip the connect cooldown like a frameless death does")
    finally:
        m.shutdown()


def test_single_framed_blip_does_not_gate_reconnect():
    socks = []

    def factory(cid):
        s = FramedThenDeadSocket() if not socks else HealthySocket()
        socks.append(s)
        return s

    m = sr.RelayManager(socket_factory=factory, connect_cooldown_s=5.0)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        time.sleep(0.4)                    # first socket dies fast (streak 1 < 3)
        m.feed_audio("c1", b"\x00" * 10)
        time.sleep(0.3)
        assert len(socks) >= 2, "a single framed blip must not cooldown-block the reopen"
        assert not socks[-1].closed
    finally:
        m.shutdown()


def test_long_lived_socket_resets_the_streak(monkeypatch):
    monkeypatch.setattr(sr, "FRAMELESS_GRACE_S", 0.15)

    class SlowDeathSocket:
        """One frame, then dies AFTER the (patched) grace window."""

        def __init__(self):
            self.closed = False
            self._served = False

        def send(self, payload):
            if self.closed:
                raise RuntimeError("down")

        def recv(self):
            if not self._served:
                self._served = True
                return '{"type": "ping"}'
            time.sleep(0.3)
            raise RuntimeError("died after grace")

        def close(self):
            self.closed = True

    socks = []

    def factory(cid):
        # two fast framed deaths, then a past-grace death (resets), then two fast again:
        # the streak never reaches 3 consecutively, so nothing may gate.
        s = SlowDeathSocket() if len(socks) == 2 else FramedThenDeadSocket()
        socks.append(s)
        return s

    m = sr.RelayManager(socket_factory=factory, connect_cooldown_s=5.0)
    try:
        end = time.time() + 1.6
        while time.time() < end:
            m.feed_audio("c1", b"\x00" * 10)
            time.sleep(0.05)
        assert len(socks) >= 5, (
            f"only {len(socks)} sockets: the past-grace death did not reset the streak — "
            "two separated fast-death pairs must never trip the storm gate")
    finally:
        m.shutdown()


def test_storm_constants_are_env_overridable(monkeypatch):
    # gm msg_913187bd: the gate must be tunable in an incident and the bite reproducible
    # without editing code — the constants read their env at import.
    import importlib
    monkeypatch.setenv("ARTURO_FRAMED_STORM_STREAK", "7")
    monkeypatch.setenv("ARTURO_FRAMELESS_GRACE_S", "0.5")
    mod = importlib.reload(sr)
    try:
        assert mod.FRAMED_STORM_STREAK == 7
        assert mod.FRAMELESS_GRACE_S == 0.5
    finally:
        monkeypatch.delenv("ARTURO_FRAMED_STORM_STREAK")
        monkeypatch.delenv("ARTURO_FRAMELESS_GRACE_S")
        importlib.reload(sr)      # restore defaults for the rest of the session
