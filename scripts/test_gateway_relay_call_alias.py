"""A dashboard call's card id (vc_client_<sha1(conv)[:12]>) resolves to the relay's vc_<random> journal."""
import hashlib
import json

import scripts.watch_gateway as G
from services.arturo.relay_live_bridge import journal_call_id


def test_the_card_id_finds_the_relay_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path)
    conv = "00000000-0000-4000-8000-0000000000bb"
    (tmp_path / "vc_485e223569e412c9.json").write_text(json.dumps({"call_id": "vc_485e223569e412c9", "conv_id": conv}))
    (tmp_path / "vc_0000000000000000.json").write_text(json.dumps({"conv_id": "someone-else"}))
    alias = journal_call_id(conv)
    assert alias == "vc_client_" + hashlib.sha1(conv.encode()).hexdigest()[:12]
    assert G._relay_call_path(alias) == tmp_path / "vc_485e223569e412c9.json"


def test_other_ids_never_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "VOICE_CALLS_DIR", tmp_path)
    (tmp_path / "vc_485e223569e412c9.json").write_text(json.dumps({"conv_id": "x"}))
    assert G._relay_call_path("vc_485e223569e412c9") is None
    assert G._relay_call_path("vc_client_nothex") is None
    assert G._relay_call_path(journal_call_id("no-such-call")) is None
