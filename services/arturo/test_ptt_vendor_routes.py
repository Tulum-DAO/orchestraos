"""RED-first — Hume Task 4: /ptt/vendor GET+PUT routes on :5071 (loopback-only), flag-gated
behind ARTURO_STREAM_RELAY. Kept out of test_stream_relay.py so the EL baseline (25) is untouched."""
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy_vendor", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    monkeypatch.delenv("ARTURO_STREAM_PARTIALS", raising=False)


def test_vendor_routes_flag_off_absent(monkeypatch):
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    mod = _load_proxy()
    c = mod.app.test_client()
    assert c.get("/ptt/vendor").status_code == 404
    assert c.put("/ptt/vendor", json={"vendor": "hume"}).status_code == 404


def test_vendor_routes_loopback_get_put(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_vendor as vv
    monkeypatch.setattr(vv, "_default",
                        vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log",
                                       creds=lambda k: "x"))
    c = mod.app.test_client()
    # loopback trust boundary
    assert c.get("/ptt/vendor", environ_base={"REMOTE_ADDR": "10.0.0.9"}).status_code == 403
    # GET state (both vendors allowed under the stub creds)
    r = c.get("/ptt/vendor", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and "vendor" in j and set(j["allowed"]) == {"elevenlabs", "hume"}
    # PUT flips vendor, echoes it, persists+logs
    r = c.put("/ptt/vendor", json={"vendor": "hume", "by": "test"},
              environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.get_json()["vendor"] == "hume"
    assert (tmp_path / "v.json").exists() and "hume" in (tmp_path / "v.log").read_text()
    # PUT unknown vendor -> 400 visible error (no silent accept)
    r = c.put("/ptt/vendor", json={"vendor": "nope"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400 and not r.get_json()["ok"]
    mod._STREAM_RELAY.shutdown()


def test_stream_audio_unavailable_vendor_is_503_with_reason(monkeypatch, tmp_path):
    """T6: a NEW conversation on an unavailable preferred vendor is refused VISIBLY at the
    route — 503 vendor_unavailable + the human reason, no holder, no socket (spec §6)."""
    monkeypatch.setenv("ARTURO_STREAM_RELAY", "1")
    mod = _load_proxy()
    from services.arturo import voice_vendor as vv
    store = vv.VendorStore(path=tmp_path / "v.json", log_path=tmp_path / "v.log",
                           creds=lambda k: "" if k.startswith("HUME_") else "x")
    monkeypatch.setattr(vv, "_default", store)
    (tmp_path / "v.json").write_text('{"vendor": "hume"}')   # pref says hume; hume creds absent
    c = mod.app.test_client()
    r = c.post("/ptt/stream/audio", data=b"\x00\x01" * 50,
               headers={"X-Conversation-Id": "cUnavail"},
               environ_base={"REMOTE_ADDR": "127.0.0.1"},
               content_type="application/octet-stream")
    j = r.get_json()
    assert r.status_code == 503 and j["error"] == "vendor_unavailable" and "HUME" in j["message"]
    assert "cUnavail" not in mod._STREAM_RELAY._holders
    mod._STREAM_RELAY.shutdown()
