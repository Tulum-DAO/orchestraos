"""Timeout rules, part 2b: hand off, choose for me, and the on-behalf answer (plan §8, Tasks 7 and 7a).

Release gate, like test_attention_rules.py: hand-off and choose-for-me never act on a permission card, a grant
proposal, a high-risk or hard-to-undo card, a declared-category card that would be held, or an ungated agent's
card. The agent's own session does the deciding (no side model); its answer is recorded "on your behalf"."""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import answer_caller as AC  # noqa: E402
import attention_detect as D  # noqa: E402
import attention_items as I  # noqa: E402
import attention_rules as AR  # noqa: E402
import gate_cards as GCD  # noqa: E402
import gate_policy as GP  # noqa: E402
import scripts.watch_gateway as G  # noqa: E402
from gate_classify import Hit  # noqa: E402
from supervision_store import SupervisionStore  # noqa: E402
from test_attention_rules import (NOW, LATER, at, astore, calls, db, ledger, only, qstore, rs,  # noqa: E402,F401
                                  _clock_rules_to)

CLI = str(HERE / "approval.py")
MENU_AB = {"question": "Pick", "options": [{"n": "1", "label": "A", "input_kind": "direct"},
                                           {"n": "2", "label": "B", "input_kind": "direct"}]}


@pytest.fixture
def registry():
    return {"dev": {"reports_to": "pm", "runtime": "claude"}, "pm": {"runtime": "claude"},
            "qa": {"reports_to": "pm", "runtime": "claude"}}


@pytest.fixture
def agent_states():
    return {"dev": "working", "pm": "idle", "qa": "idle"}


@pytest.fixture
def msgs():
    return []


@pytest.fixture
def status():
    return {}            # agent -> gate coverage; default protected


@pytest.fixture
def ss(ledger):
    s = SupervisionStore(db_path=ledger.db_path)
    s.migrate()
    return s


def _gate_view(ss, registry, status):
    def held(agent, hits):
        res = GP.resolve(ss.person_rows("alex"), {r["id"]: r for r in ss.roles("alex")}, ss.assignments("alex"),
                         agent, None)
        return GP.held_hits(hits, resolved=res, grants=ss.grants("alex", states=("active",)), agent=agent)
    return AR.GateView(covers=lambda item, raw: GP.covers_item(item, raw, ss, person="alex", registry={"agents": registry}),
                       held=held, status=lambda a: status.get(a, "protected"))


@pytest.fixture
def engine(rs, astore, ledger, qstore, calls, msgs, registry, agent_states, ss, status):
    def observe(now):
        ictx = I.Ctx(person="alex", now=now, agent_states=dict(agent_states))
        waiting = I.waiting_items(ledger.pending_to_notify(), [G._qnr_pseudo_row(q) for q in qstore.list_pending()],
                                  [], ictx)
        return D.unanswered(waiting)

    ctx = AR.EngineCtx(
        person="alex", ledger=ledger, qstore=qstore, apply_answer=G.apply_answer,
        notify_card=lambda rid: calls.notified.append(rid),
        notify_questionnaire=lambda qid: calls.notified.append(qid),
        deliver_discarded=lambda row, store: calls.discarded.append(row["id"]),
        qnr_deliver_discarded=lambda row, store: calls.discarded.append(row["id"]),
        schedule_resume=lambda row: calls.resumed.append(row["id"]),
        is_protected=lambda agent: agent == "protected-seat", observe=observe,
        msg_send=msgs.append, registry=lambda: registry, agent_states=lambda: dict(agent_states),
        history=ledger.history, prefs=lambda: {"instructions": "Prefer the cheaper option."},
        gate=_gate_view(ss, registry, status))
    return AR.Engine(rs, astore, ctx)


def card(ledger, **kw):
    kw.setdefault("from_agent", "dev")
    kw.setdefault("question", "Ship it?")
    kw.setdefault("worker_kind", "pane")
    rid = ledger.create(**kw)
    at(ledger, rid, NOW)
    return rid


