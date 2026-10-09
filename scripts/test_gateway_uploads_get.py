"""GET/HEAD /uploads/{name}: a paired device can see an attachment's bytes (DEC-1791563277424023).

End to end through the REAL gateway app (router + scope middleware + handler) in front of a fake API, so the
traversal and encoding cases go through aiohttp's own routing, and "the API is never called" is measured, not assumed.
"""
import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import scripts.watch_gateway as G

NAME = "1791561612_uazceo.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 5000


def _fake_api(hits):
    async def uploads(request):
        tail = request.match_info["tail"]
        hits.append((request.method, tail, request.headers.get("Range")))
        if tail == "telegram":
            raise web.HTTPMovedPermanently("/uploads/telegram/")
        if tail == "1791561612_page01.html":
            return web.Response(body=b"<script>x</script>", headers={
                "Content-Type": "text/plain; charset=utf-8", "Content-Disposition": "attachment",
                "X-Content-Type-Options": "nosniff"})
        if tail != NAME:
            return web.Response(status=404)
        rng = request.headers.get("Range")
        if rng == "bytes=0-9":
            return web.Response(status=206, body=PNG[:10], headers={
                "Content-Type": "image/png", "Content-Range": f"bytes 0-9/{len(PNG)}", "Accept-Ranges": "bytes"})
        return web.Response(body=PNG, headers={"Content-Type": "image/png", "Accept-Ranges": "bytes"})

    app = web.Application()
    app.router.add_route("*", "/uploads/{tail:.*}", uploads)
    return app


def _run(monkeypatch, tmp_path, scopes, fn):
    from scripts.device_tokens import DeviceStore
    store = DeviceStore(tmp_path / "devices")
    _, token = store.mint("mac", scopes)
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", lambda: store)
    hits = []

    async def go():
        async with TestServer(_fake_api(hits)) as api:
            monkeypatch.setattr(G, "API_URL", str(api.make_url("")).rstrip("/"))
            async with TestClient(TestServer(G.build_app())) as c:
                return await fn(c, {"Authorization": f"Bearer {token}"})

    return asyncio.run(go()), hits


def test_a_read_device_gets_the_bytes_with_the_safety_headers(monkeypatch, tmp_path):
    async def fn(c, h):
        r = await c.get(f"/uploads/{NAME}", headers=h)
        return r.status, await r.read(), dict(r.headers)
    (status, body, hdr), hits = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 200 and body == PNG
    assert hdr["Content-Type"] == "image/png"
    assert hdr["X-Content-Type-Options"] == "nosniff"
    assert hdr["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert hdr["Cache-Control"].startswith("private")
    assert hits == [("GET", NAME, None)]


def test_range_passes_through_so_video_plays(monkeypatch, tmp_path):
    async def fn(c, h):
        r = await c.get(f"/uploads/{NAME}", headers={**h, "Range": "bytes=0-9"})
        return r.status, await r.read(), dict(r.headers)
    (status, body, hdr), hits = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 206 and body == PNG[:10]
    assert hdr["Content-Range"] == f"bytes 0-9/{len(PNG)}" and hdr["Accept-Ranges"] == "bytes"
    assert hits[0][2] == "bytes=0-9"


def test_head_is_allowed_for_read_and_carries_no_body(monkeypatch, tmp_path):
    async def fn(c, h):
        r = await c.head(f"/uploads/{NAME}", headers=h)
        return r.status, await r.read(), dict(r.headers)
    (status, body, hdr), _ = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 200 and body == b"" and hdr["Content-Type"] == "image/png"


def test_a_device_without_read_is_refused_and_the_api_is_never_called(monkeypatch, tmp_path):
    async def fn(c, h):
        return (await c.get(f"/uploads/{NAME}", headers=h)).status
    status, hits = _run(monkeypatch, tmp_path, ["message"], fn)
    assert status == 403 and hits == []


@pytest.mark.parametrize("bad", ["telegram", ".env", "..%2Fsecret.png", "%2e%2e", "a%2Fb.png", "x.png",
                                 "123_abc.PNG", "123_abc.png%0A", "123_abc.png%00", "123_abc..png"])
def test_anything_but_an_upload_id_is_refused_before_the_api(monkeypatch, tmp_path, bad):
    async def fn(c, h):
        return (await c.get(f"/uploads/{bad}", headers=h)).status
    status, hits = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status in (400, 404), status
    assert hits == [], f"{bad!r} reached the API"


def test_a_path_with_a_slash_never_matches_the_route(monkeypatch, tmp_path):
    async def fn(c, h):
        return (await c.get("/uploads/telegram/123_abc.png", headers=h)).status
    status, hits = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 404 and hits == []


def test_an_upstream_redirect_is_a_404_never_followed(monkeypatch, tmp_path):
    # Reachable only if the name check were loosened; pin the second guard on its own.
    monkeypatch.setattr(G, "_SAFE_UPLOAD_NAME", __import__("re").compile(r"telegram"))

    async def fn(c, h):
        return (await c.get("/uploads/telegram", headers=h)).status
    status, hits = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 404
    assert hits == [("GET", "telegram", None)], "exactly one upstream call: the redirect was not followed"


def test_html_keeps_the_apis_force_download(monkeypatch, tmp_path):
    async def fn(c, h):
        r = await c.get("/uploads/1791561612_page01.html", headers=h)
        return r.status, dict(r.headers)
    (status, hdr), _ = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 200
    assert hdr["Content-Disposition"] == "attachment"
    assert hdr["Content-Type"].startswith("text/plain")
    assert "sandbox" in hdr["Content-Security-Policy"]


def test_a_missing_upload_is_404(monkeypatch, tmp_path):
    async def fn(c, h):
        return (await c.get("/uploads/123_nothere.png", headers=h)).status
    status, _ = _run(monkeypatch, tmp_path, ["read"], fn)
    assert status == 404


def test_the_capability_is_advertised():
    import inspect
    assert '"uploads_get"' in inspect.getsource(G.handle_gateway_capabilities)
