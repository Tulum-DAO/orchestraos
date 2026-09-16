"""RED-first — vendor credit-refusal visibility + claim gate (ios msg_bf132099: Hume
E0300 zero_credits arrived as a bare {type:'error'} passthrough the watch ignores — the
wrist heard the greeting then silence; and a Settings flip to a known-dead vendor was
accepted at claim). (1) A credit/auth-class Hume error event emits the SAME one-shot
visible vendor_unavailable beat as an EL 3000 close; (2) the refusal is CACHED on the
manager so a NEW conversation on that vendor is refused visibly at claim (spec §6) until
the TTL lapses or a healthy frame clears it."""
import threading
import time

from services.arturo import stream_relay as sr


class FakeHumeSocket:
    def __init__(self):
        self.sent = []
        self._incoming = []
        self._cv = threading.Condition()
        self.closed = False

    def send(self, payload):
        pass

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


E0300 = {"type": "error", "code": "E0300", "slug": "zero_credits",
         "message": "Exhausted credit balance. Visit platform.hume.ai/billing"}


def _hume_manager(**kw):
    socks = []

    def f(cid, resumed_chat_group_id=None):
        s = FakeHumeSocket()
        socks.append(s)
        return s
    m = sr.RelayManager(factories={"hume": f}, vendor_fn=lambda: "hume",
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


def test_hume_credit_error_event_emits_visible_beat_once():
    m = _hume_manager()
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        m._t[0].push(E0300)
        m._t[0].push(E0300)
        assert _wait(lambda: any(e["type"] == "vendor_unavailable"
                                 for e in m.events("h1", 0)[0]))
        time.sleep(0.1)
        beats = [e for e in m.events("h1", 0)[0] if e["type"] == "vendor_unavailable"]
        assert len(beats) == 1
        assert "hume" in beats[0]["message"] and "credit" in beats[0]["message"]
    finally:
        m.shutdown()


def test_refused_vendor_blocked_at_claim_until_ttl():
    m = _hume_manager(refusal_ttl_s=0.4)
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        m._t[0].push(E0300)
        assert _wait(lambda: any(e["type"] == "vendor_unavailable"
                                 for e in m.events("h1", 0)[0]))
        r = m.feed_audio("h2", b"\x00" * 10)          # NEW conversation on the dead vendor
        assert not r["ok"] and r["error"] == "vendor_unavailable" and "credit" in r["message"]
        assert "h2" not in m._holders
        time.sleep(0.5)                               # TTL lapses: self-healing
        assert m.feed_audio("h3", b"\x00" * 10)["ok"]
    finally:
        m.shutdown()


def test_el_credit_close_records_refusal_for_claim():
    class DyingSocket:
        def send(self, payload):
            pass

        def recv(self):
            time.sleep(0.01)
            raise RuntimeError("el socket closed: code 3000 [quota_exceeded] no credits")

        def close(self):
            pass
    m = sr.RelayManager(socket_factory=lambda cid: DyingSocket())
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: any(e["type"] == "vendor_unavailable"
                                 for e in m.events("c1", 0)[0]))
        r = m.feed_audio("c2", b"\x00" * 10)
        assert not r["ok"] and r["error"] == "vendor_unavailable"
    finally:
        m.shutdown()


def test_healthy_fresh_session_clears_refusal():
    # Recovery is proven by a FRESH session's first frame (an existing conversation's
    # reconnect succeeding), never by trailing frames on the dying socket.
    m = _hume_manager(refusal_ttl_s=60)
    try:
        m.feed_audio("h1", b"\x00" * 10)
        assert _wait(lambda: m._t)
        m._t[0].push(E0300)
        assert _wait(lambda: any(e["type"] == "vendor_unavailable"
                                 for e in m.events("h1", 0)[0]))
        assert not m.feed_audio("h2", b"\x00" * 10)["ok"]
        m._t[0].close()                                   # session dies -> h1 reconnects
        assert _wait(lambda: len(m._t) >= 2)
        m._t[-1].push({"type": "chat_metadata", "chat_id": "c", "chat_group_id": "g"})
        assert _wait(lambda: m.feed_audio("h3", b"\x00" * 10)["ok"])
    finally:
        m.shutdown()
