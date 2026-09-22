# Tests for the watch-gateway /arturo/transcribe thin proxy (item C) — the AUTHENTICATED front door
# for web dictation clips. Locks: Bearer required, route wired, 10 MB cap (not the 1 MB watch rule).
import importlib.util
import pathlib

from aiohttp.test_utils import make_mocked_request


def _load_gateway():
    spec = importlib.util.spec_from_file_location("watch_gateway", pathlib.Path("scripts/watch_gateway.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_transcribe_gateway_requires_bearer():
    mod = _load_gateway()
    resp = await mod.handle_arturo_transcribe(make_mocked_request("POST", "/arturo/transcribe"))
    assert resp.status == 401


async def test_transcribe_gateway_rejects_wrong_bearer(monkeypatch):
    mod = _load_gateway()
    monkeypatch.setattr(mod, "gateway_token", lambda: "the-real-token")
    req = make_mocked_request("POST", "/arturo/transcribe", headers={"Authorization": "Bearer wrong"})
    assert (await mod.handle_arturo_transcribe(req)).status == 401


def test_transcribe_gateway_route_registered_and_caps_are_separate():
    mod = _load_gateway()
    routes = {(r.method, r.resource.canonical) for r in mod.build_app().router.routes()}
    assert ("POST", "/arturo/transcribe") in routes
    assert mod.TRANSCRIBE_MAX_BYTES == 10_000_000 and mod.PTT_MAX_BYTES == 1_000_000
