"""PUT/DELETE /push/token and the store behind them.

The properties that would be expensive to get wrong after apps ship:
- no defaults: a registration missing platform / bundle_id / env is refused, not guessed;
- the client's rev orders everything: a stale Forget never unregisters a device that re-paired
  since (the APNs token survives Forget-then-pair-again), and a delayed older PUT never brings
  a forgotten device back;
- a token belongs to its owner: another device cannot take it over while the owner is paired;
- tokens die with their owner on POSITIVE evidence only: missing or unreadable data never
  deletes anything;
- a corrupt store is kept aside, never silently overwritten.
"""
import asyncio
import hashlib
import json
import os
import stat
import time

import pytest

import scripts.watch_gateway as G
from scripts import push_tokens as P
from scripts.device_tokens import DeviceStore
from scripts.push_tokens import (ALIVE, DEAD, UNKNOWN, PushTokenError, PushTokenStore,
                                 validate_registration)

TOK = "ab" * 32
TOK2 = "cd" * 32
TOK3 = "ef" * 32
FLEET_BEARER = "fleet-token-value"
NOW_MS = int(time.time() * 1000)
FAR_FUTURE = NOW_MS + 2 * 24 * 3600 * 1000


def _fp(bearer):
    return "legacy:" + hashlib.sha256(bearer.encode()).hexdigest()[:16]


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


FLEET = {"id": "legacy", "label": "legacy-fleet-token", "scopes": ["*"]}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.delenv("ORCHESTRA_PUSH_DELIVERY", raising=False)
    monkeypatch.delenv("ORCHESTRA_PUSH_BUNDLE_IDS", raising=False)
    monkeypatch.setattr(G, "gateway_token", lambda: FLEET_BEARER)
    return tmp_path


@pytest.fixture
def devices(data_dir):
    return DeviceStore(data_dir / "state" / "devices")


def _device(devices, label="iphone"):
    dev_id, _ = devices.mint(label, "read")
    return {"id": dev_id, "label": label, "scopes": ["read"]}


def _stored(data_dir):
    return json.loads((data_dir / "state" / "push-tokens.json").read_text())


def _live_records(data_dir):
    return {t: r for t, r in _stored(data_dir).items() if not r.get("deleted")}


# ---------------------------------------------------------------- validation: no defaults

@pytest.mark.parametrize("field", ["platform", "bundle_id", "env", "token", "rev"])
def test_every_field_is_required(field):
    body = _reg()
    del body[field]
    with pytest.raises(PushTokenError):
        validate_registration(body)


@pytest.mark.parametrize("field,value", [
    ("platform", "android"), ("env", "prod"), ("bundle_id", "com.someone.else"),
    ("token", "not-hex"), ("token", "ab" * 16), ("rev", -1), ("rev", "100"), ("rev", True),
    ("rev", 1.5), ("rev", FAR_FUTURE), ("rev", 2 ** 53), ("rev", 10 ** 30),
])
def test_an_unknown_value_is_refused_not_coerced(field, value):
    with pytest.raises(PushTokenError):
        validate_registration(_reg(**{field: value}))


def test_case_and_spaces_are_normalised_and_the_token_is_stored_lowercase():
    got = validate_registration(_reg(platform="IPHONE ", token="AB" * 32))
    assert got["platform"] == "iphone" and got["token"] == TOK


def test_rev_may_run_a_little_ahead_of_this_clock_but_not_a_day():
    """rev is ms since epoch; a phone's clock may be ahead of the gateway's, but a far-future
    rev would pin a token (or a Forget) for good."""
    assert validate_registration(_reg(rev=NOW_MS + 3600 * 1000))["rev"] == NOW_MS + 3600 * 1000
    with pytest.raises(PushTokenError):
        validate_registration(_reg(rev=FAR_FUTURE))


def test_the_watch_topic_is_accepted_by_default():
    assert validate_registration(_reg(bundle_id="co.bizypro.OrchestraOS.watchkitapp",
                                      platform="watch"))["platform"] == "watch"