def fire(rs, target, action):
    return next(json.loads(f["result"]) for f in rs.fires() if f["target"] == target and f["action"] == action)


# ---- availability and validation -----------------------------------------------------------------------------

def test_part2_actions_are_available_now(rs):
    assert AR.ACTIONS["hand_off"].available() == (True, "")
    assert AR.ACTIONS["choose_for_me"].available() == (True, "")
    av = {a["type"]: a["available"] for a in AR.available_actions()}
    assert av["hand_off"] and av["choose_for_me"]


def test_hand_off_targets_are_validated(rs, monkeypatch, registry):
    monkeypatch.setattr(AR, "_registry_agents", lambda: registry)
    with pytest.raises(AR.RuleInvalid, match="unknown_person"):
        rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "hand_off", "to": "alex"}])
    with pytest.raises(AR.RuleInvalid, match="unknown_agent"):
        rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "hand_off", "to": "nobody"}])
    ok = rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "hand_off", "to": "qa"}])
    assert ok["actions"] == [{"type": "hand_off", "to": "qa"}]
    assert rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])["actions"] == [
        {"type": "hand_off", "to": "supervisor"}]


def test_choose_for_me_params_are_validated(rs):
    with pytest.raises(AR.RuleInvalid, match="bad_by"):
        rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "anyone"}])
    r = rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me"}])
    assert r["actions"] == [{"type": "choose_for_me", "by": "self"}]
    with pytest.raises(AR.RuleInvalid):
        rs.create("alex", trigger="no_progress", after_s=1200, actions=[{"type": "choose_for_me"}])


# ---- hand off ---------------------------------------------------------------------------------------------------

def test_hand_off_to_supervisor_sends_card_and_command(rs, engine, ledger, msgs):
    rid = card(ledger, question="Pick", kind="menu", menu=MENU_AB, options=["A", "B"])
    r = only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off", "to": "supervisor"}])
    engine.tick(LATER)
    m = msgs[0]
    assert m["to_agent"] == "pm" and m["type"] == "handoff" and m["from_agent"] == "attention-rules"
    assert f"--on-behalf-of {r['id']}" in m["body"] and f"--id {rid}" in m["body"] and "1. A" in m["body"]
    assert ledger.get(rid)["status"] == "pending"                    # still the person's too
    res = fire(rs, rid, "hand_off")
    assert res["ok"] and res["detail"] == "handed_off" and res["assignee"] == "pm" and res["to"] == "pm"
    assert rs.on_behalf_map() == {}                                  # recorded only when the answer arrives


def test_hand_off_to_a_named_agent(rs, engine, ledger, msgs, monkeypatch, registry):
    monkeypatch.setattr(AR, "_registry_agents", lambda: registry)
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off", "to": "qa"}])
    engine.tick(LATER)
    assert msgs[0]["to_agent"] == "qa" and "--answer approve" in msgs[0]["body"]
    assert fire(rs, rid, "hand_off")["assignee"] == "qa"


def test_delivery_failure_retries_then_gives_up(rs, engine, ledger, msgs):
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])

    def boom(m):
        raise RuntimeError("msg store down")
    engine.ctx.msg_send = boom
    seen = []
    for i in range(5):
        engine.tick(LATER + timedelta(minutes=11 * i))
        seen.append(fire(rs, rid, "hand_off")["detail"].split(":")[0])
    assert seen == ["retry", "retry", "retry", "gave_up", "gave_up"]
    assert ledger.get(rid)["status"] == "pending"


