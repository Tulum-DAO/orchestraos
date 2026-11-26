"""model_drop: a seat out of Fable credits drops to Opus 5.5 (operator 2026-11-26, apr_bafb66c3)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model_drop as MD  # noqa: E402

SEEN = "2026-11-26T09:55:43Z"
NOW = 1795686943 + 30                                    # SEEN + 30 s
BANNER = "  ⎿  You've reached your Fable limit. Run /usage-credits to continue or switch models with /model."


def _row(window="credits", model="Fable", seen=SEEN, key="claude|default|credits|2026-11-26"):
    return {"runtime": "claude", "window": window, "model": model, "reset_at": None, "account": "default",
            "since": seen, "last_seen": seen, "key": key}


class Fake:
    def __init__(self, *, answer=(200, {"ok": True, "switching": "switched"}), attached=(), banner=True,
                 sessions=None):
        self.answer, self._attached, self._banner = answer, set(attached), banner
        self._sessions = {"gm": "gm", "pm-nod": "pm-nod"} if sessions is None else sessions
        self.switched, self.nudged, self.mails = [], [], []

    def sessions(self):
        return self._sessions

    def seat_model(self, session):
        return "claude-fable-5-1[1m]"

    def switch_model(self, session, model):
        self.switched.append((session, model))
        return self.answer(session) if callable(self.answer) else self.answer

    def attached_sessions(self):
        return set(self._attached)

    def pane_text(self, s):
        return BANNER if self._banner else "❯ \n"

    def nudge(self, s, text):
        self.nudged.append((s, text))
        return 0

    def mail(self, *a):
        self.mails.append(a)


def _tick(rows, d, state=None, now=NOW):
    log, state = [], ({} if state is None else state)
    out = MD.tick(rows, now, log.append, d, state)
    return out, log, state


def test_a_fable_seat_switches_to_opus_5_5_through_the_gateway_and_is_nudged():
    d = Fake()
    out, log, st = _tick({"gm": _row()}, d)
    assert d.switched == [("gm", "claude-opus-5-5[1m]")]
    assert [s for s, _ in d.nudged] == ["gm"] and "Opus 5.5" in d.nudged[0][1]
    assert out == ["gm"] and any(r["kind"] == "model_dropped" for r in log)
    assert d.mails and d.mails[-1][0] == "gm" and "gm" in d.mails[-1][3] and "not switched" in d.mails[-1][3]


def test_an_unnamed_credits_cap_counts_another_models_or_a_window_does_not():
    d = Fake()
    _tick({"gm": _row(model=None), "pm-nod": _row(model="Opus"), "x": _row(window="5-hour")}, d)
    assert d.switched == [("gm", "claude-opus-5-5[1m]")]


def test_a_row_older_than_2h_is_left_alone():
    d = Fake()
    _tick({"gm": _row(seen="2026-11-26T07:00:00Z")}, d)
    assert d.switched == []


def test_once_per_seat_per_episode():
    d = Fake()
    _, _, st = _tick({"gm": _row()}, d)
    _tick({"gm": _row()}, d, st, now=NOW + 120)
    assert len(d.switched) == 1
    _tick({"gm": _row(key="claude|default|credits|2026-11-27")}, d, st, now=NOW + 240)   # a new episode
    assert len(d.switched) == 2


def test_a_failed_switch_retries_after_10_min_three_times_then_mails_high_once():
    d = Fake(answer=(0, {"error": "gateway unreachable: URLError"}))
    st = {}
    for i in range(8):
        _tick({"gm": _row(seen="2026-11-26T09:55:43Z")}, d, st, now=NOW + i * 300)
    assert len(d.switched) == 3
    assert len([m for m in d.mails if m[1] == "high"]) == 1
    assert d.nudged == []


def test_a_busy_seat_is_deferred_by_the_gateway_and_not_nudged():
    d = Fake(answer=(202, {"ok": True, "status": "pending", "applies": "at turn end"}))
    out, log, st = _tick({"gm": _row()}, d)
    assert out == ["gm (at its turn end)"] and d.nudged == []
    _tick({"gm": _row()}, d, st, now=NOW + 700)
    assert len(d.switched) == 1


def test_a_seat_the_gateway_says_is_not_on_fable_is_skipped_for_good():
    d = Fake(answer=(409, {"ok": False, "error": "not on a Fable model", "reason": "not_fable"}))
    out, log, st = _tick({"gm": _row()}, d)
    assert out == [] and any(r["kind"] == "model_drop_skipped" for r in log) and not d.mails
    _tick({"gm": _row()}, d, st, now=NOW + 700)
    assert len(d.switched) == 1


def test_an_attached_seat_is_switched_but_never_typed_into():
    d = Fake(attached={"gm"})
    _tick({"gm": _row()}, d)
    assert d.switched and d.nudged == []


def test_a_seat_whose_banner_scrolled_away_is_not_nudged():
    d = Fake(banner=False)
    _tick({"gm": _row()}, d)
    assert d.switched and d.nudged == []


def test_unreadable_registry_holds_and_unknown_seat_is_skipped():
    d = Fake(sessions=None)
    d.sessions = lambda: None
    out, log, _ = _tick({"gm": _row()}, d)
    assert d.switched == [] and any(r["kind"] == "model_drop_held" for r in log)
    d = Fake(sessions={})
    _tick({"session 1a2b3c4d": _row()}, d)
    assert d.switched == []


def test_prune_forgets_a_recovered_seat_after_a_day():
    st = {"drops": {"gm|k": {"done": True, "last": NOW}}}
    MD.prune(st, {}, NOW + 3600)
    assert st["drops"]
    MD.prune(st, {}, NOW + 2 * 86400)
    assert st["drops"] == {}


def test_live_deps_never_reach_a_gateway_under_pytest(tmp_path):
    class P:
        registry_db = str(tmp_path / "none.db")
    status, body = MD.LiveDeps(P(), None).switch_model("gm", MD.TARGET)
    assert status == 599


# --- codex gating review of the rework (536a294) ------------------------------------------------------------

def test_r1_a_new_generation_hitting_fable_again_the_same_day_is_dropped_again():
    d = Fake()
    st = {}
    _tick({"gm": dict(_row(), session="sid-a")}, d, st)
    _tick({}, d, st, now=NOW + 120)                                         # recovered on Opus
    _tick({"gm": dict(_row(), session="sid-b")}, d, st, now=NOW + 3600)     # pinned seat rotated back to Fable
    assert len(d.switched) == 2


def test_r1_unreadable_tmux_clients_mean_no_nudge():
    d = Fake()
    d.attached_sessions = lambda: None
    _tick({"gm": _row()}, d)
    assert d.switched and d.nudged == []


def test_r1_the_client_list_is_read_after_the_switch_not_before():
    d = Fake()
    calls = []
    real_switch = d.switch_model
    d.switch_model = lambda s, m: (calls.append("switch"), real_switch(s, m))[1]
    d.attached_sessions = lambda: (calls.append("clients"), {"gm"})[1]     # a person attached meanwhile
    _tick({"gm": _row()}, d)
    assert calls == ["switch", "clients"] and d.nudged == []


# --- the weekly Fable window (usage_poll "weekly-fable"): proactive drop at 95%, return after its reset ------

RESET = NOW + 4 * 86400
OPUS, FABLE = "claude-opus-5-5[1m]", "claude-fable-5-1[1m]"


def _usage(pct, as_of=NOW - 60, reset=RESET):
    return {"as_of": as_of, "windows": {"5h": {"used_pct": 20.0}, "weekly": {"used_pct": 60.0},
                                        "weekly-fable": {"used_pct": pct, "resets_at": reset}}}


class WFake(Fake):
    def __init__(self, *, usage=None, models=None, seats=(("gm", "gm"), ("pm-nod", "pm-nod"), ("dev", "dev")), **kw):
        super().__init__(**kw)
        self._usage, self._seats = usage, seats
        self.models = dict(models if models is not None else {"gm": FABLE, "pm-nod": FABLE, "dev": OPUS})
        self._sessions = {r: s for r, s in seats}

    def usage(self, account):
        return self._usage

    def claude_seats(self):
        return list(self._seats)

    def seat_model(self, session):
        return self.models.get(session)

    def switch_model(self, session, model):
        out = super().switch_model(session, model)
        if (out[0] if not callable(self.answer) else 200) == 200:
            self.models[session] = model
        return out


def _win(d, state=None, now=NOW, active="strategysimple"):
    log, state = [], ({} if state is None else state)
    out = MD.tick_window(active, now, log.append, d, state)
    return out, log, state


def test_w1_at_95_percent_every_fable_seat_moves_to_opus_before_the_banner():
    d = WFake(usage=_usage(95.0))
    out, log, st = _win(d)
    assert sorted(d.switched) == [("gm", OPUS), ("pm-nod", OPUS)]               # dev was on Opus already
    assert sorted(out["dropped"]) == ["gm", "pm-nod"]
    assert st["dropped"]["gm"] == {"from": FABLE, "session": "gm", "at": NOW, "resets_at": RESET}
    assert d.nudged == [] and d.mails and "95%" in d.mails[-1][2]


def test_w1_under_95_or_a_stale_reading_moves_nobody():
    for u in (_usage(94.0), _usage(99.0, as_of=NOW - 31 * 60), None, {"as_of": NOW, "windows": {}}):
        d = WFake(usage=u)
        _win(d)
        assert d.switched == []


def test_w1_a_failed_proactive_switch_waits_10_minutes():
    d = WFake(usage=_usage(97.0), answer=(0, {"error": "gateway unreachable"}))
    _, _, st = _win(d)
    n = len(d.switched)
    _win(d, st, now=NOW + 120)
    assert len(d.switched) == n
    _win(d, st, now=NOW + 700)
    assert len(d.switched) == 2 * n


def test_w2_after_the_reset_a_dropped_seat_goes_back_to_its_fable_model():
    d = WFake(usage=_usage(96.0))
    _, _, st = _win(d)
    d._usage = _usage(3.0, as_of=RESET + 120, reset=RESET + 7 * 86400)     # a reading taken after the reset
    out, log, st = _win(d, st, now=RESET + 180)
    assert sorted(out["returned"]) == ["gm", "pm-nod"]
    assert d.models["gm"] == FABLE and st["dropped"] == {}
    assert any("back on Fable" in m[2] for m in d.mails)


def test_w2_no_return_before_the_reset_or_on_a_reading_older_than_it():
    d = WFake(usage=_usage(96.0))
    _, _, st = _win(d)
    n = len(d.switched)
    d._usage = _usage(40.0, as_of=NOW + 600)                               # another login, before the reset
    _win(d, st, now=NOW + 700)
    d._usage = _usage(40.0, as_of=RESET - 60)                              # a reading from before the reset
    _win(d, st, now=RESET + 30)
    assert len(d.switched) == n and set(st["dropped"]) == {"gm", "pm-nod"}


def test_w2_a_seat_moved_by_hand_since_is_left_alone_and_forgotten():
    d = WFake(usage=_usage(96.0))
    _, _, st = _win(d)
    d.models["gm"] = "claude-opus-5[1m]"                                   # a person picked another model
    d._usage = _usage(3.0, as_of=RESET + 120)
    _win(d, st, now=RESET + 180)
    assert ("gm", FABLE) not in d.switched and "gm" not in st["dropped"]
    assert ("pm-nod", FABLE) in d.switched


def test_w2_a_banner_drop_learns_its_reset_from_the_first_reading_and_returns_after_it():
    d = WFake(usage=None)
    _, _, st = _tick({"gm": _row()}, d)                                    # banner leg, no reading yet
    assert st["dropped"]["gm"]["from"] == FABLE and st["dropped"]["gm"]["resets_at"] is None
    d._usage = _usage(100.0)
    _win(d, st, now=NOW + 120)
    assert st["dropped"]["gm"]["resets_at"] == RESET
    d._usage = _usage(2.0, as_of=RESET + 60)
    out, _, _ = _win(d, st, now=RESET + 90)
    assert sorted(out["returned"]) == ["gm", "pm-nod"]                    # pm-nod was moved by the 100% reading


def test_w2_a_drop_whose_prior_model_was_not_fable_never_returns():
    d = WFake(usage=None)
    d.models["gm"] = None                                                  # the gateway could not say
    _, _, st = _tick({"gm": _row()}, d)
    assert st["dropped"]["gm"]["from"] is None
    d._usage = _usage(2.0, as_of=RESET + 60)
    st["dropped"]["gm"]["resets_at"] = RESET
    out, _, _ = _win(d, st, now=RESET + 90)
    assert out["returned"] == []
