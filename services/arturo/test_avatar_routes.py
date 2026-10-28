"""/avatars and /avatar (GET, PUT, DELETE): each agent's saved look. Loopback-only like /ptt/voice; served with
the relay flag on or off; the writer's device comes from the gateway's principal header, never the body."""
import importlib.util
import pathlib

import pytest


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_avatar", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(monkeypatch, tmp_path):
    from services.arturo import avatar_choice as ac
    monkeypatch.setattr(ac, "_default", ac.AvatarChoiceStore(path=tmp_path / "a.json", log_path=tmp_path / "a.log"))
    monkeypatch.delenv("ARTURO_STREAM_RELAY", raising=False)
    return _load_proxy().app.test_client(), tmp_path


LOOP = {"REMOTE_ADDR": "127.0.0.1"}


def test_routes_exist_with_the_relay_flag_off_and_are_loopback_only(client):
    c, _ = client
    assert c.get("/avatars", environ_base=LOOP).status_code == 200
    for call in (lambda e: c.get("/avatars", environ_base=e), lambda e: c.get("/avatar?agent=arturo", environ_base=e),
                 lambda e: c.put("/avatar", json={"agent": "arturo", "shape": "star"}, environ_base=e),
                 lambda e: c.delete("/avatar?agent=arturo", environ_base=e)):
        assert call({"REMOTE_ADDR": "10.0.0.9"}).status_code == 403
    # a request forwarded through a proxy (the Funnel) is not the gateway hop
    assert c.get("/avatars", environ_base=LOOP, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


def test_get_put_get_delete_round_trip(client):
    c, tmp = client
    r = c.get("/avatar?agent=arturo", environ_base=LOOP).get_json()
    assert r["ok"] and r["chosen"] is False and r["shape"] == "star" and r["color"] == "ink" and r["eyeColor"] == "auto"
    r = c.put("/avatar", json={"agent": "arturo", "shape": "heart", "color": "#1E6BE6"}, environ_base=LOOP,
              headers={"X-Arturo-Principal": "device:mac-1"})
    assert r.status_code == 200 and r.get_json()["chosen"] is True
    assert c.get("/avatar?agent=arturo", environ_base=LOOP).get_json()["shape"] == "heart"
    allj = c.get("/avatars", environ_base=LOOP).get_json()
    assert list(allj["avatars"]) == ["arturo"] and allj["avatars"]["arturo"]["color"] == "#1E6BE6"
    assert '"device": "device:mac-1"' in (tmp / "a.log").read_text()
    r = c.delete("/avatar?agent=arturo", environ_base=LOOP).get_json()
    assert r["chosen"] is False and r["shape"] == "star"
    assert c.get("/avatars", environ_base=LOOP).get_json()["avatars"] == {}


def test_bad_values_and_missing_agent_are_400(client):
    c, _ = client
    for body in ({"agent": "arturo", "shape": "triangle"}, {"agent": "arturo", "color": "#123456"},
                 {"agent": "arturo", "eyeColor": "green"}, {"agent": "", "shape": "star"}, {"agent": "../x", "shape": "star"},
                 {"agent": "arturo"}):
        r = c.put("/avatar", json=body, environ_base=LOOP)
        assert r.status_code == 400 and r.get_json()["ok"] is False, body
    assert c.get("/avatar", environ_base=LOOP).status_code == 400
    assert c.delete("/avatar?agent=a%2Fb", environ_base=LOOP).status_code == 400


def test_the_device_is_never_taken_from_the_body(client):
    c, tmp = client
    c.put("/avatar", json={"agent": "gm", "shape": "egg", "device": "spoofed"}, environ_base=LOOP)
    assert '"device": null' in (tmp / "a.log").read_text()
