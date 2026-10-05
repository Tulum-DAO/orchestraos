"""The gateway's authorization is ONE table and ONE middleware, and it defaults to DENY.

The build-failing coverage test below is the point of this file. Before this, 48 handlers each
re-checked a single fleet-wide bearer, so a new route got full gateway power the moment its
author copied that line. A scope table only helps if forgetting to edit it is LOUD.
"""
import json

import pytest

import scripts.watch_gateway as G
from scripts.device_tokens import VERBS


# ---------------------------------------------------------------- coverage (the real guard)

def test_every_route_declares_a_scope():
    """Adding a route without deciding its scope must FAIL THE BUILD.

    Not a style check: default-deny means an undeclared route 403s at runtime, which someone
    would eventually 'fix' by loosening the middleware. Failing here, at the moment the route
    is added, is what keeps the decision where it belongs."""
    app = G.build_app()
    missing = []
    for resource in app.router.resources():
        canonical = resource.canonical
        for route in resource:
            method = route.method.upper()
            if method in ("HEAD", "OPTIONS", "*"):
                continue
            if (method, canonical) not in G.ROUTE_SCOPES:
                missing.append(f"{method} {canonical}")
    assert not missing, (
        "these routes declare no scope in ROUTE_SCOPES, so they are refused at runtime "
        "(default-deny). Classify each one: " + ", ".join(sorted(missing)))


def test_the_table_has_no_entry_for_a_route_that_does_not_exist():
    """A stale entry is a scope nobody enforces and a reader's false reassurance."""
    app = G.build_app()
    real = set()
    for resource in app.router.resources():
        for route in resource:
            real.add((route.method.upper(), resource.canonical))
    stale = [f"{m} {p}" for (m, p) in G.ROUTE_SCOPES if (m, p) not in real]
    assert not stale, "ROUTE_SCOPES names routes that no longer exist: " + ", ".join(sorted(stale))


def test_every_declared_scope_is_a_known_verb_or_public():
    bad = {k: v for k, v in G.ROUTE_SCOPES.items() if v is not None and v not in VERBS}
    assert not bad, f"unknown verbs in the table: {bad}"


def test_the_only_public_routes_are_the_three_that_must_be():
    """Each one is unauthenticated BY NECESSITY, and that list should never grow quietly."""
    public = {f"{m} {p}" for (m, p), v in G.ROUTE_SCOPES.items() if v is None}
    assert public == {"GET /gateway/identity", "POST /pair/exchange", "GET /health"}, public


def test_the_dangerous_verbs_are_not_filed_under_read():
    """A regression guard with teeth: if someone reclassifies key-pressing or provider spend
    as `read`, every paired device gets it, because `read` is in every sane default."""
    for route in (("POST", "/agent-key"), ("POST", "/agent-interrupt"), ("POST", "/agent-suggest")):
        assert G.ROUTE_SCOPES[route] == "inject", route
    for route in (("POST", "/arturo/text"), ("GET", "/live")):
        assert G.ROUTE_SCOPES[route] == "voice", route
    # PTT is its own verb now (narrower than voice), but it must never fall to `read` either —
    # it spends provider credit per turn.
    assert G.ROUTE_SCOPES[("POST", "/arturo/ptt")] == "ptt"
    for route in (("POST", "/approval-answers"), ("POST", "/approvals/{id}/discard")):
        assert G.ROUTE_SCOPES[route] == "approve", route
    # `approve`, FINAL per gm 08:34Z. Safe only because the walk refuses to press a key with no
    # menu on screen (test_menu_capture_guard.py) — the scope and that guard are ONE decision.
    # Also load-bearing: the headset hydrates multi-part menus through it at this scope.
    assert G.ROUTE_SCOPES[("POST", "/agent-menu-capture")] == "approve"


# ---------------------------------------------------------------- required_scope

class _FakeResource:
    def __init__(self, canonical):
        self.canonical = canonical


class _FakeRoute:
    def __init__(self, canonical):
        self.resource = _FakeResource(canonical)


class _FakeMatchInfo:
    def __init__(self, canonical):
        self.route = _FakeRoute(canonical) if canonical is not None else None


