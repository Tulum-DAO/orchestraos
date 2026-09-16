"""RED-first — end() event/replay hygiene (ios msg_84512683: watch 212 reused its
conversation_id across calls with cursor=0 and REPLAYED the previous conversation's whole
event buffer before the new greeting — the buffer survives /end; the tombstone only gates
the UPLINK). Spec §6 'never resurrect after End' applies to the downlink too: (1) end()
drops the cid's event buffer; (2) the events route returns 410 ended for a tombstoned cid
so client-side id reuse can never replay a dead conversation."""
import importlib.util
import pathlib
import threading
import time

import pytest

from services.arturo import ptt_stream
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
            return '{"type": "agent_response", "agent_response_event": {"agent_response": "hi"}}'

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


def test_eventbuffer_drop():
    b = ptt_stream.EventBuffer()
    b.put("c1", {"type": "x"})
    assert b.since("c1", 0)[0]
    b.drop("c1")
    assert b.since("c1", 0) == ([], 0)


def test_end_drops_event_buffer_and_marks_ended():
    m = sr.RelayManager(socket_factory=lambda cid: QuietSocket())
    try:
        m.feed_audio("c1", b"\x00" * 10)
        assert _wait(lambda: m.events("c1", 0)[0])       # buffer has events
        assert m.end("c1")
        assert m.events("c1", 0) == ([], 0), "event buffer must not survive end()"
        assert m.is_ended("c1")                          # tombstone visible to the route
        assert not m.is_ended("other")
    finally:
        m.shutdown()


def test_events_route_410_on_ended_cid(monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_endhyg", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = mod.app.test_client()
    with mod._STREAM_RELAY._lock:
        mod._STREAM_RELAY._tombstones["cDead"] = time.time()
    r = c.get("/ptt/stream/events?cursor=0", headers={"X-Conversation-Id": "cDead"},
              environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 410 and r.get_json()["error"] == "ended"
    # a live/unknown cid still serves normally (empty is fine)
    r2 = c.get("/ptt/stream/events?cursor=0", headers={"X-Conversation-Id": "cFresh"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r2.status_code == 200 and r2.get_json()["ok"]
    mod._STREAM_RELAY.shutdown()