def test_missing_supervisor_appearing_later_is_picked_up(rs, engine, ledger, msgs, registry):
    registry["dev"].pop("reports_to")
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])
    engine.tick(LATER)
    assert msgs == [] and fire(rs, rid, "hand_off")["detail"] == "retry:no_supervisor"
    registry["dev"]["reports_to"] = "pm"
    engine.tick(LATER + timedelta(minutes=5))
    assert msgs == []                                                # not due yet (10 min)
    engine.tick(LATER + timedelta(minutes=11))
    assert msgs[0]["to_agent"] == "pm" and fire(rs, rid, "hand_off")["detail"] == "handed_off"


# ---- choose for me -----------------------------------------------------------------------------------------------

def test_choose_for_me_asks_the_asker_only_when_not_blocking(rs, engine, ledger, msgs, agent_states):
    rid = card(ledger)                                               # a plain approval
    agent_states["dev"] = "working"                                  # not stopped on it -> it can take a turn
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me"}])
    engine.tick(LATER)
    assert msgs[0]["to_agent"] == "dev" and msgs[0]["type"] == "choose_for_me"
    assert fire(rs, rid, "choose_for_me")["assignee"] == "dev"


def test_choose_for_me_goes_to_supervisor_when_the_asker_is_stopped(rs, engine, ledger, msgs, agent_states):
    agent_states["dev"] = "idle"                                     # a plain approval with its agent idle: blocking
    card(ledger)
    card(ledger, question="Pick", kind="menu", menu=MENU_AB, options=["A", "B"])   # a menu always blocks
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me"}])
    engine.tick(LATER)
    assert [m["to_agent"] for m in msgs] == ["pm", "pm"]


def test_choose_for_me_by_supervisor(rs, engine, ledger, msgs):
    card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    assert msgs[0]["to_agent"] == "pm"


def test_choose_for_me_includes_intent_and_prior_answers(rs, engine, ledger, msgs):
    old = ledger.create(from_agent="dev", question="Use the small box?", worker_kind="pane")
    G.apply_answer(ledger, old, "approve", fire=False, surface="web", answered_by="operator")
    rid = card(ledger, default_option="approve")
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    body = msgs[0]["body"]
    assert "Prefer the cheaper option." in body
    assert "Use the small box? → approve (operator)" in body
    assert "default: approve" in body.lower()
    assert f"--on-behalf-of" in body and "If you can't tell, don't answer." in body


def test_the_chooser_must_be_able_to_take_a_turn(rs, engine, ledger, msgs, agent_states):
    agent_states["pm"] = "crashed"
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    assert msgs == [] and fire(rs, rid, "choose_for_me")["detail"] == "retry:chooser_crashed"


def test_no_answer_within_the_window_is_recorded(rs, engine, ledger, msgs):
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor",
                                                           "answer_within_s": 900}])
    engine.tick(LATER)
    engine.tick(LATER + timedelta(minutes=16))
    assert fire(rs, rid, "choose_for_me")["detail"] == "no_answer_from:pm"
    assert ledger.get(rid)["status"] == "pending" and len(msgs) == 1


def test_a_claim_racing_the_expiry_keeps_the_claim(rs, engine, ledger, msgs, monkeypatch):
    # The chooser claims between expire_asks' read and its write: the claim (and its pending record) must survive.
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor",
                                                           "answer_within_s": 900}])
    engine.tick(LATER)
    rule_id = next(f["rule_id"] for f in rs.fires() if f["target"] == rid)
    real_fires = rs.fires

    def fires_then_claim():
        rows = real_fires()
        rs.consume(rule_id, rid, "pm", pending_on_behalf={"answer": "approve"})   # lands after the read
        return rows
    monkeypatch.setattr(rs, "fires", fires_then_claim)
    assert rs.expire_asks(LATER + timedelta(minutes=16)) == []
    monkeypatch.setattr(rs, "fires", real_fires)
    res = fire(rs, rid, "choose_for_me")
    assert res["ok"] and res["consumed_by"] == "pm" and res["pending_on_behalf"] == {"answer": "approve"}
    assert res["detail"] == "asked"


