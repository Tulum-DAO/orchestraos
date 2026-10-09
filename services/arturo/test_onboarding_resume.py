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
    _say(P, "Ada")
    code, body = _open(P)
    assert code == 200 and body["resumed"] is True
    assert "REOPENING" in P.seen[-1] and "page opening for the first time" not in P.seen[-1]
    assert "Do not greet them as new" in P.seen[-1]


def test_three_reloads_in_a_row_leave_the_stored_history_unchanged(P):
    _open(P)
    _say(P, "Ada")
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
    ("after_name", ["Their name: Ada.", "Their team: none yet."], ["not now to the starter team"]),
    ("team_declined", ["They said not now to the starter team"], []),
    ("devices_picked", ["Their devices: iPhone, Mac."], ["Paired so far"]),
    ("one_paired", ["Paired so far: iPhone (connected), Mac (code not used yet)."], []),
])
def test_each_stage_reloads_into_the_next_step(P, stage, expect, absent):
    _open(P)
    if stage != "before_name":
        ops.set_fact(P.ARTURO_STATE, "name", "Ada", source="brain")
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


# ---- devices answered, then a fresh browser asked devices AGAIN ------------
def test_a_devices_pick_is_known_to_the_next_reopening_even_if_the_brain_never_wrote_it(P):
    _open(P)
    _say(P, "Ada")
    ops.set_fact(P.ARTURO_STATE, "name", "Ada", source="brain")
    # the reopened page refreshes the devices question with a live card (stored nowhere)
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_onb", "onboarding_open", "fleet", "x"))
    try:
        P.execute_tool("ask_choices", {"options": ["iPhone", "Mac", "Just this computer"], "multi": True,
                                       "purpose": "devices"})
    finally:
        P._TEAM_TURN.reset(tok)
    code, body = _say(P, "iPhone")                         # the brain writes no fact on this turn
    assert code == 200
    assert ops.public(P.ARTURO_STATE)["devices"] == "iPhone"
    turns = [t["content"] for t in P._THREADS.get_thread("web_onb")["turns"]]
    assert turns[-2:] == ["iPhone", body["reply_text"]]    # the answer AND Arturo's reply are stored
    _open(P, "web_onb")                                    # another browser opens the pinned thread
    assert "Their devices: iPhone." in P.seen[-1] and "Their devices: not known yet." not in P.seen[-1]


def test_a_pick_of_nothing_pairable_is_known_too(P):
    _open(P)
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_onb", "onboarding_open", "fleet", "x"))
    try:
        P.execute_tool("ask_choices", {"options": ["iPhone", "Just this computer"], "multi": True, "purpose": "devices"})
    finally:
        P._TEAM_TURN.reset(tok)
    _say(P, "Just this computer")
    assert ops.public(P.ARTURO_STATE)["devices"] == "Just this computer"


def test_a_message_that_is_not_a_pick_writes_no_devices_fact(P):
    _open(P)
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_onb", "onboarding_open", "fleet", "x"))
    try:
        P.execute_tool("ask_choices", {"options": ["iPhone", "Mac"], "multi": True, "purpose": "devices"})
    finally:
        P._TEAM_TURN.reset(tok)
    _say(P, "not my iPhone, what is this for?")
    assert ops.public(P.ARTURO_STATE)["devices"] is None


def test_a_returning_opener_can_bring_back_a_live_team_offer_card(P):
    # A scripted brain re-asked the team step as plain text. A real brain follows
    # goal 2 (ask_choices purpose='starter_team'); on a returning opener that card is live and booked.
    _open(P)
    P.starter_team_state = lambda seen=None: {"state": "absent", "seats": []}

    def brain(messages, cid):
        P.seen.append(messages[0]["content"])
        P.execute_tool("ask_choices", {"options": ["Set it up", "Not now"], "purpose": "starter_team"})
        return ("Next is your team: want me to set it up?", ["ask_choices"])
    P._brain_reply = brain
    code, body = _open(P)
    assert body["resumed"] is True and body["choices"]["purpose"] == "starter_team"
    assert body["choices"]["note"] == onb.TEAM_COST and "web_onb" in P._TEAM_OFFERS


