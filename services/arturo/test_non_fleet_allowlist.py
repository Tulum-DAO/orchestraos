"""Non-fleet turns run a fail-closed tool allowlist (orchestraos-builder review of #278, G1), and the
onboarding's consent is the server's record, not the brain's (M1, M2, S1, S3, S4).

"fleet" is the gateway's own stamp for the gateway bearer: the dashboard's Arturo chat. Every other
turn is non-fleet: a paired device's ("device:<id>"), and every turn with no record or no stamp at all
(voice through /v1/chat/completions, /ptt, a raw loopback call). A non-fleet turn may only run reads that
touch no file, run no shell and change nothing.
"""
import json
import re
import time
from pathlib import Path

import pytest

from services.arturo import onboarding as onb
from services.arturo import operator_store as ops
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture (ARTURO_STATE in tmp)

_SRC = (Path(__file__).parent / "arturo-proxy.py").read_text()
# Every tool the model can name (TOOLS, the dispatcher's) and every name execute_tool dispatches on.
ALL_TOOLS = sorted(set(re.findall(r'"name": "([a-z_]+)"', _SRC)) | set(re.findall(r'name == "([a-z_]+)"', _SRC)))
NON_FLEET = [None, {"principal": "device:dev_voice"}, {"principal": ""}, {"principal": "FLEET"},
             {"principal": None}]
ALLOWED = {"knowledge", "list_agents", "query_roadmap", "read_agent_conversation", "client_briefing", "ask_choices",
           "agent_message"}


def _record(P, cid=None, step=None, principal=None):
    return P._begin_team_turn(cid, step, principal)


def _as(P, turn):
    if turn is None:
        return None
    rec = _record(P, principal=turn.get("principal"))
    return P._TEAM_TURN.set(rec)


@pytest.fixture()
def tripwires(P, monkeypatch):
    """Anything that would run a shell, a seat or a Mac command fails the test if reached."""
    hit = []

    def trip(*a, **k):
        hit.append(a)
        raise AssertionError(f"reached a side effect: {a[:1]}")
    for name in ("run_local", "_run_on_machine", "ssh_mac", "_run_commission"):
        monkeypatch.setattr(P, name, trip, raising=False)
    return hit


def test_the_tool_list_is_found():
    for name in ("run_command", "read_file", "spawn_agent", "pair_device", "async_task", "gm_command", "knowledge"):
        assert name in ALL_TOOLS


def test_the_allowlist_is_exactly_this(P):
    # adding a tool here is a deliberate, reviewed act; a new tool is refused until then
    assert P._NON_FLEET_ALLOWED == frozenset(ALLOWED)


@pytest.mark.parametrize("turn", NON_FLEET, ids=["no-record", "device", "empty", "FLEET", "none"])
@pytest.mark.parametrize("name", [n for n in ALL_TOOLS if n not in ALLOWED] + ["a_tool_added_later"])
def test_a_non_fleet_turn_never_reaches_a_tool_off_the_list(P, tripwires, name, turn):
    bucket = []
    btok = P._TOOLS_THIS_TURN.set(bucket)
    tok = _as(P, turn)
    try:
        out = P.execute_tool(name, {"command": "id", "path": "/etc/hostname", "device": "iPhone"})
    finally:
        if tok is not None:
            P._TEAM_TURN.reset(tok)
        P._TOOLS_THIS_TURN.reset(btok)
    assert out.startswith("NOT RUN") and name in out
    assert bucket == [] and tripwires == []          # refused before the dispatch, by effect


@pytest.mark.parametrize("turn", NON_FLEET, ids=["no-record", "device", "empty", "FLEET", "none"])
@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_a_non_fleet_turn_keeps_the_reads(P, name, turn):
    rec = None if turn is None else _record(P, principal=turn.get("principal"))
    assert P._non_fleet_refusal(name, rec) is None


@pytest.mark.parametrize("name", ALL_TOOLS + ["a_tool_added_later"])
def test_a_fleet_turn_is_unchanged(P, name):
    assert P._non_fleet_refusal(name, _record(P, principal="fleet")) is None