# ---- the safety gate (release gate) --------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [{"risk_level": "high"}, {"reversibility": "irreversible"},
                                   {"reversibility": "hard"}])
@pytest.mark.parametrize("action", [{"type": "hand_off"}, {"type": "choose_for_me", "by": "supervisor"}])
def test_never_on_risky_cards(rs, engine, ledger, msgs, extra, action):
    rid = card(ledger, **extra)
    only(rs, trigger="unanswered", after_s=600, actions=[action])
    engine.tick(LATER)
    assert msgs == [] and fire(rs, rid, action["type"])["detail"].startswith("safety:")


def _gate_card(ss, ledger, agent="dev"):
    hid = ss.create_hold(agent=agent, tool="Bash", fingerprint="fp1",
                         hits=[Hit("outside_world", "deploy", "netlify deploy").to_dict()],
                         level="watch_closely", session_id="s1")
    rid = GCD.file_card(ledger, hid, agent=agent, hits=[Hit("outside_world", "deploy", "netlify deploy")],
                        resolved=GP.Resolved("watch_closely", (), 0, None, "person", {}, True))
    ss.transition_hold(hid, "open", ("filing",), card_id=rid)
    at(ledger, rid, NOW)
    return hid, rid


@pytest.mark.parametrize("action", [{"type": "hand_off"}, {"type": "choose_for_me", "by": "supervisor"},
                                    {"type": "choose_for_me"}])
def test_never_on_gate_cards_or_grant_proposals(rs, engine, ledger, msgs, ss, action, monkeypatch):
    monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
    _, gate = _gate_card(ss, ledger)
    _, prop = GCD.propose_grant(ledger, ss, agent="dev", category="outside_world", narrow={"action": "deploy"},
                                said="go", person="alex")
    at(ledger, prop, NOW)
    only(rs, trigger="unanswered", after_s=600, actions=[action])
    engine.tick(LATER)
    assert msgs == []
    assert fire(rs, gate, action["type"])["detail"] == "safety:gate_card"
    assert fire(rs, prop, action["type"])["detail"] == "safety:gate_card"


def test_a_covered_gate_card_is_left_to_the_gate(rs, engine, ledger, ss, msgs, monkeypatch):
    monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
    _, gate = _gate_card(ss, ledger)
    ss.create_grant("alex", scope="agent", scope_ref="dev", category="outside_world", narrow={"action": "deploy"},
                    source="settings", by="alex")
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}, {"type": "expire"}])
    engine.tick(LATER)
    assert msgs == [] and fire(rs, gate, "hand_off")["detail"] == "covered:released_by_gate"
    assert fire(rs, gate, "expire")["detail"] == "covered:released_by_gate"


def test_declared_category_card_is_refused_when_it_would_be_held(rs, engine, ledger, ss, msgs, monkeypatch):
    ss.set_agent("alex", "dev", by="alex", level="watch_closely")
    menu = {"question": "Deploy now?", "options": [{"n": "1", "label": "Yes", "input_kind": "direct"}],
            "gate_hits": [["outside_world", "deploy"]]}
    rid = card(ledger, question="Deploy now?", kind="menu", menu=menu, options=["Yes"], default_option="1")
    only(rs, trigger="unanswered", after_s=600,
         actions=[{"type": "expire", "mode": "default"}, {"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    assert msgs == [] and ledger.get(rid)["status"] == "pending"
    assert {f["action"]: json.loads(f["result"])["detail"] for f in rs.fires() if f["target"] == rid} == {
        "expire": "safety:gated:outside_world", "choose_for_me": "safety:gated:outside_world"}


def test_declared_category_allowed_by_level_or_grant_proceeds(rs, engine, ledger, ss, msgs):
    menu = {"question": "Deploy now?", "options": [{"n": "1", "label": "Yes", "input_kind": "direct"}],
            "gate_hits": [["outside_world", "deploy"]]}
    rid = card(ledger, question="Deploy now?", kind="menu", menu=menu, options=["Yes"])
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])
    engine.tick(LATER)                                               # Let them run (default): nothing held
    assert msgs and fire(rs, rid, "hand_off")["detail"] == "handed_off"


def test_undeclared_card_from_ungated_runtime_is_refused(rs, engine, ledger, registry, msgs, status):
    registry["cx"] = {"runtime": "codex", "reports_to": "pm"}
    status["cx"] = "not_supported"
    rid = card(ledger, from_agent="cx", question="Ok?")
    low = card(ledger, from_agent="cx", question="Tidy?", risk_level="low", reversibility="easy")
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    assert fire(rs, rid, "choose_for_me")["detail"] == "safety:ungated_agent"
    assert fire(rs, low, "choose_for_me")["detail"] == "asked" and [m["to_agent"] for m in msgs] == ["pm"]


def test_a_protected_seat_is_never_handed_off(rs, engine, ledger, msgs, registry):
    registry["protected-seat"] = {"reports_to": "pm"}
    rid = card(ledger, from_agent="protected-seat")
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])
    engine.tick(LATER)
    assert msgs == [] and fire(rs, rid, "hand_off")["detail"] == "safety:protected_agent"


