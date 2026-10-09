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
from scripts import arturo_stamp


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    # the stamp secret is made in the data dir: never the real ~/.orchestra from a test
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


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


def test_fleet_travels_with_this_installs_secret(_data_dir):
    h = G._arturo_upstream_headers(_Req({"id": "legacy"}))
    assert h[arturo_stamp.HEADER] == arturo_stamp.read(_data_dir) and h[arturo_stamp.HEADER]
    assert oct(arturo_stamp.path(_data_dir).stat().st_mode & 0o777) == "0o600"


def test_a_device_never_carries_the_secret():
    h = G._arturo_upstream_headers(_Req({"id": "dev_ab12", "scopes": ["voice"]}))
    assert arturo_stamp.HEADER not in h


def test_a_client_cannot_pass_its_own_secret_through(monkeypatch):
    seen = _capture_upstream(monkeypatch)
    req = _Req({"id": "dev_voice", "scopes": ["voice"]},
               client_headers={"X-Arturo-Principal": "fleet", arturo_stamp.HEADER: "guess"})
    asyncio.run(G.handle_arturo_text(req))
    assert arturo_stamp.HEADER not in seen["headers"]


def test_the_secret_is_made_once_and_kept(_data_dir):
    first = arturo_stamp.ensure(_data_dir)
    assert first and arturo_stamp.ensure(_data_dir) == first


@pytest.mark.parametrize("principal,stamp", [({"id": "legacy"}, "fleet"), ({"id": "dev_x"}, "device:dev_x")])
def test_prewarm_forwards_the_stamp_too(monkeypatch, principal, stamp):
    # the warm process is built for one caller's prompt and tools, so Arturo must know whose it is
    seen = _capture_upstream(monkeypatch)
    asyncio.run(G.handle_arturo_text_prewarm(_Req(principal)))
    assert seen["headers"]["X-Arturo-Principal"] == stamp
    assert (arturo_stamp.HEADER in seen["headers"]) is (stamp == "fleet")


# --- G1': voice calls carry their caller ---------------------------------------------------------

def test_an_owner_device_is_stamped_owner_with_the_secret_on_voice(_data_dir):
    h = G._arturo_principal_headers(_Req({"id": "dev_phone", "scopes": ["read", "ptt", "owner"]}))
    assert h["X-Arturo-Principal"] == "owner:dev_phone"
    assert h[arturo_stamp.HEADER] == arturo_stamp.read(_data_dir)


def test_owner_is_a_voice_grant_a_text_turn_stays_a_device(monkeypatch):
    seen = _capture_upstream(monkeypatch)
    asyncio.run(G.handle_arturo_text(_Req({"id": "dev_phone", "scopes": ["voice", "owner"]})))
    assert seen["headers"]["X-Arturo-Principal"] == "device:dev_phone"
    assert arturo_stamp.HEADER not in seen["headers"]


def test_without_the_owner_verb_a_ptt_device_stays_a_device():
    h = G._arturo_principal_headers(_Req({"id": "dev_quest", "scopes": ["read", "ptt"]}))
    assert h == {"X-Arturo-Principal": "device:dev_quest"}


def test_an_owner_device_without_a_secret_is_only_a_device(monkeypatch):
    monkeypatch.setattr(arturo_stamp, "ensure", lambda *_a, **_k: None)
    h = G._arturo_principal_headers(_Req({"id": "dev_phone", "scopes": ["owner"]}))
    assert h == {"X-Arturo-Principal": "device:dev_phone"}


def _capture_stream(monkeypatch):
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

        def request(self, method, url, data=None, params=None, headers=None, timeout=None):
            seen["headers"] = dict(headers or {})
            return _Resp()

        def post(self, url, data=None, headers=None, timeout=None):
            seen["headers"] = dict(headers or {})
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    return seen


class _StreamReq(_Req):
    async def read(self):
        return b"\x00\x01"


@pytest.mark.parametrize("handler", ["handle_arturo_ptt_stream_audio", "handle_arturo_ptt_stream_end"])
@pytest.mark.parametrize("principal,stamp", [({"id": "legacy"}, "fleet"),
                                             ({"id": "dev_p", "scopes": ["ptt", "owner"]}, "owner:dev_p"),
                                             ({"id": "dev_q", "scopes": ["ptt"]}, "device:dev_q")])
