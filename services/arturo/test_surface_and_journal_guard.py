"""(1) gm msg_c0e18d14: the relay maps X-Surface EXACTLY. phone/watch/quest/ipad/mac are kept; a MISSING header
is still the watch (the watch sends none); any other value is 'unknown', never silently 'watch' (Quest calls were
journaled as watch: 14 since 09-16).
(2) Capture hole: the journal guard skipped any request whose history had no
assistant turn, meant for probes. When the operator talks over the greeting, Hume's history holds only their words, so a REAL
call got no server journal (3 of 59 Hume calls; 15 of 386 turns unjournaled). A request whose conv_id resolves to a
LIVE relay call is established, whatever its history."""
import glob
import importlib.util
import json
import pathlib

from services.arturo import stream_relay as sr
from services.arturo.test_stream_relay_hume import _manager, _wait


def test_normalize_surface_exact():
    assert sr.normalize_surface(None) == "watch" and sr.normalize_surface("") == "watch"
    for s in ("phone", "watch", "quest", "ipad", "mac"):
        assert sr.normalize_surface(s) == s
    assert sr.normalize_surface(" Quest ") == "quest"
    assert sr.normalize_surface("toaster") == "unknown"


def test_journal_surface_keeps_every_relay_device():
    fb = lambda: {"device": "fallback"}
    assert sr.journal_surface("quest", fb) == {"device": "quest", "via": "relay"}
    assert sr.journal_surface("unknown", fb) == {"device": "unknown", "via": "relay"}
    assert sr.journal_surface(None, fb) == {"device": "fallback"}


CID = "11111111-2222-4333-8444-5555555555c1"


def _proxy(monkeypatch, tmp_path, m):
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARTURO_VOICE_RESULTS", "0")
    monkeypatch.setenv("ORCHESTRA_ARTURO_BRAIN", "api")     # no runtime probe at import
    spec = importlib.util.spec_from_file_location("arturo_proxy_sjg", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert str(mod.VOICE_CALLS_DIR).startswith(str(tmp_path)), "fence: journals must land in tmp"
    monkeypatch.setattr(mod._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    monkeypatch.setattr(mod, "BEARER_TOKEN", "test-bearer")
    mod.build_context = lambda **k: "BASECTX"

    # Public: the model is reached through the brain seam (brain.complete), not a module-level client.
    monkeypatch.setattr(mod.brain, "complete", lambda **kw: mod._brain.make_response("Sure.", None, "stop"))
    monkeypatch.setattr(mod, "_STREAM_RELAY", m)
    return mod


def _post(mod, cid, messages):
    r = mod.app.test_client().post(f"/v1/chat/completions?custom_session_id={cid}", json={"messages": messages},
                                   headers={"Authorization": "Bearer test-bearer"},
                                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    r.get_data()
    return r.status_code


def _journals(tmp_path):
    out = []
    for f in glob.glob(str(tmp_path / "vc" / "vc_*.json")):
        out.append(json.load(open(f)))
    return out


def test_talk_over_greeting_still_journals_a_live_relay_call(monkeypatch, tmp_path):
    m = _manager()
    try:
        m.feed_audio(CID, b"\x00" * 10, surface_device="quest")
        assert _wait(lambda: m._t)
        mod = _proxy(monkeypatch, tmp_path, m)
        assert _post(mod, CID, [{"role": "user", "content": "It doesn't sound very good right now."}]) == 200
        js = _journals(tmp_path)
        assert len(js) == 1, "a live relay call must get a server journal even with no assistant turn yet"
        assert any(t.get("role") == "user" and "sound very good" in (t.get("text") or "") for t in js[0]["turns"])
        assert js[0].get("surface") == {"device": "quest", "via": "relay"}
        assert js[0].get("conv_id") == CID
    finally:
        m.shutdown()


def test_a_probe_with_no_assistant_turn_and_no_live_call_is_still_skipped(monkeypatch, tmp_path):
    m = _manager()
    try:
        mod = _proxy(monkeypatch, tmp_path, m)
        assert _post(mod, "11111111-2222-4333-8444-5555555555c9", [{"role": "user", "content": "ping"}]) == 200
        assert _journals(tmp_path) == []
    finally:
        m.shutdown()
