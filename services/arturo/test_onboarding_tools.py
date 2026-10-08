"""The LLM-led onboarding's playbook and tools (congruence DEC-1791485978471942 v4).

The operator: "I don't want hardcoded arturo questions, just instructions to the llm that powers it to
ensure a clean onboarding", and "Walking the user through pairing of each of the devices they select
after they select them should be table stakes" (Arturo makes the code; devices get read, approve,
message). What the brain says is the playbook's; what may HAPPEN is pinned here.
"""
import json
import logging

import pytest

from services.arturo import onboarding as onb
from services.arturo import operator_store as ops
from services.arturo.test_text_turn_brain import P  # noqa: F401 — the shared fixture (ARTURO_STATE in tmp)


# ---- the playbook ------------------------------------------------------------------------------
def _pb(**ctx):
    return onb.directive("onboarding", {"team": {"state": "absent"}, **ctx})


def test_the_playbook_carries_every_goal_in_order():
    p = _pb()
    for goal in ("set_operator_fact(field='name')", "ask_choices(purpose='starter_team')", "create_starter_team",
                 "ask_choices(purpose='devices', multi=true)", "pair_device", "check_paired", "finish_onboarding"):
        assert goal in p, goal
    assert p.index("field='name'") < p.index("purpose='starter_team'") < p.index("purpose='devices'") \
        < p.index("pair_device") < p.index("finish_onboarding")


def test_the_playbook_keeps_the_name_rules_and_the_team_facts():
    p = _pb()
    assert onb.NAME_RULES in p and onb.TEAM_SHAPE in p and onb.TEAM_COST in p


def test_the_playbook_says_what_is_already_known_so_it_is_skipped():
    p = _pb(operator={"name": "Mo", "devices": "iPhone"})
    assert "Their name: Mo." in p and "Their devices: iPhone." in p
    assert "skip any goal already done" in p


@pytest.mark.parametrize("team,phrase", [
    ({"state": "other_manager", "manager": "boss"}, "Do not offer the starter team"),
    ({"state": "unknown"}, "Do not offer it."),
    ({"state": "present", "seats": [{"name": "gm", "tier": "T0", "seen": "running"}]}, "Nothing to create."),
])
def test_the_team_fact_follows_the_server_state(team, phrase):
    assert phrase in onb.directive("onboarding", {"team": team})


def test_the_opener_is_told_it_is_not_the_operator_speaking():
    assert "not the operator speaking" in onb.directive("onboarding_open", {"team": {"state": "absent"}})
    assert "not the operator speaking" not in _pb()


def test_voice_is_mentioned_only_when_text_only():
    assert "ELEVENLABS_API_KEY" in _pb(voice_mode="text-only")
    assert "ELEVENLABS_API_KEY" not in _pb(voice_mode="voice")


def test_the_playbook_tells_the_brain_it_never_sees_a_code():
    assert "never repeat, guess or invent a code" in _pb()


# ---- ask_choices ---------------------------------------------------------------------------------
def _turn(P, cid="web_c", step="onboarding", principal="fleet"):
    return P._TEAM_TURN.set(P._begin_team_turn(cid, step, principal))


def test_a_card_records_cleaned_options_on_the_turn(P):
    tok = _turn(P)
    try:
        out = P.execute_tool("ask_choices", {"options": ["**iPhone**", "Mac", "Mac", "`Android`"], "multi": True})
        card = P._TEAM_TURN.get()["choices"]
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("Card shown")
    assert card == {"options": ["iPhone", "Mac", "Android"], "multi": True, "purpose": "other"}


@pytest.mark.parametrize("options", [["only one"], [], ["a"] * 1, [str(i) for i in range(9)], "not a list"])
def test_a_card_needs_two_to_eight_options(P, options):
    tok = _turn(P)
    try:
        out = P.execute_tool("ask_choices", {"options": options})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT SHOWN")


def test_one_card_per_turn(P):
    tok = _turn(P)
    try:
        P.execute_tool("ask_choices", {"options": ["a", "b"]})
        out = P.execute_tool("ask_choices", {"options": ["c", "d"], "purpose": "other"})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT SHOWN")


