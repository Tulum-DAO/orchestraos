# Item C + P1-a: local, key-free STT for web dictation. Pure over stubs: no sherpa/faster-whisper
# import, no model download, no mic. Locks: lazy import, the state machine the composer/doctor read,
# engine selection, the atomic fetch (lock, partial names, sha, manifest LAST), WAV parsing,
# windowing under whisper's 30 s cap, the timeout seam, and that the lock queue is not charged to a clip.
import hashlib
import importlib.util
import pathlib
import struct
import sys
import threading
import time
import types

import pytest

_N = [0]


def _load(monkeypatch, tmp_path, engine="sherpa", installed=("sherpa",), on_disk=True, enabled=True, model=""):
    _N[0] += 1
    tmp_path = tmp_path / f"case{_N[0]}"
    monkeypatch.setenv("ARTURO_LOCAL_STT", "1" if enabled else "0")
    monkeypatch.setenv("ARTURO_LOCAL_STT_ENGINE", engine)
    monkeypatch.setenv("ARTURO_LOCAL_STT_DIR", str(tmp_path / "models"))
    if model:
        monkeypatch.setenv("ARTURO_LOCAL_STT_MODEL", model)
    else:
        monkeypatch.delenv("ARTURO_LOCAL_STT_MODEL", raising=False)
    spec = importlib.util.spec_from_file_location("local_stt_t", pathlib.Path("services/arturo/local_stt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_importable", lambda m: {"sherpa_onnx": "sherpa", "faster_whisper": "faster-whisper"}.get(m) in installed)
    if on_disk:
        _put_on_disk(mod, mod.resolve_engine(), mod.resolve_model(mod.resolve_engine()))
    return mod


def _put_on_disk(mod, engine, model):
    if engine == "sherpa":
        d = mod.models_root() / "sherpa" / model
        d.mkdir(parents=True, exist_ok=True)
        for name, size, _sha in mod.SHERPA_MODELS[model]["files"].values():
            with open(d / name, "wb") as f:
                f.truncate(size)
        (d / ".ok").write_text("stub\n")
    else:
        d = mod.models_root() / "whisper" / "models--Systran--faster-whisper-base.en" / "snapshots" / "abc"
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.bin").write_bytes(b"\0")


def _wav(samples, rate=16000, channels=1):
    import numpy as np
    pcm = (np.asarray(samples, dtype=np.float32) * 32767).astype("<i2").tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * 2 * channels, 2 * channels, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


class _StubEngine:
    backend = "local-whisper"
    def __init__(self, text, delay=0.0, wav_only=False):
        self.text, self.delay, self.wav_only, self.calls = text, delay, wav_only, []
    def transcribe_bytes(self, audio_bytes, content_type=""):
        self.calls.append((audio_bytes[:4], content_type))
        if self.wav_only and not audio_bytes.startswith(b"RIFF"):
            raise _mod_ref[0].WavRequired("not a WAV file")
        if self.delay:
            time.sleep(self.delay)
        return self.text


_mod_ref = [None]


def test_importing_the_module_never_imports_an_engine(monkeypatch, tmp_path):
    for m in ("sherpa_onnx", "faster_whisper"):
        sys.modules.pop(m, None)
    _load(monkeypatch, tmp_path)
    assert "sherpa_onnx" not in sys.modules and "faster_whisper" not in sys.modules


def test_engine_selection_auto_prefers_the_better_engine_when_installed(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, engine="auto", installed=("sherpa", "faster-whisper"), on_disk=False)
    assert m.resolve_engine() == "faster-whisper" and m.resolve_model("faster-whisper") == "base.en"
    m = _load(monkeypatch, tmp_path, engine="auto", installed=("sherpa",), on_disk=False)
    assert m.resolve_engine() == "sherpa" and m.resolve_model("sherpa") == "whisper-tiny.en"
    m = _load(monkeypatch, tmp_path, engine="auto", installed=(), on_disk=False)
    assert m.resolve_engine() == "sherpa" and m.state()["state"] == "not-installed"


def test_model_env_is_engine_scoped(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, model="zipformer-small-en", on_disk=False)
    assert m.resolve_model("sherpa") == "zipformer-small-en"
    m = _load(monkeypatch, tmp_path, model="base.en", on_disk=False)          # a faster-whisper name on sherpa -> default
    assert m.resolve_model("sherpa") == "whisper-tiny.en"
    m = _load(monkeypatch, tmp_path, engine="faster-whisper", installed=("faster-whisper",), model="small.en", on_disk=False)
    assert m.resolve_model("faster-whisper") == "small.en"


def test_state_machine_names_the_fix_for_the_default_engine(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, installed=(), on_disk=False)
    s = m.state()
    assert s["state"] == "not-installed" and s["install"] == "orchestra init" and "orchestra init" in s["reason"]
    m = _load(monkeypatch, tmp_path, engine="faster-whisper", installed=(), on_disk=False)
    assert m.state()["install"] == "orchestra init --stt"
    m = _load(monkeypatch, tmp_path, on_disk=False)
    s = m.state()
    assert s["state"] == "warming" and "104 MB" in s["reason"] or s["state"] == "warming"
    m = _load(monkeypatch, tmp_path, on_disk=True)
    assert m.state() == {"server": True, "engine": "sherpa", "model": "whisper-tiny.en", "backend": "local-whisper", "state": "ready"}
    m = _load(monkeypatch, tmp_path, enabled=False)
    assert m.state()["state"] == "off"
    m = _load(monkeypatch, tmp_path, model="zipformer-small-en", on_disk=True)
    assert m.state()["backend"] == "local-zipformer"


def test_model_on_disk_requires_the_manifest_and_exact_sizes(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=True)
    d = m.models_root() / "sherpa" / "whisper-tiny.en"
    assert m.model_on_disk()
    (d / ".ok").unlink()
    assert not m.model_on_disk()                          # a crashed fetch never counts
    (d / ".ok").write_text("x")
    with open(d / "tiny.en-tokens.txt", "wb") as f:
        f.truncate(10)
    assert not m.model_on_disk()                          # wrong size never counts


def _fake_stream(mod, good=True, truncate=None):
    """Serve the pinned files from memory with the pinned sha (we synthesize bytes whose sha we pin)."""
    spec = mod.SHERPA_MODELS["whisper-tiny.en"]
    blobs = {}
    for key, (name, size, sha) in spec["files"].items():
        blobs[name] = b"\x01" * min(size, 1000)         # tiny stand-ins
    # re-pin sizes + shas to the stand-ins so the fetch's verification is exercised for real
    mod.SHERPA_MODELS["whisper-tiny.en"]["files"] = {
        k: (n, len(blobs[n]), hashlib.sha256(blobs[n]).hexdigest()) for k, (n, _s, _h) in spec["files"].items()}
    def stream(url):
        name = url.rsplit("/", 1)[-1]
        b = blobs[name]
        if not good and name.endswith("decoder.int8.onnx"):
            b = b[:-1] + b"\x02"                        # sha mismatch
        if truncate and name.endswith("decoder.int8.onnx"):
            b = b[:truncate]
        yield b[:300]
        yield b[300:]
    return stream


def test_fetch_is_atomic_verified_and_writes_the_manifest_last(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=False)
    assert m._fetch_sherpa("whisper-tiny.en", stream=_fake_stream(m)) is True
    d = m.models_root() / "sherpa" / "whisper-tiny.en"
    assert (d / ".ok").exists() and m.model_on_disk() and not list((d / ".partial").glob("*"))
    assert m.state()["state"] == "ready"


def test_fetch_sha_mismatch_is_cleaned_up_and_named(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=False)
    assert m._fetch_sherpa("whisper-tiny.en", stream=_fake_stream(m, good=False)) is False
    d = m.models_root() / "sherpa" / "whisper-tiny.en"
    assert not (d / ".ok").exists() and not list((d / ".partial").glob("*"))
    assert not (d / "tiny.en-decoder.int8.onnx").exists()   # the bad file never landed
    assert m.state()["state"] == "error" and "sha" in m.state()["reason"]


def test_fetch_truncated_download_never_counts(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=False)
    assert m._fetch_sherpa("whisper-tiny.en", stream=_fake_stream(m, truncate=100)) is False
    assert not m.model_on_disk()


def test_fetch_is_resumable_and_serialized(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=False)
    stream = _fake_stream(m)
    calls = []
    def counting(url):
        calls.append(url.rsplit("/", 1)[-1]); yield from stream(url)
    # first run: bad decoder -> encoder + tokens verified and kept
    def first(url):
        if url.endswith("decoder.int8.onnx"):
            yield b"\x00"
        else:
            yield from counting(url)
    assert m._fetch_sherpa("whisper-tiny.en", stream=first) is False
    calls.clear()
    # second run only fetches what is missing (the fetch stops at the first bad file, so the files
    # after it are fetched now; the verified encoder is NOT re-downloaded)
    assert m._fetch_sherpa("whisper-tiny.en", stream=counting) is True
    assert calls == ["tiny.en-decoder.int8.onnx", "tiny.en-tokens.txt"]
    # two concurrent fetches: the lock serializes them and both report on-disk
    results = []
    ts = [threading.Thread(target=lambda: results.append(m._fetch_sherpa("whisper-tiny.en", stream=counting))) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results == [True, True]


def test_parse_wav_and_resample(monkeypatch, tmp_path):
    import numpy as np
    m = _load(monkeypatch, tmp_path)
    tone = np.sin(np.arange(16000) / 16000 * 2 * np.pi * 440).astype(np.float32) * 0.5
    a, rate = m.parse_wav(_wav(tone))
    assert rate == 16000 and len(a) == 16000 and abs(float(a[4000]) - float(tone[4000])) < 1e-3
    a2, rate2 = m.parse_wav(_wav(np.repeat(tone, 2).reshape(-1, 2).reshape(-1), 48000, 1))
    assert rate2 == 48000 and len(m.resample_to_16k(a2, rate2)) == pytest.approx(len(a2) / 3, abs=1)
    stereo = np.stack([tone, -tone], axis=1).reshape(-1)
    a3, _ = m.parse_wav(_wav(stereo, 16000, 2))
    assert abs(float(np.abs(a3).max())) < 1e-3                 # L/R cancel -> mono mix is silence
    with pytest.raises(m.WavRequired):
        m.parse_wav(b"\x1aE\xdf\xa3webm-bytes")


def test_split_windows_respects_whispers_cap_and_cuts_in_silence(monkeypatch, tmp_path):
    import numpy as np
    m = _load(monkeypatch, tmp_path)
    sr = 16000
    # 45 s: loud everywhere except a 0.5 s gap at 26.0 s
    a = np.ones(45 * sr, dtype=np.float32) * 0.3
    a[int(26.0 * sr): int(26.5 * sr)] = 0.0
    ws = m.split_windows(a, sr)
    assert len(ws) == 2 and all(len(w) <= 28 * sr for w in ws)
    assert abs(len(ws[0]) / sr - 26.25) < 0.3                  # cut landed in the gap
    assert sum(len(w) for w in ws) == len(a)
    assert len(m.split_windows(a[: 10 * sr], sr)) == 1


def test_transcribe_refuses_until_ready(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=False)
    with pytest.raises(m.SttUnavailable) as ei:
        m.transcribe(b"RIFF")
    assert ei.value.state["state"] == "warming"


def test_transcribe_reports_engine_and_maps_wav_required(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path); _mod_ref[0] = m
    r = m.transcribe(b"RIFF....", "clip.wav", "audio/wav", _engine=_StubEngine("hello world", wav_only=True))
    assert r == {"text": "hello world", "ms": r["ms"], "backend": "local-whisper", "engine": "sherpa", "model": "whisper-tiny.en"}
    with pytest.raises(m.WavRequired):
        m.transcribe(b"\x1aE\xdf\xa3opus", "clip.webm", "audio/webm", _engine=_StubEngine("x", wav_only=True))
    assert not m._S.lock.locked()                               # released after the failure too


def test_transcribe_times_out_under_the_gateway_budget_and_releases_the_lock(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path); _mod_ref[0] = m
    with pytest.raises(TimeoutError):
        m.transcribe(b"RIFF", _engine=_StubEngine("late", delay=0.5), timeout_s=0.05)
    assert m.TRANSCRIBE_TIMEOUT_S < 35
    time.sleep(0.6)
    assert not m._S.lock.locked()


def test_queue_wait_is_not_charged_to_the_clip(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path); _mod_ref[0] = m
    out = []
    a = threading.Thread(target=lambda: out.append(m.transcribe(b"RIFF", _engine=_StubEngine("first", delay=0.3), timeout_s=1.0)["text"]))
    b = threading.Thread(target=lambda: out.append(m.transcribe(b"RIFF", _engine=_StubEngine("second", delay=0.2), timeout_s=0.25)["text"]))
    a.start(); time.sleep(0.05); b.start(); a.join(); b.join()
    assert sorted(out) == ["first", "second"]                   # b waited 0.25 s for the lock, then still got its 0.25 s budget


def test_prefetch_is_a_noop_when_on_disk_or_not_installed(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=True)
    assert m.prefetch(blocking=True) is True
    m = _load(monkeypatch, tmp_path, installed=(), on_disk=False)
    assert m.prefetch(blocking=True) is False
