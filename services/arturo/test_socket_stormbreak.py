"""RED-first — short-lived frameless-socket storm-breaker (ios msg_59d6dcd5: EL sockets
open then die before ANY frame; that bypasses the connect-cooldown, which only trips on
connect FAILURE, so the relay reconnect-storms 2-3/s). A connection that dies within the
grace window having delivered ZERO frames must count as a connect failure: it trips the
same cooldown AND blocks the reconnect chain, bounding the storm to ~1 connect per
cooldown window. Vendor-agnostic. Kept out of test_stream_relay.py (25 EL baseline)."""
import threading
import time

from services.arturo import stream_relay as sr


class FramelessSocket:
    """Opens fine, then recv fails immediately — zero frames ever delivered."""

    def __init__(self):
        self.closed = False

    def send(self, payload):
        if self.closed:
            raise RuntimeError("down")

    def recv(self):
        time.sleep(0.01)
        raise RuntimeError("closed before any frame")

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


def test_frameless_death_storm_is_bounded_by_cooldown():
    socks = []
    m = sr.RelayManager(socket_factory=lambda cid: socks.append(FramelessSocket()) or socks[-1],
                        connect_cooldown_s=5.0)
    try:
        for _ in range(10):
            m.feed_audio("c1", b"\x00" * 10)
            time.sleep(0.06)
        time.sleep(0.5)                        # let any reconnect chain run out
        assert len(socks) <= 3, (
            f"{len(socks)} sockets in ~1s: frameless open-then-die is storming — it must "
            "trip the connect cooldown and stall the reconnect chain")
    finally:
        m.shutdown()


def test_healthy_socket_unaffected_by_stormbreak():
    socks = []
    m = sr.RelayManager(socket_factory=lambda cid: socks.append(HealthySocket()) or socks[-1],
                        connect_cooldown_s=5.0)
    try:
        m.feed_audio("c1", b"\x00" * 10)
        time.sleep(0.3)                        # socket delivered a frame; stays up
        m.feed_audio("c1", b"\x00" * 10)
        assert len(socks) == 1 and not socks[0].closed
    finally:
        m.shutdown()


def test_cooldown_expiry_allows_retries_without_storming():
    # The cooldown must EXPIRE (retries happen — no permanent wedge) but stay BOUNDED
    # (~one connect per window — no storm). Feed continuously for ~1.2s at 0.3s cooldown.
    socks = []
    m = sr.RelayManager(socket_factory=lambda cid: socks.append(FramelessSocket()) or socks[-1],
                        connect_cooldown_s=0.3)
    try:
        end = time.time() + 1.2
        while time.time() < end:
            m.feed_audio("c1", b"\x00" * 10)
            time.sleep(0.05)
        assert len(socks) >= 2, "cooldown never expired — reopen permanently wedged"
        assert len(socks) <= 6, f"{len(socks)} connects in 1.2s at 0.3s cooldown — still storming"
    finally:
        m.shutdown()
