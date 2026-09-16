"""RED-first — Hume voice picker server half (the operator ask via ios-g16 msg_6b349b0a; contract
msg_f5b57c9d): a runtime voice preference exactly like the vendor pref, applied by
hume_socket_factory via session_settings.voice_id per connect (docs-verified: EVI supports
voice_id there, no config re-pin, no restart per pick)."""
import json
import sys
import types

import pytest

from services.arturo import stream_relay as sr
from services.arturo import voice_choice as vc


@pytest.fixture
def store(tmp_path):
    return vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log")


def test_default_is_none(store):
    assert store.get("hume") is None
    st = store.state("hume")
    assert st["vendor"] == "hume" and st["voice_id"] is None and st["changed_at"] is None


def test_set_persists_and_logs(store):
    st = store.set("hume", "voice-abc", by="settings-ios", source="settings")
    assert st["voice_id"] == "voice-abc" and st["changed_by"] == "settings-ios"
    assert store.get("hume") == "voice-abc"
    assert "voice-abc" in store.log_path.read_text()
    # state round-trips from disk
    st2 = vc.VoiceChoiceStore(path=store.path, log_path=store.log_path).state("hume")
    assert st2["voice_id"] == "voice-abc" and st2["changed_at"]


def test_set_refuses_bad_input(store):
    with pytest.raises(ValueError):
        store.set("elevenlabs", "v1")           # EL voice choice is client-side
    with pytest.raises(ValueError):
        store.set("hume", "")                    # empty id
    with pytest.raises(ValueError):
        store.set("hume", "x" * 500)             # overlong


def test_list_hume_voices_aggregates_and_maps():
    pages = {
        ("CUSTOM_VOICE", 0): {"total_pages": 1, "voices_page": [
            {"id": "v-frank", "name": "Frank", "provider": "CUSTOM_VOICE"}]},
        ("HUME_AI", 0): {"total_pages": 2, "voices_page": [
            {"id": "v-ito", "name": "Ito", "provider": "HUME_AI"}]},
        ("HUME_AI", 1): {"total_pages": 2, "voices_page": [
            {"id": "v-kora", "name": "Kora", "provider": "HUME_AI"}]},
    }

    def fetch(provider, page_number):
        return pages[(provider, page_number)]
    voices = vc.list_hume_voices(fetch=fetch)
    assert {"id": "v-frank", "name": "Frank", "provider": "custom"} in voices
    assert {"id": "v-ito", "name": "Ito", "provider": "hume_library"} in voices
    assert {"id": "v-kora", "name": "Kora", "provider": "hume_library"} in voices
    assert len(voices) == 3


def test_cached_voices_fetches_once_within_ttl(monkeypatch):
    # ios msg_64481ea2: first picker open timed out (cold double-provider fetch + Funnel);
    # the route now serves a TTL cache so the GET is O(ms) after the first fetch.
    calls = []
    monkeypatch.setattr(vc, "list_hume_voices",
                        lambda fetch=None: calls.append(1) or [{"id": "v1", "name": "A", "provider": "custom"}])
    vc.clear_voices_cache()
    a = vc.cached_hume_voices(ttl_s=60)
    b = vc.cached_hume_voices(ttl_s=60)
    assert a == b and len(calls) == 1                # second hit served from cache


def test_cached_voices_refreshes_after_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(vc, "list_hume_voices",
                        lambda fetch=None: calls.append(1) or [{"id": f"v{len(calls)}", "name": "A", "provider": "custom"}])
    vc.clear_voices_cache()
    vc.cached_hume_voices(ttl_s=0.05)
    import time as _t
    _t.sleep(0.1)
    vc.cached_hume_voices(ttl_s=0.05)
    assert len(calls) == 2                           # expired => refetched


def test_cached_voices_serves_stale_on_refresh_failure(monkeypatch):
    state = {"n": 0}

    def flaky(fetch=None):
        state["n"] += 1
        if state["n"] == 1:
            return [{"id": "v1", "name": "A", "provider": "custom"}]
        raise RuntimeError("hume down")
    monkeypatch.setattr(vc, "list_hume_voices", flaky)
    vc.clear_voices_cache()
    assert vc.cached_hume_voices(ttl_s=0.05)
    import time as _t
    _t.sleep(0.1)
    out = vc.cached_hume_voices(ttl_s=0.05)          # refresh fails -> stale beats 502
    assert out == [{"id": "v1", "name": "A", "provider": "custom"}]
    vc.clear_voices_cache()
    import pytest as _pt
    with _pt.raises(RuntimeError):                   # no cache at all -> error surfaces (502)
        vc.cached_hume_voices(ttl_s=60)


def test_factory_sends_voice_id_when_pref_set(monkeypatch):
    class FakeWs:
        def __init__(self):
            self.sent = []

        def send(self, payload):
            self.sent.append(json.loads(payload))

        def settimeout(self, v):
            pass

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
    monkeypatch.setattr(vc, "get_voice", lambda vendor: "voice-abc")
    sr.hume_socket_factory("c1")
    first = captured["ws"].sent[0]
    assert first["type"] == "session_settings" and first["voice_id"] == "voice-abc"
    # no pref -> no voice_id key (config default voice governs)
    monkeypatch.setattr(vc, "get_voice", lambda vendor: None)
    sr.hume_socket_factory("c2")
    assert "voice_id" not in captured["ws"].sent[0]
