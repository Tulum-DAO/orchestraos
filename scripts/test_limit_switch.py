"""limit_switch: each arming condition is a test (gm order msg_4dc56c81, 2026-11-25)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_switch as LS  # noqa: E402

NOW = 1795629600                       # 2026-11-25 18:00Z
RESET = NOW + 3 * 3600


@pytest.fixture(autouse=True)
def _operator_zone(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ", "America/Cancun")
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ_LABEL", "Local")
    monkeypatch.setenv("ORCHESTRA_ACCOUNT_SWITCH", "/x/account_switch.py")


class Fake:
    def __init__(self, *, usage=None, typing=(), banners=("pm-nod",), switch_status="switched"):
        self._usage = usage if usage is not None else {
            "default": {"as_of": NOW - 60, "windows": {"5h": {"used_pct": 20.0}, "weekly": {"used_pct": 42.0}}}}
        self._typing, self._banners, self._status = set(typing), set(banners), switch_status
        self.switched, self.nudged, self.mails = [], [], []

    def usage(self, a):
        return self._usage.get(a)

    def enrolled(self):
        return ["default", "strategysimple"]

    def claude_seats(self):
        return [("gm", "gm"), ("pm-nod", "pm-nod"), ("orchestra-builder", "orchestra-builder")]

    def human_typing(self, s):
        return f"{s}: attached and typing" if s in self._typing else None

    def pane_text(self, s):
        return ("  ⎿  You've hit your session limit · resets 4:20pm (America/New_York)\n"
                if s in self._banners else "❯ \n")

    def switch(self, a):
        self.switched.append(a)
        return {"status": self._status, "followers": 3, "to": a}

    def nudge(self, s, text):
        self.nudged.append((s, text))
        return 0

    def mail(self, to, prio, subj, body):
        self.mails.append((to, prio, subj, body))


def _ep(window="5-hour", account="strategysimple", reset=RESET, open_=True):
    return {f"claude|{account}|{window}|{reset}": {
        "runtime": "claude", "window": window, "reset_at": reset, "account": account,
        "spend_capped": True, "open": open_, "seats": ["pm-nod"]}}


def _tick(episodes, deps, state=None, active="strategysimple", now=NOW):
    log = []
    state = {} if state is None else state
    res = LS.tick(None, episodes, active, now, log.append, deps, state)
    return res, log, state


def test_1_trigger_switches_to_the_login_with_headroom():
    d = Fake()
    res, log, st = _tick(_ep(), d)
    assert res == "switched" and d.switched == ["default"]
    done = [r for r in log if r["kind"] == "switch_done"][0]
    assert done["from"] == "strategysimple" and done["to"] == "default" and done["window"] == "5-hour"


@pytest.mark.parametrize("five,week", [(85.0, 10.0), (10.0, 80.0)])
def test_1_other_login_at_or_over_80_percent_is_no_headroom(five, week):
    d = Fake(usage={"default": {"as_of": NOW - 60, "windows": {"5h": {"used_pct": five}, "weekly": {"used_pct": week}}}})
    res, log, _ = _tick(_ep(), d)
    assert res == "refused" and d.switched == []


def test_1_a_stale_usage_reading_is_no_headroom():
    d = Fake(usage={"default": {"as_of": NOW - 3600, "windows": {"5h": {"used_pct": 1.0}, "weekly": {"used_pct": 1.0}}}})
    assert _tick(_ep(), d)[0] == "refused"


def test_1_no_trigger_for_a_closed_episode_another_login_or_a_passed_reset():
    d = Fake()
    assert _tick(_ep(open_=False), d)[0] is None
    assert _tick(_ep(account="default"), d)[0] is None
    assert _tick(_ep(reset=NOW - 1), d)[0] is None
    assert d.switched == []


def test_2_never_while_a_human_is_attached_and_typing_then_switches_when_they_stop():
    d = Fake(typing={"gm"})
    st = {}
    res, log, st = _tick(_ep(), d, st)
    assert res == "held" and d.switched == []
    assert log[-1]["reason"] == "human mid-typing"
    d._typing = set()
    assert _tick(_ep(), d, st)[0] == "switched"


def test_3_at_most_one_switch_per_ten_minutes():
    d = Fake()
    st = {"last_switch": NOW - 300}
    assert _tick(_ep(), d, st)[0] == "held" and d.switched == []
    st = {"last_switch": NOW - 601}
    assert _tick(_ep(), d, st)[0] == "switched"


def test_3_never_switches_back_to_the_parked_login_before_its_reset():
    d = Fake(usage={"strategysimple": {"as_of": NOW, "windows": {"5h": {"used_pct": 96.0, "resets_at": RESET},
                                                                 "weekly": {"used_pct": 40.0}}},
                    "default": {"as_of": NOW, "windows": {"5h": {"used_pct": 20.0}, "weekly": {"used_pct": 42.0}}}})
    st = {}
    assert _tick({}, d, st)[0] == "switched"                 # strategysimple -> default, parks strategysimple
    assert st["parked"]["strategysimple"] == RESET
    later = NOW + 1200                                       # default now at 96%, before RESET
    d._usage = {"strategysimple": {"as_of": later, "windows": {"5h": {"used_pct": 0.0}, "weekly": {"used_pct": 0.0}}},
                "default": {"as_of": later, "windows": {"5h": {"used_pct": 96.0, "resets_at": later + 3600},
                                                            "weekly": {"used_pct": 50.0}}}}
    res, log, st = _tick({}, d, st, active="default", now=later)
    assert res == "refused" and d.switched == ["default"]
    assert "parked until" in [r for r in log if r["kind"] == "switch_refused"][0]["reason"]
    # after the parked login's reset it is a candidate again
    for u in d._usage.values():
        u["as_of"] = RESET + 60
    d._usage["default"]["windows"]["5h"]["resets_at"] = RESET + 3600   # a reading's reset is after its as_of
    res, _, st = _tick({}, d, st, active="default", now=RESET + 60)
    assert res == "switched" and d.switched == ["default", "strategysimple"]


def test_4_switch_is_logged_with_seats_and_mailed_once_with_the_undo():
    d = Fake()
    _, log, _ = _tick(_ep(), d)
    done = [r for r in log if r["kind"] == "switch_done"][0]
    assert done["seats"] == ["gm", "pm-nod", "orchestra-builder"]
    assert done["undo"] == "python3 /x/account_switch.py --shared strategysimple"
    assert done["reset_at"] == RESET and "5-hour limit" in done["line"]
    assert len(d.mails) == 1 and d.mails[0][0] == "gm"
    assert "Undo: python3 /x/account_switch.py --shared strategysimple" in d.mails[0][3]


def test_5_banner_stuck_seats_are_nudged_once_with_window_reset_and_new_login():
    d = Fake(banners={"pm-nod", "gm"})
    _, log, _ = _tick(_ep(), d)
    assert sorted(s for s, _ in d.nudged) == ["gm", "pm-nod"]
    text = d.nudged[0][1]
    assert "5-hour limit" in text and "Local" in text and "now default" in text
    assert [r["seat"] for r in log if r["kind"] == "seat_nudged"] == ["gm", "pm-nod"]


def test_6_no_headroom_is_refused_logged_and_mailed_high_once_per_episode():
    d = Fake(usage={})
    st = {}
    res, log, st = _tick(_ep(), d, st)
    assert res == "refused"
    assert [m[1] for m in d.mails] == ["high"] and "REFUSED" in d.mails[0][2]
    _tick(_ep(), d, st)                                    # same episode, next run: no second mail
    assert len(d.mails) == 1


def test_a_failed_switch_is_mailed_and_parks_nothing():
    d = Fake(switch_status="refused")
    res, log, st = _tick(_ep(), d)
    assert res == "failed" and "FAILED" in d.mails[0][2]
    assert st.get("parked") == {} and "last_switch" not in st


def test_weekly_outranks_five_hour_as_the_trigger():
    eps = {**_ep(), **_ep(window="weekly", reset=NOW + 86400)}
    _, log, _ = _tick(eps, Fake())
    assert [r for r in log if r["kind"] == "switch_done"][0]["window"] == "weekly"


def test_human_typing_reader_ignores_dim_ghost_text(monkeypatch):
    calls = {}

    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def fake_tmux(*a, **k):
        if a[0] == "list-clients":
            return R(0, "gm\n")                        # list-clients -F "#{client_session}"
        return R(0, calls["screen"])

    monkeypatch.setattr(LS, "_tmux", fake_tmux)
    deps = LS.LiveDeps(None, None)
    calls["screen"] = "● done\n❯ \x1b[2mTry \"fix the tests\"\x1b[0m\n"
    assert deps.human_typing("gm") is None                         # ghost suggestion only
    calls["screen"] = "● done\n❯ hello there\n"
    assert deps.human_typing("gm") == "gm: attached and typing"
    calls["screen"] = "a menu, no prompt\n"
    assert "no composer" in deps.human_typing("gm")                 # cannot tell = hold


# --- the operator's rule (2026-11-25): switch at 95% of the 5-hour window, before the banner ------

def _reading(five, week=40.0, age=60, reset=RESET):
    return {"as_of": NOW - age, "windows": {"5h": {"used_pct": five, "resets_at": reset},
                                            "weekly": {"used_pct": week, "resets_at": NOW + 5 * 86400}}}


def _poll_fake(active_5h, other_5h=20.0, other_week=42.0, **kw):
    return Fake(usage={"strategysimple": _reading(active_5h),
                       "default": _reading(other_5h, other_week)}, **kw)


def test_95_at_94_percent_does_not_switch_and_logs_both_logins():
    d = _poll_fake(94.0)
    res, log, _ = _tick({}, d)
    assert res is None and d.switched == []
    chk = [r for r in log if r["kind"] == "switch_check"][0]
    assert chk["active_5h"] == 94.0 and chk["others"]["default"]["5h"] == 20.0
    assert chk["decision"].startswith("no switch")


def test_95_at_95_percent_switches_before_any_banner():
    d = _poll_fake(95.0)
    res, log, st = _tick({}, d)                       # no episode at all: no seat has hit the banner
    assert res == "switched" and d.switched == ["default"]
    done = [r for r in log if r["kind"] == "switch_done"][0]
    assert done["window"] == "5-hour" and done["reset_at"] == RESET
    assert st["parked"]["strategysimple"] == RESET


def test_95_with_the_other_login_at_80_or_more_refuses_and_mails_high():
    d = _poll_fake(96.0, other_5h=80.0)
    res, log, _ = _tick({}, d)
    assert res == "refused" and d.switched == []
    assert [m[1] for m in d.mails] == ["high"]


def test_95_a_banner_episode_switches_even_with_a_fresh_reading_under_95():
    # reversed 2026-11-26 (gm order msg_f495a92c): the reading cannot see every limit (a model's credits
    # cap), so a banner on the active login is the reactive leg and never waits for the reading
    d = _poll_fake(60.0)
    assert _tick(_ep(), d)[0] == "switched" and d.switched == ["default"]


def test_95_banner_episode_is_the_fallback_when_the_reading_is_stale():
    d = Fake(usage={"strategysimple": _reading(60.0, age=3600), "default": _reading(20.0)})
    res, log, _ = _tick(_ep(), d)
    assert res == "switched"
    assert "banner" in [r for r in log if r["kind"] == "switch_check"][0]["decision"]


def test_95_refreshes_the_reading_every_run():
    d = _poll_fake(10.0)
    calls = []
    d.refresh_usage = lambda: calls.append(1)
    _tick({}, d)
    assert calls == [1]


# --- review of #636 (Claude reviewer, codex out of credits): H1, H2, M1-M3 ----------------------

def _runs(deps, n, state, step=120, start=NOW, active="strategysimple", episodes=None):
    """n cron runs as run_once makes them: tick, then prune_told."""
    out = []
    for i in range(n):
        now = start + i * step
        log = []
        out.append(LS.tick(None, episodes or {}, active, now, log.append, deps, state))
        LS.prune_told(state, episodes or {}, now)
    return out


def test_h1_a_usage_trigger_refusal_mails_once_across_runs_until_its_reset():
    d = _poll_fake(96.0, other_5h=100.0)
    st = {}
    assert _runs(d, 5, st) == ["refused"] * 5
    assert len(d.mails) == 1                           # not one HIGH mail every 2 minutes
    _runs(d, 1, st, start=RESET + 60)                  # the reset in the key has passed: the tag is dropped
    assert st["told"] == {} and len(d.mails) == 1


def test_h1_a_failed_switch_backs_off_ten_minutes_and_mails_once():
    d = _poll_fake(96.0, switch_status="error")
    st = {}
    res = _runs(d, 5, st)                              # 8 minutes of runs
    assert res[0] == "failed" and set(res[1:]) == {"held"}
    assert d.switched == ["default"] and len(d.mails) == 1
    _runs(d, 1, st, start=NOW + 601)
    assert d.switched == ["default", "default"] and len(d.mails) == 1


def test_h1_an_old_list_state_still_loads():
    st = {"told": ["refused|poll|strategysimple|5-hour|1"]}
    LS.prune_told(st, {}, NOW)
    assert st["told"] == {}


def test_h2_noop_is_already_on_that_login_not_a_failure():
    d = _poll_fake(96.0, switch_status="noop")
    st = {}
    res = _runs(d, 3, st)
    assert res[0] == "noop" and [m[1] for m in d.mails] == ["medium"]
    assert st.get("parked") == {}


def test_m1_unreadable_registry_holds():
    d = _poll_fake(96.0)
    d.claude_seats = lambda: None
    res, log, _ = _tick({}, d)
    assert res == "held" and d.switched == []
    assert any(r.get("reason") == "seat registry unreadable" for r in log)


def test_m1_unreadable_tmux_clients_hold():
    d = _poll_fake(96.0)
    d.attached_sessions = lambda: None
    res, log, _ = _tick({}, d)
    assert res == "held" and d.switched == []


def test_m1_live_reader_lists_every_client_once_and_fails_closed(monkeypatch):
    class R:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out
    seen = []

    def fake_tmux(*a, **k):
        seen.append(a)
        if a[0] == "list-clients":
            return R(state["rc"], "gm\npm-nod\n")
        return R(0, "❯ hi\n")
    state = {"rc": 0}
    monkeypatch.setattr(LS, "_tmux", fake_tmux)
    deps = LS.LiveDeps(None, None)
    att = deps.attached_sessions()
    assert att == {"gm", "pm-nod"} and seen[0] == ("list-clients", "-F", "#{client_session}")
    assert deps.human_typing("orchestra-builder", att) is None          # nobody on it
    assert deps.human_typing("gm", att) == "gm: attached and typing"
    state["rc"] = 1
    assert deps.attached_sessions() is None
    assert "cannot list" in deps.human_typing("gm")


def test_m2_a_timeout_reads_as_a_failure_not_an_exception(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 1)
    monkeypatch.setattr(LS.subprocess, "run", boom)
    deps = LS.LiveDeps(None, None)
    assert deps.switch("default")["status"] == "error"
    assert deps.nudge("gm", "x") != 0
    assert deps.attached_sessions() is None


def test_m2_state_is_saved_the_moment_the_switch_happened():
    d = _poll_fake(96.0)
    saved = []

    def bad_nudge(s, t):
        raise RuntimeError("nudge blew up")
    d.nudge = bad_nudge
    st = {}
    with pytest.raises(RuntimeError):
        LS.tick(None, {}, "strategysimple", NOW, lambda r: None, d, st, save=lambda s: saved.append(dict(s)))
    assert saved and saved[-1]["last_switch"] == NOW and saved[-1]["parked"] == {"strategysimple": RESET}


def test_m2_nudges_are_capped(monkeypatch):
    d = _poll_fake(96.0)
    d.claude_seats = lambda: [(f"s{i}", f"s{i}") for i in range(40)]
    d._banners = {f"s{i}" for i in range(40)}
    _tick({}, d)
    assert len(d.nudged) == LS.MAX_NUDGES


def test_m3_a_manual_switch_counts_for_the_ten_minute_gap():
    d = _poll_fake(96.0)
    d.last_switch = lambda: NOW - 120
    res, _, _ = _tick({}, d)
    assert res == "held" and d.switched == []


def test_m3_last_fleet_switch_reads_the_audit_log(tmp_path):
    p = tmp_path / "account-switch.jsonl"
    p.write_text('{"ts": "2026-11-25T17:58:00+00:00", "seat": "*shared*", "status": "switched"}\n'
                 '{"ts": "2026-11-25T17:59:00+00:00", "seat": "gm", "status": "switched"}\n'
                 '{"ts": "2026-11-25T17:59:30+00:00", "seat": "*shared*", "status": "noop"}\n')
    assert LS.last_fleet_switch(str(p)) == NOW - 120
    assert LS.last_fleet_switch(str(tmp_path / "missing")) == 0.0


# --- re-review of 9411e11: a passed reset, a noop, a deliberate switch back ----------------------

def test_rr1_a_reading_taken_before_its_reset_is_no_trigger_after_it():
    d = Fake(usage={"strategysimple": _reading(96.0, age=60, reset=NOW - 30), "default": _reading(20.0)})
    res, log, _ = _tick({}, d)
    assert res is None and d.switched == [] and d.mails == []
    assert "predates its reset" in log[0]["decision"]


def test_rr1_a_tag_lives_at_least_an_hour():
    d = Fake(usage={"strategysimple": _reading(96.0, age=-(RESET - 120 - NOW)), "default": _reading(100.0)})
    st = {}
    _runs(d, 1, st, start=RESET - 60)
    assert min(st["told"].values()) >= RESET - 60 + 3600


def test_rr2_noop_mails_gm_once_medium():
    d = _poll_fake(96.0, switch_status="noop")
    st = {}
    _runs(d, 1, st)
    _runs(d, 1, st, start=NOW + 700)
    assert [m[1] for m in d.mails] == ["medium"] and "already holds" in d.mails[0][2]


def test_rr3_a_switch_back_by_hand_after_the_trigger_is_left_alone():
    d = _poll_fake(96.0, switch_status="error")
    st = {}
    _runs(d, 1, st)                                         # first trigger at NOW (the switch fails)
    d.last_switch_row = lambda: (NOW + 900, "strategysimple")  # then someone picks strategysimple by hand
    res = _runs(d, 2, st, start=NOW + 1300)
    assert res == ["held", "held"] and d.switched == ["default"]
    assert [m[1] for m in d.mails] == ["high", "medium"]


def test_rr3_a_switch_to_the_login_before_it_got_hot_does_not_hold():
    d = _poll_fake(96.0)
    d.last_switch_row = lambda: (NOW - 7200, "strategysimple")   # it was picked hours before it got hot
    assert _tick({}, d)[0] == "switched"


# --- weekly-aware preference + warnings (gm order msg_50aa10b0, 2026-11-26) --------------------

WEEK_RESET = NOW + 4 * 86400


def _u(five, week, as_of=NOW - 60):
    return {"as_of": as_of, "windows": {"5h": {"used_pct": five, "resets_at": RESET},
                                        "weekly": {"used_pct": week, "resets_at": WEEK_RESET}}}


def test_weekly_prefers_the_login_with_the_lower_weekly_when_both_have_5h_headroom():
    d = Fake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    res, log, st = _tick({}, d, active="default")
    assert res == "preferred" and d.switched == ["strategysimple"]
    row = [r for r in log if r["kind"] == "switch_done"][0]
    assert row["reason"].startswith("weekly preference") and row["from"] == "default"
    assert "default" not in st.get("parked", {})              # the old login is not out: never parked
    assert d.nudged == []                                     # nobody is stuck on a banner
    assert any("weekly" in m[2] and "Undo" in m[3] for m in d.mails if m[0] == "gm")


def test_weekly_preference_needs_a_real_margin():
    d = Fake(usage={"default": _u(38.0, 64.0), "strategysimple": _u(5.0, 61.0)})
    assert _tick({}, d, active="default")[0] is None and d.switched == []


@pytest.mark.parametrize("other", [_u(85.0, 10.0), _u(5.0, 61.0, as_of=NOW - 3600), None])
def test_weekly_preference_never_moves_to_a_login_without_a_fresh_5h_headroom(other):
    d = Fake(usage={"default": _u(38.0, 72.0), **({"strategysimple": other} if other else {})})
    assert _tick({}, d, active="default")[0] is None and d.switched == []


def test_weekly_preference_never_when_the_active_login_lacks_5h_headroom_itself():
    # 85% on the active 5-hour: the 95% trigger owns that case, the preference stays out of it
    d = Fake(usage={"default": _u(85.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    assert _tick({}, d, active="default")[0] is None


def test_weekly_preference_at_most_once_per_hour_any_switch_counts():
    d = Fake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    st = {"last_switch": NOW - 1800}
    assert _tick({}, d, st, active="default")[0] is None and d.switched == []
    st = {"last_switch": NOW - 3601}
    assert _tick({}, d, st, active="default")[0] == "preferred"


def test_weekly_preference_holds_for_a_typing_human_and_a_parked_target():
    d = Fake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)}, typing={"gm"})
    res, log, _ = _tick({}, d, active="default")
    assert res == "held" and d.switched == []
    d = Fake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    assert _tick({}, d, {"parked": {"strategysimple": NOW + 600}}, active="default")[0] is None


def test_weekly_preference_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("LIMIT_SWITCH_PREFER_WEEKLY", "0")
    d = Fake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    assert _tick({}, d, active="default")[0] is None


def test_weekly_warns_gm_once_per_login_per_week_at_70_then_85():
    d = Fake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    st = {"last_switch": NOW - 60}                            # no preference switch in this test
    _tick({}, d, st, active="default")
    _tick({}, d, st, active="default", now=NOW + 120)
    warn = [m for m in d.mails if "weekly" in m[2].lower() and "70%" in m[2]]
    assert len(warn) == 1 and "default" in warn[0][2]
    d._usage["default"] = _u(38.0, 86.0)
    d._usage["strategysimple"] = _u(5.0, 71.0)
    _tick({}, d, st, active="default", now=NOW + 240)
    _tick({}, d, st, active="default", now=NOW + 360)
    subj = sorted(m[2] for m in d.mails if "weekly" in m[2].lower() and "%" in m[2])
    assert len(subj) == 3                                     # default 70, default 85, strategysimple 70
    assert any("85%" in s and "default" in s for s in subj)
    assert any("70%" in s and "strategysimple" in s for s in subj)


def test_weekly_warning_comes_back_the_next_week():
    d = Fake(usage={"default": _u(38.0, 72.0)})
    st = {"last_switch": NOW - 60}
    _tick({}, d, st, active="default")
    LS.prune_told(st, {}, now=WEEK_RESET + 1)
    d._usage["default"] = _u(38.0, 72.0, as_of=WEEK_RESET + 3000)
    d._usage["default"]["windows"]["weekly"]["resets_at"] = WEEK_RESET + 7 * 86400
    _tick({}, d, st, active="default", now=WEEK_RESET + 3060)
    assert len([m for m in d.mails if "70%" in m[2]]) == 2


# --- the reactive leg: any banner on the active login switches (gm order msg_f495a92c, 2026-11-26) --------

FABLE_PANE = "  ⎿  You've reached your Fable limit. Run /usage-credits to continue or switch models with /model.\n"


class FableFake(Fake):
    """gm's day: the active login's 5-hour reading is FRESH and LOW, yet its seats sit on the Fable banner."""

    def __init__(self, *, attached=(), limited=(), **kw):
        kw.setdefault("usage", {"default": _u(1.0, 73.0), "strategysimple": _u(5.0, 61.0)})
        kw.setdefault("banners", ("gm", "pm-nod"))
        super().__init__(**kw)
        self._attached, self._limited = set(attached), set(limited)

    def pane_text(self, s):
        return FABLE_PANE if s in self._banners else "❯ \n"

    def attached_sessions(self):
        return set(self._attached)

    def human_typing(self, s, attached=None):
        return None

    def limited_seats(self):
        return set(self._limited)


def _credits_ep(account="default", model="Fable", seats=("gm",), open_=True):
    return {f"claude|{account}|credits|2026-11-26": {
        "runtime": "claude", "window": "credits", "reset_at": None, "account": account, "model": model,
        "spend_capped": False, "open": open_, "seats": list(seats)}}


def test_reactive_a_credits_banner_switches_even_with_a_fresh_low_5h_reading():
    d = FableFake()
    res, log, st = _tick(_credits_ep(), d, active="default", state={"last_switch": NOW - 3601})
    assert res == "switched" and d.switched == ["strategysimple"]
    done = [r for r in log if r["kind"] == "switch_done"][0]
    assert done["window"] == "credits" and done["model"] == "Fable"
    mail = [m for m in d.mails if m[0] == "gm"][-1]
    assert "Fable" in mail[3] and "default" in mail[3] and "strategysimple" in mail[3] and "Undo" in mail[3]


def test_reactive_banner_on_another_login_or_closed_does_not_switch():
    d = FableFake(usage={"default": _u(1.0, 61.0), "strategysimple": _u(5.0, 61.0)})   # no weekly preference
    assert _tick(_credits_ep(account="strategysimple"), d, active="default")[0] != "switched"
    assert _tick(_credits_ep(open_=False), d, active="default")[0] != "switched"
    assert d.switched == []


def test_reactive_credits_target_with_its_own_credits_episode_is_refused():
    d = FableFake()
    eps = {**_credits_ep(), **_credits_ep(account="strategysimple")}
    res, log, _ = _tick(eps, d, active="default")
    assert res == "refused" and d.switched == []
    assert any(m[1] == "high" and "REFUSED" in m[2] for m in d.mails)


def test_reactive_credits_target_with_no_usage_reading_is_tried():
    d = FableFake(usage={"default": _u(1.0, 73.0)})
    assert _tick(_credits_ep(), d, active="default")[0] == "switched"


def test_reactive_credits_target_whose_5h_is_used_up_is_no_headroom():
    d = FableFake(usage={"default": _u(1.0, 73.0), "strategysimple": _u(100.0, 61.0)})
    assert _tick(_credits_ep(), d, active="default")[0] == "refused"


def test_reactive_safety_conditions_still_hold():
    d = FableFake()
    assert _tick(_credits_ep(), d, {"last_switch": NOW - 300}, active="default")[0] == "held"
    d = FableFake(typing={"gm"})
    d.human_typing = lambda s, attached=None: f"{s}: attached and typing" if s == "gm" else None
    assert _tick(_credits_ep(), d, active="default")[0] == "held" and d.switched == []


def test_nudges_only_unattached_bannered_seats_with_the_model_named():
    d = FableFake(attached={"pm-nod"})
    _tick(_credits_ep(seats=("gm", "pm-nod")), d, active="default")
    assert [s for s, _ in d.nudged] == ["gm"]
    assert "Fable" in d.nudged[0][1] and "strategysimple" in d.nudged[0][1]


def test_after_the_switch_gm_hears_which_seats_resumed():
    d = FableFake(banners=("gm",), limited={"gm"})
    res, log, st = _tick(_credits_ep(), d, active="default")
    assert res == "switched" and st["verify"]["seats"] == ["gm"]
    n = len(d.mails)
    _tick({}, d, st, active="strategysimple", now=NOW + 120)              # gm still limited: nothing yet
    assert len(d.mails) == n
    d._limited = set()
    _tick({}, d, st, active="strategysimple", now=NOW + 240)
    assert "verify" not in st
    assert any("resumed" in m[2] and "gm" in m[3] for m in d.mails[n:])


def test_after_the_switch_no_resume_in_15_minutes_is_a_high_mail():
    d = FableFake(banners=("gm",), limited={"gm"})
    _, _, st = _tick(_credits_ep(), d, active="default")
    n = len(d.mails)
    _tick({}, d, st, active="strategysimple", now=NOW + 16 * 60)
    assert any(m[1] == "high" and "did not resume" in m[2] for m in d.mails[n:])
    assert "verify" not in st


def test_live_deps_reads_the_limited_seats_from_the_seat_rows(tmp_path):
    class P:
        seat_rows = str(tmp_path / "limits.json")
    d = LS.LiveDeps(P(), None)
    assert d.limited_seats() is None                                  # unreadable: the check waits
    (tmp_path / "limits.json").write_text(json.dumps({"seats": {"gm": {}, "pm-nod": {}}}))
    assert d.limited_seats() == {"gm", "pm-nod"}


# --- codex gating review of #664+#666 (codex exec, 2026-11-26): findings 1, 2, 4 ---------------------------

def test_f1_a_credits_episode_with_no_new_row_for_2h_no_longer_triggers():
    eps = _credits_ep()
    next(iter(eps.values()))["last_seen"] = "2026-11-25T15:00:00+00:00"       # NOW is 18:00Z: 3 h quiet
    d = FableFake(usage={"default": _u(1.0, 61.0), "strategysimple": _u(5.0, 61.0)})
    assert _tick(eps, d, active="default")[0] is None and d.switched == []
    next(iter(eps.values()))["last_seen"] = "2026-11-25T17:30:00+00:00"
    assert _tick(eps, d, active="default")[0] == "switched"


def test_f1_a_targets_credits_episode_older_than_12h_does_not_disqualify_it():
    target = _credits_ep(account="strategysimple")
    next(iter(target.values()))["last_seen"] = "2026-11-25T04:00:00+00:00"    # 14 h old: refilled since, maybe
    d = FableFake()
    assert _tick({**_credits_ep(), **target}, d, active="default")[0] == "switched"


def test_f2_weekly_preference_never_moves_to_a_login_with_a_live_credits_episode():
    d = FableFake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    eps = _credits_ep(account="strategysimple")                                # strategysimple's Fable is out
    assert _tick(eps, d, active="default")[0] is None and d.switched == []


def test_f2_our_own_switch_is_not_read_as_a_hand_switch_back():
    d = Fake(usage={"strategysimple": _reading(97.0), "default": _reading(20.0)})
    st = {"first_trigger": {f"poll|strategysimple|5-hour|{RESET}": NOW - 600}, "last_switch": NOW - 120,
          "last_to": "strategysimple"}
    d.last_switch_row = lambda: (NOW - 120, "strategysimple")                  # the log row of OUR switch
    d.last_switch = lambda: NOW - 900
    res, log, _ = _tick({}, d, st)
    assert not any(r.get("reason") == "switched back by hand" for r in log)


def test_f4_a_weekly_reading_taken_before_its_reset_does_not_warn_after_it():
    d = Fake(usage={"default": _u(38.0, 86.0)})
    d._usage["default"]["windows"]["weekly"]["resets_at"] = NOW - 60           # the week already turned over
    st = {"last_switch": NOW - 60}
    for i in range(3):
        _tick({}, d, st, active="default", now=NOW + 120 * i)
    assert not [m for m in d.mails if "weekly" in m[2].lower() and "%" in m[2]]


# --- codex re-review at 3e38672 --------------------------------------------------------------------------

def test_r2_a_hand_switch_back_60s_after_ours_is_still_a_hand_switch():
    # we moved strategysimple -> default (last_to default); the operator put strategysimple back 60 s later
    d = Fake(usage={"strategysimple": _reading(97.0), "default": _reading(20.0)})
    st = {"first_trigger": {f"poll|strategysimple|5-hour|{RESET}": NOW - 600}, "last_switch": NOW - 120,
          "last_to": "default"}
    d.last_switch_row = lambda: (NOW - 60, "strategysimple")
    d.last_switch = lambda: NOW - 60
    res, log, _ = _tick({}, d, st)
    assert res == "held" and d.switched == []
    assert any(r.get("reason") == "switched back by hand" for r in log)


def test_r2_a_window_trigger_never_moves_to_a_login_whose_credits_are_out():
    d = FableFake(usage={"default": _u(96.0, 40.0), "strategysimple": _u(5.0, 30.0)})
    res, log, _ = _tick(_credits_ep(account="strategysimple"), d, active="default")
    assert res == "refused" and d.switched == []
    assert any("strategysimple" in r.get("reason", "") and "limit" in r.get("reason", "") for r in log
               if r["kind"] == "switch_refused")


def test_r2_every_switch_records_where_it_went():
    d = FableFake()
    _, _, st = _tick(_credits_ep(), d, active="default")
    assert st["last_to"] == "strategysimple"
    d = FableFake(usage={"default": _u(38.0, 72.0), "strategysimple": _u(5.0, 61.0)})
    _, _, st = _tick({}, d, active="default")
    assert st["last_to"] == "strategysimple"
