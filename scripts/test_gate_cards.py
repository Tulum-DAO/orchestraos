import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import approval_resume as AR  # noqa: E402
import gate_cards as GCD  # noqa: E402
import gate_policy as GP  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from gate_classify import Hit  # noqa: E402
from supervision_store import SupervisionStore  # noqa: E402

DEPLOY = Hit("outside_world", "deploy", "netlify deploy --prod")
DEPLOY_DICT = DEPLOY.to_dict()
SUPERVISED_RES = GP.Resolved("watch_closely", (), 0, "rS", "agent", {"level": "role"}, True)
NAMES = {"rS": "Supervised"}
LATER = "2030-01-01T00:00:00Z"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))      # attention's default hold lookup reads this ledger
    monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
    (tmp_path / "state").mkdir()
    return tmp_path / "state" / "tasks.db"


@pytest.fixture
def ledger(db):
    s = ApprovalStore(db_path=str(db))
    s.migrate()
    return s


@pytest.fixture
def ss(db, ledger):
    s = SupervisionStore(db_path=db)
    s.migrate()
    return s


@pytest.fixture
def msgs(monkeypatch):
    out = []
    monkeypatch.setattr(GCD, "_default_send", out.append)
    return out


def _hold(ss, session="s1", hits=(DEPLOY_DICT,)):
    return ss.create_hold(agent="dev", tool="Bash", fingerprint="fp1", hits=list(hits), level="watch_closely",
                          session_id=session)


@pytest.fixture
def hold(ss):
    return _hold(ss)


def _make(ss, ledger, state, session="s1"):
    hid = _hold(ss, session=session)
    rid = GCD.file_card(ledger, hid, agent="dev", hits=[DEPLOY], resolved=SUPERVISED_RES, role_names=NAMES)
    assert ss.transition_hold(hid, state, ("filing",), card_id=rid)
    return hid, rid


def make_waiting_hold(ss, ledger, session="s1"):
    return _make(ss, ledger, "waiting", session)


def make_open_hold(ss, ledger, session="s1"):
    return _make(ss, ledger, "open", session)


def apply(ledger, rid, option_n, answer_text=None, fire=True):
    import watch_gateway as WG
    res = WG.apply_answer(ledger, rid, "option", option_n=option_n, answer_text=answer_text, fire=fire,
                          answered_by="p", surface="web")
    assert res["ok"] and res["applied"], res
    return res


# ---- the card ----
def test_build_card_shape(hold):
    kw = GCD.card_kwargs(hold, agent="dev", hits=[DEPLOY], resolved=SUPERVISED_RES, role_names=NAMES)
    assert kw["kind"] == "menu" and kw["origin"] == "gate" and kw["op_key"] == f"gate:{hold}"
    assert kw["worker_kind"] == "pane" and kw["from_agent"] == "dev" and kw["risk_level"] == "high"
    labels = ["Allow once", "Allow for this session", "Always for this agent", "Always for all agents", "Don't allow"]
    assert [o["label"] for o in kw["menu"]["options"]] == labels
    assert kw["options"] == labels                                   # decode-safe top-level shape: strings
    assert kw["menu"]["bridge"] == {"kind": "gate", "permission": True, "hold_id": hold,
                                    "hits": [DEPLOY_DICT], "watch_ok": ["1", "2", "5"]}
    assert kw["question"] == "dev wants to deploy: netlify deploy --prod"
    assert "Held because dev's role is Supervised" in kw["summary"]
    assert kw["menu"]["options"][2]["detail"] == "dev may deploy without asking"
    assert kw["menu"]["options"][4]["input_kind"] == "free_text"
    inst = GCD.card_kwargs(hold, agent="dev", hits=[Hit("install", "npm", "npm i x")], resolved=SUPERVISED_RES)
    assert inst["risk_level"] == "medium"


def test_file_card_dedupes_by_op_key(ledger, hold):
    a = GCD.file_card(ledger, hold, agent="dev", hits=[DEPLOY], resolved=SUPERVISED_RES)
    b = GCD.file_card(ledger, hold, agent="dev", hits=[DEPLOY], resolved=SUPERVISED_RES)
    assert a == b and len(ledger.find_by_op_key(f"gate:{hold}")) == 1
    row = ledger.get(a)
    assert GCD.is_gate(row) and AR._is_gate_card(row) and not AR._is_headless_card(row)


