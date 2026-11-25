"""limit_switch: each arming condition is a test (gm order msg_4dc56c81, 2026-11-25)."""
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
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ_LABEL", "Tulum")
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
    d = Fake(usage={"strategysimple": {"as_of": NOW, "windows": {"5h": {"used_pct": 0.0}, "weekly": {"used_pct": 0.0}}},
                    "default": {"as_of": NOW - 60, "windows": {"5h": {"used_pct": 20.0}, "weekly": {"used_pct": 42.0}}}})
    st = {}
    _tick(_ep(), d, st)                                   # strategysimple -> default, parks strategysimple
    assert st["parked"]["strategysimple"] == RESET
    later = NOW + 1200                                     # default now hits a limit, before RESET
    res, log, st = _tick(_ep(account="default", reset=later + 3600), d, st, active="default", now=later)
    assert res == "refused" and d.switched == ["default"]
    assert "parked until" in [r for r in log if r["kind"] == "switch_refused"][0]["reason"]
    # after the parked login's reset it is a candidate again (with a fresh reading)
    d._usage["strategysimple"]["as_of"] = RESET
    res, _, st = _tick(_ep(account="default", reset=RESET + 7200, window="weekly"), d, st,
                       active="default", now=RESET + 60)
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
    assert "5-hour limit" in text and "Tulum" in text and "now default" in text
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
            return R(0, "/dev/pts/1: gm [200x50]\n")
        return R(0, calls["screen"])

    monkeypatch.setattr(LS, "_tmux", fake_tmux)
    deps = LS.LiveDeps(None, None)
    calls["screen"] = "● done\n❯ \x1b[2mTry \"fix the tests\"\x1b[0m\n"
    assert deps.human_typing("gm") is None                         # ghost suggestion only
    calls["screen"] = "● done\n❯ hello there\n"
    assert deps.human_typing("gm") == "gm: attached and typing"
    calls["screen"] = "a menu, no prompt\n"
    assert "no composer" in deps.human_typing("gm")                 # cannot tell = hold
