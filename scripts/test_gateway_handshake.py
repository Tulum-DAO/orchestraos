"""RED-first for the onboarding handshake (The operator, 2026-09-18: strangers must be able to
onboard on Saturday, on the web port AND the iOS app).

Contract adopted verbatim from devex-review's ruling (msg_b1135ce2), relayed by
ios-watch-dev, which is building the iOS client against exactly this tonight:

  GET /gateway/identity      UNAUTHENTICATED, frozen forever:
                             {"service":"orchestraos-gateway","protocol":1}
  GET /gateway/capabilities  BEHIND THE BEARER, additive-only:
                             {"providers":[{"id":..,"kind":..}],"surfaces":[..],"pending":N}

Why it exists: today ANY 200 counts as a gateway, so someone who types the dashboard port
instead of the gateway port gets a FALSE GREEN and a broken app with no explanation. The
identity endpoint turns that into the single most useful sentence on the screen.

/health is deliberately untouched here — it has callers (doctor, supervisor, INSTALL.md).
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_gateway():
    spec = importlib.util.spec_from_file_location("watch_gateway", ROOT / "scripts" / "watch_gateway.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def gw(monkeypatch, tmp_path):
    mod = _load_gateway()
    token_file = tmp_path / "token"
    token_file.write_text("test-token\n")
    monkeypatch.setattr(mod, "TOKEN_FILE", token_file, raising=False)
    return mod


class _Req:
    """Minimal aiohttp-request stand-in: headers + match_info + query are all these
    handlers read."""

    def __init__(self, headers=None, match_info=None, query=None):
        self.headers = headers or {}
        self.match_info = match_info or {}
        self.query = query or {}


def _body(resp):
    import json as _json
    return _json.loads(resp.body.decode())


# --- identity: unauthenticated, exact, frozen ------------------------------------------------

def test_identity_answers_without_a_token(gw):
    import asyncio
    r = asyncio.run(gw.handle_gateway_identity(_Req()))
    assert r.status == 200
    assert _body(r) == {"service": "orchestraos-gateway", "protocol": 1}


def test_identity_protocol_is_an_INTEGER_never_a_string(gw):
    """Integer, never semver — the client compares it numerically."""
    import asyncio
    body = _body(asyncio.run(gw.handle_gateway_identity(_Req())))
    assert isinstance(body["protocol"], int)
    assert not isinstance(body["protocol"], bool)


def test_identity_ignores_a_bearer_if_one_is_sent(gw):
    """Unauthenticated means it answers the same either way: a phone probes it BEFORE it
    has a token, and a wrong token must not turn the useful sentence into a 401."""
    import asyncio
    a = _body(asyncio.run(gw.handle_gateway_identity(_Req())))
    b = _body(asyncio.run(gw.handle_gateway_identity(_Req({"Authorization": "Bearer wrong"}))))
    assert a == b


# --- capabilities: behind the bearer, additive ------------------------------------------------

def test_capabilities_without_a_token_is_401(gw):
    import asyncio
    r = asyncio.run(gw.handle_gateway_capabilities(_Req()))
    assert r.status == 401


def test_capabilities_with_the_bearer_returns_the_three_blocks(gw, monkeypatch):
    import asyncio
    monkeypatch.setattr(gw, "_capability_providers", lambda: [{"id": "gemini", "kind": "voice"}], raising=False)
    monkeypatch.setattr(gw, "_pending_count", lambda: 7, raising=False)
    r = asyncio.run(gw.handle_gateway_capabilities(_Req({"Authorization": "Bearer test-token"})))
    assert r.status == 200
    body = _body(r)
    assert body["providers"] == [{"id": "gemini", "kind": "voice"}]
    assert "approvals" in body["surfaces"]
    assert body["pending"] == 7


def test_capabilities_provider_ids_are_free_strings_not_an_enum(gw, monkeypatch):
    """The phone renders FROM THE LIST. An id the server invented tomorrow must survive."""
    import asyncio
    monkeypatch.setattr(gw, "_capability_providers",
                        lambda: [{"id": "some-future-provider", "kind": "text"}], raising=False)
    r = asyncio.run(gw.handle_gateway_capabilities(_Req({"Authorization": "Bearer test-token"})))
    assert _body(r)["providers"][0]["id"] == "some-future-provider"


def test_capabilities_survives_a_provider_probe_that_throws(gw, monkeypatch):
    """An absent block means UNKNOWN, never none — but the endpoint must still answer, or
    the phone cannot tell 'no providers' from 'gateway broken'."""
    import asyncio
    def boom():
        raise RuntimeError("probe exploded")
    monkeypatch.setattr(gw, "_capability_providers", boom, raising=False)
    r = asyncio.run(gw.handle_gateway_capabilities(_Req({"Authorization": "Bearer test-token"})))
    assert r.status == 200
    assert "providers" not in _body(r)          # absent, not []
    assert "surfaces" in _body(r)


# --- /health must not have been touched --------------------------------------------------------

def test_health_route_still_registered_and_separate(gw):
    """doctor, supervisor and INSTALL.md call /health. New paths only."""
    src = (ROOT / "scripts" / "watch_gateway.py").read_text()
    assert 'app.router.add_get("/health", handle_health)' in src
    assert 'app.router.add_get("/gateway/identity"' in src
    assert 'app.router.add_get("/gateway/capabilities"' in src


# --- POST /pair/exchange: the phone trades a code for {base_url, token} -----------------------
# UNAUTHENTICATED by necessity — the caller has no token yet; that IS what it is asking for.
# The code is the credential, which is why it is single-use and short-lived.

class _PostReq(_Req):
    def __init__(self, payload, headers=None):
        super().__init__(headers)
        self._payload = payload

    async def json(self):
        return self._payload


def test_pair_exchange_trades_a_valid_code_for_the_token(gw, tmp_path, monkeypatch):
    import asyncio
    from scripts.pairing import PairingStore
    store = PairingStore(tmp_path / "pairing")
    code = store.mint(base_url="https://box:8443", token="test-token")
    monkeypatch.setattr(gw, "_pairing_store", lambda: store, raising=False)
    r = asyncio.run(gw.handle_pair_exchange(_PostReq({"code": code})))
    assert r.status == 200
    assert _body(r) == {"ok": True, "base_url": "https://box:8443", "token": "test-token"}


def test_pair_exchange_refuses_a_spent_code(gw, tmp_path, monkeypatch):
    import asyncio
    from scripts.pairing import PairingStore
    store = PairingStore(tmp_path / "pairing")
    code = store.mint(base_url="u", token="t")
    monkeypatch.setattr(gw, "_pairing_store", lambda: store, raising=False)
    asyncio.run(gw.handle_pair_exchange(_PostReq({"code": code})))
    second = asyncio.run(gw.handle_pair_exchange(_PostReq({"code": code})))
    assert second.status == 400
    assert "token" not in _body(second)


def test_pair_exchange_refuses_unknown_and_spent_IDENTICALLY(gw, tmp_path, monkeypatch):
    """No oracle: the response must not reveal whether the code ever existed."""
    import asyncio
    from scripts.pairing import PairingStore
    store = PairingStore(tmp_path / "pairing")
    code = store.mint(base_url="u", token="t")
    monkeypatch.setattr(gw, "_pairing_store", lambda: store, raising=False)
    asyncio.run(gw.handle_pair_exchange(_PostReq({"code": code})))
    spent = asyncio.run(gw.handle_pair_exchange(_PostReq({"code": code})))
    never = asyncio.run(gw.handle_pair_exchange(_PostReq({"code": "never-minted-at-all"})))
    assert spent.status == never.status
    assert _body(spent) == _body(never)


def test_pair_exchange_with_no_code_is_a_400_not_a_crash(gw, monkeypatch, tmp_path):
    import asyncio
    from scripts.pairing import PairingStore
    monkeypatch.setattr(gw, "_pairing_store", lambda: PairingStore(tmp_path / "p"), raising=False)
    r = asyncio.run(gw.handle_pair_exchange(_PostReq({})))
    assert r.status == 400


def test_pair_exchange_route_is_registered(gw):
    src = (ROOT / "scripts" / "watch_gateway.py").read_text()
    assert 'app.router.add_post("/pair/exchange"' in src