def test_kind_watch_ok_and_blocking_in_attention(ledger, ss, hold):
    rid = GCD.file_card(ledger, hold, agent="dev", hits=[DEPLOY], resolved=SUPERVISED_RES, role_names=NAMES)
    import attention_items as AI
    ss.set_hold(hold, state="open")
    busy = AI.from_pending_row(ledger.get(rid), AI.Ctx(person="p", agent_states={"dev": "working"}))
    idle = AI.from_pending_row(ledger.get(rid), AI.Ctx(person="p", agent_states={"dev": "idle"}))
    assert busy["kind"] == "permission" and busy["blocking"] is False and idle["blocking"] is True   # open hold, D2
    ss.set_hold(hold, state="waiting")
    assert AI.from_pending_row(ledger.get(rid), AI.Ctx(person="p", agent_states={"dev": "working"}))["blocking"] is True
    assert [a["watch_ok"] for a in busy["actions"]] == [True, True, False, False, True]
    assert busy["actions"][4]["needs_text"] == "optional"
    # the gateway can hand the states in (no lookup)
    pre = AI.Ctx(person="p", agent_states={"dev": "working"}, gate_holds={hold: "open"})
    assert AI.from_pending_row(ledger.get(rid), pre)["blocking"] is False


def test_other_menus_keep_their_watch_ok_and_required_text():
    import attention_items as AI
    row = {"id": "apr_x", "kind": "menu", "from_agent": "a",
           "menu": json.dumps({"question": "q", "options": [
               {"n": "1", "label": "A", "input_kind": "direct"}, {"n": "2", "label": "B", "input_kind": "chat"},
               {"n": "3", "label": "C", "input_kind": "free_text"}]})}
    it = AI.from_pending_row(row, AI.Ctx(person="p"))
    assert [(a["watch_ok"], a["needs_text"]) for a in it["actions"]] == [(True, "none"), (False, "none"),
                                                                         (True, "required")]
    assert it["blocking"] is True


# ---- the lane ----
@pytest.mark.parametrize("n,decision,grants", [
    ("1", "allow_once", []), ("2", "allow_session", [("session", "s1")]),
    ("3", "allow_agent", [("agent", "dev")]), ("4", "allow_fleet", [("fleet", None)]), ("5", "deny", [])])
def test_answer_writes_grants_and_hold(ledger, ss, n, decision, grants, msgs):
    hold, rid = make_waiting_hold(ss, ledger, session="s1")
    apply(ledger, rid, option_n=n)                          # watch_gateway.apply_answer(fire=True)
    h = ss.hold(hold)
    assert h["decision"] == decision and h["state"] == ("deny" == decision and "denied" or "allowed")
    got = [(x["scope"], x["scope_ref"]) for x in ss.grants("p")]
    assert got == grants and all(x["narrow_action"] == "deploy" for x in ss.grants("p"))
    assert all(x["source"] == "card" and x["source_card"] == rid for x in ss.grants("p"))
    assert ledger.get(rid)["status"] == "resumed"
    assert h["retry_until"] is None                         # the waiting hook answers in place: no pass
    assert msgs == []                                       # a waiting hook needs no message


def test_answer_on_open_hold_messages_agent_retry_now_with_one_time_pass(ledger, ss, msgs):   # the default path (D2)
    hold, rid = make_open_hold(ss, ledger)
    apply(ledger, rid, option_n="1")
    h = ss.hold(hold)
    assert h["state"] == "allowed" and h["retry_until"] and h["notified_at"]
    assert msgs[0]["to_agent"] == "dev" and msgs[0]["type"] == "gate_answer" and "Retry it now" in msgs[0]["body"]
    assert msgs[0]["from_agent"] == "gate" and msgs[0]["priority"] == "high"
    assert msgs[0]["body"] == "Allowed by p: `netlify deploy --prod`. Retry it now; it will pass once."
    assert ss.take_retry("dev", "fp1") == hold and ss.take_retry("dev", "fp1") is None        # passes once
    assert ledger.get(rid)["status"] == "resumed"