def test_no_card_outside_a_chat_turn(P):
    assert P.execute_tool("ask_choices", {"options": ["a", "b"]}).startswith("NOT SHOWN")


def test_a_team_card_when_the_team_is_up_records_no_offer(P, monkeypatch):
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "present", "seats": []})
    P._TEAM_OFFERS.clear()
    tok = _turn(P, cid="web_up")
    try:
        out = P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": "starter_team"})
        card = P._TEAM_TURN.get()["choices"]
    finally:
        P._TEAM_TURN.reset(tok)
    assert "web_up" not in P._TEAM_OFFERS and card["purpose"] == "other" and "note" not in card
    assert "no team offer recorded" in out


# ---- finish_onboarding -------------------------------------------------------------------------
@pytest.mark.parametrize("step", ["onboarding_open", None])
def test_finish_is_refused_on_the_opener_and_off_onboarding(P, step):
    tok = _turn(P, step=step)
    try:
        out = P.execute_tool("finish_onboarding", {})
    finally:
        P._TEAM_TURN.reset(tok)
    assert out.startswith("NOT RUN") and not P.onboarded()


def test_finish_sets_the_flag_and_health_reports_it(P):
    c = P.app.test_client()
    assert c.get("/health").get_json()["onboarded"] is False
    tok = _turn(P)
    try:
        assert not P.execute_tool("finish_onboarding", {}).startswith("NOT RUN")
        assert P.execute_tool("finish_onboarding", {}) == "Onboarding was already finished."
    finally:
        P._TEAM_TURN.reset(tok)
    assert c.get("/health").get_json()["onboarded"] is True


