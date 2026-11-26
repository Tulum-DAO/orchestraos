"""codex_watch: the reviewer rule flips itself (gm order msg_50aa10b0, 2026-11-26).

Each order point is a test: the classifier on the real OUT texts of 11-25/11-26, the 2-reading
debounce, one mail per seat and one to gm per episode, the probe's clean environment and cadence.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_watch as CW  # noqa: E402
import limit_event as LE  # noqa: E402

NOW = 1795680000                       # 2026-11-26 08:00Z
RESET = NOW + 3 * 3600

# the real texts (codex exec stderr / pane banners / rollout reasons, 11-25 and 11-26)
OUT_CREDITS = "■ Your workspace is out of credits. Ask your workspace owner to refill in order to continue."
OUT_CREDITS_EXEC = "ERROR: Your workspace is out of credits. Add credits to continue."
OUT_USAGE = ("■ Usage limit reached. You've reached your usage limit. Increase your limits to continue "
             "using codex.")
OUT_CARD = "Usage limit reached Request a limit increase from your owner to continue using codex."
OK_REPLY = "ok\n"


@pytest.fixture(autouse=True)
def _zone(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ", "America/Cancun")
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ_LABEL", "Tulum")


# --- 1. the classifier -------------------------------------------------------------------------

@pytest.mark.parametrize("text,window", [
    (OUT_CREDITS, "credits"),
    (OUT_CREDITS_EXEC, "credits"),
    (OUT_USAGE, "unknown"),
    (OUT_CARD, "unknown"),
])
def test_1_probe_out_texts_are_out_with_the_window_named(text, window):
    r = CW.classify_probe(1, "", text, now=NOW)
    assert r.status == "out" and r.window == window and r.source == "probe"


def test_1_out_text_on_stdout_with_rc0_is_still_out():
    assert CW.classify_probe(0, OUT_CREDITS, "", now=NOW).status == "out"


def test_1_a_normal_reply_is_ok():
    r = CW.classify_probe(0, OK_REPLY, "some log noise", now=NOW)
    assert r.status == "ok"


@pytest.mark.parametrize("rc,err", [(124, "timed out"), (1, "Error: not logged in"), (127, "codex: not found")])
def test_1_any_other_failure_is_unknown_never_ok_or_out(rc, err):
    assert CW.classify_probe(rc, "", err, now=NOW).status == "unknown"


def test_1_usage_reading_at_100_is_out_and_names_the_window():
    u = {"as_of": NOW - 30, "windows": {"5h": {"used_pct": 100.0, "resets_at": RESET},
                                         "weekly": {"used_pct": 70.0, "resets_at": NOW + 86400}}}
    r = CW.classify_usage(u, NOW)
    assert (r.status, r.window, r.reset_at, r.source) == ("out", "5-hour", RESET, "usage")
    u["windows"]["5h"]["used_pct"] = 15.0
    u["windows"]["weekly"]["used_pct"] = 100.0
    assert CW.classify_usage(u, NOW).window == "weekly"


def test_1_usage_reading_under_100_is_ok_and_a_stale_one_is_no_reading():
    u = {"as_of": NOW - 30, "windows": {"5h": {"used_pct": 15.0}, "weekly": {"used_pct": 70.0}}}
    assert CW.classify_usage(u, NOW).status == "ok"
    u["as_of"] = NOW - 3600
    assert CW.classify_usage(u, NOW) is None
    assert CW.classify_usage(None, NOW) is None


def test_1_a_codex_rollout_event_is_an_out_reading():
    ev = LE.LimitEvent("codex", "credits", None, source="rollout", text="workspace_owner_credits_depleted")
    r = CW.from_events([ev], NOW)
    assert r.status == "out" and r.window == "credits" and r.source == "rollout"
    assert CW.from_events([LE.LimitEvent("claude", "5-hour", RESET)], NOW) is None


# --- 2. debounce + state file + mails ------------------------------------------------------------

class Box:
    def __init__(self):
        self.mails = []

    def mail(self, to, prio, subj, body):
        self.mails.append((to, prio, subj, body))


def _r(status, window=None, source="probe", ev="x"):
    return CW.Reading(status, window, None, source, ev)


def _run(tmp_path, readings, state=None, box=None, now=NOW):
    box = box or Box()
    state = {} if state is None else state
    out = []
    for i, r in enumerate(readings):
        out.append(CW.decide(state, [r], now + 120 * i, box.mail,
                             availability_path=str(tmp_path / "codex-availability.json"),
                             log=lambda row: None))
    return out, state, box


def _avail(tmp_path):
    return json.load(open(tmp_path / "codex-availability.json"))


def test_2_one_out_reading_does_not_flip_two_in_a_row_do(tmp_path):
    out, st, box = _run(tmp_path, [_r("ok"), _r("ok")])
    assert _avail(tmp_path)["status"] == "ok" and box.mails == []        # baseline OK: silent
    out, st, box = _run(tmp_path, [_r("out", "credits")], st)
    assert _avail(tmp_path)["status"] == "ok" and box.mails == []
    out, st, box = _run(tmp_path, [_r("out", "credits")], st, now=NOW + 600)
    a = _avail(tmp_path)
    assert a["status"] == "out" and a["window"] == "credits" and a["gating_reviewer"] == "claude"
    assert a["evidence"] and a["since"]


def test_2_an_interleaved_ok_resets_the_streak(tmp_path):
    _, st, box = _run(tmp_path, [_r("ok"), _r("ok"), _r("out", "credits"), _r("ok"), _r("out", "credits")])
    assert _avail(tmp_path)["status"] == "ok" and box.mails == []


def test_2_unknown_readings_neither_count_nor_reset(tmp_path):
    _, st, box = _run(tmp_path, [_r("ok"), _r("ok"), _r("out", "credits"), _r("unknown"),
                                 _r("out", "credits")])
    assert _avail(tmp_path)["status"] == "out"


def test_2_flip_mails_each_review_seat_one_line_and_gm_once_per_episode(tmp_path):
    _, st, box = _run(tmp_path, [_r("ok"), _r("ok"), _r("out", "credits"), _r("out", "credits"),
                                 _r("out", "credits"), _r("out", "credits")])
    to = [m[0] for m in box.mails]
    assert sorted(to) == sorted(CW.DEFAULT_RECIPIENTS + ["gm"])           # once each, never repeated
    seat_mail = [m for m in box.mails if m[0] == "orchestra-builder"][0]
    assert "\n" not in seat_mail[3].strip() and "Claude" in seat_mail[3]
    n = len(box.mails)
    _, st, box = _run(tmp_path, [_r("ok"), _r("ok")], st, box, now=NOW + 3600)
    a = _avail(tmp_path)
    assert a["status"] == "ok" and a["gating_reviewer"] == "codex"
    back = box.mails[n:]
    assert sorted(m[0] for m in back) == sorted(CW.DEFAULT_RECIPIENTS + ["gm"])
    assert all("codex" in m[3].lower() for m in back)


def test_2_baseline_out_mails_because_the_fleet_assumes_codex(tmp_path):
    _, st, box = _run(tmp_path, [_r("out", "credits"), _r("out", "credits")])
    assert _avail(tmp_path)["status"] == "out" and len(box.mails) == len(CW.DEFAULT_RECIPIENTS) + 1


def test_2_recipients_come_from_the_config_when_set(tmp_path):
    st = {}
    box = Box()
    for i in range(2):
        CW.decide(st, [_r("out", "credits")], NOW + i, box.mail,
                  availability_path=str(tmp_path / "a.json"), log=lambda r: None,
                  recipients=["only-seat"])
    assert sorted(m[0] for m in box.mails) == ["gm", "only-seat"]


def test_2_the_reset_time_is_in_the_mail_when_known(tmp_path):
    st, box = {}, Box()
    for i in range(2):
        CW.decide(st, [CW.Reading("out", "5-hour", RESET, "usage", "used_pct 100")], NOW + i, box.mail,
                  availability_path=str(tmp_path / "a.json"), log=lambda r: None)
    assert all("Tulum" in m[3] and "5-hour" in m[3] for m in box.mails)


# --- 3. the probe ------------------------------------------------------------------------------

def test_3_probe_env_carries_no_seat_identity(monkeypatch):
    for k, v in {"TMUX": "/tmp/t", "TMUX_PANE": "%1", "AGENT_ID": "orchestra-builder",
                 "ORCHESTRA_AGENT_ID": "x", "CLAUDE_CODE_SESSION": "s", "CODEX_HOME": "/c"}.items():
        monkeypatch.setenv(k, v)
    env = CW.probe_env()
    assert "TMUX" not in env and "TMUX_PANE" not in env and "AGENT_ID" not in env
    assert "ORCHESTRA_AGENT_ID" not in env and "CLAUDE_CODE_SESSION" not in env
    assert env["CODEX_HOME"] == "/c" and env.get("HOME") and env.get("PATH")


def test_3_probe_argv_is_ephemeral_read_only_hookless_and_outside_git():
    argv = CW.probe_argv("codex")
    assert argv[:2] == ["codex", "exec"]
    for flag in ("--ephemeral", "--skip-git-repo-check"):
        assert flag in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert "features.hooks=false" in argv


@pytest.mark.parametrize("status,since_probe,fresh_usage,want", [
    ("out", 120, True, True),       # OUT: every run, so the way back is seen within two runs
    (None, 120, False, True),       # no status yet: probe
    ("ok", 120, True, False),       # OK with a fresh usage reading: no tokens spent
    ("ok", 120, False, False),      # OK, recent probe
    ("ok", CW.OK_PROBE_S, False, True),
    ("ok", CW.OK_PROBE_S, True, True),   # the credits cross-check still runs on the slow cadence
])
def test_3_probe_cadence(status, since_probe, fresh_usage, want):
    st = {"last_probe": NOW - since_probe}
    if status:
        st["status"] = status
    assert CW.should_probe(st, NOW, fresh_usage) is want


# --- the run, as limit_watch calls it ----------------------------------------------------------

class _Paths:
    def __init__(self, root):
        self.orch = str(root)
        self.state_dir = str(root / "state" / "limit_watch")
        self.usage_dir = str(root / "state" / "usage")
        self.failover_cfg = str(root / "state" / "limit_failover.json")
        os.makedirs(self.usage_dir, exist_ok=True)


def _usage(paths, as_of, five=15.0, week=70.0):
    with open(os.path.join(paths.usage_dir, "api-codex-default.json"), "w") as f:
        json.dump({"as_of": as_of, "windows": {"5h": {"used_pct": five, "resets_at": RESET},
                                               "weekly": {"used_pct": week, "resets_at": NOW + 86400}}}, f)


def test_tick_counts_one_usage_reading_once(tmp_path):
    paths, box = _Paths(tmp_path), Box()
    os.makedirs(paths.state_dir, exist_ok=True)
    st_path = os.path.join(paths.state_dir, "codex_watch.json")
    json.dump({"status": "ok", "last_probe": NOW}, open(st_path, "w"))       # OK and just probed: no probe
    no_probe = lambda now: (_ for _ in ()).throw(AssertionError("not due"))
    _usage(paths, NOW - 10, five=100.0)
    CW.tick(paths, [], NOW + 60, box.mail, lambda r: None, probe=no_probe)
    CW.tick(paths, [], NOW + 180, box.mail, lambda r: None, probe=no_probe)  # same file: not re-counted
    st = json.load(open(st_path))
    assert st["status"] == "ok" and st["streak"] == {"status": "out", "count": 1, "last": "5-hour used_pct 100.0"}
    _usage(paths, NOW + 200, five=100.0)
    CW.tick(paths, [], NOW + 300, box.mail, lambda r: None, probe=no_probe)  # a new reading: second in a row
    a = json.load(open(tmp_path / "state" / "codex-availability.json"))
    assert a["status"] == "out" and a["window"] == "5-hour" and a["gating_reviewer"] == "claude"


def test_a_live_probe_outranks_a_usage_file_in_the_same_run(tmp_path):
    st, box = {"status": "ok"}, Box()
    for i in range(3):
        CW.decide(st, [CW.Reading("out", "5-hour", RESET, "usage", "100"),
                       CW.Reading("ok", None, None, "probe", "reply: ok")], NOW + i, box.mail,
                  availability_path=str(tmp_path / "a.json"), log=lambda r: None)
    assert st["status"] == "ok" and box.mails == []


def test_3_probe_while_out_runs_every_run_so_recovery_is_two_runs(tmp_path):
    paths, box, calls = _Paths(tmp_path), Box(), []
    os.makedirs(paths.state_dir, exist_ok=True)
    json.dump({"status": "out", "window": "credits", "last_probe": NOW}, open(
        os.path.join(paths.state_dir, "codex_watch.json"), "w"))
    ok = lambda now: calls.append(now) or CW.Reading("ok", None, None, "probe", "reply: ok")
    CW.tick(paths, [], NOW + 120, box.mail, lambda r: None, probe=ok)
    CW.tick(paths, [], NOW + 240, box.mail, lambda r: None, probe=ok)
    assert len(calls) == 2
    assert json.load(open(tmp_path / "state" / "codex-availability.json"))["status"] == "ok"


def test_tick_rollout_event_drives_out_with_no_probe_budget_spent_while_ok(tmp_path):
    paths, box = _Paths(tmp_path), Box()
    st_path = os.path.join(paths.state_dir, "codex_watch.json")
    os.makedirs(paths.state_dir, exist_ok=True)
    json.dump({"status": "ok", "last_probe": NOW - 60}, open(st_path, "w"))
    ev = LE.LimitEvent("codex", "credits", None, source="rollout", text="workspace_owner_credits_depleted")
    boom = lambda now: (_ for _ in ()).throw(AssertionError("no probe while OK and recently probed"))
    CW.tick(paths, [ev], NOW, box.mail, lambda r: None, probe=boom)
    CW.tick(paths, [ev], NOW + 120, box.mail, lambda r: None, probe=lambda now: CW.Reading(
        "out", "credits", None, "probe", "x"))
    a = json.load(open(tmp_path / "state" / "codex-availability.json"))
    assert a["status"] == "out" and a["window"] == "credits"
