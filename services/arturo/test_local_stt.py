# Item C — local, key-free STT for web dictation. Pure over a stub model: no faster-whisper import,
# no model download, no mic. Locks: lazy import, the state machine the composer/doctor read, the
# 25 s timeout seam, and that the module never fetches inside transcribe().
import importlib.util
import pathlib
import sys
import types

import pytest


_N = [0]


def _load(monkeypatch, tmp_path, installed=True, on_disk=True, enabled=True):
    _N[0] += 1
    tmp_path = tmp_path / f"case{_N[0]}"          # one model dir per load: states never leak across cases
    monkeypatch.setenv("ARTURO_LOCAL_STT", "1" if enabled else "0")
    monkeypatch.setenv("ARTURO_LOCAL_STT_DIR", str(tmp_path / "whisper"))
    monkeypatch.delenv("ARTURO_LOCAL_STT_MODEL", raising=False)
    spec = importlib.util.spec_from_file_location("local_stt_t", pathlib.Path("services/arturo/local_stt.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "installed", lambda: installed)
    if on_disk:
        d = tmp_path / "whisper" / "models--Systran--faster-whisper-base.en" / "snapshots" / "abc"
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"\0")
    return mod


class _Seg:
    def __init__(self, text): self.text = text


class _StubModel:
    def __init__(self, segs, delay=0.0):
        self.segs, self.delay, self.calls = segs, delay, []
    def transcribe(self, buf, **kw):
        import time
        self.calls.append((buf.getvalue(), kw))
        if self.delay:
            time.sleep(self.delay)
        return iter([_Seg(t) for t in self.segs]), types.SimpleNamespace(duration=1.0)


def test_importing_the_module_never_imports_faster_whisper(monkeypatch, tmp_path):
    sys.modules.pop("faster_whisper", None)
    _load(monkeypatch, tmp_path)
    assert "faster_whisper" not in sys.modules


def test_state_machine_names_the_fix(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, installed=False, on_disk=False)
    s = m.state()
    assert s["state"] == "not-installed" and s["server"] is False and s["install"] == "orchestra init --stt"
    m = _load(monkeypatch, tmp_path, installed=True, on_disk=False)
    assert m.state()["state"] == "warming"
    m = _load(monkeypatch, tmp_path, installed=True, on_disk=True)
    assert m.state() == {"server": True, "backend": "local-whisper", "state": "ready", "model": "base.en"}
    m = _load(monkeypatch, tmp_path, enabled=False)
    assert m.state()["state"] == "off"


def test_transcribe_refuses_with_503_class_error_until_ready(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, installed=True, on_disk=False)
    with pytest.raises(m.SttUnavailable) as ei:
        m.transcribe(b"webm")
    assert ei.value.state["state"] == "warming"


def test_transcribe_joins_segments_and_reports_ms(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    stub = _StubModel([" hello ", "world "])
    r = m.transcribe(b"webm-bytes", "clip.webm", "audio/webm", _model=stub)
    assert r["text"] == "hello world" and r["backend"] == "local-whisper" and r["ms"] >= 0
    assert stub.calls[0][0] == b"webm-bytes" and stub.calls[0][1]["beam_size"] == 1


def test_transcribe_empty_means_no_speech_not_error(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    assert m.transcribe(b"x", _model=_StubModel([])) ["text"] == ""


def test_transcribe_times_out_under_the_gateway_budget(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path)
    with pytest.raises(TimeoutError):
        m.transcribe(b"x", _model=_StubModel(["late"], delay=0.5), timeout_s=0.05)
    assert m.TRANSCRIBE_TIMEOUT_S < 35


def test_prefetch_is_a_noop_when_files_are_on_disk_or_not_installed(monkeypatch, tmp_path):
    m = _load(monkeypatch, tmp_path, on_disk=True)
    assert m.prefetch(blocking=True) is True
    m = _load(monkeypatch, tmp_path, installed=False, on_disk=False)
    assert m.prefetch(blocking=True) is False
