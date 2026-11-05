"""Timeout rules: store, matching, clock, the remind / mark-stuck / expire actions, and the safety gate.

The safety-gate tests are a RELEASE GATE: expire (and, in part 2, hand-off and choose-for-me) must never
act for the person on a permission card, a high-risk card, or a hard-to-undo card without a standing
permission. Part 1 has no standing permissions, so it never acts on those at all."""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attention_detect as D  # noqa: E402
import attention_items as I  # noqa: E402
import attention_rules as AR  # noqa: E402
import attention_store as S  # noqa: E402
import scripts.watch_gateway as G  # noqa: E402
from approval_schema import ApprovalStore  # noqa: E402
from questionnaire_schema import QuestionnaireStore  # noqa: E402

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=3)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path / "data"))
    (tmp_path / "data" / "state").mkdir(parents=True)
    return str(tmp_path / "data" / "state" / "tasks.db")


@pytest.fixture
def ledger(db):
    st = ApprovalStore(db_path=db); st.migrate()
    return st


@pytest.fixture
def qstore(db):
    st = QuestionnaireStore(db_path=db); st.migrate()
    return st


@pytest.fixture
def astore(db):
    st = S.AttentionStore(db_path=db); st.migrate()
    return st


@pytest.fixture
def rs(astore):
    r = AR.RuleStore(astore)
    r.seed_defaults("alex")
    astore.set_meta("rules_epoch", NOW.isoformat())
    return r


class Calls:
    def __init__(self):
        self.notified, self.discarded, self.resumed, self.answers = [], [], [], []


@pytest.fixture
def calls():
    return Calls()


def _clock_rules_to(rs, t, rule_id=None):
    """Rules created by a test carry the wall clock as updated_at; pin them to `t` for a stable clock."""
    c = sqlite3.connect(rs.astore.db_path)
    if rule_id:
        c.execute("UPDATE attention_rules SET updated_at=? WHERE id=?", [t.isoformat(), rule_id])
    else:
        c.execute("UPDATE attention_rules SET updated_at=?", [t.isoformat()])
    c.commit(); c.close()


@pytest.fixture
def engine(rs, astore, ledger, qstore, calls):
    def apply_answer(store, rid, answer, **kw):
        calls.answers.append((rid, answer, kw))
        return G.apply_answer(store, rid, answer, **kw)

    def observe(now):
        ictx = I.Ctx(person="alex", now=now)
        waiting = I.waiting_items(ledger.pending_to_notify(), [G._qnr_pseudo_row(q) for q in qstore.list_pending()],
                                  [], ictx)
        return D.unanswered(waiting)

    ctx = AR.EngineCtx(
        person="alex", ledger=ledger, qstore=qstore, apply_answer=apply_answer,
        notify_card=lambda rid: calls.notified.append(rid),
        notify_questionnaire=lambda qid: calls.notified.append(qid),
        deliver_discarded=lambda row, store: calls.discarded.append(row["id"]),
        qnr_deliver_discarded=lambda row, store: calls.discarded.append(row["id"]),
        schedule_resume=lambda row: calls.resumed.append(row["id"]),
        is_protected=lambda agent: agent == "protected-seat",
        observe=observe)
    return AR.Engine(rs, astore, ctx)


def fires(rs):
    return rs.fires()


def fire_result(rs, target, action="expire"):
    return next(json.loads(f["result"]) for f in rs.fires() if f["target"] == target and f["action"] == action)


def backdate(ledger, rid, days):
    c = sqlite3.connect(ledger.db_path)
    c.execute("UPDATE approval_requests SET created_at=? WHERE id=?",
              [(NOW - timedelta(days=days)).isoformat(), rid])
    c.commit(); c.close()


def at(ledger, rid, t):
    c = sqlite3.connect(ledger.db_path)
    c.execute("UPDATE approval_requests SET created_at=? WHERE id=?", [t.isoformat(), rid])
    c.commit(); c.close()


def only(rs, **kw):
    """Remove the seeded defaults and add exactly one rule (clock pinned to NOW). Deleted, not disabled: a
    disabled person-wide rule edited at the wall clock would win the newest-edit tie against NOW."""
    for r in rs.list("alex"):
        rs.delete("alex", r["id"])
    made = rs.create("alex", **kw)
    _clock_rules_to(rs, NOW, made["id"])
    return made


# ---- store, defaults, matching ---------------------------------------------------------------