def test_an_install_with_its_own_build_sets_its_own_topics(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_PUSH_BUNDLE_IDS", "org.example.Mine, org.example.Mine.watch")
    assert validate_registration(_reg(bundle_id="org.example.Mine"))["bundle_id"] == "org.example.Mine"
    with pytest.raises(PushTokenError):
        validate_registration(_reg())


def test_a_put_missing_platform_is_400_and_stores_nothing(data_dir, devices):
    body = _reg()
    del body["platform"]
    r = _put(_device(devices), body)
    assert r.status == 400 and "platform" in _body(r)["error"]
    assert not (data_dir / "state" / "push-tokens.json").exists()


def test_bad_json_is_400(data_dir, devices):
    phone = _device(devices)
    assert _put(phone, ValueError("nope")).status == 400
    assert _delete(phone, ValueError("nope")).status == 400


# ---------------------------------------------------------------- registration

def test_put_files_the_token_under_the_CALLER_never_an_id_from_the_body(data_dir, devices):
    phone = _device(devices)
    r = _put(phone, _reg(principal="someone-else", device_id="someone-else"))
    assert r.status == 200
    assert _body(r) == {"ok": True, "registered": True, "delivery": "none"}
    rec = _stored(data_dir)[TOK]
    assert rec["principal"] == phone["id"]
    assert {k: rec[k] for k in ("platform", "bundle_id", "env", "rev")} == {
        "platform": "iphone", "bundle_id": "co.bizypro.OrchestraOS", "env": "production", "rev": 100}


def test_the_store_file_is_owner_only(data_dir, devices):
    _put(_device(devices), _reg())
    assert stat.S_IMODE(os.stat(data_dir / "state" / "push-tokens.json").st_mode) == 0o600


def test_a_leftover_wide_tmp_file_cannot_widen_the_store(data_dir, devices):
    state = data_dir / "state"
    state.mkdir(parents=True)
    tmp = state / "push-tokens.tmp"
    tmp.write_text("{}")
    os.chmod(tmp, 0o644)
    _put(_device(devices), _reg())
    assert stat.S_IMODE(os.stat(state / "push-tokens.json").st_mode) == 0o600


def test_a_retry_at_the_same_rev_by_the_same_caller_is_idempotent(data_dir, devices):
    phone = _device(devices)
    _put(phone, _reg())
    assert _body(_put(phone, _reg()))["registered"] is True
    assert list(_stored(data_dir)) == [TOK]


def test_an_older_rev_does_not_overwrite_a_newer_registration(data_dir, devices):
    phone = _device(devices)
    _put(phone, _reg(rev=200, env="production"))
    assert _body(_put(phone, _reg(rev=100, env="sandbox"))) == {
        "ok": True, "registered": False, "delivery": "none", "reason": "stale_rev"}
    assert _stored(data_dir)[TOK]["env"] == "production"


def test_the_shared_fleet_bearer_files_devices_apart_by_platform(data_dir):
    _put(FLEET, _reg(token=TOK, platform="iphone"))
    _put(FLEET, _reg(token=TOK2, platform="watch", bundle_id="co.bizypro.OrchestraOS.watchkitapp"))
    got = {r["platform"]: r["principal"] for r in _stored(data_dir).values()}
    assert got == {"iphone": _fp(FLEET_BEARER), "watch": _fp(FLEET_BEARER)}


def test_put_reports_the_configured_delivery(data_dir, devices, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_PUSH_DELIVERY", "direct")
    assert _body(_put(_device(devices), _reg()))["delivery"] == "direct"


# ---------------------------------------------------------------- a token belongs to its owner

def test_another_device_cannot_take_a_paired_devices_token_even_with_a_huge_rev(data_dir, devices):
    a, b = _device(devices, "phone-a"), _device(devices, "phone-b")
    _put(a, _reg(rev=5))
    assert _put(b, _reg(rev=FAR_FUTURE)).status == 400                   # cannot pin
    got = _body(_put(b, _reg(rev=NOW_MS)))
    assert got["registered"] is False and got["reason"] == "owned"      # A is still paired
    assert _stored(data_dir)[TOK]["principal"] == a["id"]
    assert _body(_put(a, _reg(rev=6)))["registered"] is True             # A is not locked out
    assert _body(_delete(a, {"token": TOK, "rev": 7}))["removed"] is True


def test_a_revoked_devices_token_can_be_taken_over(data_dir, devices):
    a, b = _device(devices, "old"), _device(devices, "new")
    _put(a, _reg(rev=5))
    devices.revoke(a["id"])
    assert _body(_put(b, _reg(rev=6)))["registered"] is True
    assert _stored(data_dir)[TOK]["principal"] == b["id"]


def test_an_unreadable_owner_keeps_its_token(data_dir, devices):
    """UNKNOWN is not DEAD: an owner whose record we can't read keeps the token."""
    a, b = _device(devices, "a"), _device(devices, "b")
    _put(a, _reg(rev=5))
    (devices.dir / f"{a['id']}.json").write_text("{corrupt")
    assert _body(_put(b, _reg(rev=6)))["registered"] is False


def test_a_phone_can_move_off_the_fleet_bearer_onto_its_own_token(data_dir, devices):
    _put(FLEET, _reg(rev=5))
    phone = _device(devices)
    assert _body(_put(phone, _reg(rev=5)))["registered"] is False        # needs a newer rev
    assert _body(_put(phone, _reg(rev=6)))["registered"] is True
    assert _stored(data_dir)[TOK]["principal"] == phone["id"]


def test_forget_cannot_remove_another_principals_token(data_dir, devices):
    a, b = _device(devices, "a"), _device(devices, "b")
    _put(a, _reg(rev=100))
    assert _body(_delete(b, {"token": TOK, "rev": 999}))["removed"] is False
    assert TOK in _live_records(data_dir)


# ---------------------------------------------------------------- forget

def test_forget_removes_the_callers_own_token(data_dir, devices):
    phone = _device(devices)
    _put(phone, _reg(rev=100))
    assert _body(_delete(phone, {"token": TOK, "rev": 150})) == {"ok": True, "removed": True}
    assert _live_records(data_dir) == {}


def test_a_STALE_forget_does_not_unregister_a_device_that_re_paired_since(data_dir, devices):
    """Forget at rev 100, pair again (same app install => SAME APNs token) at rev 300, and
    the original Forget's request arrives late. The re-paired device must stay registered."""
    phone = _device(devices)
    _put(phone, _reg(rev=300))
    assert _body(_delete(phone, {"token": TOK, "rev": 100})) == {"ok": True, "removed": False}
    assert TOK in _live_records(data_dir)


def test_a_DELAYED_older_put_cannot_bring_a_forgotten_device_back(data_dir, devices):
    phone = _device(devices)
    _put(phone, _reg(rev=1))
    _delete(phone, {"token": TOK, "rev": 2})
    assert _body(_put(phone, _reg(rev=1)))["reason"] == "forgotten"
    assert _body(_put(phone, _reg(rev=2)))["registered"] is False
    assert _live_records(data_dir) == {}
    assert _body(_put(phone, _reg(rev=3)))["registered"] is True          # a real re-pair


def test_a_forget_is_remembered_for_30_days_then_dropped(data_dir, devices, monkeypatch):
    phone = _device(devices)
    _put(phone, _reg(rev=1))
    _delete(phone, {"token": TOK, "rev": 2})
    now = __import__("time").time()
    monkeypatch.setattr(P.time, "time", lambda: now + P.TOMBSTONE_TTL_S + 1)
    _put(phone, _reg(token=TOK2, rev=5))                                  # any write expires it
    assert TOK not in _stored(data_dir)


def test_forget_of_an_unknown_token_is_idempotent(data_dir, devices):
    r = _delete(_device(devices), {"token": TOK, "rev": 1})
    assert r.status == 200 and _body(r) == {"ok": True, "removed": False}


def test_forget_needs_a_rev(data_dir, devices):
    phone = _device(devices)
    _put(phone, _reg())
    assert _delete(phone, {"token": TOK}).status == 400
    assert TOK in _live_records(data_dir)


# ---------------------------------------------------------------- bounded

def test_each_device_holds_at_most_8_tokens_and_the_oldest_is_evicted(data_dir, devices):
    phone = _device(devices)
    toks = [f"{i:02x}" * 32 for i in range(10)]
    for i, t in enumerate(toks):
        assert _body(_put(phone, _reg(token=t, rev=i + 1)))["registered"] is True
    kept = set(_live_records(data_dir))
    assert len(kept) == P.MAX_PER_DEVICE and kept == set(toks[2:])


def test_a_put_forget_loop_cannot_grow_the_store_past_32_tombstones(data_dir, devices):
    phone = _device(devices)
    for i in range(50):
        t = f"{i:02x}" * 32
        _put(phone, _reg(token=t, rev=2 * i + 1))
        _delete(phone, {"token": t, "rev": 2 * i + 2})
    tombs = [r for r in _stored(data_dir).values() if r.get("deleted")]
    assert len(tombs) == P.MAX_TOMBSTONES


def test_the_fleet_bearer_holds_up_to_16(data_dir):
    for i in range(20):
        _put(FLEET, _reg(token=f"{i:02x}" * 32, rev=i + 1))
    assert len(_live_records(data_dir)) == P.MAX_PER_LEGACY


# ---------------------------------------------------------------- tokens die with their owner

def test_a_revoked_devices_tokens_are_pruned_from_the_send_list(data_dir, devices):
    live, gone = _device(devices, "iphone"), _device(devices, "old-ipad")
    _put(live, _reg(token=TOK))
    _put(gone, _reg(token=TOK2, platform="ipad"))
    _put(FLEET, _reg(token=TOK3, platform="watch", bundle_id="co.bizypro.OrchestraOS.watchkitapp"))
    devices.revoke(gone["id"])
    assert {r["token"] for r in G.push_targets()} == {TOK, TOK3}
    assert TOK2 not in _stored(data_dir), "pruned, not just skipped"


def test_a_RECORDED_rotation_kills_the_old_bearers_tokens(data_dir, monkeypatch):
    _put(FLEET, _reg())
    G._push_store().retire_bearer(FLEET_BEARER)
    monkeypatch.setattr(G, "gateway_token", lambda: "a-new-bearer")
    assert G.push_targets() == []
    assert TOK not in _stored(data_dir)


def test_a_merely_DIFFERENT_bearer_is_not_a_rotation(data_dir, monkeypatch):
    """The round-2 blocker: a sender that reads another token file (other HOME, other env) sees
    a different bearer. That is not evidence of a rotation, so nothing may be pruned."""
    _put(FLEET, _reg())
    monkeypatch.setattr(G, "gateway_token", lambda: "a-bearer-from-another-token-file")
    assert G.push_targets() == []
    assert TOK in _stored(data_dir), "skipped, but kept"


def test_rotate_fleet_token_records_the_old_bearer_as_retired(data_dir, monkeypatch):
    from orchestra_cli.pair_cmd import run_rotate_fleet_token
    token_file = data_dir / "state" / "watch-gateway-token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(FLEET_BEARER + "\n")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(token_file))

    class _Args:
        minted_by = None
        revoke_only = False

    assert run_rotate_fleet_token(_Args(), out=lambda *_: None) == 0
    assert _fp(FLEET_BEARER) in G._push_store().retired_fingerprints()
    assert token_file.read_text().strip() != FLEET_BEARER


def test_an_unset_fleet_bearer_is_unknown_not_a_rotation(data_dir, monkeypatch):
    _put(FLEET, _reg())
    monkeypatch.setattr(G, "gateway_token", lambda: None)
    assert G.push_targets() == []
    assert TOK in _stored(data_dir), "kept: we could not tell"


@pytest.mark.parametrize("damage", ["missing_dir", "unreadable_dir", "corrupt_file", "missing_file"])
def test_missing_or_unreadable_device_data_NEVER_prunes(data_dir, devices, damage):
    """The review's blocker: an empty or unreadable device list must not unregister everyone."""
    phone = _device(devices)
    _put(phone, _reg())
    path = devices.dir / f"{phone['id']}.json"
    if damage == "missing_dir":
        for f in devices.dir.iterdir():
            f.unlink()
        devices.dir.rmdir()
    elif damage == "unreadable_dir":
        os.chmod(devices.dir, 0)
    elif damage == "corrupt_file":
        path.write_text("{corrupt")
    elif damage == "missing_file":
        path.unlink()
    try:
        targets = G.push_targets()
    finally:
        if devices.dir.exists():
            os.chmod(devices.dir, 0o700)
    if damage == "unreadable_dir" and os.geteuid() == 0:
        pytest.skip("root reads a 000 dir")
    assert targets == []
    assert TOK in _stored(data_dir), "skipped, but kept"


def test_owner_state_is_positive_evidence_only(data_dir, devices):
    phone = _device(devices)
    assert G._push_owner_state(phone["id"]) == ALIVE
    devices.revoke(phone["id"])
    assert G._push_owner_state(phone["id"]) == DEAD
    assert G._push_owner_state("../../etc/passwd") == UNKNOWN
    assert G._push_owner_state("0123456789abcdef") == UNKNOWN            # no such file
    assert G._push_owner_state(_fp(FLEET_BEARER)) == ALIVE
    assert G._push_owner_state(_fp("old-bearer")) == UNKNOWN
    G._push_store().retire_bearer("old-bearer")
    assert G._push_owner_state(_fp("old-bearer")) == DEAD


def test_apns_410_removes_the_token_without_a_rev(tmp_path):
    store = PushTokenStore(tmp_path / "p.json")
    assert store.register("0123456789abcdef", validate_registration(_reg()), lambda _: ALIVE) is None
    assert store.remove_unregistered(TOK) is True
    assert store.remove_unregistered(TOK) is False


# ---------------------------------------------------------------- a corrupt store is kept aside

@pytest.mark.parametrize("content", ["{corrupt", "[1, 2]", "null"])
def test_a_corrupt_store_is_moved_aside_never_silently_overwritten(data_dir, devices, content):
    state = data_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "push-tokens.json").write_text(content)
    _put(_device(devices), _reg())
    aside = list(state.glob("push-tokens.json.corrupt-*"))
    assert len(aside) == 1 and aside[0].read_text() == content
    assert TOK in _stored(data_dir)


def test_a_tampered_stored_rev_is_not_a_500(data_dir, devices):
    phone = _device(devices)
    _put(phone, _reg())
    path = data_dir / "state" / "push-tokens.json"
    rec = json.loads(path.read_text())
    rec[TOK]["rev"] = "garbage"
    path.write_text(json.dumps(rec))
    assert _put(phone, _reg(rev=101)).status == 200


def test_non_object_records_are_ignored_not_a_500(data_dir, devices):
    state = data_dir / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "push-tokens.json").write_text(json.dumps({TOK2: "junk"}))
    assert _put(_device(devices), _reg()).status == 200


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


