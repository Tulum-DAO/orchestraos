# Tests for the :5071 Flask /ptt route — a THIN loopback-only adapter over ptt_turn (which carries
# the RED-first-tested pipeline logic). These guard the trust boundary + multipart wiring.

import importlib.util
import io
import pathlib


def _load_proxy():
    spec = importlib.util.spec_from_file_location(
        "arturo_proxy", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ptt_route_rejects_non_loopback(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "ptt_turn", lambda *a, **k: (200, {"ok": True}))
    c = mod.app.test_client()
    r = c.post("/ptt", data={"audio": (io.BytesIO(b"x"), "u.m4a")},
               content_type="multipart/form-data",
               environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert r.status_code == 403


def test_ptt_route_missing_audio_400(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "ptt_turn", lambda *a, **k: (200, {"ok": True}))
    c = mod.app.test_client()
    r = c.post("/ptt", data={"conversation_id": "c"}, content_type="multipart/form-data",
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 400


def test_ptt_route_forwards_fields_and_returns_ptt_turn_result(monkeypatch):
    mod = _load_proxy()
    seen = {}
    def fake_turn(audio_bytes, filename, conversation_id, turn_id, content_type="audio/m4a"):
        seen.update(dict(audio=audio_bytes, filename=filename, conv=conversation_id,
                         turn=turn_id, ct=content_type))
        return 200, {"ok": True, "reply_text": "hi", "stt_text": "hey", "audio": "QUJD"}
    monkeypatch.setattr(mod, "ptt_turn", fake_turn)
    c = mod.app.test_client()
    r = c.post("/ptt",
               data={"audio": (io.BytesIO(b"AUDIOBYTES"), "clip.m4a"),
                     "conversation_id": "conv-9", "turn_id": "turn-9"},
               content_type="multipart/form-data",
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["reply_text"] == "hi" and body["stt_text"] == "hey"
    assert seen["audio"] == b"AUDIOBYTES"
    assert seen["conv"] == "conv-9" and seen["turn"] == "turn-9"
    assert seen["filename"] == "clip.m4a"


def test_ptt_route_propagates_error_status(monkeypatch):
    mod = _load_proxy()
    monkeypatch.setattr(mod, "ptt_turn",
                        lambda *a, **k: (422, {"ok": False, "error": "no_speech"}))
    c = mod.app.test_client()
    r = c.post("/ptt", data={"audio": (io.BytesIO(b"x"), "u.m4a")},
               content_type="multipart/form-data",
               environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 422
    assert r.get_json()["error"] == "no_speech"