def test_defaults_seeded_once_remind_only_and_progress_off(rs):
    rules = rs.list("alex")
    assert {(r["trigger"], r["after_s"], r["enabled"]) for r in rules} == {("unanswered", 3600, 1),
                                                                          ("no_progress", 1200, 0)}
    assert all([a["type"] for a in r["actions"]] == ["remind"] for r in rules)
    assert all(r["is_default"] == 1 for r in rules)
    rs.delete("alex", next(r["id"] for r in rules if r["trigger"] == "unanswered"))
    rs.seed_defaults("alex")                                          # never re-seeded once touched
    assert len(rs.list("alex")) == 1


def test_rules_carry_what_the_settings_ui_needs(rs):
    r = rs.list("alex")[0]
    assert {a["type"]: a["available"] for a in r["available_actions"]} == {
        "remind": True, "mark_stuck": True, "expire": True, "hand_off": True, "choose_for_me": True}
    assert AR.min_after_s("no_progress") == 600 and AR.min_after_s("unanswered") == 60


def test_stall_constant_matches_the_detector():
    assert AR.STALL_S == G._agent_status().STALL_S


def test_most_specific_rule_wins_and_a_disabled_override_silences(rs):
    base = rs.create("alex", trigger="unanswered", after_s=3600, actions=[{"type": "remind"}])
    gm = rs.create("alex", trigger="unanswered", after_s=600, scope_agent="gm", actions=[{"type": "mark_stuck"}])
    rs.create("alex", trigger="unanswered", after_s=60, scope_agent="qa", enabled=False, actions=[{"type": "remind"}])
    hi = rs.create("alex", trigger="unanswered", after_s=600, scope_risk="high", actions=[{"type": "remind"}])
    rules = rs.list("alex")
    assert AR.pick(rules, trigger="unanswered", agent="gm-gen4", kind="decision", risk=None)["id"] == gm["id"]
    assert AR.pick(rules, trigger="unanswered", agent="pm", kind="decision", risk=None)["id"] == base["id"]
    assert AR.pick(rules, trigger="unanswered", agent="pm", kind="decision", risk="high")["id"] == hi["id"]
    assert AR.pick(rules, trigger="unanswered", agent="qa", kind="decision", risk=None)["enabled"] == 0
    assert AR.pick(rules, trigger="no_progress", agent="pm", kind=None, risk=None)["trigger"] == "no_progress"


def test_validation(rs):
    for bad in [dict(after_s=10), dict(after_s=10**7), dict(trigger="x"), dict(actions=[]),
                dict(actions=[{"type": "nope"}]),
                dict(actions=[{"type": "remind", "repeat_every_s": 30}]),
                dict(scope_kind="permission", actions=[{"type": "expire"}]),
                dict(scope_risk="high", actions=[{"type": "expire"}]),
                dict(scope_kind="bogus"), dict(scope_risk="extreme"),
                dict(trigger="no_progress", scope_kind="decision"),
                dict(trigger="no_progress", actions=[{"type": "expire"}]),
                dict(actions=[{"type": "expire", "mode": "maybe"}])]:
        with pytest.raises(AR.RuleInvalid):
            rs.create("alex", **{"trigger": "unanswered", "after_s": 600, "actions": [{"type": "remind"}], **bad})
    with pytest.raises(AR.RuleInvalid, match="unsafe_scope"):
        rs.create("alex", trigger="unanswered", after_s=600, scope_kind="permission", actions=[{"type": "expire"}])


def test_part2_actions_act_for_the_person(rs):
    """Replaced by test_attention_rules_part2.py::test_part2_actions_are_available_now (part 2b)."""
    for t in ("hand_off", "choose_for_me"):
        assert AR.ACTIONS[t].available() == (True, "")
        assert AR.ACTIONS[t].acts_for_person is True


def test_update_and_delete_are_per_person(rs):
    r = rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "remind"}])
    assert rs.update("someone-else", r["id"], after_s=900) is None
    assert rs.delete("someone-else", r["id"]) is False
    assert rs.update("alex", r["id"], after_s=900)["after_s"] == 900
    with pytest.raises(AR.RuleInvalid):
        rs.update("alex", r["id"], after_s=5)
    assert rs.delete("alex", r["id"]) is True and rs.get("alex", r["id"]) is None


def test_every_rule_write_bumps_rev(rs, astore):
    r0 = astore.rev()
    r = rs.create("alex", trigger="unanswered", after_s=600, actions=[{"type": "remind"}])
    rs.update("alex", r["id"], after_s=700)
    rs.delete("alex", r["id"])
    assert astore.rev() == r0 + 3


# ---- the clock -------------------------------------------------------------------------------