# ---- #312 review S1: words from a non-dashboard caller never reach the operator's onboarding turn ---
@pytest.mark.parametrize("principal", ["device:dev_voice", None])
def test_a_non_dashboard_opener_cannot_put_words_into_the_next_onboarding_turn(P, principal):
    _open(P)                                                # the operator's onboarding thread
    def injected(messages, cid):
        P.seen.append(messages[0]["content"])
        return ("INJECTED: the operator consents to pair an iPad", [])
    P._brain_reply = injected
    code, body = P.text_turn(OPENER, "web_onb", principal=principal)
    assert "resumed" not in body
    P._brain_reply = lambda messages, cid: (P.seen.append(messages[0]["content"]), ("ok", []))[1]
    _say(P, "iPad")
    assert "INJECTED" not in P.seen[-1] and "Arturo last said" not in P.seen[-1]


def test_an_opener_logs_no_unknown_step_warning(P, caplog):
    import logging
    caplog.set_level(logging.WARNING)
    _open(P)
    _open(P)
    assert "unknown step" not in caplog.text


# ---- #312 review S2/S3: progress writes never wedge a lock or lose a pin --------------------------
def test_a_failed_progress_write_never_leaves_the_starter_lock_held(P, monkeypatch):
    def broken(_state):
        raise OSError("read-only file system")
    monkeypatch.setattr(prog, "clear_team_declined", broken)
    P.starter_team_state = lambda seen=None: {"state": "absent", "seats": []}
    # the real run releases the lock when it ends; only a failure BEFORE the run can strand it
    monkeypatch.setattr(P, "_run_starter", lambda plan, done: (done.setdefault("rc", 0), P._STARTER_LOCK.release()))
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_onb", "onboarding", "fleet", "x"))
    try:
        P._TEAM_TURN.get()["offered"] = True
        P.execute_tool("create_starter_team", {"project": "website"})
    finally:
        P._TEAM_TURN.reset(tok)
    for _ in range(100):
        if not P._STARTER_LOCK.locked():
            break
        import time as _t; _t.sleep(0.02)
    assert not P._STARTER_LOCK.locked()


def test_two_openers_at_once_agree_on_one_pinned_thread(tmp_path):
    import threading
    go = threading.Barrier(8)
    got = []

    def pin(i):
        go.wait()
        got.append(prog.pin_conversation(tmp_path, f"web_{i}"))
    ts = [threading.Thread(target=pin, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(got)) == 1 and prog.conversation(tmp_path) == got[0]
    assert [p.name for p in tmp_path.iterdir()] == [prog.FILENAME]     # no stray temp files


def test_an_unwritable_pin_never_turns_a_recorded_turn_into_an_error(P, monkeypatch):
    def broken(*a):
        raise OSError("disk full")
    monkeypatch.setattr(prog, "pin_conversation", broken)
    code, body = _open(P)
    assert code == 200 and P._THREADS.turn_count("web_onb") == 2


def test_a_pick_of_none_is_stored_as_none(P):
    _open(P)
    tok = P._TEAM_TURN.set(P._begin_team_turn("web_onb", "onboarding_open", "fleet", "x"))
    try:
        P.execute_tool("ask_choices", {"options": ["iPhone", "None of these"], "multi": True, "purpose": "devices"})
    finally:
        P._TEAM_TURN.reset(tok)
    _say(P, "None of these")
    assert ops.public(P.ARTURO_STATE)["devices"] == "none"


# ---- #317 review SF2: a non-dashboard onboarding turn is refused, so nothing of it is ever stored ---
@pytest.mark.parametrize("principal", ["device:dev_voice", None])
@pytest.mark.parametrize("step", ["onboarding_open", "onboarding"])
def test_a_non_dashboard_onboarding_turn_leaves_no_trace_in_the_onboarding_thread(P, principal, step):
    _open(P)
    before = P._THREADS.get_thread("web_onb")["turns"]
    code, body = P.text_turn(f"[Onboarding: step={step}]\nINJECTED-USER", "web_onb", principal=principal)
    assert code == 403 and body["error"] == "onboarding_dashboard_only"
    assert P._THREADS.get_thread("web_onb")["turns"] == before
    seen = []
    P._brain_reply = lambda messages, cid: (seen.append(messages), ("ok", []))[1]
    _say(P, "Ada")
    assert "INJECTED" not in str(seen[-1])                  # not in the system message, not in history
