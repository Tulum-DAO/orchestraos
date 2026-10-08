"""PUT/DELETE /push/token and the store behind them.

The three properties that would be expensive to get wrong after apps ship:
- no defaults: a registration missing platform / bundle_id / env is refused, not guessed;
- a stale Forget never unregisters a device that re-paired since (the APNs token survives
  Forget-then-pair-again, because it belongs to the app install);
- tokens die with their device, on every revoke path.
"""
import asyncio
import json
import os
import stat

import pytest

import scripts.watch_gateway as G
from scripts.device_tokens import DeviceStore
from scripts.push_tokens import PushTokenError, PushTokenStore, validate_registration

TOK = "ab" * 32
TOK2 = "cd" * 32


def _reg(**over):
    body = {"token": TOK, "platform": "iphone", "bundle_id": "co.bizypro.OrchestraOS",
            "env": "production", "rev": 100}
    body.update(over)
    return body


class _Req(dict):
    """A request the middleware already resolved: `principal` set, a JSON body."""

    def __init__(self, principal, payload):
        super().__init__()
        self["principal"] = principal
        self.headers = {}
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _body(resp):
    return json.loads(resp.body)


def _put(principal, payload):
    return asyncio.run(G.handle_push_token_put(_Req(principal, payload)))


def _delete(principal, payload):
    return asyncio.run(G.handle_push_token_delete(_Req(principal, payload)))


PHONE = {"id": "dev-phone", "label": "iphone", "scopes": ["read"]}
OTHER = {"id": "dev-other", "label": "quest", "scopes": ["read"]}
FLEET = {"id": "legacy", "label": "legacy-fleet-token", "scopes": ["*"]}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.delenv("ORCHESTRA_PUSH_DELIVERY", raising=False)
    monkeypatch.delenv("ORCHESTRA_PUSH_BUNDLE_IDS", raising=False)
    return tmp_path


def _stored(data_dir):
    return json.loads((data_dir / "state" / "push-tokens.json").read_text())


# ---------------------------------------------------------------- validation: no defaults

@pytest.mark.parametrize("field", ["platform", "bundle_id", "env", "token", "rev"])
def test_every_field_is_required(field):
    body = _reg()
    del body[field]
    with pytest.raises(PushTokenError):
        validate_registration(body)


@pytest.mark.parametrize("field,value", [
    ("platform", "android"), ("platform", "IPHONE "), ("env", "prod"),
    ("bundle_id", "com.someone.else"), ("token", "not-hex"), ("token", "ab" * 8),
    ("rev", -1), ("rev", "100"), ("rev", True), ("rev", 1.5),
])
def test_an_unknown_value_is_refused_not_coerced(field, value):
    body = _reg(**{field: value})
    if field == "platform" and value == "IPHONE ":
        assert validate_registration(body)["platform"] == "iphone"   # case/space only
        return
    with pytest.raises(PushTokenError):
        validate_registration(body)


def test_the_watch_topic_is_accepted_by_default():
    assert validate_registration(_reg(bundle_id="co.bizypro.OrchestraOS.watchkitapp",
                                      platform="watch"))["platform"] == "watch"