def test_clock_starts_at_release_and_at_rule_edit(rs, engine, ledger, calls):
    rule = next(r for r in rs.list("alex") if r["trigger"] == "unanswered")
    _clock_rules_to(rs, NOW - timedelta(days=30))
    rid = ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")   # created long before release
    backdate(ledger, rid, days=10)
    engine.tick(NOW + timedelta(minutes=59)); assert fires(rs) == []
    engine.tick(NOW + timedelta(minutes=61)); assert [f["action"] for f in fires(rs)] == ["remind"]
    assert calls.notified == [rid]
    rs.update("alex", rule["id"], after_s=600)                           # edit restarts the clock
    _clock_rules_to(rs, NOW + timedelta(minutes=62))
    engine.tick(NOW + timedelta(minutes=63)); assert len(fires(rs)) == 1
    engine.tick(NOW + timedelta(minutes=63)); assert calls.notified == [rid]   # fires once per target


def test_remind_repeats_every_n_and_stops_when_answered(rs, engine, ledger, calls):
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "remind", "repeat_every_s": 900}])
    rid = ledger.create(from_agent="gm", question="Ship?", worker_kind="pane"); at(ledger, rid, NOW)
    engine.tick(NOW + timedelta(minutes=11))
    engine.tick(NOW + timedelta(minutes=20))
    assert calls.notified == [rid]
    engine.tick(NOW + timedelta(minutes=27))
    assert calls.notified == [rid, rid]
    assert rs.reminders()[rid]["n"] == 2
    ledger.record_answer(rid, "approve", None, surface="web", answered_by="operator")
    engine.tick(NOW + timedelta(minutes=60))
    assert calls.notified == [rid, rid]


def test_remind_respects_quiet_hours_and_nothing(rs, engine, ledger):
    from attention_push import apply_reminder
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "remind"}])
    rid = ledger.create(from_agent="gm", question="Ship?", worker_kind="pane"); at(ledger, rid, NOW)
    engine.tick(NOW + timedelta(minutes=11))
    rem = rs.reminders()[rid]
    base = {"decision": "list", "reason": "notify_level=needs_me"}
    q = {"quiet_hours": {"from": "11:00", "to": "13:00"}}
    assert apply_reminder(base, rem, q, "gm", NOW, "UTC")["decision"] == "held"
    assert apply_reminder(base, rem, {"notify_level": "nothing"}, "gm", NOW, "UTC")["decision"] == "list"
    assert apply_reminder(base, rem, {}, "gm", NOW, "UTC")["reason"] == f"reminder:{rem['rule_id']}"


def test_mark_stuck_is_quiet_and_resolves_from_the_ledger(rs, engine, astore, ledger):
    only(rs, trigger="unanswered", after_s=2700, scope_agent="gm", actions=[{"type": "mark_stuck"}])
    rid = ledger.create(from_agent="gm", question="Ship?", worker_kind="pane"); at(ledger, rid, NOW)
    engine.tick(NOW + timedelta(minutes=46))
    row = astore.get_by_key_any(f"stuck:gm:question:{rid}")
    assert row and row["resolved_at"] is None and row["title"] == "gm has waited 45 min for your answer (your rule)"
    assert json.loads(row["push_override"])["decision"] == "list"
    from attention_config import load
    D.apply_tick(astore, [], NOW, load({}), live_agents={"gm"}, ledger=ledger)
    assert astore.get_by_key_any(f"stuck:gm:question:{rid}")["resolved_at"] is None
    ledger.record_answer(rid, "approve", None, surface="web", answered_by="operator")
    D.apply_tick(astore, [], NOW, load({}), live_agents={"gm"}, ledger=ledger)
    assert astore.get_by_key_any(f"stuck:gm:question:{rid}")["resolved_reason"] == "answered"


def test_no_progress_rule_opens_one_stuck_row_per_episode_and_resolves_on_progress(rs, engine, astore):
    rule = only(rs, trigger="no_progress", after_s=1200, actions=[{"type": "remind"}, {"type": "mark_stuck"}])
    since = NOW - timedelta(minutes=5)
    engine.tick(NOW, [D.NoProgress("qa", since)])
    assert astore.get_by_key_any("stuck:qa:no_progress") is None
    engine.tick(NOW + timedelta(minutes=19), [D.NoProgress("qa", since)])        # the rule's clock: from NOW
    assert astore.get_by_key_any("stuck:qa:no_progress") is None
    engine.tick(NOW + timedelta(minutes=21), [D.NoProgress("qa", since + timedelta(seconds=1))])  # jitter: same episode
    row = astore.get_by_key_any("stuck:qa:no_progress")
    assert row["title"] == "qa has made no progress for 20 min (your rule)" and row["rule_id"] == rule["id"]
    assert json.loads(row["push_override"])["reminder"]["rule_id"] == rule["id"]   # remind wins over quiet
    engine.tick(NOW + timedelta(minutes=22), [D.NoProgress("qa", since)])
    assert len([f for f in fires(rs) if f["action"] == "remind"]) == 1
    engine.tick(NOW + timedelta(minutes=23), [])                                  # it moved again
    assert astore.get_by_key_any("stuck:qa:no_progress")["resolved_reason"] == "progressed"
    engine.tick(NOW + timedelta(minutes=23), [], agents_ok=False)


