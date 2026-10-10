"""RED-first — /ptt/voices + /ptt/voice routes (Hume voice picker, contract msg_f5b57c9d).
Flag-gated under ARTURO_STREAM_RELAY like /ptt/vendor; loopback-only; gateway adds Bearer."""
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_voice", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    from services.arturo import voice_choice as _vc
    _vc.clear_voices_cache()          # the route serves a TTL cache; isolate per test
    yield
    _vc.clear_voices_cache()


def test_voice_routes_flag_off_absent(monkeypatch):
    mod = _load_proxy()
    c = mod.app.test_client()
    assert c.get("/ptt/voices?vendor=hume").status_code == 404
    assert c.get("/ptt/voice").status_code == 404
    assert c.put("/ptt/voice", json={"vendor": "hume", "voice_id": "v1"}).status_code == 404


def test_voices_get_and_voice_get_put(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_choice as vc
    monkeypatch.setattr(vc, "_default",
                        vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log"))
    monkeypatch.setattr(vc, "list_hume_voices",
                        lambda fetch=None: [{"id": "v-frank", "name": "Frank", "provider": "custom"}])
    c = mod.app.test_client()
    loop = {"REMOTE_ADDR": "127.0.0.1"}
    # loopback trust boundary
    assert c.get("/ptt/voices?vendor=hume",
                 environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403
    # voices list + current (null before any PUT)
    r = c.get("/ptt/voices?vendor=hume", environ_base=loop)
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and j["vendor"] == "hume"
    assert j["current"] is None
    # contract v2 (msg_a7cf1484): server-rendered, strictly typed display rows
    assert j["voices"] == [{"id": "v-frank", "name": "Frank", "title": "Frank", "subtitle": None,
                            "recommended": True, "order": 0, "sample_url": None, "group": "Your voices"}]
    # EL voices are client-side
    assert c.get("/ptt/voices?vendor=elevenlabs", environ_base=loop).status_code == 400
    # PUT persists, echoes the record (no voices list in the PUT response)
    r = c.put("/ptt/voice", json={"vendor": "hume", "voice_id": "v-frank", "by": "settings-ios"},
              environ_base=loop)
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and j["voice_id"] == "v-frank"
    assert j["changed_by"] == "settings-ios" and "voices" not in j
    # GET voice reflects it; GET voices now shows current
    assert c.get("/ptt/voice", environ_base=loop).get_json()["voice_id"] == "v-frank"
    assert c.get("/ptt/voices?vendor=hume", environ_base=loop).get_json()["current"] == "v-frank"
    # bad PUTs are refused visibly
    assert c.put("/ptt/voice", json={"vendor": "elevenlabs", "voice_id": "x"},
                 environ_base=loop).status_code == 400
    assert c.put("/ptt/voice", json={"vendor": "hume", "voice_id": ""},
                 environ_base=loop).status_code == 400
    mod._STREAM_RELAY.shutdown()


def test_voices_upstream_failure_is_502(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_choice as vc
    monkeypatch.setattr(vc, "_default",
                        vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log"))

    def boom(fetch=None):
        raise RuntimeError("hume unreachable")
    monkeypatch.setattr(vc, "list_hume_voices", boom)
    r = mod.app.test_client().get("/ptt/voices?vendor=hume",
                                  environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 502 and not r.get_json()["ok"]
    mod._STREAM_RELAY.shutdown()


def test_a_voice_write_logs_the_writing_DEVICE_from_the_gateway_never_the_body(monkeypatch, tmp_path):
    """gm ruling msg_f3c2cec6, condition 2 (by, source, device): the device is the gateway's fresh
    X-Arturo-Principal; "by" stays the client's label; a "device" in the body is ignored; a local
    tool with no header logs device null (safe, never guessed)."""
    import json as _json
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_choice as vc
    store = vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log")
    monkeypatch.setattr(vc, "_default", store)
    # contract v2: a hume pick must be one of Hume's voices (fails closed without the list)
    monkeypatch.setattr(vc, "list_hume_voices", lambda fetch=None: [{"id": "v-a", "name": "A", "provider": "custom"},
                                                                   {"id": "v-b", "name": "B", "provider": "custom"}])
    c = mod.app.test_client()
    loop = {"REMOTE_ADDR": "127.0.0.1"}
    r = c.put("/ptt/voice", json={"vendor": "hume", "voice_id": "v-a", "by": "settings-mac", "device": "forged"},
              headers={"X-Arturo-Principal": "device:mac-1"}, environ_base=loop)
    assert r.status_code == 200 and r.get_json()["changed_by"] == "settings-mac"
    c.put("/ptt/voice", json={"vendor": "hume", "voice_id": "v-b", "by": "local-tool"}, environ_base=loop)
    log = [_json.loads(x) for x in store.log_path.read_text().splitlines()]
    assert log[0]["device"] == "device:mac-1" and log[0]["changed_by"] == "settings-mac"
    assert log[1]["device"] is None


def test_no_vendor_param_on_a_default_install_still_means_hume(monkeypatch, tmp_path):
    """Public adaptation: the shipped Hume picker sends no ?vendor=. On a public install the live vendor defaults
    to elevenlabs, which has no server-side list; that must still mean hume (as before contract v2), never a 400."""
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_choice as vc, voice_vendor as vv
    monkeypatch.setattr(vc, "_default", vc.VoiceChoiceStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log"))
    monkeypatch.setattr(vc, "list_hume_voices", lambda fetch=None: [{"id": "v-frank", "name": "Frank", "provider": "custom"}])
    c = mod.app.test_client()
    loop = {"REMOTE_ADDR": "127.0.0.1"}
    monkeypatch.setattr(vv, "get_vendor", lambda: "elevenlabs")
    r = c.get("/ptt/voices", environ_base=loop)
    assert r.status_code == 200 and r.get_json()["vendor"] == "hume"
    assert c.get("/ptt/voice", environ_base=loop).get_json()["vendor"] == "hume"
    monkeypatch.setattr(vv, "get_vendor", lambda: "openai")          # a vendor WITH a list is still followed
    assert c.get("/ptt/voices", environ_base=loop).get_json()["vendor"] == "openai"
