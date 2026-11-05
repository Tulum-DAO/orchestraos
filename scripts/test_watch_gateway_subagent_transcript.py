"""GET /subagent-transcript?agent=&id=: one subagent's own log for the phone/watch/Quest,
proxied to the API's /api/agents/:id/subagents/:aid/transcript."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import watch_gateway as G  # noqa: E402

assert os.path.realpath(G.__file__) == os.path.join(os.path.realpath(_HERE), "watch_gateway.py")

SUB = "a0000000000000def"


def test_route_is_declared_read_and_registered():
    assert G.ROUTE_SCOPES[("GET", "/subagent-transcript")] == "read"
    app = G.build_app()
    assert ("GET", "/subagent-transcript") in {(r.method, r.resource.canonical) for r in app.router.routes()
                                               if r.resource is not None}


async def _client(aiohttp_client, monkeypatch, authorized=True):
    from aiohttp import web
    hits = []

    async def fake_api(request):
        hits.append((request.match_info["id"], request.match_info["aid"], request.query.get("limit")))
        if request.match_info["aid"] == "deadbeef00":
            return web.json_response({"error": "no such subagent"}, status=404)
        return web.json_response({"agent_id": request.match_info["id"], "subagent_id": request.match_info["aid"],
                                  "items": [{"kind": "text", "text": "found 3 hooks"}], "render_items": []})
    api = web.Application()
    api.router.add_get("/api/agents/{id}/subagents/{aid}/transcript", fake_api)
    api_client = await aiohttp_client(api)
    monkeypatch.setattr(G, "API_URL", str(api_client.make_url("")).rstrip("/"))
    monkeypatch.setattr(G, "_authorized", lambda r: authorized)
    gw = web.Application()
    gw.router.add_get("/subagent-transcript", G.handle_subagent_transcript)
    return await aiohttp_client(gw), hits


async def test_proxies_the_subagent_envelope(aiohttp_client, monkeypatch):
    c, hits = await _client(aiohttp_client, monkeypatch)
    r = await c.get("/subagent-transcript", params={"agent": "pm-x", "id": SUB, "limit": "40"})
    body = await r.json()
    assert r.status == 200 and body["subagent_id"] == SUB and body["items"][0]["text"] == "found 3 hooks"
    assert hits == [("pm-x", SUB, "40")]
    r = await c.get("/subagent-transcript", params={"agent": "pm-x", "id": "deadbeef00"})
    assert r.status == 404                                   # the API's miss passes through


async def test_refusals_never_reach_the_api(aiohttp_client, monkeypatch):
    c, hits = await _client(aiohttp_client, monkeypatch)
    for params in ({"agent": "pm-x"}, {"id": SUB}, {"agent": "../etc", "id": SUB},
                   {"agent": "pm-x", "id": "../../x"}, {"agent": "pm-x/../y", "id": SUB}):
        r = await c.get("/subagent-transcript", params=params)
        assert r.status == 400, params
    assert hits == []
    c2, hits2 = await _client(aiohttp_client, monkeypatch, authorized=False)
    assert (await c2.get("/subagent-transcript", params={"agent": "pm-x", "id": SUB})).status == 401
    assert hits2 == []
