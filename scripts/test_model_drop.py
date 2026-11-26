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