def test_a_fleet_turn_still_runs_a_command_by_effect(P, monkeypatch):
    ran = []
    monkeypatch.setattr(P, "run_local", lambda cmd, timeout=15: (ran.append(cmd), (True, "ok"))[1])
    tok = P._TEAM_TURN.set(_record(P, principal="fleet"))
    try:
        out = P.execute_tool("run_command", {"command": "echo hi"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert ran == ["echo hi"] and out.startswith("Command output")


# ---- each way in -----------------------------------------------------------------------------------
def _text_running(P, monkeypatch, path, headers):
    ran, results = [], []
    monkeypatch.setattr(P, "run_local", lambda cmd, timeout=15: (ran.append(cmd), (True, "ok"))[1])
    monkeypatch.setattr(P, "_brain_reply", lambda m, c: (
        results.append(P.execute_tool("run_command", {"command": "cat /etc/hostname"})), ("ok", ["run_command"], []))[1])
    with P.app.test_client() as c:
        r = c.post(path, json={"text": "what is the hostname", "conversation_id": "web_g1"}, headers=headers)
        r.get_data()                                   # a stream runs its turn as it is read
    return ran, results


@pytest.mark.parametrize("path", ["/text", "/text/stream"])
@pytest.mark.parametrize("headers", [{}, {"X-Arturo-Principal": "device:dev_voice"}, {"X-Arturo-Principal": "Fleet"}],
                         ids=["raw-loopback", "device", "wrong-case"])
def test_a_text_turn_that_is_not_fleet_runs_no_command(P, monkeypatch, path, headers):
    ran, results = _text_running(P, monkeypatch, path, headers)
    assert ran == [] and results and results[0].startswith("NOT RUN")


@pytest.mark.parametrize("path", ["/text", "/text/stream"])
def test_the_dashboards_text_turn_still_runs_it(P, monkeypatch, path, fleet_stamp):
    ran, results = _text_running(P, monkeypatch, path, fleet_stamp(P))
    assert ran == ["cat /etc/hostname"]


def test_voice_has_no_turn_record_so_it_is_non_fleet(P, tripwires):
    # /v1/chat/completions and /ptt never make a turn record: the gate sees None
    assert P._TEAM_TURN.get() is None
    assert P.execute_tool("run_command", {"command": "id"}).startswith("NOT RUN") and tripwires == []


def _wait_for(pred, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_a_fleet_turns_background_task_keeps_the_principal(P, monkeypatch):
    # async_task runs on a fresh thread; without the turn's context it would be a non-fleet turn
    ran, delivered = [], []
    monkeypatch.setattr(P, "run_local", lambda cmd, timeout=15: (ran.append(cmd), (True, "ok"))[1])
    monkeypatch.setattr(P._TG_OUTBOX, "allow", lambda text, now=None: (delivered.append(text), (False, "test"))[1])
    tok = P._TEAM_TURN.set(_record(P, principal="fleet"))
    try:
        P.execute_tool("async_task", {"tool_name": "run_command", "tool_args": {"command": "uptime"}, "summary": "s"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert _wait_for(lambda: delivered)
    assert ran == ["uptime"] and "NOT RUN" not in delivered[0]


def test_a_non_fleet_turn_cannot_reach_the_background_either(P, tripwires):
    tok = P._TEAM_TURN.set(_record(P, principal="device:dev_voice"))
    try:
        out = P.execute_tool("async_task", {"tool_name": "run_command", "tool_args": {"command": "id"}, "summary": "s"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT RUN") and tripwires == []


# ---- M1: an offer or a devices card is the onboarding's ----------------------------------------------
@pytest.mark.parametrize("purpose", ["starter_team", "devices"])
def test_off_the_onboarding_a_purpose_card_is_a_plain_card(P, monkeypatch, purpose):
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})
    P._TEAM_OFFERS.clear(); P._DEVICE_CARDS.clear()
    tok = P._TEAM_TURN.set(_record(P, "web_m1", None, "fleet"))
    try:
        out = P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": purpose})
        card = P._TEAM_TURN.get()["choices"]
    finally:
        P._TEAM_TURN.reset(tok)
    assert card["purpose"] == "other" and "note" not in card and "plain card" in out
    assert "web_m1" not in P._TEAM_OFFERS and "web_m1" not in P._DEVICE_CARDS
    # so the next turn is no consent: create_starter_team is refused there
    nxt = _record(P, "web_m1", None, "fleet")
    assert nxt["offered"] is False and nxt["devices_answer"] is False


def test_on_the_onboarding_the_offer_is_recorded(P, monkeypatch):
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})
    P._TEAM_OFFERS.clear()
    tok = P._TEAM_TURN.set(_record(P, "web_m1b", "onboarding", "fleet"))
    try:
        P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": "starter_team"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert _record(P, "web_m1b", "onboarding", "fleet")["offered"] is True


def test_stale_offers_and_device_cards_are_swept(P):
    P._TEAM_OFFERS.clear(); P._DEVICE_CARDS.clear()
    old = time.time() - P._TEAM_OFFER_TTL_S - 1
    P._TEAM_OFFERS["web_old"] = (old, "fleet")
    P._DEVICE_CARDS["web_old2"] = (old, ("iPhone",), "fleet")
    _record(P, "web_any", "onboarding", "fleet")
    assert P._TEAM_OFFERS == {} and P._DEVICE_CARDS == {}


# ---- M2: pairing needs the operator's answer to a devices card ---------------------------------------
@pytest.fixture()
def pairing(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", tmp_path / "data")
    monkeypatch.setenv("ORCHESTRA_PUBLIC_URL", "https://box.example.ts.net:8443")
    from scripts.device_tokens import DeviceStore
    return DeviceStore(tmp_path / "data" / "state" / "devices")


def _on(P, cid, step, tool, args):
    tok = P._TEAM_TURN.set(_record(P, cid, step, "fleet"))
    try:
        return P.execute_tool(tool, args), P._TEAM_TURN.get()
    finally:
        P._TEAM_TURN.reset(tok)


def test_a_devices_fact_the_brain_wrote_is_not_consent(P, pairing):
    ops.set_fact(P.ARTURO_STATE, "devices", "iPhone, Mac")
    out, turn = _on(P, "web_m2", "onboarding", "pair_device", {"device": "iPhone"})
    assert out.startswith("NOT PAIRED") and turn["pair_card"] is None and pairing.list() == []


def test_writing_the_fact_and_pairing_in_one_turn_is_not_consent(P, pairing):
    tok = P._TEAM_TURN.set(_record(P, "web_m2b", "onboarding", "fleet"))
    try:
        P.execute_tool("set_operator_fact", {"field": "devices", "value": "iPhone"})
        out = P.execute_tool("pair_device", {"device": "iPhone"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT PAIRED") and pairing.list() == []


def test_the_answer_to_a_devices_card_is_consent_for_what_was_on_it(P, pairing):
    _on(P, "web_m2c", "onboarding", "ask_choices",
        {"options": ["iPhone", "iPad", "None of these"], "multi": True, "purpose": "devices"})
    _on(P, "web_m2c", "onboarding", "set_operator_fact", {"field": "devices", "value": "iPhone, Mac"})
    assert P._answered_devices() == ["iPhone"]               # Mac was never on the card
    out, turn = _on(P, "web_m2c", "onboarding", "pair_device", {"device": "iPhone"})
    assert out.startswith("A pairing code") and turn["pair_card"]["code"].startswith("orc1_")
    out, _ = _on(P, "web_m2c", "onboarding", "pair_device", {"device": "Mac"})
    assert out.startswith("NOT PAIRED")


def test_a_devices_fact_on_a_turn_with_no_card_before_it_records_no_answer(P):
    _on(P, "web_m2d", "onboarding", "set_operator_fact", {"field": "devices", "value": "iPhone"})
    assert P._answered_devices() == []


# ---- S1: an expired, unredeemed Arturo code leaves nothing behind ------------------------------------
def _age(store_dir, device_id, by):
    p = Path(store_dir) / f"{device_id}.json"
    rec = json.loads(p.read_text())
    rec["created_at"] -= by
    p.write_text(json.dumps(rec))


def test_expired_unredeemed_arturo_devices_are_revoked(P, pairing, tmp_path):
    from scripts.pairing import PairingStore
    store = PairingStore(tmp_path / "data" / "state" / "pairing")
    ttl = store.ttl_s
    stale, _ = pairing.mint("iphone (arturo)", ["read"], minted_by="arturo-onboarding")
    fresh, _ = pairing.mint("ipad (arturo)", ["read"], minted_by="arturo-onboarding")
    used, _ = pairing.mint("mac (arturo)", ["read"], minted_by="arturo-onboarding")
    cli, _ = pairing.mint("my phone", ["read"])
    for d in (stale, used, cli):
        _age(pairing.dir, d, ttl + 5)
    pairing.touch(used)
    code = store.mint(base_url="https://b:8443", token="t")
    expired = store._path(code)
    body = json.loads(expired.read_text()); body["expires_at"] = time.time() - 1; expired.write_text(json.dumps(body))
    out, _ = _on(P, "web_s1", None, "check_paired", {"device_id": fresh})
    rows = {r["id"]: r for r in pairing.list()}
    assert rows[stale]["revoked_at"]
    assert not rows[fresh]["revoked_at"] and not rows[used]["revoked_at"] and not rows[cli]["revoked_at"]
    assert not expired.exists()
    assert out.startswith("Not yet")


def test_the_proxy_sweeps_at_startup():
    assert "sweep_unredeemed_pairings()" in _SRC.split('if __name__ == "__main__":')[1]


# ---- S3: guarantees #268 had, ported -------------------------------------------------------------------
def _team_turn(P, cid, step="onboarding"):
    return P._TEAM_TURN.set(_record(P, cid, step, "fleet"))


def test_a_decline_takes_the_offer_off_the_book_and_wins_the_turn(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})
    monkeypatch.setattr(P, "_run_commission", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    P._TEAM_OFFERS.clear()
    tok = _team_turn(P, "web_s3")
    try:
        assert P.execute_tool("create_starter_team", {}).startswith("NOT STARTED")   # it asks: an offer goes on
        assert "web_s3" in P._TEAM_OFFERS
        P.execute_tool("decline_starter_team", {})
        assert P._TEAM_TURN.get()["declined"] is True
        assert "said no" in P.execute_tool("create_starter_team", {})
    finally:
        P._TEAM_TURN.reset(tok)
    assert "web_s3" not in P._TEAM_OFFERS
    assert _record(P, "web_s3", "onboarding", "fleet")["offered"] is False         # the next turn is no yes


def test_off_the_onboarding_the_team_tool_puts_no_offer_on_the_book(P, monkeypatch, tmp_path):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", tmp_path)
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})
    P._TEAM_OFFERS.clear()
    tok = _team_turn(P, "web_s3b", step=None)
    try:
        out = P.execute_tool("create_starter_team", {})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT STARTED") and "orchestra starter" in out and P._TEAM_OFFERS == {}


@pytest.mark.parametrize("state", [{"state": "other_manager", "manager": "boss"}, {"state": "unknown"},
                                   {"state": "starting", "seats": []}, {"state": "present", "seats": []}])
def test_a_team_card_offers_nothing_unless_the_team_is_absent_or_incomplete(P, monkeypatch, state):
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: state)
    P._TEAM_OFFERS.clear()
    tok = _team_turn(P, "web_s3c")
    try:
        out = P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": "starter_team"})
        card = P._TEAM_TURN.get()["choices"]
    finally:
        P._TEAM_TURN.reset(tok)
    assert P._TEAM_OFFERS == {} and card["purpose"] == "other" and "note" not in card
    assert "no team offer recorded" in out


def test_the_page_is_told_onboarding_is_done_only_on_an_onboarding_turn_after_the_flag(P):
    rec = _record(P, "web_s3d", "onboarding", "fleet")
    assert P._turn_extras(rec, True) == {"onboarding": {"done": False}}
    assert P._turn_extras(_record(P, "web_s3d", None, "fleet"), False) == {}
    tok = P._TEAM_TURN.set(rec)
    try:
        P.execute_tool("finish_onboarding", {})
    finally:
        P._TEAM_TURN.reset(tok)
    assert P._turn_extras(rec, True)["onboarding"] == {"done": True}


# ---- S4: "Just this computer" rules out the rest ------------------------------------------------------
def _card(P, args, step="onboarding"):
    tok = P._TEAM_TURN.set(_record(P, "web_s4", step, "fleet"))
    try:
        P.execute_tool("ask_choices", args)
        return P._TEAM_TURN.get()["choices"]
    finally:
        P._TEAM_TURN.reset(tok)


def test_a_devices_card_marks_the_exclusive_option_even_unnamed(P):
    card = _card(P, {"options": list(onb.DEVICES), "multi": True, "purpose": "devices"})
    assert card["exclusive"] == "Just this computer"
    card = _card(P, {"options": ["iPhone", "Mac", "None of these"], "multi": True, "purpose": "devices"})
    assert card["exclusive"] == "None of these"


def test_the_brain_may_name_the_exclusive_option_but_only_one_on_the_card(P):
    assert _card(P, {"options": ["a", "b", "neither"], "multi": True, "exclusive": "neither"})["exclusive"] == "neither"
    assert "exclusive" not in _card(P, {"options": ["a", "b"], "multi": True, "exclusive": "c"})
    assert "exclusive" not in _card(P, {"options": ["a", "None"], "multi": False, "exclusive": "None"})


# ---- bind level: a non-fleet turn is never even shown the rest ----------------------------------------
def _names(tools):
    return {t["function"]["name"] for t in tools}


@pytest.mark.parametrize("turn", NON_FLEET, ids=["no-record", "device", "empty", "FLEET", "none"])
def test_a_non_fleet_turn_is_offered_only_the_allowlist(P, turn):
    rec = None if turn is None else _record(P, principal=turn.get("principal"))
    tok = P._TEAM_TURN.set(rec) if rec is not None else None
    try:
        offered = _names(P._bound_tools())
    finally:
        if tok is not None:
            P._TEAM_TURN.reset(tok)
    assert offered and offered <= ALLOWED
    assert "run_command" not in offered and "inject_message" not in offered


def test_a_fleet_turn_is_offered_every_tool(P):
    assert P._bound_tools(_record(P, principal="fleet")) == P.TOOLS


def test_a_voice_call_asking_for_a_command_is_offered_none_and_runs_none(P, monkeypatch, tmp_path):
    # by effect, through /v1/chat/completions: the brain is offered no shell, and a call it makes anyway
    # (a model may name a tool it was not given) is refused
    monkeypatch.setenv("ARTURO_VOICE_CALLS_DIR", str(tmp_path / "vc"))
    monkeypatch.setattr(P, "BEARER_TOKEN", "test-bearer")
    monkeypatch.setattr(P._REQ_GUARD, "is_duplicate", lambda *a, **k: False)
    ran, offered, results = [], [], []
    monkeypatch.setattr(P, "run_local", lambda cmd, timeout=15: (ran.append(cmd), (True, "ok"))[1])
    from types import SimpleNamespace as NS

    def complete(**kw):
        offered.append(_names(kw.get("tools") or []))
        if len(offered) == 1:
            call = NS(id="c1", type="function", function=NS(name="run_command", arguments='{"command": "id"}'))
            return P._brain.make_response(None, [call])
        results.extend(m.get("content") for m in kw["messages"] if m.get("role") == "tool")
        return P._brain.make_response("done", None, "stop")
    monkeypatch.setattr(P.brain, "complete", complete)
    body = {"messages": [{"role": "user", "content": "run id on the server for me please right now"}]}
    with P.app.test_client() as c:
        r = c.post("/v1/chat/completions?custom_session_id=CALLG1", json=body,
                   headers={"Authorization": "Bearer test-bearer"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        r.get_data()
    assert offered and all(o <= ALLOWED for o in offered)
    assert ran == []
    assert results and all("NOT RUN" in (x or "") for x in results)


# ---- seat messaging off the dashboard: Arturo's own, and marked unverified ------------------------------
@pytest.fixture()
def outbox(P, monkeypatch):
    import sys
    import types
    sent = []

    class _Store:
        def send(self, **kw):
            sent.append(kw)
            return "msg_test"
    monkeypatch.setitem(sys.modules, "msg_store", types.SimpleNamespace(MessageStore=_Store))
    return sent


def _msg(P, rec):
    tok = P._TEAM_TURN.set(rec) if rec is not None else None
    try:
        return P.execute_tool("agent_message", {"from_agent": "gm", "to_agent": "dev-x", "subject": "s", "body": "do it"})
    finally:
        if tok is not None:
            P._TEAM_TURN.reset(tok)


def test_a_device_turns_message_is_arturos_and_says_unverified(P, outbox):
    _msg(P, _record(P, "web_x", None, "device:dev_voice"))
    assert outbox[0]["from_agent"] == "arturo"
    assert outbox[0]["body"] == "do it\n\n(via device:dev_voice, unverified caller)"


def test_a_voice_calls_message_names_the_call(P, outbox):
    tok = P._CALLER_CONV.set("conv_abc")
    try:
        _msg(P, None)
    finally:
        P._CALLER_CONV.reset(tok)
    assert outbox[0]["from_agent"] == "arturo"
    assert outbox[0]["body"].endswith("(via voice call conv_abc, unverified caller)")


def test_an_unstamped_text_turns_message_is_marked_too(P, outbox):
    _msg(P, _record(P, "web_y", None, None))
    assert outbox[0]["from_agent"] == "arturo" and "unverified caller" in outbox[0]["body"]


def test_a_dashboard_turns_message_is_unchanged(P, outbox):
    _msg(P, _record(P, "web_z", None, "fleet"))
    assert outbox[0]["from_agent"] == "gm" and outbox[0]["body"] == "do it"


@pytest.mark.parametrize("tool,args", [("agent_message", {"to_agent": "dev-x", "subject": "s"}),
                                       ("read_agent_conversation", {"agent_id": "dev-x"})])
def test_the_seat_message_tools_reach_the_store(P, monkeypatch, tool, args):
    # they named `sys`, which this module imports only as `_sys`: every call failed with a NameError
    import sys
    import types

    class _Store:
        def send(self, **kw):
            return "msg_ok"

        def inbox(self, agent, tenant_id=None):
            return []
    monkeypatch.setitem(sys.modules, "msg_store", types.SimpleNamespace(MessageStore=_Store))
    tok = P._TEAM_TURN.set(_record(P, principal="fleet"))
    try:
        out = P.execute_tool(tool, args)
    finally:
        P._TEAM_TURN.reset(tok)
    assert "not defined" not in out and not out.startswith("Failed")


# ---- the gateway's principal stamp is authenticated ----------------------------------------------------
_FWD = [{}, {"X-Forwarded-For": "203.0.113.9"}, {"X-Forwarded-Host": "box.ts.net"}, {"Tailscale-Funnel-Request": "?1"},
        {"Forwarded": "for=203.0.113.9"}]


@pytest.mark.parametrize("path", ["/text", "/text/stream"])
@pytest.mark.parametrize("extra", _FWD, ids=["loopback", "xff", "xfh", "funnel", "forwarded"])
@pytest.mark.parametrize("secret", ["none", "wrong"])
def test_a_fleet_stamp_without_this_installs_secret_is_not_fleet(P, monkeypatch, fleet_stamp, path, extra, secret):
    good = fleet_stamp(P)                                    # the install HAS a secret; the caller lacks it
    h = {"X-Arturo-Principal": "fleet", **extra}
    if secret == "wrong":
        h["X-Arturo-Stamp"] = good["X-Arturo-Stamp"][::-1]
    ran, results = _text_running(P, monkeypatch, path, h)
    assert ran == [] and results and results[0].startswith("NOT RUN")


@pytest.mark.parametrize("path", ["/text", "/text/stream"])
@pytest.mark.parametrize("extra", _FWD[1:], ids=["xff", "xfh", "funnel", "forwarded"])
def test_even_the_right_secret_is_ignored_on_a_forwarded_request(P, monkeypatch, fleet_stamp, path, extra):
    ran, results = _text_running(P, monkeypatch, path, {**fleet_stamp(P), **extra})
    assert ran == [] and results[0].startswith("NOT RUN")


@pytest.mark.parametrize("state", ["missing", "empty"])
def test_no_secret_file_means_no_fleet_ever(P, monkeypatch, fleet_stamp, state):
    h = fleet_stamp(P)
    from scripts import arturo_stamp
    f = arturo_stamp.path(P.ORCHESTRA_DIR)
    if state == "missing":
        f.unlink()
    else:
        f.write_text("")
    ran, results = _text_running(P, monkeypatch, "/text", h)
    assert ran == [] and results[0].startswith("NOT RUN")
    h["X-Arturo-Stamp"] = ""                                  # an empty presented secret never matches an empty file
    ran, results = _text_running(P, monkeypatch, "/text", h)
    assert ran == []


# ---- S6: never promise a text that will not come ---------------------------------------------------------
def test_a_refused_background_run_is_never_promised(P):
    assert P._promise_or_refusal("NOT RUN: async_task is not available here.", "On it, I'll text you.") == P._NOT_FROM_HERE
    assert "text you" not in P._NOT_FROM_HERE and "dashboard" in P._NOT_FROM_HERE
    assert P._promise_or_refusal("Task queued: x.", "On it, I'll text you.") == "On it, I'll text you."


def test_both_voice_escalations_check_the_result_before_speaking():
    # the two places that speak a promise right after async_task (the gm_command guardrail and the
    # can't-answer escalation) say it only through _promise_or_refusal
    assert _SRC.count("_promise_or_refusal(") == 3                    # the definition + 2 call sites
    assert 'yield make_sse_chunk("On it, I\'ll text you' not in _SRC
    assert "yield make_sse_chunk(\"That's a deeper one" not in _SRC


# ---- S7: consent is bound to the principal that was shown the card ----------------------------------------
@pytest.mark.parametrize("purpose", ["starter_team", "devices"])
def test_a_device_turn_cannot_arm_a_consent(P, monkeypatch, purpose):
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "absent"})
    P._TEAM_OFFERS.clear(); P._DEVICE_CARDS.clear()
    tok = P._TEAM_TURN.set(_record(P, "web_s7", "onboarding", "device:dev_voice"))
    try:
        P.execute_tool("ask_choices", {"options": list(onb.DEVICES), "multi": True, "purpose": purpose})
        card = P._TEAM_TURN.get()["choices"]
    finally:
        P._TEAM_TURN.reset(tok)
    assert card["purpose"] == "other" and P._TEAM_OFFERS == {} and P._DEVICE_CARDS == {}
    nxt = _record(P, "web_s7", "onboarding", "fleet")                 # the operator's next dashboard turn
    assert nxt["offered"] is False and nxt["devices_answer"] is False


def test_a_card_armed_by_one_principal_is_not_answered_by_another(P):
    now = time.time()
    P._TEAM_OFFERS.clear(); P._DEVICE_CARDS.clear()
    P._TEAM_OFFERS["web_s7b"] = (now, "device:dev_voice")
    P._DEVICE_CARDS["web_s7b"] = (now, ("iPhone",), "device:dev_voice")
    rec = _record(P, "web_s7b", "onboarding", "fleet")
    assert rec["offered"] is False and rec["devices_answer"] is False
    P._TEAM_OFFERS["web_s7c"] = (now, "fleet")
    P._DEVICE_CARDS["web_s7c"] = (now, ("iPhone",), "fleet")
    rec = _record(P, "web_s7c", "onboarding", "fleet")
    assert rec["offered"] is True and rec["devices_answer"] is True


# ---- S8: a devices answer is consent for minutes, not for good ------------------------------------------
def test_an_expired_devices_answer_refuses_pairing(P, pairing):
    _on(P, "web_s8", "onboarding", "ask_choices", {"options": list(onb.DEVICES), "multi": True, "purpose": "devices"})
    _on(P, "web_s8", "onboarding", "set_operator_fact", {"field": "devices", "value": "iPhone and Mac"})
    rec = json.loads(P._devices_answer_path().read_text())
    assert rec["devices"] == ["iPhone", "Mac"]                         # "and" counts as a separator
    assert rec["expires_at"] - rec["answered_at"] == P._DEVICES_ANSWER_TTL_S == 600
    rec["expires_at"] = time.time() - 1
    P._devices_answer_path().write_text(json.dumps(rec))
    out, turn = _on(P, "web_s8", "onboarding", "pair_device", {"device": "iPhone"})
    assert out.startswith("NOT PAIRED") and turn["pair_card"] is None and pairing.list() == []