def test_validate_refuses_hand_off_scoped_to_permission_cards(rs):
    for a in ({"type": "hand_off"}, {"type": "choose_for_me"}):
        with pytest.raises(AR.RuleInvalid, match="unsafe_scope"):
            rs.create("alex", trigger="unanswered", after_s=600, scope_kind="permission", actions=[a])


# ---- the on-behalf answer (approval.py answer --on-behalf-of) ---------------------------------------------------

@pytest.fixture
def cli_env(db, tmp_path, registry):
    import gate_config as GC
    data = Path(db).parent.parent
    (data / "registry.json").write_text(json.dumps({"agents": registry}))
    GC.write_gate(str(data), mode="enforce")                          # the gate covers dev (a Claude seat)
    for a in registry:
        GC.heartbeat(str(data), a, None, "enforce")
    env = {k: v for k, v in os.environ.items() if k != "ORCHESTRA_AGENT_ID"}
    env.update(APPROVAL_DB_PATH=db, ORCHESTRA_DIR=str(data), ORCH_DIR=str(data), APPROVAL_DDL_ARMED="all", GATE_CARDS_NOTIFY="0",
               APPROVAL_RESUME_BLOCK_REAL_SEAMS="1")
    env.pop("TMUX_PANE", None)
    return env


def run_cli(env, agent, *args):
    e = dict(env)
    if agent:
        e["ORCHESTRA_AGENT_ID"] = agent
    return subprocess.run([sys.executable, CLI, "answer", *args], env=e, capture_output=True, text=True, timeout=60)


def on_behalf(env, agent, rid, rule_id, *, answered_by=None, option=None, answer="approve", reasoning="fits"):
    args = ["--id", rid, "--surface", "rule", "--answered-by", answered_by or agent, "--on-behalf-of", rule_id,
            "--reasoning", reasoning]
    args += ["--answer", "option", "--option-n", option] if option else ["--answer", answer]
    return run_cli(env, agent, *args)


def _handed(rs, engine, ledger, **kw):
    rid = card(ledger, **kw)
    r = only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])
    engine.tick(LATER)
    assert fire(rs, rid, "hand_off")["detail"] == "handed_off"
    return rid, r


def test_on_behalf_answer_records_reasoning(rs, engine, ledger, astore, cli_env):
    rid, r = _handed(rs, engine, ledger, question="Pick", kind="menu", menu=MENU_AB, options=["A", "B"])
    p = on_behalf(cli_env, "pm", rid, r["id"], option="2", reasoning="matches last week")
    assert p.returncode == 0, p.stderr
    row = ledger.get(rid)
    assert (row["answer_surface"], row["answered_by"], row["option_n"]) == ("rule", "pm", "2")
    ob = astore.on_behalf_for(rid)
    assert ob["by"] == "pm" and ob["reasoning"] == "matches last week" and ob["chosen"] == "B"
    assert ob["action"] == "hand_off" and ob["rule_id"] == r["id"]
    assert rs.on_behalf_map()[rid]["by"] == "pm"