def test_always_on_open_hold_names_the_grant(ledger, ss, msgs):
    hold, rid = make_open_hold(ss, ledger)
    apply(ledger, rid, option_n="3")
    assert "from now on" in msgs[0]["body"]
    assert msgs[0]["body"] == ("Allowed by p from now on: dev may deploy without asking. "
                               "Retry `netlify deploy --prod` now.")
    assert ss.hold(hold)["retry_until"] is None


def test_deny_on_open_hold_says_drop_it_with_words(ledger, ss, msgs):
    hold, rid = make_open_hold(ss, ledger)
    apply(ledger, rid, option_n="5", answer_text="not on a Friday")
    assert "not on a Friday" in msgs[0]["body"] and "Drop it" in msgs[0]["body"] and ss.hold(hold)["decision"] == "deny"
    assert msgs[0]["body"] == "Not allowed by p: `netlify deploy --prod`. not on a Friday. Drop it."


def test_dont_allow_words_are_optional(ledger, ss, msgs):
    hold, rid = make_open_hold(ss, ledger)
    apply(ledger, rid, option_n="5")
    assert msgs[0]["body"] == "Not allowed by p: `netlify deploy --prod`. Drop it."


def test_message_is_sent_at_most_once(ledger, ss, msgs):
    hold, rid = make_open_hold(ss, ledger)
    apply(ledger, rid, option_n="1")
    AR.fire_resume(ledger.get(rid), ledger)
    assert len(msgs) == 1
    GCD.deliver(dict(ledger.get(rid), status="answered"), ledger, msg_send=msgs.append)   # forced replay
    assert len(msgs) == 1


def test_failed_message_keeps_the_card_for_the_watchdog(ledger, ss, msgs, monkeypatch):
    hold, rid = make_open_hold(ss, ledger)

    def boom(m):
        raise RuntimeError("inbox down")
    monkeypatch.setattr(GCD, "_default_send", boom)
    apply(ledger, rid, option_n="3")
    assert ss.hold(hold)["state"] == "allowed" and ss.hold(hold)["notified_at"] is None
    assert ledger.get(rid)["status"] == "answered"
    monkeypatch.setattr(GCD, "_default_send", msgs.append)
    AR.fire_resume(ledger.get(rid), ledger)                 # the watchdog's next beat
    assert len(msgs) == 1 and ledger.get(rid)["status"] == "resumed" and len(ss.grants("p")) == 1


def test_grant_made_elsewhere_releases_open_hold_and_messages(ledger, ss, msgs, tmp_path):
    hold, rid = make_open_hold(ss, ledger)
    other, other_rid = make_open_hold(ss, ledger)
    ss.set_hold(other, hits=[Hit("destructive", "delete_files", "rm -rf x", folder="/p/x").to_dict()])
    ss.create_grant("p", scope="agent", scope_ref="dev", category="outside_world", narrow={"action": "deploy"},
                    source="drawer", by="p")
    out = GCD.release_covered(ss, ledger, now=LATER, msg_send=msgs.append, person="p", registry={},
                              data_dir=str(tmp_path))
    assert out == [hold]
    assert ss.hold(hold)["state"] == "released" and ledger.get(rid)["status"] == "retired" and "retry" in msgs[0]["body"]
    assert ss.hold(hold)["decision"] == "covered" and len(msgs) == 1
    assert ss.hold(other)["state"] == "open" and ledger.get(other_rid)["status"] == "pending"
    assert GCD.release_covered(ss, ledger, now=LATER, msg_send=msgs.append, person="p", registry={},
                               data_dir=str(tmp_path)) == []


def test_replay_is_idempotent(ledger, ss, msgs):
    hold, rid = make_waiting_hold(ss, ledger)
    apply(ledger, rid, option_n="3")
    AR.fire_resume(ledger.get(rid), ledger)                 # the watchdog replays
    GCD.deliver(dict(ledger.get(rid), status="answered"), ledger)
    assert len(ss.grants("p")) == 1


def test_answer_on_a_final_hold_is_stale(ledger, ss, msgs):
    hold, rid = make_open_hold(ss, ledger)
    assert ss.transition_hold(hold, "withdrawn", ("open",))
    apply(ledger, rid, option_n="1")
    assert ss.hold(hold)["state"] == "withdrawn" and msgs == [] and ledger.get(rid)["status"] == "resumed"


