"""Proxy wiring for the GPT-Live vendor (docs/ARTURO.md "GPT-Live"): the relay's delegation POSTs OUR CLM
endpoint over loopback exactly as Hume does (same bearer, same custom_session_id, no internal nonce), so the
caller stamp and the fail-closed allowlist apply UNCHANGED; the call journal records non-delegated turns with a
flag; a live openai call's loopback request is origin 'relay' (not 'local'). Fenced: ORCHESTRA_DIR + journals in
tmp, Telegram neutered, no network."""
import glob
import importlib.util
import json
import pathlib

from services.arturo.test_stream_relay_openai import _manager as _oa_manager, _live as _oa_live
from services.arturo.test_stream_relay_hume import _manager as _hume_manager, _wait

CID = "11111111-2222-4333-8444-5555555555d1"


def _proxy(monkeypatch, tmp_path, m, offered):
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARTURO_VOICE_RESULTS", "0")
    monkeypatch.setenv("ORCHESTRA_ARTURO_BRAIN", "api")     # no runtime probe at import
    spec = importlib.util.spec_from_file_location("arturo_proxy_oaw", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert str(mod.VOICE_CALLS_DIR).startswith(str(tmp_path)) and str(mod.ARTURO_STATE).startswith(str(tmp_path))
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    monkeypatch.setattr(mod._TG_OUTBOX, "allow", lambda *a, **k: (False, "test"))
    mod.build_context = lambda **k: "BASECTX"

    # Public: the model is reached through the brain seam (brain.complete), not a module-level client.
    def complete(**kw):
        offered.append(sorted(t["function"]["name"] for t in (kw.get("tools") or [])))
        return mod._brain.make_response("gm is reviewing the restart card.", None, "stop")
    monkeypatch.setattr(mod.brain, "complete", complete)
    monkeypatch.setattr(mod, "_STREAM_RELAY", m)

    def via_test_client(cid, messages):             # the real handler, the exact request the relay makes
        r = mod.app.test_client().post(f"/v1/chat/completions?custom_session_id={cid}",
                                       json={"messages": messages, "stream": True},
                                       headers={"Authorization": f"Bearer {mod.BEARER_TOKEN}"},
                                       environ_base={"REMOTE_ADDR": "127.0.0.1"})
        return r.get_data(as_text=True)
    monkeypatch.setattr(mod, "_OPENAI_POST", via_test_client)
    mod._wire_openai(m)                               # what the proxy does to its own relay at import
    return mod


def _journals(tmp_path):
    return [json.load(open(f)) for f in glob.glob(str(tmp_path / "vc" / "vc_*.json"))]


def test_delegation_runs_our_brain_with_full_tools_for_a_fleet_call(monkeypatch, tmp_path):
    offered = []
    m = _oa_manager()
    try:
        _oa_live(m, CID)
        mod = _proxy(monkeypatch, tmp_path, m, offered)
        mod._record_call(CID, "fleet")
        text = m.openai_delegate(CID, [{"role": "assistant", "content": "Hey the operator."},
                                       {"role": "user", "content": "what is gm doing"}])
        assert text == "gm is reviewing the restart card."
        assert offered and not set(offered[0]) <= mod._NON_FLEET_ALLOWED, f"fleet call must get full tools, got {offered[0]}"
    finally:
        m.shutdown()


def test_delegation_from_an_unstamped_call_is_lookup_only(monkeypatch, tmp_path):
    offered = []
    m = _oa_manager()
    try:
        _oa_live(m, CID)
        mod = _proxy(monkeypatch, tmp_path, m, offered)
        m.openai_delegate(CID, [{"role": "assistant", "content": "Hey the operator."},
                                {"role": "user", "content": "what is gm doing"}])
        assert offered and set(offered[0]) <= mod._NON_FLEET_ALLOWED, f"no caller record -> lookup-only, got {offered[0]}"
    finally:
        m.shutdown()


def test_a_live_openai_calls_loopback_turn_is_origin_relay(monkeypatch, tmp_path):
    m = _oa_manager()
    try:
        _oa_live(m, CID)
        mod = _proxy(monkeypatch, tmp_path, m, [])
        m.openai_delegate(CID, [{"role": "assistant", "content": "Hey the operator."},
                                {"role": "user", "content": "what is gm doing"}])
        js = _journals(tmp_path)
        assert len(js) == 1 and js[0]["origin"] == "relay"
    finally:
        m.shutdown()


def test_a_hume_calls_local_request_stays_origin_local(monkeypatch, tmp_path):
    m = _hume_manager()
    try:
        m.feed_audio(CID, b"\x00" * 10)
        assert _wait(lambda: m._t)
        mod = _proxy(monkeypatch, tmp_path, m, [])
        mod._OPENAI_POST(CID, [{"role": "assistant", "content": "Hey the operator."},
                               {"role": "user", "content": "what is gm doing"}])
        js = _journals(tmp_path)
        assert len(js) == 1 and js[0]["origin"] == "local"
    finally:
        m.shutdown()


def test_journal_hook_records_a_non_delegated_turn_with_its_flag(monkeypatch, tmp_path):
    m = _oa_manager()
    try:
        _oa_live(m, CID)
        mod = _proxy(monkeypatch, tmp_path, m, [])
        m.journal_hook(CID, "user", "how's it going", delegated=False)
        m.journal_hook(CID, "arturo", "Doing well!", delegated=False)
        js = _journals(tmp_path)
        assert len(js) == 1 and js[0].get("conv_id") == CID
        turns = [t for t in js[0]["turns"] if t.get("role") in ("user", "arturo")]
        assert turns[-2:] == [{"role": "user", "text": "how's it going", "ts": turns[-2]["ts"], "delegated": False},
                              {"role": "arturo", "text": "Doing well!", "ts": turns[-1]["ts"], "delegated": False}]
    finally:
        m.shutdown()


def test_the_proxy_wires_its_own_relay_at_import(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    spec = importlib.util.spec_from_file_location("arturo_proxy_oaw2", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        assert callable(mod._STREAM_RELAY.openai_delegate) and callable(mod._STREAM_RELAY.journal_hook)
    finally:
        mod._STREAM_RELAY.shutdown()