# ---- pair_device / check_paired --------------------------------------------------------------------
@pytest.fixture()
def pairing(P, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ORCHESTRA_DIR", tmp_path / "data")
    monkeypatch.setenv("ORCHESTRA_PUBLIC_URL", "https://box.example.ts.net:8443")
    ops.set_fact(P.ARTURO_STATE, "devices", "iPhone, Apple Watch, Mac")
    from scripts.device_tokens import DeviceStore
    return DeviceStore(tmp_path / "data" / "state" / "devices")


def _pair(P, device="iPhone", **turn):
    tok = _turn(P, **turn)
    try:
        out = P.execute_tool("pair_device", {"device": device})
        card = P._TEAM_TURN.get()["pair_card"]
    finally:
        P._TEAM_TURN.reset(tok)
    return out, card


def test_pairing_mints_fixed_scopes_and_the_code_goes_only_to_the_card(P, pairing, caplog):
    caplog.set_level(logging.INFO)
    out, card = _pair(P)
    assert card["code"].startswith("orc1_")
    assert card["code"] not in out and "orc1_" not in out           # the brain never has the code
    assert card["code"] not in caplog.text                          # nor the log
    rec = next(r for r in pairing.list() if r["id"] == card["device_id"])
    assert rec["scopes"] == ["read", "approve", "message"]
    assert rec["minted_by"] == "arturo-onboarding" and rec["label"] == "iphone (arturo)"
    assert card["where"] == onb.PASTE_WHERE["iPhone"] and card["device_id"] in card["revoke"]


def test_the_model_cannot_choose_scopes_or_label(P, pairing):
    tok = _turn(P)
    try:
        P.execute_tool("pair_device", {"device": "iPhone", "scopes": "admin,inject", "label": "gm"})
        card = P._TEAM_TURN.get()["pair_card"]
    finally:
        P._TEAM_TURN.reset(tok)
    rec = next(r for r in pairing.list() if r["id"] == card["device_id"])
    assert rec["scopes"] == ["read", "approve", "message"] and rec["label"] == "iphone (arturo)"


@pytest.mark.parametrize("principal", [None, "device:dev_voice", "", "FLEET"])
def test_only_a_fleet_stamped_turn_can_pair(P, pairing, principal):
    out, card = _pair(P, principal=principal)
    assert out.startswith("NOT PAIRED") and card is None and pairing.list() == []


def test_no_turn_record_at_all_cannot_pair(P, pairing):
    # voice (/v1/chat/completions) and /ptt run tools with no turn record
    assert P.execute_tool("pair_device", {"device": "iPhone"}).startswith("NOT PAIRED")
    assert pairing.list() == []


def test_the_opener_cannot_pair(P, pairing):
    out, _ = _pair(P, step="onboarding_open")
    assert out.startswith("NOT PAIRED")


@pytest.mark.parametrize("device", ["iPad", "Apple Watch", "Android phone", "toaster"])
def test_only_a_pairable_device_on_record_pairs(P, pairing, device):
    out, card = _pair(P, device=device)
    assert out.startswith("NOT PAIRED") and card is None


def test_no_public_address_means_no_code(P, pairing, monkeypatch):
    monkeypatch.delenv("ORCHESTRA_PUBLIC_URL")
    out, card = _pair(P)
    assert out.startswith("NOT PAIRED") and "ORCHESTRA_PUBLIC_URL" in out and card is None


def test_a_new_code_revokes_only_arturos_previous_one_for_that_device(P, pairing):
    cli_id, _ = pairing.mint("iphone (arturo)", ["read"], minted_by=None)       # hand-minted, same label
    _, first = _pair(P)
    _, second = _pair(P)
    rows = {r["id"]: r for r in pairing.list()}
    assert rows[first["device_id"]]["revoked_at"] and not rows[second["device_id"]]["revoked_at"]
    assert not rows[cli_id]["revoked_at"]


def test_check_paired_reports_by_effect(P, pairing):
    _, card = _pair(P)
    dev = card["device_id"]
    assert P.execute_tool("check_paired", {"device_id": dev}).startswith("Not yet")
    pairing.touch(dev)
    assert P.execute_tool("check_paired", {"device_id": dev}).startswith("Connected")
    _pair(P)                                                    # a newer code revokes the old record
    assert "revoked" in P.execute_tool("check_paired", {"device_id": dev})


def _text_with_pair(P, monkeypatch, path, headers):
    results = []
    monkeypatch.setattr(P, "_brain_reply",
                        lambda m, c: (results.append(P.execute_tool("pair_device", {"device": "iPhone"})), ("ok", ["pair_device"], []))[1])
    monkeypatch.setattr(P, "starter_team_state", lambda seen=None: {"state": "present", "seats": []})
    with P.app.test_client() as c:
        r = c.post(path, json={"text": "[Onboarding: step=onboarding]\npair my iphone", "conversation_id": "web_p"},
                   headers=headers)
    return results, r


def test_a_raw_loopback_text_call_without_the_stamp_cannot_pair(P, pairing, monkeypatch):
    results, _ = _text_with_pair(P, monkeypatch, "/text", {})
    assert results[0].startswith("NOT PAIRED")


def test_a_device_stamped_text_call_cannot_pair(P, pairing, monkeypatch):
    results, _ = _text_with_pair(P, monkeypatch, "/text", {"X-Arturo-Principal": "device:dev_voice"})
    assert results[0].startswith("NOT PAIRED")


def test_a_fleet_text_call_pairs_and_the_reply_carries_the_card_not_the_history(P, pairing, monkeypatch):
    results, r = _text_with_pair(P, monkeypatch, "/text", {"X-Arturo-Principal": "fleet"})
    body = r.get_json()
    assert results[0].startswith("A pairing code") and body["pair_card"]["code"].startswith("orc1_")
    archived = json.dumps(P._THREADS.get_thread("web_p"))
    assert body["pair_card"]["code"] not in archived


def test_the_stream_fallback_keeps_the_principal(P, pairing, monkeypatch):
    # an onboarding turn on /text/stream runs whole through text_turn; the stamp must ride along
    results, r = _text_with_pair(P, monkeypatch, "/text/stream", {"X-Arturo-Principal": "fleet"})
    data = r.get_data(as_text=True)            # the turn runs as the stream is read
    assert results and results[0].startswith("A pairing code")
    assert '"pair_card"' in data