@pytest.mark.parametrize("n", ["2", "3", "4"])
def test_an_answer_racing_a_final_hold_mints_no_grant(ledger, ss, msgs, n):
    hold, rid = make_open_hold(ss, ledger)
    assert ss.transition_hold(hold, "withdrawn", ("open",))
    apply(ledger, rid, option_n=n)
    assert ss.hold(hold)["state"] == "withdrawn" and ss.grants("p") == []


def test_a_session_grant_from_a_card_names_its_agent(ledger, ss, msgs):
    hold, rid = make_waiting_hold(ss, ledger, session="s1")
    apply(ledger, rid, option_n="2")
    assert [(g["scope"], g["scope_ref"], g["agent"]) for g in ss.grants("p")] == [("session", "s1", "dev")]


def test_dismissed_gate_card_is_a_deny(ledger, ss, msgs):
    hold, rid = make_open_hold(ss, ledger)
    assert ledger.discard(rid)
    out = AR.deliver_discarded(ledger.get(rid), ledger)
    assert out["decision"] == "deny" and ss.hold(hold)["state"] == "denied"
    assert msgs[0]["body"].endswith("Drop it.") and ss.grants("p") == []


def test_grant_confirm_card(ledger, ss, msgs):
    gid, rid = GCD.propose_grant(ledger, ss, agent="dev", category="install", narrow={"action": "npm"},
                                 scope="agent", said="go ahead and npm install", person="p")
    assert ss.grant(gid)["state"] == "pending"
    row = ledger.get(rid)
    assert row["question"] == "Confirm: dev may install npm packages without asking?"
    assert row["summary"] == "You said in chat: 'go ahead and npm install'" and row["origin"] == "gate"
    assert json.loads(row["menu"])["bridge"] == {"kind": "gate_grant", "grant_id": gid, "scope": "agent",
                                                  "permission": False}
    apply(ledger, rid, option_n="1")
    assert ss.grant(gid)["state"] == "active" and ledger.get(rid)["status"] == "resumed" and msgs == []


def test_grant_confirm_declined_and_dismissed(ledger, ss):
    gid, rid = GCD.propose_grant(ledger, ss, agent="dev", category="install", narrow={"action": "pip"}, person="p")
    apply(ledger, rid, option_n="2")
    assert ss.grant(gid)["state"] == "declined"
    gid2, rid2 = GCD.propose_grant(ledger, ss, agent="dev", category="install", narrow={"action": "npm"}, person="p")
    ledger.discard(rid2)
    AR.deliver_discarded(ledger.get(rid2), ledger)
    assert ss.grant(gid2)["state"] == "declined"
    import attention_items as AI
    assert AI.from_pending_row(dict(ledger.get(rid2), status="pending"), AI.Ctx(person="p"))["blocking"] is False


def test_decision_of():
    def row(n=None, status="answered", kind="gate"):
        return {"option_n": n, "status": status, "menu": json.dumps({"bridge": {"kind": kind}})}
    assert [GCD.decision_of(row(n)) for n in "12345"] == ["allow_once", "allow_session", "allow_agent",
                                                          "allow_fleet", "deny"]
    assert GCD.decision_of(row(status="discarded")) == "deny" and GCD.decision_of(row(status="retired")) == "deny"
    assert GCD.decision_of(row("9")) is None and GCD.decision_of({"menu": "{}"}) is None
    assert GCD.decision_of(row("1", kind="gate_grant")) == "confirm"
    assert GCD.decision_of(row(status="discarded", kind="gate_grant")) == "decline"


def test_grants_for_narrowing():
    hits = [Hit("outside_world", "message", "mail", recipient="ann@example.com").to_dict(),
            Hit("outside_folder", "edit_outside", "x", folder="/srv/site").to_dict(),
            Hit("outside_folder", "edit_outside", "y", folder="/srv/site").to_dict()]
    hold = {"agent": "dev-gen3", "session_id": None, "hits": json.dumps(hits)}
    g = GCD.grants_for(hold, "allow_agent", "apr_1")
    assert [(x["scope"], x["scope_ref"], x["category"], x["narrow"]) for x in g] == [
        ("agent", "dev", "outside_world", {"action": "message", "recipient": "ann@example.com"}),
        ("agent", "dev", "outside_folder", {"action": "edit_outside", "folder": "/srv/site"})]
    assert GCD.grants_for(hold, "allow_session", "apr_1") == []          # no session on the hold
    assert GCD.grants_for(hold, "allow_once", "apr_1") == [] and GCD.grants_for(hold, "deny", "apr_1") == []


