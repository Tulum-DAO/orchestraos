"""RED-first — journal-finalize must END the relay conversation (ios msg_33a85910: the
finalize-watchdog ended journal vc_7b22821477e940fe at 07:09:10Z but the relay conversation
lived on — Hume socket reconnect-looping at 07:21Z, then the 07:26Z client resume re-opened
conv 6A4ADC85 as a NEW journal instead of getting 410). _finalize_journal_file is the single
convergence point (watchdog sweep + client-notify), so the hook lands there: after the
status->ended flip, relay.end(conv_id) tombstones the cid (events/audio -> 410).
Recursion-safe both directions: relay-initiated end() -> on_finalize -> finalize no-ops on
status=ended; finalize-initiated relay.end() on a missing holder returns before on_finalize."""
import importlib.util
import json
import pathlib
import threading
import time


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


def _load_proxy(name, monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    # NEVER the live state/voice-calls/ (env-overridable exactly for tests)
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "voice-calls"))
    spec = importlib.util.spec_from_file_location(
        name, pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # swap in a hermetic manager (B1 pin: custom socket_factory => vendor pinned to EL,
    # no vendor file / network), wired to the module's own finalize callback so the
    # relay->journal direction stays real for the recursion pin.
    from services.arturo import stream_relay as sr
    mod._STREAM_RELAY.shutdown()
    mod._STREAM_RELAY = sr.RelayManager(socket_factory=lambda c: QuietSocket(),
                                        on_finalize=mod._relay_finalize)
    return mod


def _live_journal(mod, call_id, conv_id):
    p = mod.VOICE_CALLS_DIR / f"{call_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "call_id": call_id, "started_at": time.time() - 1200, "page": 1,
        "status": "live", "conv_id": conv_id, "origin": "local", "surface": "watch",
        "turns": [{"role": "user", "text": "hi"}, {"role": "arturo", "text": "hey"}],
    }))
    return p


def test_watchdog_finalize_ends_and_tombstones_relay_conversation(monkeypatch, tmp_path):
    mod = _load_proxy("arturo_proxy_finrelay1", monkeypatch, tmp_path)
    try:
        cid = "CONVREUSE1"
        mod._STREAM_RELAY.feed_audio(cid, b"\x00" * 10)
        assert cid in mod._STREAM_RELAY._holders
        p = _live_journal(mod, "vc_wdtest1", cid)
        assert mod._finalize_journal_file(p) is True
        assert mod._STREAM_RELAY.is_ended(cid), \
            "journal-finalize must tombstone the relay conversation (410 to a zombie client)"
        assert cid not in mod._STREAM_RELAY._holders
    finally:
        mod._STREAM_RELAY.shutdown()


def test_finalize_without_conv_id_touches_no_relay_state(monkeypatch, tmp_path):
    mod = _load_proxy("arturo_proxy_finrelay2", monkeypatch, tmp_path)
    try:
        other = "OTHERCONV"
        mod._STREAM_RELAY.feed_audio(other, b"\x00" * 10)
        assert other in mod._STREAM_RELAY._holders
        p = _live_journal(mod, "vc_wdtest2", "")     # no conv_id on the journal
        assert mod._finalize_journal_file(p) is True
        assert not mod._STREAM_RELAY.is_ended(other)
        assert other in mod._STREAM_RELAY._holders
    finally:
        mod._STREAM_RELAY.shutdown()


def test_relay_initiated_end_does_not_recurse(monkeypatch, tmp_path):
    # relay.end -> on_finalize -> _finalize_journal_file -> (hook) relay.end(conv) must
    # terminate: the journal is already status=ended under the lock, and a second end()
    # finds no holder. Regression pin: no deadlock/recursion, one usage record.
    mod = _load_proxy("arturo_proxy_finrelay3", monkeypatch, tmp_path)
    try:
        cid = "CONVRECURSE"
        mod._STREAM_RELAY.feed_audio(cid, b"\x00" * 10)
        assert cid in mod._STREAM_RELAY._holders
        _live_journal(mod, "vc_wdtest3", cid)
        done = []
        t = threading.Thread(target=lambda: done.append(mod._STREAM_RELAY.end(cid)))
        t.start()
        t.join(timeout=5)
        assert not t.is_alive(), "end() must not deadlock through the finalize hook"
        assert done == [True]
        assert mod._STREAM_RELAY.is_ended(cid)
    finally:
        mod._STREAM_RELAY.shutdown()