def test_only_the_assignee_may_answer_and_only_once(rs, engine, ledger, cli_env):
    rid, r = _handed(rs, engine, ledger)
    p = on_behalf(cli_env, "qa", rid, r["id"])
    assert p.returncode == 4 and "handed to pm" in p.stderr
    p = on_behalf(cli_env, "pm-gen2", rid, r["id"])
    assert p.returncode == 0, p.stderr
    p = on_behalf(cli_env, "pm", rid, r["id"])
    assert p.returncode == 4 and "not waiting any more" in p.stderr


def test_the_claim_is_one_cas(rs, engine, ledger):
    rid, r = _handed(rs, engine, ledger)
    with pytest.raises(AR.RuleInvalid, match="handed_to_other"):
        rs.consume(r["id"], rid, "qa")
    action, res = rs.consume(r["id"], rid, "pm-gen3")
    assert action == "hand_off" and res["consumed_by"] == "pm-gen3"
    with pytest.raises(AR.RuleInvalid, match="already_answered"):
        rs.consume(r["id"], rid, "pm")
    rs.unconsume(r["id"], rid, "hand_off")
    assert rs.consume(r["id"], rid, "pm")[0] == "hand_off"
    with pytest.raises(AR.RuleInvalid, match="no_handoff"):
        rs.consume("rul_other", rid, "pm")


def test_the_caller_must_be_the_agent_it_names(rs, engine, ledger, cli_env):
    rid, r = _handed(rs, engine, ledger)
    p = on_behalf(cli_env, "qa", rid, r["id"], answered_by="pm")       # qa claims to be pm
    assert p.returncode == 4 and "you are qa" in p.stderr
    assert ledger.get(rid)["status"] == "pending"
    if not AC.of_process().agents:                                     # a person's shell is no agent at all
        p = on_behalf(cli_env, None, rid, r["id"], answered_by="pm")
        assert p.returncode == 4 and "own session" in p.stderr
    assert on_behalf(cli_env, "pm", rid, r["id"]).returncode == 0     # the consume was not spent


def test_on_behalf_needs_surface_rule_and_an_agent(rs, engine, ledger, cli_env):
    rid, r = _handed(rs, engine, ledger)
    p = run_cli(cli_env, "pm", "--id", rid, "--answer", "approve", "--surface", "web", "--answered-by", "pm",
                "--on-behalf-of", r["id"], "--reasoning", "x")
    assert p.returncode == 2
    p = run_cli(cli_env, "pm", "--id", rid, "--answer", "approve", "--surface", "rule", "--answered-by",
                "operator", "--on-behalf-of", r["id"], "--reasoning", "x")
    assert p.returncode == 4


def test_on_behalf_answer_refused_without_a_handoff(rs, engine, ledger, cli_env):
    rid = card(ledger)
    r = only(rs, trigger="unanswered", after_s=3600 * 24, actions=[{"type": "hand_off"}])   # not fired yet
    p = on_behalf(cli_env, "pm", rid, r["id"])
    assert p.returncode == 4 and "no rule handed this card to you" in p.stderr
    assert ledger.get(rid)["status"] == "pending"


def test_on_behalf_rechecks_the_safety_gate(rs, engine, ledger, cli_env):
    rid, r = _handed(rs, engine, ledger)
    c = sqlite3.connect(ledger.db_path)
    c.execute("UPDATE approval_requests SET risk_level='high' WHERE id=?", [rid])      # became risky since
    c.commit(); c.close()
    p = on_behalf(cli_env, "pm", rid, r["id"])
    assert p.returncode == 4 and "needs your person" in p.stderr and "high_risk" in p.stderr
    assert ledger.get(rid)["status"] == "pending"
    assert json.loads(next(f for f in rs.fires() if f["target"] == rid)["result"]).get("consumed_at") is None


