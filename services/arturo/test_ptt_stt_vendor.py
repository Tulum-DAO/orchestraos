"""RED-first tests — PTT STT vendor swap: OpenAI Whisper -> ElevenLabs scribe_v1
(gm lane ruling msg_be5c97df: OpenAI is UNFUNDED — billable 429 verified twice; EL is the
funded, funnel-consistent vendor ~0.65s flat; Gemini 2.5 Flash is the funded fallback).

Pins the vendor at the TRANSPORT seam (_ptt_http) so the green suite proves the LIVE path
would hit the right endpoints — the defect class here was exactly 'tests stub the vendor,
live path 502s'.
"""
import importlib.util
import json
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_stt", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AUDIO = b"\x00\x01" * 400


def test_default_stt_model_is_scribe():
    mod = _load_proxy()
    assert mod.PTT_STT_MODEL == "scribe_v1", "default STT model must be EL scribe_v1, not whisper"


def test_stt_uses_elevenlabs_scribe(monkeypatch):
    mod = _load_proxy()
    calls = []
    def fake_http(url, headers, body, timeout):
        calls.append((url, headers, body))
        return 200, json.dumps({"text": "hello from scribe"}).encode()
    monkeypatch.setattr(mod, "_ptt_http", fake_http)
    out = mod._ptt_stt(AUDIO, "u.m4a")
    assert out == "hello from scribe"
    url, headers, body = calls[0]
    assert "api.elevenlabs.io" in url and "speech-to-text" in url
    assert any(k.lower() == "xi-api-key" for k in headers), "EL auth header required"
    assert b"scribe_v1" in body and AUDIO in body, "multipart must carry model_id + audio"


def test_stt_never_calls_openai(monkeypatch):
    mod = _load_proxy()
    urls = []
    def fake_http(url, headers, body, timeout):
        urls.append(url)
        return 200, json.dumps({"text": "ok"}).encode()
    monkeypatch.setattr(mod, "_ptt_http", fake_http)
    mod._ptt_stt(AUDIO, "u.m4a")
    assert not any("openai" in u for u in urls), "OpenAI is UNFUNDED — no STT call may touch it"
    assert not hasattr(mod, "_ptt_get_stt_client"), "dead OpenAI STT client plumbing must be removed"


def test_stt_falls_back_to_gemini_on_el_failure(monkeypatch):
    mod = _load_proxy()
    urls = []
    def fake_http(url, headers, body, timeout):
        urls.append(url)
        if "elevenlabs" in url:
            raise RuntimeError("EL down")
        return 200, json.dumps({"candidates": [{"content": {"parts": [
            {"text": " gemini transcript "}]}}]}).encode()
    monkeypatch.setattr(mod, "_ptt_http", fake_http)
    out = mod._ptt_stt(AUDIO, "u.m4a")
    assert out == "gemini transcript"
    assert any("elevenlabs" in u for u in urls) and any("generativelanguage" in u for u in urls)


def test_stt_both_vendors_fail_raises_for_502(monkeypatch):
    """ptt_turn's existing contract: an STT exception -> 502 stt_failed. Both-vendors-down must
    raise (never return '' — '' means 'no speech' 422, a lie when the vendors are down)."""
    mod = _load_proxy()
    def fake_http(url, headers, body, timeout):
        raise RuntimeError("everything down")
    monkeypatch.setattr(mod, "_ptt_http", fake_http)
    with pytest.raises(Exception):
        mod._ptt_stt(AUDIO, "u.m4a")


def test_el_non_200_falls_back(monkeypatch):
    mod = _load_proxy()
    def fake_http(url, headers, body, timeout):
        if "elevenlabs" in url:
            return 401, b'{"detail":"bad key"}'
        return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": "g"}]}}]}).encode()
    monkeypatch.setattr(mod, "_ptt_http", fake_http)
    assert mod._ptt_stt(AUDIO, "u.m4a") == "g"