def test_an_install_with_its_own_build_sets_its_own_topics(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_PUSH_BUNDLE_IDS", "org.example.Mine, org.example.Mine.watch")
    assert validate_registration(_reg(bundle_id="org.example.Mine"))["bundle_id"] == "org.example.Mine"
    with pytest.raises(PushTokenError):
        validate_registration(_reg())


def test_a_put_missing_platform_is_400_and_stores_nothing(data_dir):
    body = _reg()
    del body["platform"]
    r = _put(PHONE, body)
    assert r.status == 400 and "platform" in _body(r)["error"]
    assert not (data_dir / "state" / "push-tokens.json").exists()


def test_bad_json_is_400(data_dir):
    assert _put(PHONE, ValueError("nope")).status == 400
    assert _delete(PHONE, ValueError("nope")).status == 400


# ---------------------------------------------------------------- registration

def test_put_files_the_token_under_the_CALLER_never_an_id_from_the_body(data_dir):
    r = _put(PHONE, _reg(principal="dev-other", device_id="dev-other"))
    assert r.status == 200
    assert _body(r) == {"ok": True, "registered": True, "delivery": "none"}
    rec = _stored(data_dir)[TOK]
    assert rec["principal"] == "dev-phone"
    assert {k: rec[k] for k in ("platform", "bundle_id", "env", "rev")} == {
        "platform": "iphone", "bundle_id": "co.bizypro.OrchestraOS", "env": "production", "rev": 100}


def test_the_store_file_is_owner_only(data_dir):
    _put(PHONE, _reg())
    mode = stat.S_IMODE(os.stat(data_dir / "state" / "push-tokens.json").st_mode)
    assert mode == 0o600


def test_put_is_idempotent(data_dir):
    _put(PHONE, _reg())
    r = _put(PHONE, _reg())
    assert _body(r)["registered"] is True
    assert list(_stored(data_dir)) == [TOK]


def test_an_older_rev_does_not_overwrite_a_newer_registration(data_dir):
    _put(PHONE, _reg(rev=200, env="production"))
    r = _put(PHONE, _reg(rev=100, env="sandbox"))
    assert _body(r)["registered"] is False
    assert _stored(data_dir)[TOK]["env"] == "production"


def test_a_token_re_registered_by_another_principal_moves_to_it(data_dir):
    _put(PHONE, _reg(rev=100))
    _put(OTHER, _reg(rev=101))
    assert _stored(data_dir)[TOK]["principal"] == "dev-other"


def test_the_shared_fleet_bearer_files_three_devices_apart_by_platform(data_dir):
    _put(FLEET, _reg(token=TOK, platform="iphone"))
    _put(FLEET, _reg(token=TOK2, platform="watch", bundle_id="co.bizypro.OrchestraOS.watchkitapp"))
    got = {r["platform"]: r["principal"] for r in _stored(data_dir).values()}
    assert got == {"iphone": "legacy", "watch": "legacy"}


def test_put_reports_the_configured_delivery(data_dir, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_PUSH_DELIVERY", "direct")
    assert _body(_put(PHONE, _reg()))["delivery"] == "direct"


# ---------------------------------------------------------------- forget

def test_forget_removes_the_callers_own_token(data_dir):
    _put(PHONE, _reg(rev=100))
    r = _delete(PHONE, {"token": TOK, "rev": 150})
    assert _body(r) == {"ok": True, "removed": True}
    assert _stored(data_dir) == {}


def test_a_STALE_forget_does_not_unregister_a_device_that_re_paired_since(data_dir):
    """Forget at rev 100, pair again (same app install => SAME APNs token) at rev 300, and
    the original Forget's request arrives late. The re-paired device must stay registered."""
    _put(PHONE, _reg(rev=300))
    r = _delete(PHONE, {"token": TOK, "rev": 100})
    assert _body(r) == {"ok": True, "removed": False}
    assert TOK in _stored(data_dir)


def test_forget_cannot_remove_another_principals_token(data_dir):
    _put(PHONE, _reg(rev=100))
    r = _delete(OTHER, {"token": TOK, "rev": 999})
    assert _body(r)["removed"] is False
    assert TOK in _stored(data_dir)


def test_forget_of_an_unknown_token_is_idempotent(data_dir):
    r = _delete(PHONE, {"token": TOK, "rev": 1})
    assert r.status == 200 and _body(r) == {"ok": True, "removed": False}


def test_forget_needs_a_rev(data_dir):
    _put(PHONE, _reg())
    assert _delete(PHONE, {"token": TOK}).status == 400
    assert TOK in _stored(data_dir)


# ---------------------------------------------------------------- tokens die with their device

def test_a_revoked_devices_tokens_are_pruned_from_the_send_list(data_dir):
    devices = DeviceStore(data_dir / "state" / "devices")
    live_id, _ = devices.mint("iphone", "read")
    gone_id, _ = devices.mint("old-ipad", "read")
    _put({"id": live_id, "scopes": ["read"]}, _reg(token=TOK))
    _put({"id": gone_id, "scopes": ["read"]}, _reg(token=TOK2, platform="ipad"))
    _put(FLEET, _reg(token="ef" * 32, platform="watch",
                     bundle_id="co.bizypro.OrchestraOS.watchkitapp"))
    devices.revoke(gone_id)
    targets = {r["token"] for r in G.push_targets()}
    assert targets == {TOK, "ef" * 32}
    assert TOK2 not in _stored(data_dir), "pruned, not just skipped"


def test_a_token_whose_device_record_is_gone_is_pruned(data_dir):
    _put({"id": "dev-never-existed", "scopes": ["read"]}, _reg())
    assert G.push_targets() == []
    assert _stored(data_dir) == {}


def test_apns_410_removes_the_token_without_a_rev(tmp_path):
    store = PushTokenStore(tmp_path / "p.json")
    store.register("dev-phone", validate_registration(_reg()))
    assert store.remove_unregistered(TOK) is True
    assert store.remove_unregistered(TOK) is False


# ---------------------------------------------------------------- scope + capabilities

def test_both_routes_are_self_report_reads_in_the_scope_table():
    assert G.ROUTE_SCOPES[("PUT", "/push/token")] == "read"
    assert G.ROUTE_SCOPES[("DELETE", "/push/token")] == "read"


def _call(method, headers):
    mw = G.scope_middleware_factory()

    class _Route:
        class resource:
            canonical = "/push/token"

    class _MI:
        route = _Route

    req = _Req(None, _reg())
    req.pop("principal")
    req.method, req.path, req.headers, req.match_info = method, "/push/token", headers, _MI
    reached = {}

    async def handler(r):
        reached["yes"] = True
        return G._json({"ok": True})

    return asyncio.run(mw(req, handler)), bool(reached)


def test_no_bearer_is_401_through_the_middleware(data_dir, monkeypatch):
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    resp, reached = _call("PUT", {})
    assert resp.status == 401 and not reached


def test_a_read_only_device_may_register_its_own_token(data_dir, monkeypatch):
    devices = DeviceStore(data_dir / "state" / "devices")
    _, token = devices.mint("iphone", "read")
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", lambda: devices)
    for method in ("PUT", "DELETE"):
        resp, reached = _call(method, {"Authorization": f"Bearer {token}"})
        assert reached and resp.status == 200, method


def _capabilities(monkeypatch):
    monkeypatch.setattr(G, "_capability_providers", lambda: [])
    monkeypatch.setattr(G, "_pending_count", lambda: 0)
    return _body(asyncio.run(G.handle_gateway_capabilities(_Req(FLEET, None))))


def test_capabilities_advertise_push_with_delivery_none_by_default(data_dir, monkeypatch):
    body = _capabilities(monkeypatch)
    assert "push" in body["features"]
    assert body["push"] == {"delivery": "none",
                            "bundle_ids": ["co.bizypro.OrchestraOS", "co.bizypro.OrchestraOS.watchkitapp"]}


@pytest.mark.parametrize("env,want", [("direct", "direct"), ("RELAY", "relay"),
                                      ("none", "none"), ("yes", "none"), ("", "none")])
def test_delivery_never_claims_a_channel_it_was_not_given(data_dir, monkeypatch, env, want):
    monkeypatch.setenv("ORCHESTRA_PUSH_DELIVERY", env)
    assert _capabilities(monkeypatch)["push"]["delivery"] == want