def test_gate_cards_never_take_a_menu_lane(ledger, ss, msgs, monkeypatch):
    for name in ("_fire_menu_resume", "_fire_menu_inject", "_fire_pane_resume", "coalesce_menu_injects",
                 "fire_menu_batch_resume", "_default_inject", "_default_msg_send", "_fire_human_task_resume"):
        monkeypatch.setattr(AR, name, lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(_n)))
    hold, rid = make_waiting_hold(ss, ledger)
    apply(ledger, rid, option_n="1")
    assert ss.hold(hold)["state"] == "allowed" and ledger.get(rid)["status"] == "resumed"
    # the watchdog: an answered gate card is not an "authored menu" to coalesce; it re-enters the gate lane
    hold2, rid2 = make_open_hold(ss, ledger)
    apply(ledger, rid2, option_n="5", fire=False)
    row = ledger.get(rid2)
    assert AR._is_authored_menu(row, live_fn=lambda s: False) is False
    AR.watchdog(ledger, live_fn=lambda s: False)
    assert ss.hold(hold2)["state"] == "denied" and ledger.get(rid2)["status"] == "resumed" and len(msgs) == 1


def test_coalesce_skips_gate_cards(ledger, ss):
    hold, rid = make_open_hold(ss, ledger)
    apply(ledger, rid, option_n="1", fire=False)
    sent = []
    out = AR.coalesce_menu_injects([ledger.get(rid)], ledger, resolve=lambda a: ("sess", None),
                                   inject=lambda s, b: sent.append(b) or (True, {}),
                                   msg_send=lambda r, b: sent.append(b))
    assert out == [] and sent == [] and ledger.get(rid)["status"] == "answered"


def test_free_text_still_required_on_other_menus(ledger):
    import watch_gateway as WG
    rid = ledger.create(from_agent="a", question="q", worker_kind="pane", kind="menu",
                        menu={"question": "q", "options": [{"n": "1", "label": "Other", "input_kind": "free_text"}]},
                        options=["Other"], op_key="x:1")
    res = WG.apply_answer(ledger, rid, "option", option_n="1", fire=False)
    assert res["ok"] is False and "answer_text required" in res["error"]


def test_default_send_respects_the_real_seam_fence(monkeypatch):
    monkeypatch.setenv("APPROVAL_RESUME_BLOCK_REAL_SEAMS", "1")
    with pytest.raises(RuntimeError):
        GCD.__dict__["_default_send"]({"to_agent": "dev"})


def test_menu_bridge_and_session_scoped_ledger_reads_never_see_gate_cards(ledger, ss):
    import menu_bridge
    hold, rid = make_open_hold(ss, ledger)
    assert menu_bridge.ledger_pending_op_keys(ledger) == set()              # reconcile_orphans' candidate set
    assert ledger.pending_menu_row_for_session("dev") is None               # multi-part submit anchor
    assert ledger.resolve_pending_menus_for_session("dev") == 0
    assert ledger.get(rid)["status"] == "pending"
    import headless_cards
    assert headless_cards.bridge_of(ledger.get(rid)) is None


def test_grants_belong_to_the_configured_person_when_the_card_says_operator(db, tmp_path):
    # Every web and gateway answer records answered_by="operator"; the hook looks grants up under the
    # gate.json person ([operator].id), so a card's "operator" must resolve to that person.
    d = tmp_path / "state" / "attention"
    d.mkdir(parents=True, exist_ok=True)
    (d / "gate.json").write_text(json.dumps({"person": "alex"}))
    assert GCD._person_of({"answered_by": "operator"}) == "alex"
    assert GCD._person_of({"answered_by": "sam"}) == "sam"             # a named person stays that person
    assert GCD._person_of({"answered_by": "rule:rul_1"}) == "alex"      # a rule is not a person
    assert GCD._person_of(None) == "alex"
