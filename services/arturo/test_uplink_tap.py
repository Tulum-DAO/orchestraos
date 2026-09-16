"""RED-first — uplink PCM tap (ios msg_58d8b8af): ARTURO_UPLINK_TAP=1 appends the raw
s16le/16k PCM the relay receives, per conversation, to state/uplink-tap/<cid>.pcm —
vendor-independent, BEFORE any gating, capped, and NEVER able to affect the call. Lets ios
compute an RMS timeline of the wrist audio (silence holes = gate flap) and replay the
identical bytes without needing the phone cabled. Default OFF => zero filesystem writes."""
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


def _manager():
    return sr.RelayManager(socket_factory=lambda cid: QuietSocket())


def test_tap_off_by_default_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.delenv("ARTURO_UPLINK_TAP", raising=False)
    m = _manager()
    try:
        m.feed_audio("c1", b"\x01\x02" * 100)
        assert not (tmp_path / "state" / "uplink-tap").exists()
    finally:
        m.shutdown()


def test_tap_appends_exact_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setenv("ARTURO_UPLINK_TAP", "1")
    m = _manager()
    try:
        m.feed_audio("c1", b"\x01\x02" * 50)
        m.feed_audio("c1", b"\x03\x04" * 50)
        p = tmp_path / "state" / "uplink-tap" / "c1.pcm"
        assert p.read_bytes() == b"\x01\x02" * 50 + b"\x03\x04" * 50
    finally:
        m.shutdown()


def test_tap_size_cap_stops_appending(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setattr(sr, "UPLINK_TAP_MAX_BYTES", 150)
    monkeypatch.setenv("ARTURO_UPLINK_TAP", "1")
    m = _manager()
    try:
        m.feed_audio("c1", b"\x00" * 100)
        m.feed_audio("c1", b"\x00" * 100)   # crosses the cap
        m.feed_audio("c1", b"\x00" * 100)   # must NOT append further
        p = tmp_path / "state" / "uplink-tap" / "c1.pcm"
        assert p.stat().st_size == 200
    finally:
        m.shutdown()


def test_tap_sanitizes_cid_no_path_escape(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setenv("ARTURO_UPLINK_TAP", "1")
    m = _manager()
    try:
        m.feed_audio("../../evil", b"\x01" * 10)
        tap_dir = tmp_path / "state" / "uplink-tap"
        files = list(tap_dir.glob("*.pcm"))
        assert len(files) == 1 and files[0].parent == tap_dir   # stayed inside the dir
        assert not (tmp_path / "evil.pcm").exists()
    finally:
        m.shutdown()


def test_tap_failure_never_breaks_the_call(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "ORCHESTRA_DIR", tmp_path / "missing-and-unwritable")
    monkeypatch.setenv("ARTURO_UPLINK_TAP", "1")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(sr.Path, "mkdir", boom)
    m = _manager()
    try:
        assert m.feed_audio("c1", b"\x00" * 10)["ok"]   # tap failure is invisible to the call
    finally:
        m.shutdown()