# ---- expire ----------------------------------------------------------------------------------

def test_expire_takes_the_default_then_no_answer(rs, engine, ledger, astore, calls):
    a = ledger.create(from_agent="gm", question="Pick", worker_kind="pane", options=["A", "B"], kind="menu",
                      menu={"question": "Pick", "options": [{"n": "1", "label": "A", "input_kind": "direct"},
                                                            {"n": "2", "label": "B", "input_kind": "direct"}]},
                      default_option="2")
    b = ledger.create(from_agent="gm", question="Ship?", worker_kind="pane")
    c = ledger.create(from_agent="gm", question="Deploy?", worker_kind="pane", default_option="approve")
    for rid in (a, b, c):
        at(ledger, rid, NOW)
    rule = only(rs, trigger="unanswered", after_s=600, actions=[{"type": "expire", "mode": "default"}])
    engine.tick(LATER)
    ra, rb, rc = ledger.get(a), ledger.get(b), ledger.get(c)
    assert (ra["option_n"], ra["answer_surface"], ra["answered_by"]) == ("2", "rule", f"rule:{rule['id']}")
    assert (rc["answer"], rc["answered_by"]) == ("approve", f"rule:{rule['id']}")
    assert rb["status"] == "discarded" and calls.discarded == [b]       # no default -> no answer, agent told
    assert set(calls.resumed) == {a, c}                                  # answers reach the agent by the resume path
    ob = fire_result(rs, a)["on_behalf"]
    assert ob["by"] == f"rule:{rule['id']}" and ob["action"] == "expire" and ob["chosen"] == "B" and ob["reasoning"]
    assert fire_result(rs, b)["on_behalf"]["chosen"] == "no answer"
    assert astore.get_by_key_any(f"on_behalf:{a}")["kind"] == "on_behalf"
    assert rs.on_behalf_map()[a]["chosen"] == "B"


def test_expire_no_answer_mode_ignores_the_default(rs, engine, ledger, calls):
    c = ledger.create(from_agent="gm", question="Deploy?", worker_kind="pane", default_option="approve")
    at(ledger, c, NOW)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "expire", "mode": "no_answer"}])
    engine.tick(LATER)
    assert ledger.get(c)["status"] == "discarded"


def test_expire_closes_a_questionnaire_through_its_discard_path(rs, engine, qstore, calls):
    qid = qstore.create(from_agent="gm", title="Prefs", questions=[{"prompt": "Colour?", "kind": "free_text"}])
    c = sqlite3.connect(qstore.db_path)
    c.execute("UPDATE questionnaires SET created_at=? WHERE id=?", [NOW.isoformat(), qid]); c.commit(); c.close()
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "expire"}])
    engine.tick(LATER)
    assert qstore.get(qid)["status"] == "discarded" and calls.discarded == [qid]


def test_expire_never_drops_a_live_pane_menu_without_a_default(rs, engine, ledger):
    rid = ledger.create(from_agent="gm", question="Pick", worker_kind="pane", kind="menu", origin="menu_bridge",
                        op_key="menu:gm:abc", options=["A"],
                        menu={"question": "Pick", "source_session": "gm",
                              "options": [{"n": "1", "label": "A", "input_kind": "direct"}]})
    at(ledger, rid, NOW)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "expire"}])
    engine.tick(LATER)
    assert ledger.get(rid)["status"] == "pending"
    assert fire_result(rs, rid)["ok"] is False


