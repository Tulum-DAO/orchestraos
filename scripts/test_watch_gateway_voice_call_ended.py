"""POST /voice-call-ended keeps the Arturo thread a call names (item 10).

The dashboard's native engine sends `thread_id` (the thread the call was started from); the gateway stores it on the
client journal so the proxy's finalize can write the call's card there. Optional: a malformed one is ignored, never a
400 (iOS/watch post here too and send none). Scratch VOICE_CALLS_DIR; the :5071 finalize hop is a stand-in.
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_gateway as G  # noqa: E402


class _Req:
    def __init__(self, payload):
        self.headers = {"Authorization": "Bearer secret_tok"}
        self._payload = payload
        self.query = {}
        self.match_info = {}

    async def json(self):
        return self._payload


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "gateway_token", lambda: "secret_tok")
    monkeypatch.setattr(G, "_authorized", lambda request: True)
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path)
    hops = []

    async def notify(conv_id, call_id):
        hops.append((conv_id, call_id))
        return True
    monkeypatch.setattr(G, "_notify_arturo_finalize", notify)
    # who posted: stubbed (the real one resolves the device store and may write the stamp secret)
    monkeypatch.setattr(G, "_arturo_principal_headers", lambda request, voice=True: {"X-Arturo-Principal": "fleet"})
    return tmp_path, hops


def _post(body):
    r = asyncio.run(G.handle_voice_call_ended(_Req(body)))
    return r.status, json.loads(r.body)


def _body(**extra):
    return {"started_at": 1_800_000_000.0, "ended_at": 1_800_000_030.0, "conv_id": "native-abc",
            "turns": [{"role": "user", "text": "hi", "ts": 1_800_000_001.0}], **extra}


def test_a_valid_thread_id_is_stored_on_the_journal(gw):
    d, hops = gw
    status, out = _post(_body(thread_id="web_k3j9x2ab"))
    assert status == 200 and out["ok"]
    j = json.loads((d / f"{out['call_id']}.json").read_text())
    assert j["thread_id"] == "web_k3j9x2ab"
    assert hops == [("native-abc", out["call_id"])]


@pytest.mark.parametrize("bad", ["", "has space", "a/b", "x" * 81, "web_1\n", 42, ["web_1"], {"id": 1}, None])
def test_a_malformed_thread_id_is_ignored_never_a_400(gw, bad):
    d, _ = gw
    status, out = _post(_body(thread_id=bad))
    assert status == 200 and out["ok"]
    j = json.loads((d / f"{out['call_id']}.json").read_text())
    assert "thread_id" not in j


def test_no_thread_id_journal_is_unchanged(gw):
    d, _ = gw
    status, out = _post(_body())
    j = json.loads((d / f"{out['call_id']}.json").read_text())
    assert set(j) == {"call_id", "started_at", "ended_at", "page", "status", "source", "turns", "summary", "conv_id"}


def test_the_journal_names_who_posted_the_thread_for_the_card(gw, monkeypatch):
    d, _ = gw
    status, out = _post(_body(thread_id="web_k3j9x2ab"))
    assert json.loads((d / f"{out['call_id']}.json").read_text())["thread_principal"] == "fleet"
    monkeypatch.setattr(G, "_arturo_principal_headers", lambda request, voice=True: {"X-Arturo-Principal": "device:d1"})
    status, out = _post(_body(thread_id="web_k3j9x2ab", conv_id="native-dev"))
    assert json.loads((d / f"{out['call_id']}.json").read_text())["thread_principal"] == "device:d1"