class _FakeRequest(dict):
    def __init__(self, method, canonical, headers=None):
        super().__init__()
        self.method = method
        self.path = canonical or "/unmatched"
        self.headers = headers or {}
        self.match_info = _FakeMatchInfo(canonical)


def test_required_scope_reads_the_table():
    assert G.required_scope(_FakeRequest("GET", "/agents")) == "read"
    assert G.required_scope(_FakeRequest("POST", "/agent-key")) == "inject"
    assert G.required_scope(_FakeRequest("GET", "/gateway/identity")) is None


def test_an_undeclared_route_raises_so_the_middleware_can_refuse():
    with pytest.raises(KeyError):
        G.required_scope(_FakeRequest("POST", "/a-route-nobody-classified"))


def test_an_unmatched_path_is_left_alone_to_404():
    """A 404 must not be reported as a 403 — that would turn every typo into a security event
    and tell a prober that the path exists."""
    assert G.required_scope(_FakeRequest("GET", None)) is None


# ---------------------------------------------------------------- principal resolution

def test_the_fleet_token_resolves_to_star_so_phone_and_watch_are_unaffected(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    p = G.resolve_principal(_FakeRequest("GET", "/agents",
                                         {"Authorization": "Bearer fleet-token-value"}))
    assert p and p["scopes"] == ["*"] and p["label"] == "legacy-fleet-token"


def test_a_device_token_resolves_to_its_own_scopes(tmp_path, monkeypatch):
    from scripts.device_tokens import DeviceStore
    store = DeviceStore(tmp_path / "devices")
    dev_id, token = store.mint("quest", ["read", "approve", "message"])
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", lambda: store)
    p = G.resolve_principal(_FakeRequest("GET", "/agents", {"Authorization": f"Bearer {token}"}))
    assert p and p["id"] == dev_id and p["scopes"] == ["read", "approve", "message"]


def test_a_revoked_device_resolves_to_nobody(tmp_path, monkeypatch):
    from scripts.device_tokens import DeviceStore
    store = DeviceStore(tmp_path / "devices")
    dev_id, token = store.mint("quest", ["read"])
    store.revoke(dev_id)
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", lambda: store)
    assert G.resolve_principal(_FakeRequest("GET", "/agents",
                                            {"Authorization": f"Bearer {token}"})) is None


def test_no_bearer_and_a_wrong_bearer_both_resolve_to_nobody(tmp_path, monkeypatch):
    from scripts.device_tokens import DeviceStore
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", lambda: DeviceStore(tmp_path / "devices"))
    assert G.resolve_principal(_FakeRequest("GET", "/agents")) is None
    assert G.resolve_principal(_FakeRequest("GET", "/agents", {"Authorization": "Bearer nope"})) is None
    assert G.resolve_principal(_FakeRequest("GET", "/agents", {"Authorization": "Basic x"})) is None


def test_an_unreadable_device_store_authenticates_nobody(monkeypatch):
    """Fail closed. An exception reading the store must not become a free pass."""
    def _boom():
        raise OSError("disk gone")
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", _boom)
    assert G.resolve_principal(_FakeRequest("GET", "/agents",
                                            {"Authorization": "Bearer anything"})) is None


def test_authorized_uses_the_resolved_principal_when_the_middleware_ran():
    req = _FakeRequest("GET", "/agents")
    req["principal"] = {"id": "d1", "label": "quest", "scopes": ["read"]}
    assert G._authorized(req) is True
    req2 = _FakeRequest("GET", "/agents")
    req2["principal"] = None
    assert G._authorized(req2) is False


# ---------------------------------------------------------------- middleware decisions

def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def _call(method, canonical, headers=None):
    """Drive the middleware with an inert handler, so these assert the AUTHORIZATION
    decision and nothing about what a handler would have done."""
    mw = G.scope_middleware_factory()
    req = _FakeRequest(method, canonical, headers)
    reached = {"handler": False}

    async def handler(r):
        reached["handler"] = True
        return G._json({"ok": True})

    resp = _run(mw(req, handler))
    return resp, reached["handler"], req


def _with_store(monkeypatch, tmp_path, scopes):
    from scripts.device_tokens import DeviceStore
    store = DeviceStore(tmp_path / "devices")
    dev_id, token = store.mint("quest", scopes)
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_device_store", lambda: store)
    return store, dev_id, token


def test_a_public_route_runs_with_no_bearer_at_all(monkeypatch, tmp_path):
    _with_store(monkeypatch, tmp_path, ["read"])
    resp, reached, req = _call("GET", "/gateway/identity")
    assert reached and resp.status == 200
    assert req["principal"] is None       # resolved, and recorded as anonymous


def test_an_undeclared_route_is_refused_403_and_never_reaches_the_handler(monkeypatch, tmp_path):
    _with_store(monkeypatch, tmp_path, ["read"])
    resp, reached, _ = _call("POST", "/a-route-nobody-classified",
                             {"Authorization": "Bearer fleet-token-value"})
    assert resp.status == 403 and not reached, "default-deny: even the fleet token cannot call it"


def test_a_scoped_route_with_no_bearer_is_401(monkeypatch, tmp_path):
    _with_store(monkeypatch, tmp_path, ["read"])
    resp, reached, _ = _call("GET", "/agents")
    assert resp.status == 401 and not reached


def test_a_device_inside_its_scope_is_allowed(monkeypatch, tmp_path):
    _, _, token = _with_store(monkeypatch, tmp_path, ["read", "approve", "message"])
    resp, reached, req = _call("GET", "/agents", {"Authorization": f"Bearer {token}"})
    assert reached and resp.status == 200
    assert req["principal"]["label"] == "quest"


def test_a_device_OUTSIDE_its_scope_is_403_server_side(monkeypatch, tmp_path):
    """The whole point. The Quest's 'no inject' stops being a UI promise."""
    _, _, token = _with_store(monkeypatch, tmp_path, ["read", "approve", "message"])
    resp, reached, _ = _call("POST", "/agent-key", {"Authorization": f"Bearer {token}"})
    assert resp.status == 403 and not reached


def test_a_read_approve_message_device_can_approve_but_not_spend_provider_money(monkeypatch, tmp_path):
    _, _, token = _with_store(monkeypatch, tmp_path, ["read", "approve", "message"])
    ok, reached_ok, _ = _call("POST", "/approval-answers", {"Authorization": f"Bearer {token}"})
    assert reached_ok and ok.status == 200
    deny, reached_deny, _ = _call("POST", "/arturo/ptt", {"Authorization": f"Bearer {token}"})
    assert deny.status == 403 and not reached_deny


def test_a_revoked_device_is_401_on_the_very_next_request(monkeypatch, tmp_path):
    """Revocation proven by effect: allowed, revoked, refused — same token."""
    store, dev_id, token = _with_store(monkeypatch, tmp_path, ["read"])
    first, reached_first, _ = _call("GET", "/agents", {"Authorization": f"Bearer {token}"})
    assert reached_first and first.status == 200
    assert store.revoke(dev_id) is True
    after, reached_after, _ = _call("GET", "/agents", {"Authorization": f"Bearer {token}"})
    assert after.status == 401 and not reached_after


def test_the_fleet_token_still_reaches_every_declared_route(monkeypatch, tmp_path):
    """Back-compat for Shaw's phone and watch: no route regresses while he migrates."""
    _with_store(monkeypatch, tmp_path, ["read"])
    hdr = {"Authorization": "Bearer fleet-token-value"}
    for (method, canonical), verb in G.ROUTE_SCOPES.items():
        resp, reached, _ = _call(method, canonical, hdr)
        assert reached, f"fleet token was refused on {method} {canonical}"
        assert resp.status == 200


def test_a_403_names_the_scope_it_wanted_without_leaking_anything_else(monkeypatch, tmp_path):
    _, _, token = _with_store(monkeypatch, tmp_path, ["read"])
    resp, _, _ = _call("POST", "/agent-key", {"Authorization": f"Bearer {token}"})
    body = json.loads(resp.body.decode())
    assert body["needed_scope"] == "inject" and body["scopes"] == ["read"]
    assert "token" not in resp.body.decode()


# ---------------------------------------------------------------- device provenance (gm (d))

def test_the_answering_device_is_derived_from_the_principal_not_the_body():
    """Same trust boundary as `answered_by`: a client that could name its own device could
    name somebody else's, and the field's whole value is that it is not the client's word."""
    req = _FakeRequest("POST", "/approval-answers")
    req["principal"] = {"id": "abc123", "label": "quest-headset", "scopes": ["approve"]}
    assert G._answer_device(req) == "abc123:quest-headset"


def test_the_fleet_bearer_records_no_device_rather_than_a_made_up_one():
    """It identifies no device. Inventing a label would be a fabrication that reads like
    evidence in an audit."""
    req = _FakeRequest("POST", "/approval-answers")
    req["principal"] = {"id": "legacy", "label": "legacy-fleet-token", "scopes": ["*"]}
    assert G._answer_device(req) is None


def test_an_anonymous_request_records_no_device():
    req = _FakeRequest("POST", "/approval-answers")
    req["principal"] = None
    assert G._answer_device(req) is None


def test_a_device_with_no_label_still_records_its_id():
    req = _FakeRequest("POST", "/approval-answers")
    req["principal"] = {"id": "abc123", "label": "", "scopes": ["approve"]}
    assert G._answer_device(req) == "abc123"


def test_record_answer_persists_the_device_when_the_column_is_armed(tmp_path, monkeypatch):
    """The column rides the arming gate, so this proves BOTH paths: armed records it,
    unarmed still lands the answer (fail-open — a missing tag never blocks an answer)."""
    from scripts.approval_schema import GATED_MIGRATIONS, ApprovalStore

    # An EXPLICIT db_path and HOME, like the rest of this suite: resolving the store from
    # ambient env made these two pass alone and fail in the full run, because another test
    # had already pointed the default elsewhere.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("APPROVAL_DDL_ARMED", ",".join(m["id"] for m in GATED_MIGRATIONS))
    db = str(tmp_path / "tasks.db")
    store = ApprovalStore(db_path=db); store.migrate()
    rid = store.create(from_agent="t", question="q?", worker_kind="pane",
                       options=["a", "b"], op_key="k1")
    assert store.record_answer(rid, "a", "a", surface="watch", answered_by="operator",
                               device="abc123:quest-headset") is True
    row = dict(store.get(rid))
    assert row["status"] == "answered"
    assert row.get("answer_device") == "abc123:quest-headset", row.get("answer_device")


def test_an_unarmed_device_column_still_lets_the_answer_land(tmp_path, monkeypatch):
    from scripts.approval_schema import ApprovalStore
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("APPROVAL_DDL_ARMED", raising=False)
    store = ApprovalStore(db_path=str(tmp_path / "tasks.db")); store.migrate()
    rid = store.create(from_agent="t", question="q?", worker_kind="pane",
                       options=["a", "b"], op_key="k2")
    assert store.record_answer(rid, "a", "a", device="abc123:quest") is True
    assert store.get(rid)["status"] == "answered", "an unarmed provenance column must never block"


# ---------------------------------------------------------------- capabilities wire format

def test_capabilities_expands_the_wildcard_so_a_client_never_special_cases_it(monkeypatch, tmp_path):
    """The fleet bearer holds ['*'] internally. A client reading `scopes` as a verb list would
    conclude it may do NOTHING — quest-orchestra reported exactly that reading. The wire format
    is therefore always the concrete effective verbs."""
    import asyncio
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_capability_providers", lambda: [{"id": "claude", "kind": "text"}])
    monkeypatch.setattr(G, "_pending_count", lambda: 0)
    req = _FakeRequest("GET", "/gateway/capabilities")
    req["principal"] = {"id": "legacy", "label": "legacy-fleet-token", "scopes": ["*"]}
    resp = asyncio.new_event_loop().run_until_complete(G.handle_gateway_capabilities(req))
    body = json.loads(resp.body.decode())
    assert "*" not in body["scopes"]
    assert set(body["scopes"]) == set(VERBS)
    assert body["all_scopes"] is True, "but it must still be identifiable as the unscoped one"


def test_capabilities_reports_a_scoped_device_verbatim(monkeypatch, tmp_path):
    import asyncio
    monkeypatch.setattr(G, "gateway_token", lambda: "fleet-token-value")
    monkeypatch.setattr(G, "_capability_providers", lambda: [])
    monkeypatch.setattr(G, "_pending_count", lambda: 3)
    req = _FakeRequest("GET", "/gateway/capabilities")
    req["principal"] = {"id": "d1", "label": "quest-headset",
                        "scopes": ["read", "approve", "message"]}
    resp = asyncio.new_event_loop().run_until_complete(G.handle_gateway_capabilities(req))
    body = json.loads(resp.body.decode())
    assert body["scopes"] == ["read", "approve", "message"]
    assert body["all_scopes"] is False
    assert body["device"] == {"id": "d1", "label": "quest-headset"}
    assert body["verbs"] == list(VERBS), "the full vocabulary, so a client can render unheld verbs"


# ---------------------------------------------------------------- ptt is narrower than voice

def test_push_to_talk_needs_only_ptt_not_voice():
    """A headset should be able to speak one turn without holding the richer voice surfaces."""
    for route in (("POST", "/arturo/ptt"), ("POST", "/arturo/ptt/stream/audio"),
                  ("GET", "/arturo/ptt/stream/events"), ("POST", "/arturo/ptt/stream/end")):
        assert G.ROUTE_SCOPES[route] == "ptt", route


def test_the_FLEET_WIDE_voice_config_writes_require_admin_not_voice():
    """PUT /arturo/ptt/vendor and PUT /arturo/ptt/voice proxy to ONE loopback Arturo service and
    take effect on the NEXT conversation — they are process-level settings for the whole box.
    That is administration of voice, not use of it.

    Raised to `admin` rather than merely adding `ptt`, because this closes the hole for EVERY
    holder of `voice`, present and future. A narrow scope for one device would have left the
    fleet-wide switch reachable by the next device granted `voice`."""
    for route in (("PUT", "/arturo/ptt/vendor"), ("PUT", "/arturo/ptt/voice"),
                  ("POST", "/voice-call-ended")):
        assert G.ROUTE_SCOPES[route] == "admin", route


def test_reading_the_active_vendor_stays_read():
    """Only the WRITES moved. Knowing which vendor is live is harmless, and a client that cannot
    read it would have to guess what to display."""
    for route in (("GET", "/arturo/ptt/vendor"), ("GET", "/arturo/ptt/voice"),
                  ("GET", "/arturo/ptt/voices")):
        assert G.ROUTE_SCOPES[route] == "read", route


def test_a_ptt_device_cannot_reach_the_vendor_switch(monkeypatch, tmp_path):
    """The point of the split, asserted end to end through the middleware."""
    _, _, token = _with_store(monkeypatch, tmp_path, ["read", "approve", "message", "ptt"])
    ok, reached_ok, _ = _call("POST", "/arturo/ptt", {"Authorization": f"Bearer {token}"})
    assert reached_ok and ok.status == 200, "a ptt device must still be able to speak"
    for route in (("PUT", "/arturo/ptt/vendor"), ("PUT", "/arturo/ptt/voice")):
        deny, reached, _ = _call(*route, {"Authorization": f"Bearer {token}"})
        assert deny.status == 403 and not reached, route


def test_holding_voice_does_NOT_imply_ptt_and_vice_versa(monkeypatch, tmp_path):
    """No implication between verbs, deliberately — an implication graph is a second policy
    nobody reviews. So the transition is a RE-MINT, not a silent widening: a device granted only
    `voice` loses PTT when this lands, which is why it ships with a coordinated re-mint."""
    _, _, voice_only = _with_store(monkeypatch, tmp_path, ["voice"])
    deny, reached, _ = _call("POST", "/arturo/ptt", {"Authorization": f"Bearer {voice_only}"})
    assert deny.status == 403 and not reached
    assert G.ROUTE_SCOPES[("GET", "/live")] == "voice", "the richer surface still needs voice"
