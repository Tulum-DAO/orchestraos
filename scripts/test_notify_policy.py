"""Settings › Notifications on the sending side: notify_policy.should_alert() runs every case in
contract/notify-policy-cases.json (the API runs the same cases, api/src/lib/notifyPolicy.test.ts); allowed() reads the
operator's prefs and the install zone and fails open; approval_notify holds a push the person does not want now, without
marking it sent, and pushes exactly as before when nothing is set."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_policy as P
import approval_notify as N
from approval_schema import ApprovalStore
from questionnaire_schema import QuestionnaireStore

# captured at import, before the autouse hermetic fixture (scripts/conftest.py) replaces them for each test
_REAL_DECISION = P.decision
_REAL_CONFIG = P._config
CASES = json.loads((Path(__file__).resolve().parent.parent / "contract" / "notify-policy-cases.json").read_text())["cases"]


def _utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_contract_case(case):
    args = (case["prefs"], case["event"], case["agent"], _utc(case["now"]), case["tz"])
    assert P.decide(*args) == case["reason"], case["name"]
    assert P.should_alert(*args) is case["expected"], case["name"]
    assert (case["reason"] == "ok") is case["expected"], case["name"]


def test_contract_covers_every_level_event_and_kind_of_window():
    names = " | ".join(c["name"] for c in CASES)
    for lvl in P.LEVELS:
        for ev in P.EVENTS:
            assert f"level {lvl} / {ev}" in names
    for must in ("quiet 22-07 New York, 23:59", "quiet 22-07 New York, 00:00", "Kolkata", "July", "January",
                 "from == to", "unknown zone", "from dev-b (no override)", "quiet beats an agent's own All"):
        assert must in names, must
    assert len(CASES) >= 100


def test_contract_has_rotated_ids_and_every_reason():
    names = " | ".join(c["name"] for c in CASES)
    assert "rotated id: dev-3 Nothing applies to dev-3-gen4" in names
    assert "rotated id: global Nothing + dev-3 All alerts for dev-3-gen4" in names
    assert {c["reason"] for c in CASES} == {"ok", "quiet", "suppressed"}


def test_an_unusable_zone_is_logged_once_and_reads_as_utc(capsys):
    P._warned_zones.discard("Mars/Olympus")
    q = {"quiet_hours": {"from": "22:00", "to": "07:00"}}
    for _ in range(3):
        assert P.decide(q, "needs_me", "a", datetime(2026, 11, 3, 23, tzinfo=timezone.utc), "Mars/Olympus") == "quiet"
    assert capsys.readouterr().err.count("'Mars/Olympus' unusable") == 1


def test_naive_now_is_utc():
    q = {"quiet_hours": {"from": "13:00", "to": "17:00"}}
    assert P.should_alert(q, "needs_me", "a", datetime(2026, 11, 3, 14, 0), "UTC") is False


def test_prefs_not_a_dict_reads_as_defaults():
    now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    for junk in (None, [], "all", 3):
        assert P.should_alert(junk, "finished", "a", now, "UTC") is True


# --------------------------------------------------------------------------------------------- allowed(): the files

def _prefs(tmp_path, user, d):
    f = tmp_path / "state" / "users" / user / "prefs.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d))


NOON_NY = datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)
NIGHT_NY = datetime(2026, 11, 4, 5, 0, tzinfo=timezone.utc)   # 00:00 in New York


def test_allowed_with_no_file_is_todays_behaviour(tmp_path):
    cfg = {"operator": {"id": "operator", "timezone": "America/New_York"}}
    for ev in P.EVENTS:
        assert P.allowed(ev, "dev-a", NIGHT_NY, data_dir=tmp_path, config=cfg) is True


def test_allowed_reads_the_operators_file_and_the_install_zone(tmp_path):
    cfg = {"operator": {"id": "sam", "timezone": "America/New_York"}}
    _prefs(tmp_path, "sam", {"notify_level": "needs_me", "notify_agents": {"noisy": "nothing"},
                              "quiet_hours": {"from": "22:00", "to": "07:00"}})
    _prefs(tmp_path, "operator", {"notify_level": "nothing"})      # another name's file is not read
    assert P.allowed("needs_me", "dev-a", NOON_NY, data_dir=tmp_path, config=cfg) is True
    assert P.allowed("finished", "dev-a", NOON_NY, data_dir=tmp_path, config=cfg) is False
    assert P.allowed("needs_me", "noisy", NOON_NY, data_dir=tmp_path, config=cfg) is False
    assert P.allowed("needs_me", "dev-a", NIGHT_NY, data_dir=tmp_path, config=cfg) is False
    # the same instant with the install in UTC is 05:00, still quiet; in Kolkata it is 10:30, not quiet
    cfg["operator"]["timezone"] = "Asia/Kolkata"
    assert P.allowed("needs_me", "dev-a", NIGHT_NY, data_dir=tmp_path, config=cfg) is True


def test_install_user_and_zone_match_the_api_defaults():
    assert P.install_user_and_zone({}) == ("operator", "")
    assert P.install_user_and_zone({"operator": {"id": ""}}) == ("operator", "")
    assert P.install_user_and_zone({"operator": {"id": "sam", "timezone": "Europe/Berlin"}}) == ("sam", "Europe/Berlin")
    assert P.install_user_and_zone({"operator": "x"}) == ("operator", "")


def test_a_bad_user_name_or_broken_file_reads_as_no_choices(tmp_path):
    assert P.load_prefs(tmp_path, "../etc") == {}
    assert P.load_prefs(tmp_path, "") == {}
    f = tmp_path / "state" / "users" / "operator" / "prefs.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json")
    assert P.load_prefs(tmp_path, "operator") == {}
    f.write_text("[1, 2]")
    assert P.load_prefs(tmp_path, "operator") == {}


def test_allowed_fails_open(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("config unreadable")
    monkeypatch.setattr(P, "install_user_and_zone", boom)
    assert P.allowed("needs_me", "dev-a", NOON_NY, data_dir=tmp_path) is True


def test_allowed_reads_the_repo_config_by_default(tmp_path, monkeypatch):
    toml = tmp_path / "orchestra.toml"
    toml.write_text('[operator]\nid = "sam"\ntimezone = "UTC"\n')
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(toml))
    monkeypatch.setattr(P, "_config", _REAL_CONFIG)
    _prefs(tmp_path, "sam", {"notify_level": "nothing"})
    assert P.allowed("needs_me", "dev-a", NOON_NY, data_dir=tmp_path) is False


# ------------------------------------------------------------------------------------ approval_notify: the push path

@pytest.fixture
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr(N, "ntfy_token", lambda: "tk")
    monkeypatch.setattr(N, "_http_post", lambda url, headers, data: sent.append(json.loads(data)) or 200)
    return sent


def _policy(monkeypatch, tmp_path, prefs, now=NOON_NY, tz="America/New_York"):
    """Point notify_policy at a temp data dir with these prefs, a fixed clock and zone."""
    if prefs is not None:
        _prefs(tmp_path, "operator", prefs)
    monkeypatch.setattr(P, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(P, "_config", lambda: {"operator": {"id": "operator", "timezone": tz}})
    monkeypatch.setattr(P, "decision", lambda ev, agent, now_=None, **k: _REAL_DECISION(ev, agent, now, **k))


def _stores(tmp_path):
    s = ApprovalStore(db_path=str(tmp_path / "t.db")); s.migrate()
    q = QuestionnaireStore(db_path=str(tmp_path / "t.db")); q.migrate()
    return s, q


def _card(s, agent="worker", question="Ship it?"):
    return s.create(from_agent=agent, question=question, worker_kind="dev", options=["approve", "deny", "hold"])


def _beat(tmp_path, s, q, publish=None):
    return N.cron_backstop(store=s, qstore=q, tg_send=lambda t: True, tg_state_path=str(tmp_path / "tg.json"),
                           tailnet_status={"off_tailnet": False}, tailnet_state_path=str(tmp_path / "tn.json"),
                           ntfy_publish=publish)


def test_nothing_set_pushes_and_stamps_as_before(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, None)
    s, _ = _stores(tmp_path)
    rid = _card(s)
    N.notify(rid, store=s)
    assert len(pushes) == 1 and pushes[0]["message"] == "Ship it?"
    assert s.get(rid)["notified_at"] and s.get(rid)["notify_skipped"] is None


@pytest.mark.parametrize("prefs,level", [({"notify_level": "nothing"}, "nothing"),
                                         ({"notify_agents": {"worker": "nothing"}}, "nothing"),
                                         ({"notify_agents": {"worker": "nothing"}, "notify_level": "all"}, "nothing")])
def test_nothing_suppresses_and_switching_back_to_all_never_floods(monkeypatch, tmp_path, pushes, prefs, level):
    _policy(monkeypatch, tmp_path, prefs)
    s, q = _stores(tmp_path)
    rid = _card(s)
    N.notify(rid, store=s)
    row = s.get(rid)
    assert pushes == [] and not row["notified_at"] and row["status"] == "pending"
    assert row["notify_skipped"].startswith(f"suppressed:{level}@")
    _policy(monkeypatch, tmp_path, {"notify_level": "all"})       # back to All
    _beat(tmp_path, s, q)
    _beat(tmp_path, s, q)
    assert pushes == [], "an old suppressed card is never pushed by the backstop"
    rid2 = _card(s, question="New one?")                           # a new card after the switch does push
    N.notify(rid2, store=s)
    assert [p["message"] for p in pushes] == ["New one?"]


def test_a_rotated_seat_follows_its_roots_override(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, {"notify_level": "nothing", "notify_agents": {"dev-3": "all"}})
    s, _ = _stores(tmp_path)
    a, b = _card(s, agent="dev-3-gen4"), _card(s, agent="dev-4-gen2")
    N.notify(a, store=s); N.notify(b, store=s)
    assert len(pushes) == 1 and s.get(a)["notified_at"] and s.get(b)["notify_skipped"].startswith("suppressed:nothing@")


def test_needs_me_still_pushes_cards(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, {"notify_level": "needs_me"})
    s, _ = _stores(tmp_path)
    N.notify(_card(s), store=s)
    assert len(pushes) == 1


def test_another_agents_override_does_not_hold_this_card(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, {"notify_level": "all", "notify_agents": {"someone-else": "nothing"}})
    s, _ = _stores(tmp_path)
    N.notify(_card(s), store=s)
    assert len(pushes) == 1


QUIET = {"quiet_hours": {"from": "22:00", "to": "07:00"}}


def test_quiet_hours_hold_with_their_own_stamp_then_the_backstop_sends_after(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, q = _stores(tmp_path)
    rid = _card(s)
    N.notify(rid, store=s)
    _beat(tmp_path, s, q)
    row = s.get(rid)
    assert pushes == [] and not row["notified_at"] and row["notify_skipped"].startswith("held:quiet@")
    _policy(monkeypatch, tmp_path, QUIET, now=NOON_NY)   # morning
    _beat(tmp_path, s, q)
    assert len(pushes) == 1 and pushes[0]["message"] == "Ship it?" and s.get(rid)["notified_at"]


def test_a_held_card_answered_keeps_its_own_stamp_not_answered_before_push(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, _ = _stores(tmp_path)
    rid = _card(s)
    N.notify(rid, store=s)
    s.record_answer(rid, "approve", None)
    N.stamp_outrun_skips(store=s)
    assert s.get(rid)["notify_skipped"].startswith("held:quiet@")


def test_suppression_replaces_a_quiet_hold_but_nothing_replaces_a_suppression(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, _ = _stores(tmp_path)
    rid = _card(s)
    N.notify(rid, store=s)
    _policy(monkeypatch, tmp_path, {**QUIET, "notify_level": "nothing"}, now=NIGHT_NY)
    N.notify(rid, store=s)
    first = s.get(rid)["notify_skipped"]
    assert first.startswith("suppressed:nothing@")
    _policy(monkeypatch, tmp_path, {**QUIET, "notify_level": "all"}, now=NIGHT_NY)
    N.notify(rid, store=s)
    assert s.get(rid)["notify_skipped"] == first


def test_more_than_three_held_cards_go_out_as_one_summary(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, q = _stores(tmp_path)
    ids = [_card(s, question=f"Card {i}?") for i in range(4)]
    qid = q.create(from_agent="worker", title="Intake", questions=[{"prompt": "Name?", "kind": "free_text"}])
    _beat(tmp_path, s, q)
    assert pushes == []
    _policy(monkeypatch, tmp_path, QUIET, now=NOON_NY)
    published = []
    _beat(tmp_path, s, q, publish=lambda p: published.append(p) or True)
    assert [p["message"] for p in published] == ["5 things waited during quiet hours"]
    assert pushes == [], "no card pushed one by one"
    assert all(s.get(i)["notified_at"] for i in ids) and q.get(qid)["notified_at"]
    _beat(tmp_path, s, q, publish=lambda p: published.append(p) or True)
    assert len(published) == 1 and pushes == []


def test_three_held_cards_or_fewer_go_out_one_by_one(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, q = _stores(tmp_path)
    for i in range(3):
        _card(s, question=f"Card {i}?")
    _beat(tmp_path, s, q)
    _policy(monkeypatch, tmp_path, QUIET, now=NOON_NY)
    published = []
    _beat(tmp_path, s, q, publish=lambda p: published.append(p) or True)
    assert published == [] and sorted(p["message"] for p in pushes) == ["Card 0?", "Card 1?", "Card 2?"]


def test_a_failed_summary_is_retried_and_nothing_is_marked_sent(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, q = _stores(tmp_path)
    ids = [_card(s) for _ in range(4)]
    _beat(tmp_path, s, q)
    _policy(monkeypatch, tmp_path, QUIET, now=NOON_NY)
    assert N.quiet_summary(s, q, publish=lambda p: False) == []
    assert not any(s.get(i)["notified_at"] for i in ids)
    assert sorted(N.quiet_summary(s, q, publish=lambda p: True)) == sorted(ids)


def test_a_card_held_then_suppressed_is_not_counted_in_the_summary(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    s, q = _stores(tmp_path)
    for _ in range(4):
        _card(s, agent="noisy")
    _beat(tmp_path, s, q)
    _policy(monkeypatch, tmp_path, {**QUIET, "notify_agents": {"noisy": "nothing"}}, now=NOON_NY)
    published = []
    _beat(tmp_path, s, q, publish=lambda p: published.append(p) or True)
    assert published == [] and pushes == []


def test_questionnaire_suppressed_the_same_way(monkeypatch, tmp_path, pushes):
    _policy(monkeypatch, tmp_path, {"notify_level": "nothing"})
    s, q = _stores(tmp_path)
    qid = q.create(from_agent="worker", title="Intake", questions=[{"prompt": "Name?", "kind": "free_text"}])
    N.notify_questionnaire(qid, store=q)
    assert pushes == [] and not q.get(qid)["notified_at"]
    assert q.get(qid)["notify_skipped"].startswith("suppressed:nothing@")
    _policy(monkeypatch, tmp_path, {"notify_level": "all"})
    _beat(tmp_path, s, q)
    assert pushes == []


def test_return_refire_drops_a_suppressed_card_and_keeps_a_quiet_one(monkeypatch, tmp_path):
    s, q = _stores(tmp_path)
    a, b = _card(s, agent="noisy"), _card(s, agent="worker")
    st = str(tmp_path / "tn.json")
    fired = []
    pub = lambda p: fired.append(p) or True
    _policy(monkeypatch, tmp_path, {**QUIET, "notify_agents": {"noisy": "nothing"}}, now=NIGHT_NY)
    N.refire_on_return(s, q, {"off_tailnet": True}, state_path=st, publish=pub, candidates={a, b})
    assert N.refire_on_return(s, q, {"off_tailnet": False}, state_path=st, publish=pub, candidates={a, b}) == []
    assert fired == [] and json.loads(Path(st).read_text())["targets"] == [b]
    _policy(monkeypatch, tmp_path, {**QUIET, "notify_agents": {"noisy": "nothing"}}, now=NOON_NY)
    assert N.refire_on_return(s, q, {"off_tailnet": False}, state_path=st, publish=pub) == [b]


def test_an_existing_suite_never_sees_the_machines_prefs():
    """scripts/conftest.py: each test gets an empty data dir and a fixed config, whatever is on this machine."""
    assert P.load_prefs(P._data_dir(), "operator") == {}
    assert P._config() == {"operator": {"id": "operator", "timezone": ""}}
    assert P.decision("needs_me", "x") == "ok"


# ------------------------------------------------------------------------------------ approval_transport: the seam

def _transport(monkeypatch, mode):
    import types
    import importlib
    calls = []
    cfg = types.ModuleType("apns_config"); cfg.APPROVAL_TRANSPORT = mode
    apns = types.ModuleType("apns_notify")

    class ApnsNotReady(Exception):
        pass
    apns.ApnsNotReady = ApnsNotReady
    apns.notify = lambda rid, store=None: calls.append(("apns", rid))
    monkeypatch.setitem(sys.modules, "apns_config", cfg)
    monkeypatch.setitem(sys.modules, "apns_notify", apns)
    monkeypatch.delitem(sys.modules, "approval_transport", raising=False)
    T = importlib.import_module("approval_transport")
    return T, calls


@pytest.mark.parametrize("mode", ["apns", "auto"])
def test_the_transport_asks_the_settings_before_apns(monkeypatch, tmp_path, pushes, mode):
    T, calls = _transport(monkeypatch, mode)
    s, _ = _stores(tmp_path)
    _policy(monkeypatch, tmp_path, {"notify_level": "nothing"})
    rid = _card(s)
    assert T.notify(rid, store=s) is None
    assert calls == [] and pushes == [] and s.get(rid)["notify_skipped"].startswith("suppressed:nothing@")
    _policy(monkeypatch, tmp_path, QUIET, now=NIGHT_NY)
    rid2 = _card(s)
    T.notify(rid2, store=s)
    assert calls == [] and s.get(rid2)["notify_skipped"].startswith("held:quiet@")
    _policy(monkeypatch, tmp_path, {"notify_level": "all"}, now=NOON_NY)
    rid3 = _card(s)
    T.notify(rid3, store=s)
    assert calls == [("apns", rid3)]


def test_the_transport_ntfy_mode_is_gated_too(monkeypatch, tmp_path, pushes):
    T, _ = _transport(monkeypatch, "ntfy")
    s, _ = _stores(tmp_path)
    _policy(monkeypatch, tmp_path, {"notify_level": "nothing"})
    T.notify(_card(s), store=s)
    assert pushes == []
    _policy(monkeypatch, tmp_path, {"notify_level": "all"})
    T.notify(_card(s), store=s)
    assert len(pushes) == 1