def test_no_bearer_is_401_through_the_middleware(data_dir):
    resp, reached = _call("PUT", {})
    assert resp.status == 401 and not reached


def test_a_read_only_device_may_register_its_own_token(data_dir, devices, monkeypatch):
    _, token = devices.mint("iphone", "read")
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


def test_a_token_someone_else_holds_always_answers_owned_never_its_rev(data_dir, devices):
    """No oracle: stale_rev vs owned would let a device binary-search when the owner last
    registered."""
    a, b = _device(devices, "a"), _device(devices, "b")
    _put(a, _reg(rev=NOW_MS - 1000))
    for rev in (1, NOW_MS - 1000, NOW_MS):
        assert _body(_put(b, _reg(rev=rev)))["reason"] == "owned", rev


def test_a_corrupt_retired_file_is_moved_aside_not_overwritten(data_dir):
    store = G._push_store()
    store.retired_path.parent.mkdir(parents=True, exist_ok=True)
    store.retired_path.write_text("{corrupt")
    assert store.retired_fingerprints() == set()
    assert list(store.retired_path.parent.glob("push-tokens.retired-bearers.json.corrupt-*"))


def test_a_retire_failure_never_aborts_the_rotation(data_dir, monkeypatch):
    from orchestra_cli.pair_cmd import run_rotate_fleet_token
    token_file = data_dir / "state" / "watch-gateway-token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(FLEET_BEARER + "\n")
    monkeypatch.setenv("WATCH_GATEWAY_TOKEN_FILE", str(token_file))

    def boom(self, bearer):
        raise OSError("disk full")

    monkeypatch.setattr(P.PushTokenStore, "retire_bearer", boom)

    class _Args:
        minted_by = None
        revoke_only = False

    said = []
    assert run_rotate_fleet_token(_Args(), out=said.append) == 0
    assert token_file.read_text().strip() != FLEET_BEARER, "the bearer still rotated"
    assert any("could not record" in s for s in said)
