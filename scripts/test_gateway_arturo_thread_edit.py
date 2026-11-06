"""The gateway passes a chat rename (PATCH) and delete (DELETE) through to Arturo with the caller's stamp.

Only the title and the forget_facts flag go upstream; the id is escaped, never pasted into the path.
"""
import asyncio

import aiohttp
import pytest

import scripts.watch_gateway as G


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))


class _Req(dict):
    def __init__(self, principal, cid="c1", body=None, query=None):
        super().__init__({"principal": principal})
        self.headers = {}
        self.match_info = {"conversation_id": cid}
        self.query = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _capture(monkeypatch):
    seen = {}

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"ok": True}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        def patch(self, url, json=None, headers=None, timeout=None):
            seen.update(method="PATCH", url=url, json=json, headers=dict(headers or {}))
            return _Resp()

        def delete(self, url, params=None, headers=None, timeout=None):
            seen.update(method="DELETE", url=url, params=params, headers=dict(headers or {}))
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    return seen


def test_rename_forwards_only_the_title_with_the_stamp(monkeypatch):
    seen = _capture(monkeypatch)
    asyncio.run(G.handle_arturo_thread_rename(_Req({"id": "dev_x"}, cid="a/b", body={"title": "New", "x": 1})))
    assert seen["method"] == "PATCH" and seen["url"].endswith("/threads/a%2Fb")
    assert seen["json"] == {"title": "New"}
    assert seen["headers"]["X-Arturo-Principal"] == "device:dev_x"


def test_rename_without_a_title_is_refused_here(monkeypatch):
    seen = _capture(monkeypatch)
    r = asyncio.run(G.handle_arturo_thread_rename(_Req({"id": "legacy"}, body={"title": 5})))
    assert r.status == 400 and not seen


def test_delete_forwards_forget_facts_only_when_asked(monkeypatch):
    seen = _capture(monkeypatch)
    asyncio.run(G.handle_arturo_thread_delete(_Req({"id": "legacy"}, query={"forget_facts": "1", "other": "x"})))
    assert seen["method"] == "DELETE" and seen["params"] == {"forget_facts": "1"}
    assert seen["headers"]["X-Arturo-Principal"] == "fleet"
    asyncio.run(G.handle_arturo_thread_delete(_Req({"id": "legacy"}, query={"forget_facts": "yes"})))
    assert seen["params"] == {}


def test_delete_passes_on_who_asked_when_it_is_a_plain_login(monkeypatch):
    seen = _capture(monkeypatch)
    asyncio.run(G.handle_arturo_thread_delete(_Req({"id": "legacy"}, query={"by": "ops@example.com"})))
    assert seen["params"] == {"by": "ops@example.com"}
    asyncio.run(G.handle_arturo_thread_delete(_Req({"id": "legacy"}, query={"by": "a b\nc"})))
    assert seen["params"] == {}