def test_a_forged_hand_off_of_a_gate_card_is_refused(rs, ledger, ss, cli_env, monkeypatch):
    """Plan Task 7a: even a fire row naming an agent can't get a gate card answered; the owner never."""
    monkeypatch.setenv("GATE_CARDS_NOTIFY", "0")
    hid, rid = _gate_card(ss, ledger)
    r = only(rs, trigger="unanswered", after_s=600, actions=[{"type": "hand_off"}])
    for assignee in ("dev", "pm"):
        c = sqlite3.connect(rs.astore.db_path)
        c.execute("INSERT OR REPLACE INTO attention_rule_fires (rule_id, target, action, fired_at, last_at, count, "
                  "result) VALUES (?,?,?,?,?,1,?)",
                  [r["id"], rid, "hand_off", NOW.isoformat(), NOW.isoformat(),
                   json.dumps({"ok": True, "detail": "handed_off", "assignee": assignee, "to": assignee})])
        c.commit(); c.close()
        p = on_behalf(cli_env, assignee, rid, r["id"], option="3")
        assert p.returncode == 4, p.stderr
        assert "gate_card" in p.stderr or "own_card" in p.stderr
    assert ledger.get(rid)["status"] == "pending" and ss.hold(hid)["state"] == "open"


def test_failed_apply_rolls_back_the_consume(rs, engine, ledger, cli_env):
    rid, r = _handed(rs, engine, ledger, question="Pick", kind="menu", menu=MENU_AB, options=["A", "B"])
    p = on_behalf(cli_env, "pm", rid, r["id"], option="9")           # not an option: apply_answer refuses
    assert p.returncode == 1
    res = json.loads(next(f for f in rs.fires() if f["target"] == rid)["result"])
    assert res.get("consumed_at") is None
    assert on_behalf(cli_env, "pm", rid, r["id"], option="1").returncode == 0


# ---- approval.py request --category ---------------------------------------------------------------------------

