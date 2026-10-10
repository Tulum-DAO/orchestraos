"""/webhook/post-call (public over the Funnel, no auth) must not let its conversation_id steer the
ElevenLabs URL or the transcript file path.

Measured 2026-10-09: `requests` normalises dot segments, so conversation_id "../../user" fetched
https://api.elevenlabs.io/v1/user with the operator's key, and save_transcript wrote
VOICE_TRANSCRIPTS_DIR / f"{id}.json", which a "../" id walks out of. Only a plain id is used now.
Since gm msg_1fb3f0ee the route also requires an ElevenLabs signature; these pushes are SIGNED, so the plain-id
guard is still tested as the second layer behind it (a leaked secret must not reopen the traversal).
"""
import hashlib
import hmac
import importlib.util
import json as _json
import pathlib
import time

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_post_call", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def proxy(monkeypatch, tmp_path):
    mod = _load_proxy()
    calls = {"get": [], "saved": []}

    class _Resp:
        status_code = 200

        def json(self):
            return {"transcript": []}

    import requests
    monkeypatch.setattr(requests, "get", lambda url, **kw: calls["get"].append(url) or _Resp())
    monkeypatch.setattr(mod, "save_transcript", lambda cid, data: calls["saved"].append(cid))
    monkeypatch.setitem(mod.secrets, "ELEVENLABS_API_KEY", "k")
    from services.arturo import postcall_auth as pa
    monkeypatch.setattr(pa, "_secret", lambda k: "s3cret")
    monkeypatch.delenv("ARTURO_POSTCALL_AUTH", raising=False)
    pa.reset_seen()
    return _Signed(mod.app.test_client()), calls


class _Signed:
    """Posts JSON with a valid ElevenLabs-Signature for the secret above."""
    def __init__(self, c):
        self.c = c

    def post(self, path, json=None):
        body = _json.dumps(json)
        t = int(time.time())
        mac = hmac.new(b"s3cret", f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
        return self.c.post(path, data=body, headers={"Content-Type": "application/json",
                                                      "ElevenLabs-Signature": f"t={t},v0={mac}"})


@pytest.mark.parametrize("cid", ["../../user", "../../../registry", "a/b", "conv_1?x=y", "x" * 200, "..", ""])
def test_a_non_plain_conversation_id_fetches_and_writes_nothing(proxy, cid):
    c, calls = proxy
    r = c.post("/webhook/post-call", json={"conversation_id": cid})
    assert r.status_code in (200, 400)
    assert calls["get"] == [] and calls["saved"] == []


def test_a_plain_elevenlabs_id_still_works(proxy):
    c, calls = proxy
    r = c.post("/webhook/post-call", json={"conversation_id": "conv_example_plain_id_123"})
    assert r.status_code == 200
    assert calls["get"] == ["https://api.elevenlabs.io/v1/convai/conversations/conv_example_plain_id_123"]
    assert calls["saved"] == ["conv_example_plain_id_123"]
