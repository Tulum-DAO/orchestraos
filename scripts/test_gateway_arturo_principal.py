"""The gateway tells Arturo WHO is calling (congruence DEC-1791485978471942, A8).

Arturo makes a pairing code (read, approve, message) only on a turn the gateway stamped "fleet": the
fleet bearer, which is the dashboard's path and already holds every scope. A paired device holding
only `voice` reaches /arturo/text too, so without this stamp it could ask Arturo to "pair my iPhone"
and walk away with more power than it has.
"""
import asyncio

import aiohttp
import pytest

import scripts.watch_gateway as G


class _Req(dict):
    """A request double the handlers accept: a Mapping holding the middleware's principal, with
    client headers that must NOT reach Arturo."""

    def __init__(self, principal, client_headers=None):
        super().__init__({"principal": principal})
        self.headers = client_headers or {}

    async def read(self):
        return b'{"text":"pair my iphone","conversation_id":"c1"}'


def test_the_fleet_bearer_is_stamped_fleet():
    h = G._arturo_upstream_headers(_Req({"id": "legacy"}))
    assert h["X-Arturo-Principal"] == "fleet"


def test_a_device_is_stamped_with_its_own_id_never_fleet():
    h = G._arturo_upstream_headers(_Req({"id": "dev_ab12", "label": "quest", "scopes": ["voice"]}))
    assert h["X-Arturo-Principal"] == "device:dev_ab12"


def test_no_resolved_principal_means_no_stamp_so_arturo_refuses():
    class _NoMiddleware:
        headers = {}
    assert "X-Arturo-Principal" not in G._arturo_upstream_headers(_NoMiddleware())


def _capture_upstream(monkeypatch):
    seen = {}

    class _Resp:
        status = 200
        headers = {"Content-Type": "application/json"}

        async def json(self, content_type=None):
            return {"ok": True}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        def post(self, url, data=None, headers=None, timeout=None):
            seen["headers"] = dict(headers or {})
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    return seen


def test_a_client_cannot_forge_the_stamp_through_text(monkeypatch):
    seen = _capture_upstream(monkeypatch)
    req = _Req({"id": "dev_voice", "scopes": ["voice"]}, client_headers={"X-Arturo-Principal": "fleet"})
    asyncio.run(G.handle_arturo_text(req))
    assert seen["headers"]["X-Arturo-Principal"] == "device:dev_voice"


@pytest.mark.parametrize("principal,stamp", [({"id": "legacy"}, "fleet"), ({"id": "dev_x"}, "device:dev_x")])
def test_text_forwards_the_stamp(monkeypatch, principal, stamp):
    seen = _capture_upstream(monkeypatch)
    asyncio.run(G.handle_arturo_text(_Req(principal)))
    assert seen["headers"]["X-Arturo-Principal"] == stamp
    assert seen["headers"]["Content-Type"] == "application/json"
