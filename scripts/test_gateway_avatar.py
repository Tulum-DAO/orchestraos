"""The gateway's avatar and assistant-name pass-throughs: /arturo/avatars, /arturo/avatar and
/arturo/assistant/name forward to Arturo's loopback routes with the caller stamped fresh, only ?agent= on the
query, and the body passed as is (Arturo validates it)."""
import asyncio

import aiohttp
import pytest

import scripts.watch_gateway as G


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))


class _Req(dict):
    def __init__(self, method, path, query=None, body=b"", principal=None, client_headers=None):
        super().__init__({"principal": principal or {"id": "legacy"}})
        self.method, self.path, self.query = method, path, query or {}
        self.headers = client_headers or {}
        self._body = body

    async def read(self):
        return self._body


def _capture(monkeypatch, status=200, out=None, fail=None):
    seen = {}

    class _Resp:
        def __init__(self):
            self.status = status

        async def json(self, content_type=None):
            return out if out is not None else {"ok": True}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        def request(self, method, url, data=None, headers=None, timeout=None):
            if fail:
                raise fail
            seen.update(method=method, url=url, data=data, headers=dict(headers or {}))
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    return seen


def test_list_forwards_to_avatars(monkeypatch):
    seen = _capture(monkeypatch)
    r = asyncio.run(G.handle_arturo_avatar(_Req("GET", "/arturo/avatars", {"x": "1"})))
    assert r.status == 200
    assert seen["method"] == "GET" and seen["url"] == G.ARTURO_AVATAR_BASE + "/avatars"


def test_one_forwards_only_the_agent_escaped(monkeypatch):
    seen = _capture(monkeypatch)
    asyncio.run(G.handle_arturo_avatar(_Req("GET", "/arturo/avatar", {"agent": "dev shore&x=1", "extra": "no"})))
    assert seen["url"] == G.ARTURO_AVATAR_BASE + "/avatar?agent=dev+shore%26x%3D1"


def test_put_passes_the_body_and_stamps_the_caller_never_the_clients_claim(monkeypatch):
    seen = _capture(monkeypatch)
    body = b'{"agent":"arturo","shape":"heart"}'
    asyncio.run(G.handle_arturo_avatar(_Req("PUT", "/arturo/avatar", body=body, principal={"id": "dev_mac"},
                                            client_headers={"X-Arturo-Principal": "fleet"})))
    assert seen["method"] == "PUT" and seen["data"] == body
    assert seen["headers"]["X-Arturo-Principal"] == "device:dev_mac"
    assert seen["headers"]["Content-Type"] == "application/json"


def test_delete_forwards_the_agent(monkeypatch):
    seen = _capture(monkeypatch)
    asyncio.run(G.handle_arturo_avatar(_Req("DELETE", "/arturo/avatar", {"agent": "arturo"})))
    assert seen["method"] == "DELETE" and seen["url"].endswith("/avatar?agent=arturo") and seen["data"] is None


def test_arturos_refusal_passes_through_and_a_dead_upstream_is_502(monkeypatch):
    _capture(monkeypatch, status=400, out={"ok": False, "error": "shape must be one of: circle"})
    r = asyncio.run(G.handle_arturo_avatar(_Req("PUT", "/arturo/avatar", body=b"{}")))
    assert r.status == 400
    _capture(monkeypatch, fail=aiohttp.ClientConnectionError("refused"))
    r = asyncio.run(G.handle_arturo_avatar(_Req("GET", "/arturo/avatars")))
    assert r.status == 502


def test_assistant_name_forwards_with_the_caller_stamped_and_no_query(monkeypatch):
    seen = _capture(monkeypatch)
    body = b'{"name":"Nova"}'
    asyncio.run(G.handle_arturo_assistant_name(_Req("PUT", "/arturo/assistant/name", {"x": "1"}, body=body,
                                                    principal={"id": "dev_mac"},
                                                    client_headers={"X-Arturo-Principal": "fleet"})))
    assert seen["method"] == "PUT" and seen["url"] == G.ARTURO_AVATAR_BASE + "/assistant/name"
    assert seen["data"] == body and seen["headers"]["X-Arturo-Principal"] == "device:dev_mac"
    assert seen["headers"]["X-Name-Source"] == "settings"
    seen = _capture(monkeypatch)
    asyncio.run(G.handle_arturo_assistant_name(_Req("GET", "/arturo/assistant/name")))
    assert seen["method"] == "GET" and seen["data"] is None
    _capture(monkeypatch, fail=aiohttp.ClientConnectionError("refused"))
    assert asyncio.run(G.handle_arturo_assistant_name(_Req("DELETE", "/arturo/assistant/name"))).status == 502
