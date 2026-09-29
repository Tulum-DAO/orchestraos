# Tests for the :5071 /transcribe route (item C) — loopback-only, transcribe ONLY, honest codes.
# The local STT is stubbed at the module seam; no faster-whisper, no model, no mic.
import importlib.util
import io
import pathlib
import sys


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _post(c, data, addr="127.0.0.1"):
    return c.post("/transcribe", data=data, content_type="multipart/form-data", environ_base={"REMOTE_ADDR": addr})


def test_loading_the_proxy_never_imports_faster_whisper():
    sys.modules.pop("faster_whisper", None)
    _load_proxy()
    assert "faster_whisper" not in sys.modules


def test_transcribe_rejects_non_loopback():
    mod = _load_proxy()
    r = _post(mod.app.test_client(), {"audio": (io.BytesIO(b"x"), "c.webm", "audio/webm")}, addr="10.0.0.9")
    assert r.status_code == 403


def test_transcribe_400_without_audio_and_413_over_cap(monkeypatch):
    mod = _load_proxy()
    c = mod.app.test_client()
    assert _post(c, {"x": "1"}).status_code == 400
    monkeypatch.setattr(mod._ptt, "MAX_TRANSCRIBE_BYTES", 10)
    r = _post(c, {"audio": (io.BytesIO(b"x" * 11), "c.webm", "audio/webm")})
    assert r.status_code == 413 and r.get_json()["error"] == "too_large"


def test_transcribe_accepts_webm_and_returns_text(monkeypatch):
    mod = _load_proxy()
    seen = {}
    def fake(audio_bytes, filename, content_type):
        seen.update(audio=audio_bytes, filename=filename, ct=content_type)
        return {"text": "list the agents", "ms": 412, "backend": "local-whisper", "engine": "sherpa", "model": "whisper-tiny.en"}
    monkeypatch.setattr(mod._local_stt, "transcribe", fake)
    r = _post(mod.app.test_client(), {"audio": (io.BytesIO(b"\x1aE\xdf\xa3opus"), "clip.webm", "audio/webm;codecs=opus")})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "text": "list the agents", "backend": "local-whisper", "engine": "sherpa", "model": "whisper-tiny.en", "ms": 412}
    assert seen["audio"].startswith(b"\x1aE") and seen["ct"].startswith("audio/webm")


def test_transcribe_422_on_silence(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod._local_stt, "transcribe", lambda *a, **k: {"text": "", "ms": 5, "backend": "local-whisper", "engine": "sherpa", "model": "whisper-tiny.en"})
    r = _post(mod.app.test_client(), {"audio": (io.BytesIO(b"x"), "c.wav", "audio/wav")})
    assert r.status_code == 422 and r.get_json()["error"] == "no_speech"


def test_transcribe_503_names_the_install_command_when_not_ready(monkeypatch):
    mod = _load_proxy()
    st = {"state": "not-installed", "install": "orchestra init --stt", "server": False}
    def unavailable(*a, **k):
        raise mod._local_stt.SttUnavailable("faster-whisper is not installed", st)
    monkeypatch.setattr(mod._local_stt, "transcribe", unavailable)
    r = _post(mod.app.test_client(), {"audio": (io.BytesIO(b"x"), "c.webm", "audio/webm")})
    body = r.get_json()
    assert r.status_code == 503 and body["error"] == "stt_unavailable"
    assert body["reason"] == "not-installed" and body["install"] == "orchestra init --stt"


def test_transcribe_504_on_timeout(monkeypatch):
    mod = _load_proxy()
    def slow(*a, **k):
        raise TimeoutError("transcription exceeded 25s")
    monkeypatch.setattr(mod._local_stt, "transcribe", slow)
    r = _post(mod.app.test_client(), {"audio": (io.BytesIO(b"x"), "c.webm", "audio/webm")})
    assert r.status_code == 504


def test_health_carries_the_stt_state(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod._local_stt, "state", lambda: {"server": True, "backend": "local-whisper", "state": "ready", "model": "base.en"})
    body = mod.app.test_client().get("/health").get_json()
    assert body["stt"]["state"] == "ready" and body["voice"] in (True, False)


def test_validate_transcribe_audio_contract():
    from services.arturo import ptt
    assert ptt.validate_transcribe_audio(0, "audio/webm") == (False, "empty")
    assert ptt.validate_transcribe_audio(ptt.MAX_TRANSCRIBE_BYTES + 1, "audio/webm") == (False, "too_large")
    assert ptt.validate_transcribe_audio(10, "text/plain") == (False, "bad_type")
    for ct in ("audio/webm;codecs=opus", "audio/ogg", "audio/wav", "audio/mp4", "audio/m4a", ""):
        assert ptt.validate_transcribe_audio(10, ct) == (True, None), ct
    # the watch rule is untouched
    assert ptt.validate_audio(10, "audio/webm") == (False, "bad_type") and ptt.MAX_AUDIO_BYTES == 1_000_000


def test_proxy_boot_prefetch_runs_after_the_import(monkeypatch):
    # Regression: the boot-time prefetch once sat ABOVE the local_stt import and killed :5071 at
    # start (NameError) on the box, while tests passed because that block is env-guarded. Loading
    # the module must call prefetch exactly once with the module logger, in every environment.
    import services.arturo.local_stt as real
    calls = []
    monkeypatch.setattr(real, "prefetch", lambda *a, **k: calls.append(k) or False)
    mod = _load_proxy()
    assert mod._local_stt is real and len(calls) == 1 and calls[0]["log"] is mod.log


def test_transcribe_415_when_the_default_engine_gets_a_non_wav_clip(monkeypatch):
    mod = _load_proxy()
    def wav_only(*a, **k):
        raise mod._local_stt.WavRequired("not a WAV file")
    monkeypatch.setattr(mod._local_stt, "transcribe", wav_only)
    r = _post(mod.app.test_client(), {"audio": (io.BytesIO(b"\x1aE\xdf\xa3opus"), "clip.webm", "audio/webm")})
    body = r.get_json()
    assert r.status_code == 415 and body["error"] == "wav_required" and body["install"] == "orchestra init --stt"