@pytest.mark.parametrize("card", [
    {"kind": "menu", "menu": {"bridge": {"permission": True},
                              "options": [{"n": "1", "label": "Allow", "input_kind": "direct"}]}},   # headless "Allow X?"
    {"risk_level": "high"}, {"reversibility": "irreversible"}, {"reversibility": "hard"},
    {"from_agent": "protected-seat"},
])
def test_safety_gate_never_acts_for_the_person_on_held_or_risky_cards(rs, engine, ledger, card, calls):
    kw = {"from_agent": "gm", "question": "x", "worker_kind": "pane", "default_option": "approve", **card}
    if kw.get("kind") == "menu":
        kw["default_option"] = "1"
    rid = ledger.create(**kw)
    at(ledger, rid, NOW)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "remind"}, {"type": "expire"}])
    engine.tick(LATER)
    assert ledger.get(rid)["status"] == "pending"
    assert {f["action"]: json.loads(f["result"])["ok"] for f in fires(rs)} == {"remind": True, "expire": False}
    assert json.loads(next(f["result"] for f in fires(rs) if f["action"] == "expire"))["detail"].startswith("safety:")
    assert calls.answers == [] and calls.discarded == []


def test_safety_gate_covers_live_permission_prompts():
    item = {"id": "perm:gm:x:1", "kind": "permission", "agent": "gm", "risk": None, "card": {}}
    assert AR.may_act_for_person(item)[0] is False
    ok, why = AR.may_act_for_person({"id": "apr_1", "kind": "decision", "agent": "gm", "risk": None, "card": {}})
    assert ok is True and why is None


def test_human_task_is_never_expired(rs, engine, ledger, calls):
    rid = ledger.create(from_agent="gm", question="Plug in", worker_kind="pane", kind="human_task",
                        options=["done", "cant", "snooze"], block_task="Plug in", blocks_what="deploy")
    at(ledger, rid, NOW)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "expire"}])
    engine.tick(LATER)
    assert ledger.get(rid)["status"] == "pending" and calls.answers == []
    assert fire_result(rs, rid)["ok"] is False


def test_a_failing_action_never_stops_the_tick(rs, engine, ledger, calls, monkeypatch):
    a = ledger.create(from_agent="gm", question="A?", worker_kind="pane"); at(ledger, a, NOW)
    b = ledger.create(from_agent="gm", question="B?", worker_kind="pane"); at(ledger, b, NOW)
    only(rs, trigger="unanswered", after_s=600, actions=[{"type": "remind"}])
    seen = []

    def boom(rid):
        seen.append(rid)
        if len(seen) == 1:
            raise RuntimeError("ntfy down")
    engine.ctx.notify_card = boom
    engine.tick(LATER)
    assert len(seen) == 2
    assert sorted(json.loads(f["result"])["ok"] for f in fires(rs)) == [False, True]


def test_default_option_column_and_cli_flag(tmp_path, monkeypatch, capsys):
    import approval
    monkeypatch.setenv("APPROVAL_DDL_ARMED", "all")
    monkeypatch.setenv("APPROVAL_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(approval, "_authorship_guard", lambda a: True)
    monkeypatch.setattr(approval, "notify", lambda *a, **k: None)
    st = ApprovalStore(db_path=str(tmp_path / "t.db"))
    assert approval.main(["request", "Pick", "--from", "gm", "--worker-kind", "pane", "--options", "A,B",
                          "--default", "2"]) == 0
    assert st.get(capsys.readouterr().out.strip())["default_option"] == "2"
    assert approval.main(["request", "Ship?", "--from", "gm", "--worker-kind", "pane", "--default", "deny"]) == 0
    assert st.get(capsys.readouterr().out.strip())["default_option"] == "deny"
    assert approval.main(["request", "Pick", "--from", "gm", "--worker-kind", "pane", "--options", "A,B",
                          "--default", "9"]) == 2
    assert approval.main(["request", "Ship?", "--from", "gm", "--worker-kind", "pane", "--default", "1"]) == 2


def test_a_protected_seat_is_protected_under_its_rotated_name_too():
    item = {"id": "apr_1", "kind": "decision", "agent": "protected-seat-gen3", "risk": None, "card": {}}
    assert AR.may_act_for_person(item, None, lambda a: a == "protected-seat") == (False, "protected_agent")


def test_defaults_are_seeded_once_even_when_two_callers_race(astore):
    import threading
    rs1, rs2 = AR.RuleStore(astore), AR.RuleStore(astore)
    ts = [threading.Thread(target=r.seed_defaults, args=("bob",)) for r in (rs1, rs2, rs1, rs2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(rs1.list("bob")) == 2


def test_gated_columns_are_written_with_the_card(ledger):
    rid = ledger.create(from_agent="gm", question="x", worker_kind="pane", urgency=2, default_option="deny")
    r = ledger.get(rid)
    assert (r["urgency"], r["default_option"]) == (2, "deny")
