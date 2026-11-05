"""/live never picks an engine by itself (Arturo decides from the voice provider), refuses plainly when there is
none, and carries ?agent= to the relay (contract with the API's voice-live route: the parameter is `agent`).
Stand-ins only: no Arturo, no real call, no key."""
import asyncio

import pytest

import scripts.watch_gateway as G


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(G, "gateway_token", lambda: "tok")


class _WS:
    def __init__(self):
        self.sent, self.closed = [], False

    async def prepare(self, request):
        return self

    async def send_json(self, obj):
        self.sent.append(obj)

    async def close(self):
        self.closed = True


class _Req(dict):
    def __init__(self, query):
        super().__init__({"principal": {"id": "legacy"}})
        self.headers = {"Authorization": "Bearer tok"}
        self.query = query


def _rig(monkeypatch, engine, reason=None):
    from aiohttp import web
    from services.arturo import relay_live_bridge
    made = {"ws": _WS()}

    class _Relay:
        def __init__(self, ws, base, headers, voice="", on_surface=None, **kw):
            made["relay"] = kw

        async def run(self):
            return None

    class _Gemini:
        def __init__(self, *a, **k):
            made["gemini"] = True

        async def run(self):
            return None

    async def _engine():
        G._LIVE_REASON["reason"] = reason
        return engine
    monkeypatch.setattr(web, "WebSocketResponse", lambda: made["ws"])
    monkeypatch.setattr(G, "GeminiLiveSession", _Gemini)
    monkeypatch.setattr(G, "_live_engine", _engine)
    monkeypatch.setattr(relay_live_bridge, "RelayLiveSession", _Relay)
    return made


@pytest.mark.parametrize("reason,said", [
    ("Live calls aren't available with this voice provider yet", "Live calls aren't available with this voice provider yet"),
    (None, "Live calls aren't available with this voice provider yet")])
def test_no_engine_is_refused_with_the_plain_reason_and_never_gemini(monkeypatch, reason, said):
    made = _rig(monkeypatch, None, reason)
    asyncio.run(G.handle_gemini_live(_Req({"agent": "dev-demo"})))
    assert made["ws"].sent == [{"event": "error", "reason": said}] and made["ws"].closed
    assert "gemini" not in made and "relay" not in made


def test_relay_gets_the_agent(monkeypatch):
    made = _rig(monkeypatch, "relay")
    asyncio.run(G.handle_gemini_live(_Req({"agent": "dev-demo"})))
    assert made["relay"] == {"agent": "dev-demo"} and "gemini" not in made


def test_no_agent_is_arturos_own_call(monkeypatch):
    made = _rig(monkeypatch, "relay")
    asyncio.run(G.handle_gemini_live(_Req({})))
    assert made["relay"] == {}


def test_a_malformed_agent_is_refused_before_the_upgrade(monkeypatch):
    made = _rig(monkeypatch, "relay")
    r = asyncio.run(G.handle_gemini_live(_Req({"agent": "../x y"})))
    assert r.status == 400 and "relay" not in made and not made["ws"].sent


def test_gemini_runs_only_when_arturo_says_gemini(monkeypatch):
    made = _rig(monkeypatch, "gemini")
    asyncio.run(G.handle_gemini_live(_Req({})))
    assert made.get("gemini") and "relay" not in made


def _health(monkeypatch, body=None, fail=False):
    import aiohttp

    class _Resp:
        async def json(self, content_type=None):
            return body

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _S:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            if fail:
                raise aiohttp.ClientConnectionError("down")
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url):
            return _Resp()
    monkeypatch.setattr(aiohttp, "ClientSession", _S)
    engine = asyncio.run(G._live_engine())
    return engine, G._LIVE_REASON["reason"]


def test_unreachable_arturo_is_refused_never_gemini(monkeypatch):
    assert _health(monkeypatch, fail=True) == (None, "Live calls aren't available right now")


def test_engine_and_reason_come_from_arturo_health(monkeypatch):
    assert _health(monkeypatch, {"live_engine": "relay", "live_reason": None}) == ("relay", None)
    assert _health(monkeypatch, {"live_engine": None, "live_reason": "OPENAI_API_KEY is not set"}) == (
        None, "OPENAI_API_KEY is not set")
    assert _health(monkeypatch, {"live_engine": None}) == (
        None, "Live calls aren't available with this voice provider yet")
    assert _health(monkeypatch, {"live": True}) == (                     # older Arturo, no live_engine
        None, "Live calls aren't available with this voice provider yet")


def test_the_relay_bridge_sends_the_agent_header_only_when_set():
    from services.arturo.relay_live_bridge import RelayLiveSession
    assert RelayLiveSession(None, "http://x", {}, agent="dev-demo").headers["X-Arturo-Agent"] == "dev-demo"
    assert "X-Arturo-Agent" not in RelayLiveSession(None, "http://x", {}).headers
