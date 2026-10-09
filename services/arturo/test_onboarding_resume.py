"""Leaving onboarding partway and coming back (the operator, 2026-10-09; DEC-1791511578959986).

"Arturo should always know how far along in the onboarding a user is in case they leave part way
through and come back. That way they always come back to Arturo knowing exactly what to do next."

Before: every page open sent the first-run opener again into the same thread, and the playbook
told the brain "the page is opening for the first time: greet them". So a returning operator got a
new-user greeting mid-onboarding, with their history hidden above it.
"""
import importlib.util
import json
import pathlib

import pytest

from services.arturo import onboarding as onb
from services.arturo import onboarding_progress as prog
from services.arturo import operator_store as ops

OPENER = "[Onboarding: step=onboarding_open]\n" + "(first run: the operator just opened OrchestraOS)"


def _load_proxy():
    spec = importlib.util.spec_from_file_location("arturo_proxy_resume", pathlib.Path("services/arturo/arturo-proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def P(tmp_path, monkeypatch):
    mod = _load_proxy()
    from services.arturo.thread_store import ThreadStore
    mod._THREADS = ThreadStore(tmp_path / "threads.db")
    mod.ARTURO_STATE = tmp_path / "arturo"
    mod.ORCHESTRA_DIR = tmp_path / "data"
    mod.build_context = lambda **k: "BASECTX"
    mod.starter_team_state = lambda seen=None: {"state": "absent", "seats": []}
    mod.seen = []
    replies = iter([f"reply {i}" for i in range(100)])

    def brain(messages, cid):
        mod.seen.append(messages[0]["content"])
        return (next(replies), [])
    mod._brain_reply = brain
    return mod


def _open(P, cid="web_onb", principal="fleet"):
    return P.text_turn(OPENER, cid, principal=principal)


def _say(P, text, cid="web_onb"):
    return P.text_turn("[Onboarding: step=onboarding]\n" + text, cid, principal="fleet")


def test_the_first_opener_greets_and_pins_the_onboarding_thread(P):
    code, body = _open(P)
    assert code == 200 and "page opening for the first time" in P.seen[-1] and "REOPENING" not in P.seen[-1]
    assert P._THREADS.turn_count("web_onb") == 2                   # stored: it marks the onboarding thread
    assert body["onboarding_conversation"] == "web_onb" == prog.conversation(P.ARTURO_STATE)
    assert "resumed" not in body


def test_coming_back_is_a_return_not_a_first_greeting(P):
    _open(P)
    _say(P, "Shaw")
    code, body = _open(P)
    assert code == 200 and body["resumed"] is True
    assert "REOPENING" in P.seen[-1] and "page opening for the first time" not in P.seen[-1]
    assert "Do not greet them as new" in P.seen[-1]


def test_three_reloads_in_a_row_leave_the_stored_history_unchanged(P):
    _open(P)
    _say(P, "Shaw")
    before = P._THREADS.get_thread("web_onb")["turns"]
    for _ in range(3):
        code, body = _open(P)
        assert code == 200 and body["resumed"] is True
    assert P._THREADS.get_thread("web_onb")["turns"] == before


def test_the_operators_answer_follows_what_the_reopened_page_showed(P):
    _open(P)
    _open(P)                                                     # reload: "reply 1" shown, not stored
    _say(P, "yes please")
    assert "Arturo last said, when the page reopened (the operator is answering it): reply 1" in P.seen[-1]
    _say(P, "and then?")
    assert "Arturo last said" not in P.seen[-1]                   # one turn only


def test_a_second_browser_switches_to_the_pinned_thread_instead_of_a_second_first_run(P):
    _open(P, "web_onb")
    n = len(P.seen)
    code, body = _open(P, "web_other")
    assert code == 200 and body["switch"] is True and body["onboarding_conversation"] == "web_onb"
    assert len(P.seen) == n and P._THREADS.turn_count("web_other") == 0   # no brain, nothing stored


def test_a_thread_from_before_the_pin_is_still_recognised_and_pinned(P):
    # an install upgraded mid-onboarding: the thread holds an opener, but nothing was pinned yet
    P._THREADS.record_turn("web_old", "(first run: the operator just opened OrchestraOS)", "Hi! Your name?")
    code, body = _open(P, "web_old")
    assert body["resumed"] is True and body["onboarding_conversation"] == "web_old"


def test_a_non_dashboard_opener_pins_nothing(P):
    _open(P, principal="device:dev_voice")
    assert prog.conversation(P.ARTURO_STATE) == ""


# ---- each stage: the returning opener knows exactly where the operator is ----------------------
def _devices(P, *records):
    d = P.ORCHESTRA_DIR / "state" / "devices"
    d.mkdir(parents=True, exist_ok=True)
    for i, rec in enumerate(records):
        (d / f"dev{i}.json").write_text(json.dumps({"id": f"dev{i}", "scopes": ["read"], **rec}))


@pytest.mark.parametrize("stage,expect,absent", [
    ("before_name", ["Their name: not known yet."], []),
    ("after_name", ["Their name: Shaw.", "Their team: none yet."], ["not now to the starter team"]),
    ("team_declined", ["They said not now to the starter team"], []),
    ("devices_picked", ["Their devices: iPhone, Mac."], ["Paired so far"]),
    ("one_paired", ["Paired so far: iPhone (connected), Mac (code not used yet)."], []),
])
def test_each_stage_reloads_into_the_next_step(P, stage, expect, absent):
    _open(P)
    if stage != "before_name":
        ops.set_fact(P.ARTURO_STATE, "name", "Shaw", source="brain")
    if stage == "team_declined":
        prog.set_team_declined(P.ARTURO_STATE)
    if stage in ("devices_picked", "one_paired"):
        ops.set_fact(P.ARTURO_STATE, "devices", "iPhone, Mac", source="brain")
    if stage == "one_paired":
        _devices(P, {"label": "iphone (arturo)", "minted_by": "arturo-onboarding", "last_seen_at": 1.0, "created_at": 1.0},
                 {"label": "mac (arturo)", "minted_by": "arturo-onboarding", "created_at": 2.0})
    code, body = _open(P)
    assert code == 200 and body["resumed"] is True and "REOPENING" in P.seen[-1]
    for e in expect:
        assert e in P.seen[-1], e
    for a in absent:
        assert a not in P.seen[-1], a


def test_finished_onboarding_hands_out_no_onboarding_thread(P):
    _open(P)
    (P.ARTURO_STATE / "onboarding.json").write_text("{}")
    with P.app.test_client() as c:
        h = c.get("/health", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
    assert h["onboarded"] is True and h["onboarding_conversation"] is None


def test_health_names_the_onboarding_thread_only_to_the_gateway(P):
    _open(P)
    with P.app.test_client() as c:
        mine = c.get("/health", environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_json()
        funnel = c.get("/health", environ_base={"REMOTE_ADDR": "127.0.0.1"},
                       headers={"X-Forwarded-For": "203.0.113.9"}).get_json()
    assert mine["onboarding_conversation"] == "web_onb"
    assert funnel["onboarding_conversation"] is None


# ---- a declined team is remembered, and only from the dashboard ----------------------------------
def _tool_on(P, principal, step, tool, args, cid="web_onb"):
    tok = P._TEAM_TURN.set(P._begin_team_turn(cid, step, principal, "x"))
    try:
        return P.execute_tool(tool, args)
    finally:
        P._TEAM_TURN.reset(tok)


def test_a_no_to_the_team_on_the_dashboard_is_remembered(P):
    _tool_on(P, "fleet", "onboarding", "decline_starter_team", {})
    assert prog.team_declined(P.ARTURO_STATE)


@pytest.mark.parametrize("principal,step", [("device:dev_voice", "onboarding"), (None, "onboarding"),
                                            ("fleet", "onboarding_open"), ("fleet", None)])
def test_a_no_from_anywhere_else_is_not_remembered(P, principal, step):
    _tool_on(P, principal, step, "decline_starter_team", {})
    assert not prog.team_declined(P.ARTURO_STATE)


def test_paired_lines_never_carry_a_code_or_an_id():
    recs = [{"id": "dev_secret", "label": "iphone (arturo)", "minted_by": "arturo-onboarding", "last_seen_at": 5},
            {"id": "dev_x", "label": "ipad (arturo)", "minted_by": "arturo-onboarding", "revoked_at": 3},
            {"id": "dev_y", "label": "my-phone", "minted_by": "operator"}]
    assert prog.paired(recs) == {"iPhone": "connected"}
    text = onb.playbook({"paired": prog.paired(recs)})
    assert "dev_secret" not in text and "iPhone (connected)" in text
