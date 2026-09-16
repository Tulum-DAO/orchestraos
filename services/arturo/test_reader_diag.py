"""RED-first — reader diagnostics + visible credit-close (ios msg_137baa61 + msg_2c5d8d9c):
(1) _reader must LOG the recv exception at retire (two silent socket_downs on the operator's first
hume wrist call had no recorded cause); (2) a dispatch exception must never silently kill
the reader (deaf-alive socket with NO socket_down); (3) the EL socket wrapper surfaces the
CLOSE code+reason (EL storms were close 3000 [quota_exceeded] — invisible until a direct
probe); (4) a credit-class close emits ONE visible vendor_unavailable beat instead of a
silent bounded storm. Kept out of test_stream_relay.py (25 EL baseline untouched)."""
import logging
import threading
import time

from services.arturo import stream_relay as sr


class DyingSocket:
    def __init__(self, err):
        self.err = err

    def send(self, payload):
        pass

    def recv(self):
        time.sleep(0.01)
        raise RuntimeError(self.err)

    def close(self):
        pass


class PushSocket:
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False

    def send(self, payload):
        import json
        self.sent.append(json.loads(payload))

    def push(self, ev):
        import json
        with self._cv:
            self._incoming.append(json.dumps(ev))
            self._cv.notify()

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


def _wait(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_reader_logs_recv_failure_with_cause(caplog):
    m = sr.RelayManager(socket_factory=lambda cid: DyingSocket("el socket closed: code 1006 going away"))
    try:
        with caplog.at_level(logging.WARNING, logger="arturo-stream-relay"):
            m.feed_audio("c1", b"\x00" * 10)
            assert _wait(lambda: any("reader recv failed" in r.message for r in caplog.records))
        msgs = [r.message for r in caplog.records if "reader recv failed" in r.message]
        assert any("1006" in x for x in msgs), "the cause (repr) must be in the log line"
    finally:
        m.shutdown()


def test_dispatch_exception_does_not_kill_reader(monkeypatch, caplog):
    orig = sr._Holder._dispatch_el

    def boomy(self, d, sock):
        if d.get("type") == "boom":
            raise ValueError("kaboom")
        return orig(self, d, sock)
    monkeypatch.setattr(sr._Holder, "_dispatch_el", boomy)
    socks = []
    m = sr.RelayManager(socket_factory=lambda cid: socks.append(PushSocket()) or socks[-1])
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: socks)
        with caplog.at_level(logging.ERROR, logger="arturo-stream-relay"):
            socks[0].push({"type": "boom"})
            assert _wait(lambda: any("dispatch failed" in r.message for r in caplog.records))
        # reader survived: a ping still gets its pong on the SAME socket, no retirement
        socks[0].push({"type": "ping", "ping_event": {"event_id": 7}})
        assert _wait(lambda: any(x.get("type") == "pong" for x in socks[0].sent)), \
            "dispatch exception killed the reader (deaf-alive socket)"
        assert len(socks) == 1 and m._holders["c1"].sock is socks[0]
    finally:
        m.shutdown()


def test_el_sock_wrapper_surfaces_close_code_and_reason():
    import websocket

    class FakeFrame:
        data = (3000).to_bytes(2, "big") + b"[quota_exceeded] You've run out of credits."

    class FakeWs:
        def recv_data(self, control_frame=False):
            return websocket.ABNF.OPCODE_CLOSE, FakeFrame()

        def close(self):
            pass
    s = sr._el_sock(FakeWs())
    try:
        s.recv()
        raise AssertionError("close frame must raise")
    except RuntimeError as e:
        assert "3000" in str(e) and "quota_exceeded" in str(e)


def test_recv_timeout_is_idle_not_death():
    # ios msg_fd94e988: create_connection(timeout=30) doubles as the READ timeout —
    # Hume sends NO frames while the user is quiet, recv raised WebSocketTimeoutException
    # every 30s and the reader retired the socket into a fresh chat (33.6s/30.1s chat
    # lifetimes on the operator's call). A read-timeout is vendor SILENCE, never death.
    import websocket

    class TimeoutSocket:
        def __init__(self):
            self.closed = False

        def send(self, payload):
            pass

        def recv(self):
            time.sleep(0.02)
            raise websocket.WebSocketTimeoutException("timed out")

        def close(self):
            self.closed = True

    socks = []
    m = sr.RelayManager(socket_factory=lambda cid: socks.append(TimeoutSocket()) or socks[-1])
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: socks)
        time.sleep(0.4)                     # many timeout cycles
        assert len(socks) == 1, "read-timeout retired the socket (reconnect chained)"
        assert m._holders["c1"].sock is socks[0] and not socks[0].closed
    finally:
        m.shutdown()


def test_hume_factory_clears_read_timeout_after_handshake(monkeypatch):
    import sys
    import types

    class FakeWs:
        def __init__(self):
            self.sent = []
            self.timeouts = []

        def send(self, payload):
            self.sent.append(payload)

        def settimeout(self, v):
            self.timeouts.append(v)

        def close(self):
            pass

    captured = {}

    def fake_create_connection(url, header=None, timeout=None):
        ws = FakeWs()
        captured["ws"] = ws
        return ws

    monkeypatch.setitem(sys.modules, "websocket",
                        types.SimpleNamespace(create_connection=fake_create_connection))
    monkeypatch.setattr(sr, "_secret", lambda k: "x")
    sr.hume_socket_factory("c1")
    assert None in captured["ws"].timeouts, \
        "factory must ws.settimeout(None) after handshake (30s is the CONNECT guard only)"


def test_credit_close_emits_visible_vendor_unavailable_once():
    m = sr.RelayManager(socket_factory=lambda cid: DyingSocket(
        "el socket closed: code 3000 [quota_exceeded] You've run out of credits."),
        connect_cooldown_s=0.2)
    try:
        for _ in range(6):
            m.feed_audio("c1", b"\x00" * 10)
            time.sleep(0.12)
        assert _wait(lambda: any(e["type"] == "vendor_unavailable"
                                 for e in m.events("c1", 0)[0]))
        beats = [e for e in m.events("c1", 0)[0] if e["type"] == "vendor_unavailable"]
        assert len(beats) == 1, "credit beat must be one-shot per outage, not per retry"
        assert "credit" in beats[0]["message"]
    finally:
        m.shutdown()
