"""/webhook/post-call (public over the Funnel, no auth) must not let its conversation_id steer the
ElevenLabs URL or the transcript file path.

Measured 2026-10-09: `requests` normalises dot segments, so conversation_id "../../user" fetched
https://api.elevenlabs.io/v1/user with the operator's key, and save_transcript wrote
VOICE_TRANSCRIPTS_DIR / f"{id}.json", which a "../" id walks out of. Only a plain id is used now.
"""
import importlib.util
import pathlib

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
    return mod.app.test_client(), calls


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