def test_request_category_is_stored_as_gate_hits(cli_env, ledger):
    p = subprocess.run([sys.executable, CLI, "request", "Deploy now?", "--from", "dev", "--worker-kind", "pane",
                        "--options", "Yes,No", "--category", "outside_world:deploy", "--category", "install"],
                       env=cli_env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    rid = p.stdout.strip().splitlines()[-1]
    menu = json.loads(ledger.get(rid)["menu"])
    assert menu["gate_hits"] == [["outside_world", "deploy"], ["install", None]]
    bad = subprocess.run([sys.executable, CLI, "request", "x", "--from", "dev", "--worker-kind", "pane",
                          "--category", "outside_world:teleport"], env=cli_env, capture_output=True, text=True)
    assert bad.returncode == 2 and "unknown" in bad.stderr
    plain = subprocess.run([sys.executable, CLI, "request", "Ok?", "--from", "dev", "--worker-kind", "pane",
                            "--category", "destructive:delete_files"], env=cli_env, capture_output=True, text=True)
    assert plain.returncode == 2 and "--options or --menu-json" in plain.stderr     # a plain approve/deny has no menu


def test_category_flag_is_documented_in_help_only():
    p = subprocess.run([sys.executable, CLI, "request", "--help"], capture_output=True, text=True)
    assert "--category" in p.stdout
    found = subprocess.run(["grep", "-rl", "--include=*.md", "--category", str(HERE.parent / "prompts")],
                           capture_output=True, text=True)
    assert found.stdout.strip() == ""


# ---- review r1: a claim is a lease, and a lost record is reconciled ----

def test_a_claim_is_spent_once_even_if_its_answer_never_landed(rs, engine, ledger):
    """Review r3: no lease. A claim whose process died leaves the card waiting for the person (fail closed)."""
    rid, r = _handed(rs, engine, ledger)
    rs.consume(r["id"], rid, "pm", now=LATER)
    for later in (timedelta(minutes=1), timedelta(hours=5)):
        with pytest.raises(AR.RuleInvalid, match="already_answered"):
            rs.consume(r["id"], rid, "pm", now=LATER + later)
    assert ledger.get(rid)["status"] == "pending"


def test_a_record_lost_after_the_answer_is_written_by_the_next_tick(rs, engine, ledger, astore):
    rid, r = _handed(rs, engine, ledger)
    ob = {"by": "pm", "rule_id": r["id"], "chosen": "approve", "reasoning": "fits"}
    rs.consume(r["id"], rid, "pm", pending_on_behalf=ob)
    G.apply_answer(ledger, rid, "approve", fire=False, surface="rule", answered_by="pm")   # then the CLI died
    assert astore.on_behalf_for(rid) is None
    engine.tick(LATER + timedelta(minutes=1))
    got = astore.on_behalf_for(rid)
    assert got["by"] == "pm" and got["action"] == "hand_off" and got["reasoning"] == "fits"
    assert rs.on_behalf_map()[rid]["by"] == "pm"


def test_reconcile_leaves_a_card_the_person_answered(rs, engine, ledger, astore):
    rid, r = _handed(rs, engine, ledger)
    rs.consume(r["id"], rid, "pm", pending_on_behalf={"by": "pm", "rule_id": r["id"], "chosen": "x", "reasoning": "y"})
    G.apply_answer(ledger, rid, "deny", fire=False, surface="web", answered_by="operator")
    engine.tick(LATER + timedelta(minutes=1))
    assert astore.on_behalf_for(rid) is None


def test_only_the_claims_own_token_gives_it_back_or_finalises_it(rs, engine, ledger):
    rid, r = _handed(rs, engine, ledger)
    _, a = rs.consume(r["id"], rid, "pm", now=LATER)
    assert rs.unconsume(r["id"], rid, "hand_off", claim="not-the-token") is False
    assert rs.record_on_behalf(r["id"], rid, "hand_off", {"by": "pm"}, claim="not-the-token") is False
    assert rs.unconsume(r["id"], rid, "hand_off", claim=a["claim"]) is True


def test_a_chooser_with_no_live_session_is_retried(rs, engine, ledger, msgs, agent_states):
    agent_states.pop("pm")
    rid = card(ledger)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    assert msgs == [] and fire(rs, rid, "choose_for_me")["detail"] == "retry:chooser_not_running"


def test_the_command_names_the_exact_fire(rs, engine, ledger, msgs, agent_states):
    agent_states["dev"] = "working"
    rid = card(ledger)
    r = only(rs, trigger="unanswered", after_s=600,
             actions=[{"type": "hand_off"}, {"type": "choose_for_me", "by": "supervisor"}])
    engine.tick(LATER)
    bodies = {m["type"]: m["body"] for m in msgs}
    assert f"--on-behalf-of {r['id']}:hand_off" in bodies["handoff"]
    assert f"--on-behalf-of {r['id']}:choose_for_me" in bodies["choose_for_me"]
    assert rs.consume(r["id"], rid, "pm", action="choose_for_me")[0] == "choose_for_me"
    assert rs.consume(r["id"], rid, "pm", action="hand_off")[0] == "hand_off"


def test_on_behalf_with_the_action_suffix(rs, engine, ledger, cli_env, astore):
    rid, r = _handed(rs, engine, ledger)
    assert on_behalf(cli_env, "pm", rid, f"{r['id']}:teleport").returncode == 2
    p = on_behalf(cli_env, "pm", rid, f"{r['id']}:hand_off")
    assert p.returncode == 0, p.stderr
    assert astore.on_behalf_for(rid)["rule_id"] == r["id"]
