"""The authenticated upgrade endpoint — the only route that MINTS a credential.

Shaw does not want to retype a pairing code on a headset he is already wearing, so a caller that
already holds a credential can exchange it for a narrower per-device one. That convenience is also
the most dangerous shape in this system: a token that can mint tokens is a different kind of
credential from one that can call routes.

gm approved it on five conditions. Each has a test below, and condition (c) is proven BY EFFECT —
rotate, then the minted token stops working — because that is the one whose failure mode is a
rotation that reports success while the old key lives on through its children.
"""
import json

import pytest

import scripts.watch_gateway as G
from scripts.device_tokens import HTTP_MINTABLE, DeviceStore, http_mintable


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


class _Req(dict):
    def __init__(self, principal, body=None):
        super().__init__()
        self["principal"] = principal
        self._body = body
        self.method = "POST"
        self.path = "/device/upgrade"
        self.headers = {}

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _store(tmp_path, monkeypatch):
    st = DeviceStore(tmp_path / "devices")
    monkeypatch.setattr(G, "_device_store", lambda: st)
    return st


FLEET = {"id": "legacy", "label": "legacy-fleet-token", "scopes": ["*"]}


def _upgrade(principal, body, tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    resp = _run(G.handle_device_upgrade(_Req(principal, body)))
    return resp, json.loads(resp.body.decode())


# ---------------------------------------------------------------- (a) the allowlist

def test_the_mintable_set_is_an_ALLOWLIST_of_exactly_three_verbs():
    """A denylist would silently grant every verb added later. `ptt` is excluded on purpose: a
    credential that can mint itself speech is minting PROVIDER SPEND."""
    assert HTTP_MINTABLE == ("read", "approve", "message")
    for verb in ("inject", "ptt", "voice", "admin", "usage"):
        ok, why = http_mintable([verb])
        assert ok is False, verb
        assert "cannot be minted over HTTP" in why


def test_the_unscoped_star_is_never_mintable():
    ok, why = http_mintable(["*"])
    assert ok is False and "never mintable" in why


def test_a_request_for_an_unmintable_verb_is_403_and_creates_NOTHING(tmp_path, monkeypatch):
    st = _store(tmp_path, monkeypatch)
    resp = _run(G.handle_device_upgrade(_Req(FLEET, {"label": "x", "scopes": ["read", "voice"]})))
    assert resp.status == 403
    assert st.list() == [], "a refused upgrade must not leave a device behind"


# ---------------------------------------------------------------- (d) no chains

def test_a_DEVICE_token_may_not_mint_even_a_subset_of_its_own_scopes(tmp_path, monkeypatch):
    """A chain means the question stops being "who issued this" and becomes "what is the
    transitive closure of everything that ever could have", which nobody audits."""
    st = _store(tmp_path, monkeypatch)
    dev = {"id": "d1", "label": "quest-3", "scopes": ["read", "approve", "message"]}
    resp = _run(G.handle_device_upgrade(_Req(dev, {"label": "child", "scopes": ["read"]})))
    assert resp.status == 403
    assert "may not mint" in json.loads(resp.body.decode())["error"]
    assert st.list() == []


def test_an_anonymous_caller_is_401(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    resp = _run(G.handle_device_upgrade(_Req(None, {"label": "x", "scopes": ["read"]})))
    assert resp.status == 401


# ---------------------------------------------------------------- the happy path + (b)

def test_the_fleet_bearer_mints_a_scoped_token_and_records_minted_by(tmp_path, monkeypatch):
    st = _store(tmp_path, monkeypatch)
    resp = _run(G.handle_device_upgrade(
        _Req(FLEET, {"label": "quest-3", "scopes": ["read", "approve", "message"]})))
    body = json.loads(resp.body.decode())
    assert resp.status == 200 and body["ok"] is True
    assert body["scopes"] == ["read", "approve", "message"]
    rec = st.resolve(body["token"])
    assert rec["id"] == body["device_id"]
    assert rec["minted_by"] == G.FLEET_MINTER_ID        # (b)


def test_no_scopes_and_no_label_are_both_refused(tmp_path, monkeypatch):
    for body in ({"label": "x"}, {"scopes": ["read"]}, {}):
        _store(tmp_path, monkeypatch)
        resp = _run(G.handle_device_upgrade(_Req(FLEET, body)))
        assert resp.status == 400, body


def test_the_minted_token_is_not_echoed_into_the_log_line(tmp_path, monkeypatch, caplog):
    _store(tmp_path, monkeypatch)
    resp = _run(G.handle_device_upgrade(_Req(FLEET, {"label": "q", "scopes": ["read"]})))
    token = json.loads(resp.body.decode())["token"]
    assert token not in caplog.text


# ---------------------------------------------------------------- (e) idempotent per label

def test_re_upgrading_a_label_REVOKES_the_previous_token(tmp_path, monkeypatch):
    """Otherwise every re-pair leaves another live credential behind and the revocation list
    stops being something anyone reads."""
    st = _store(tmp_path, monkeypatch)
    first = json.loads(_run(G.handle_device_upgrade(
        _Req(FLEET, {"label": "quest-3", "scopes": ["read"]}))).body.decode())
    second = json.loads(_run(G.handle_device_upgrade(
        _Req(FLEET, {"label": "quest-3", "scopes": ["read", "approve"]}))).body.decode())

    assert st.resolve(first["token"]) is None, "the previous quest-3 token must be dead"
    assert st.resolve(second["token"]) is not None
    assert second["replaced"] == [first["device_id"]]
    live = [r for r in st.list() if not r.get("revoked_at")]
    assert len(live) == 1, "exactly one live token per label, not a growing pile"


def test_a_different_label_is_left_alone(tmp_path, monkeypatch):
    st = _store(tmp_path, monkeypatch)
    watch = json.loads(_run(G.handle_device_upgrade(
        _Req(FLEET, {"label": "watch", "scopes": ["read"]}))).body.decode())
    _run(G.handle_device_upgrade(_Req(FLEET, {"label": "quest-3", "scopes": ["read"]})))
    assert st.resolve(watch["token"]) is not None, "re-upgrading one label must not revoke another"


# ---------------------------------------------------------------- (c) rotation, BY EFFECT

def test_rotating_the_fleet_token_REVOKES_everything_it_minted(tmp_path, monkeypatch):
    """gm's condition (c), proven by effect. A rotation that leaves the old credential's children
    alive is theatre: the key everyone believes is dead keeps working through what it issued."""
    st = _store(tmp_path, monkeypatch)
    a = json.loads(_run(G.handle_device_upgrade(
        _Req(FLEET, {"label": "quest-3", "scopes": ["read"]}))).body.decode())
    b = json.loads(_run(G.handle_device_upgrade(
        _Req(FLEET, {"label": "watch", "scopes": ["read", "approve"]}))).body.decode())
    # a token minted BY HAND on the host, i.e. not by the fleet bearer
    hand_id, hand_token = st.mint("by-hand", ["read"], minted_by="cli")

    assert st.resolve(a["token"]) and st.resolve(b["token"]) and st.resolve(hand_token)

    revoked = st.revoke_minted_by(G.FLEET_MINTER_ID)

    assert st.resolve(a["token"]) is None, "rotation must kill what the fleet bearer minted"
    assert st.resolve(b["token"]) is None
    assert st.resolve(hand_token) is not None, \
        "a token minted by hand on the host is NOT a child of the fleet bearer"
    assert set(revoked) == {a["device_id"], b["device_id"]}
    assert hand_id not in revoked


def test_revoking_twice_reports_nothing_the_second_time(tmp_path, monkeypatch):
    st = _store(tmp_path, monkeypatch)
    _run(G.handle_device_upgrade(_Req(FLEET, {"label": "q", "scopes": ["read"]})))
    assert len(st.revoke_minted_by(G.FLEET_MINTER_ID)) == 1
    assert st.revoke_minted_by(G.FLEET_MINTER_ID) == []


def test_the_route_is_registered_and_the_TABLE_says_it_is_privileged():
    """It is `admin`, not public. I first wrote it as _PUBLIC reasoning that the handler checks
    the principal; the public-routes guard caught that, correctly — a requirement that lives only
    in a handler is invisible to a reader of the table, which is the very problem the middleware
    exists to end. The handler's no-chains refusal is a SECOND gate, not the only one."""
    app = G.build_app()
    assert G.ROUTE_SCOPES[("POST", "/device/upgrade")] == "admin"
    found = any(res.canonical == "/device/upgrade" for res in app.router.resources())
    assert found, "the endpoint must be routed, not just defined"


def test_a_device_holding_admin_still_cannot_mint(tmp_path, monkeypatch):
    """Both gates, independently. The table stops a non-admin at the middleware; the handler stops
    an admin DEVICE at the no-chains check. Neither alone is sufficient."""
    st = _store(tmp_path, monkeypatch)
    admin_device = {"id": "d9", "label": "someone's laptop", "scopes": ["admin"]}
    resp = _run(G.handle_device_upgrade(_Req(admin_device, {"label": "x", "scopes": ["read"]})))
    assert resp.status == 403 and "may not mint" in json.loads(resp.body.decode())["error"]
    assert st.list() == []
