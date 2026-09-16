"""RED-first — relay daemon observability (gm msg_1f417cdd: /proc comm probe is a false
negative for Python thread names, so daemon liveness must be gate-checkable by effect:
one startup log line per daemon thread + a /health field listing active relay daemons).
Kept out of test_stream_relay.py (25 EL baseline untouched)."""
import importlib.util
import pathlib
import threading

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


def test_orphan_sweeper_registers_daemon_and_logs(caplog):
    with caplog.at_level("INFO", logger="arturo-stream-relay"):
        m = sr.RelayManager(socket_factory=lambda cid: QuietSocket(),
                            orphan_end=True, orphan_end_s=60.0)
    try:
        assert "orphan-sweeper" in m.daemons
        assert any("orphan sweeper started" in r.message for r in caplog.records), \
            "ctor must emit a startup log line for the sweeper daemon"
    finally:
        m.shutdown()


def test_orphan_disabled_registers_no_daemon(caplog):
    with caplog.at_level("INFO", logger="arturo-stream-relay"):
        m = sr.RelayManager(socket_factory=lambda cid: QuietSocket(), orphan_end=False)
    try:
        assert "orphan-sweeper" not in m.daemons
        assert not any("orphan sweeper started" in r.message for r in caplog.records)
    finally:
        m.shutdown()


def test_health_lists_relay_daemons(monkeypatch):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    monkeypatch.setenv("ARTURO_ORPHAN_END", "1")
    monkeypatch.delenv("ARTURO_WARM_VOICES_CACHE", raising=False)
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_daemonobs", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        c = mod.app.test_client()
        r = c.get("/health")
        assert r.status_code == 200
        daemons = r.get_json().get("daemons")
        assert daemons is not None, "/health must carry a daemons field"
        assert "orphan-sweeper" in daemons
        assert "voices-cache-warm" not in daemons  # warm flag off => not claimed
    finally:
        mod._STREAM_RELAY.shutdown()


def test_health_daemons_empty_when_relay_off(monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    monkeypatch.delenv("ARTURO_WARM_VOICES_CACHE", raising=False)
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_daemonobs_off", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    c = mod.app.test_client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.get_json().get("daemons") == []
