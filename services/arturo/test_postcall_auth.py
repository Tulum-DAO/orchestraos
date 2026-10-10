"""RED-first: the public /webhook/post-call route accepts only pushes SIGNED by ElevenLabs (gm msg_1fb3f0ee; card
apr_15363e88's exposure). Format from elevenlabs-python webhooks_custom.py: header `ElevenLabs-Signature:
t=<unix s>,v0=<hex>`, hex = HMAC-SHA256(secret, f"{t}.{raw_body}"); older than 30 min is refused. Through the REAL
handler: an unsigned push must never reach the ElevenLabs fetch that spends the operator's key."""
import hashlib
import hmac
import importlib.util
import json
import logging
import pathlib
import time

import pytest

from services.arturo import postcall_auth as pa

SECRET = "wsec_test_123"


def _sig(body, secret=SECRET, t=None):
    t = int(time.time()) if t is None else t
    mac = hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v0={mac}"


@pytest.fixture
def proxy(monkeypatch):
    spec = importlib.util.spec_from_file_location("arturo_proxy_postcall", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fetched, saved = [], []

    class _Resp:
        status_code = 200

        def json(self):
            return {"transcript": [{"role": "user", "message": "hi"}]}
    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: (fetched.append(url), _Resp())[1])
    monkeypatch.setattr(mod, "save_transcript", lambda cid, data: saved.append(cid))
    monkeypatch.setitem(mod.secrets, "ELEVENLABS_API_KEY", "el-key")
    monkeypatch.setattr(pa, "_secret", lambda k: SECRET if k == pa.SECRET_KEY else "")
    monkeypatch.delenv("ARTURO_POSTCALL_AUTH", raising=False)
    pa.reset_counts()
    mod._t = (fetched, saved)
    return mod


def _post(mod, body, sig=None):
    h = {"Content-Type": "application/json"}
    if sig is not None:
        h["ElevenLabs-Signature"] = sig
    return mod.app.test_client().post("/webhook/post-call", data=body, headers=h)


BODY = json.dumps({"type": "post_call_transcription", "conversation_id": "conv_abc123"})


def test_an_unsigned_push_is_refused_and_never_spends_the_key(proxy, caplog):
    caplog.set_level(logging.INFO)
    r = _post(proxy, BODY)
    assert r.status_code == 401
    assert proxy._t == ([], []), "an unsigned push reached the ElevenLabs fetch"
    assert "conv_abc123" not in caplog.text, "a refusal must log no body content"
    assert pa.counts()["missing"] == 1


def test_a_correctly_signed_push_still_saves_the_transcript(proxy):
    r = _post(proxy, BODY, _sig(BODY))
    assert r.status_code == 200
    assert proxy._t[1] == ["conv_abc123"]


@pytest.mark.parametrize("case", ["wrong_secret", "tampered", "stale", "future", "garbage"])
def test_a_bad_signature_is_refused(proxy, case):
    sig = {"wrong_secret": _sig(BODY, secret="nope"),
           "tampered": _sig(BODY.replace("abc", "abd")),
           "stale": _sig(BODY, t=int(time.time()) - 31 * 60),
           "future": _sig(BODY, t=int(time.time()) + 10 * 60),
           "garbage": "v0=deadbeef"}[case]
    r = _post(proxy, BODY, sig)
    assert r.status_code == 401, case
    assert proxy._t == ([], [])


def test_no_secret_configured_refuses_rather_than_opening_the_door(proxy, monkeypatch):
    monkeypatch.setattr(pa, "_secret", lambda k: "")
    r = _post(proxy, BODY, _sig(BODY))
    assert r.status_code == 401 and proxy._t == ([], [])
    assert pa.counts()["no_secret"] == 1


def test_log_mode_lets_a_push_through_but_counts_what_it_would_refuse(proxy, monkeypatch):
    monkeypatch.setenv("ARTURO_POSTCALL_AUTH", "log")
    r = _post(proxy, BODY)
    assert r.status_code == 200 and proxy._t[1] == ["conv_abc123"]
    assert pa.counts()["missing"] == 1


def test_verify_is_the_sdk_format():
    t = 1791600000
    body = '{"a":1}'
    mac = hmac.new(SECRET.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    assert pa.verify(body.encode(), f"t={t},v0={mac}", SECRET, now=t + 60) is None
    assert pa.verify(body.encode(), f"v0={mac},t={t}", SECRET, now=t + 60) is None     # order-free, as the SDK
    assert pa.verify(body.encode(), f"t={t},v0={mac}", SECRET, now=t + 31 * 60) == "stale"


def test_secret_is_read_from_env_then_the_data_dirs_env_secrets(monkeypatch, tmp_path):
    """Public adaptation: the env first, then <data dir>/.env.secrets (where every other Arturo secret
    lives, docs/ARTURO.md); upstream read the file only. Per call: no restart after setting it."""
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.delenv(pa.SECRET_KEY, raising=False)
    assert pa._secret(pa.SECRET_KEY) == ""
    (tmp_path / ".env.secrets").write_text(f"OTHER=x\n{pa.SECRET_KEY}=from-file\n")
    assert pa._secret(pa.SECRET_KEY) == "from-file"
    monkeypatch.setenv(pa.SECRET_KEY, "from-env")
    assert pa._secret(pa.SECRET_KEY) == "from-env"