def test_the_stream_relay_forwards_the_caller(monkeypatch, handler, principal, stamp):
    seen = _capture_stream(monkeypatch)
    req = _StreamReq(principal, client_headers={"X-Conversation-Id": "c1",
                                                "X-Arturo-Principal": "fleet", arturo_stamp.HEADER: "guess"})
    asyncio.run(getattr(G, handler)(req))
    assert seen["headers"]["X-Arturo-Principal"] == stamp
    assert seen["headers"]["X-Conversation-Id"] == "c1"
    assert (arturo_stamp.HEADER in seen["headers"]) is (stamp != f"device:{principal['id']}")
    assert seen["headers"].get(arturo_stamp.HEADER) != "guess"


def test_the_single_ptt_turn_forwards_the_caller(monkeypatch):
    seen = _capture_stream(monkeypatch)

    class _Field:
        def __init__(self, name, value):
            self.name, self._v = name, value
            self.filename, self.headers = "a.m4a", {}
            self._sent = False

        async def read_chunk(self, n):
            if self._sent:
                return b""
            self._sent = True
            return self._v

        async def text(self):
            return self._v.decode()

    class _Reader:
        def __init__(self):
            self._f = [_Field("audio", b"\x00\x01"), _Field("conversation_id", b"c1")]

        async def next(self):
            return self._f.pop(0) if self._f else None

    class _PttReq(_Req):
        async def multipart(self):
            return _Reader()

    asyncio.run(G.handle_arturo_ptt(_PttReq({"id": "dev_p", "scopes": ["ptt", "owner"]})))
    assert seen["headers"]["X-Arturo-Principal"] == "owner:dev_p"
    assert arturo_stamp.HEADER in seen["headers"]


def test_gemini_live_opens_the_session_as_fleet(monkeypatch):
    from aiohttp import web
    monkeypatch.setattr(G, "gateway_token", lambda: "tok")
    made = {}

    class _WS:
        async def prepare(self, request):
            return self

    class _Session:
        def __init__(self, ws, voice_name=None, principal=None):
            made["principal"] = principal

        async def run(self):
            return None

    monkeypatch.setattr(web, "WebSocketResponse", _WS)
    monkeypatch.setattr(G, "GeminiLiveSession", _Session)

    class _LiveReq(_Req):
        query = {}

    req = _LiveReq({"id": "legacy"}, client_headers={"Authorization": "Bearer tok"})
    asyncio.run(G.handle_gemini_live(req))
    assert made["principal"] == "fleet"


# --- DEC-1791518421640932: a turn read-back carries its reader, and the turn id survives the hop ------------

@pytest.mark.parametrize("principal,stamp", [({"id": "legacy"}, "fleet"), ({"id": "dev_x"}, "device:dev_x")])
def test_a_thread_read_carries_its_reader_and_the_turn_query(monkeypatch, principal, stamp):
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

        def get(self, url, params=None, headers=None, timeout=None):
            seen.update(url=url, params=dict(params or {}), headers=dict(headers or {}))
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(G, "_authorized", lambda request: True)
    req = _Req(principal, client_headers={"X-Arturo-Principal": "fleet"})
    req.match_info = {"conversation_id": "c1"}
    req.query = {"turn": "t_abc123456", "evil": "1"}
    asyncio.run(G.handle_arturo_threads(req))
    assert seen["url"].endswith("/threads/c1")
    assert seen["params"] == {"turn": "t_abc123456"}
    assert seen["headers"]["X-Arturo-Principal"] == stamp      # built fresh, never the client's own header


# --- a health read carries its reader: the onboarding thread id goes only to the dashboard -------------------

@pytest.mark.parametrize("principal,stamp", [({"id": "legacy"}, "fleet"), ({"id": "dev_x"}, "device:dev_x")])
def test_a_health_read_carries_its_reader(monkeypatch, principal, stamp):
    seen = {}

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"status": "ok"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, *a, **k):
            pass

        def get(self, url, headers=None, timeout=None):
            seen.update(url=url, headers=dict(headers or {}))
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(G, "_authorized", lambda request: True)
    req = _Req(principal, client_headers={"X-Arturo-Principal": "fleet"})
    asyncio.run(G.handle_arturo_health(req))
    assert seen["url"].endswith("/health")
    assert seen["headers"].get("X-Arturo-Principal") == stamp   # built fresh, never the client's own header
