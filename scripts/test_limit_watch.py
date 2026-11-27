"""limit_watch end to end on a scratch tree: transcripts + rollouts in, log / seat rows / sidecar / mail out."""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_watch as LW  # noqa: E402

@pytest.fixture(autouse=True)
def _operator_zone(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ", "America/Cancun")
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ_LABEL", "Local")


SID_C = "11111111-2222-3333-4444-555555555555"
SID_X = "00000000-0000-7000-a000-00000000c0de"

NOW = 1795629600            # 2026-11-25 18:00Z, the fixtures' clock
WEEKLY_Q = {"status": "rejected", "resetsAt": NOW + 3 * 86400, "rateLimitType": "seven_day",
            "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled_until"}
FIVE_Q = {"status": "rejected", "resetsAt": 1795641600, "rateLimitType": "five_hour",
          "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled_until"}


def _err_row(ts, quota, text="You've hit your org's monthly spend limit"):
    return {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit", "timestamp": ts,
            "sessionId": SID_C, "message": {"content": [{"type": "text", "text": text}]},
            "quotaLimits": quota}


def _prompt_row(ts, text="[MSG from gm | high] continue"):
    return {"type": "user", "timestamp": ts, "sessionId": SID_C, "message": {"role": "user", "content": text}}


def _ok_row(ts):
    return {"type": "assistant", "timestamp": ts, "sessionId": SID_C,
            "message": {"content": [{"type": "text", "text": "done"}]}}


@pytest.fixture
def tree(tmp_path):
    orch, claude, codex = tmp_path / "orch", tmp_path / "claude", tmp_path / "codex"
    (orch / "state" / "usage").mkdir(parents=True)
    (orch / "logs").mkdir()
    (claude / "projects" / "-p").mkdir(parents=True)
    (codex / "sessions" / "2026" / "11" / "25").mkdir(parents=True)
    db = sqlite3.connect(orch / "state" / "orchestra-registry.db")
    db.execute("CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, session_id TEXT)")
    db.execute("INSERT INTO generations (root, session_id) VALUES ('pm-nod', ?)", (SID_C,))
    db.execute("INSERT INTO generations (root, session_id) VALUES ('codex-gm-g13', ?)", (SID_X,))
    db.commit()
    db.close()
    (orch / "logs" / "account-switch.jsonl").write_text(json.dumps(
        {"ts": "2026-11-25T17:46:32+00:00", "seat": "*shared*", "to": "strategysimple",
         "status": "switched", "from": "default"}) + "\n")
    (orch / "state" / "claude_profiles.json").write_text(json.dumps({"active": "default", "profiles": [
        {"id": "default", "enrolled": True}, {"id": "strategysimple", "enrolled": True}]}))
    (orch / "state" / "usage" / "api-claude-default.json").write_text(json.dumps(
        {"windows": {"5h": {"used_pct": 20.0}, "weekly": {"used_pct": 43.0}}}))
    (orch / "state" / "initiatives.json").write_text(json.dumps({"items": [
        {"id": "nod-review", "owner": "pm-nod", "owed": "review X", "due": "2026-11-26T12:00",
         "status": "open", "last_check": "", "note": ""},
        {"id": "other", "owner": "gm", "owed": "y", "due": "", "status": "open"}]}))
    paths = LW.Paths(orch=str(orch), claude_dir=str(claude), codex_home=str(codex))
    paths.transcript = claude / "projects" / "-p" / f"{SID_C}.jsonl"
    paths.rollout = codex / "sessions" / "2026" / "11" / "25" / f"rollout-2026-11-25T01-25-59-{SID_X}.jsonl"
    return paths


def _add(path, *rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _log(paths):
    return [json.loads(l) for l in open(paths.log)] if os.path.exists(paths.log) else []


class Mail:
    def __init__(self):
        self.sent = []

    def __call__(self, to, prio, subject, body):
        self.sent.append((to, prio, subject, body))


def _run(paths, mail=None, inbox=None, now=None, backfill=True):
    return LW.run_once(paths, now=now or NOW, mail_fn=mail, start_at_end=not backfill,
                       inbox_fn=inbox or (lambda agent: []))


def test_weekly_hit_names_window_reset_account_mails_gm_and_logs_the_switch_decision(tree):
    _add(tree.transcript, _err_row("2026-11-25T18:00:00Z", WEEKLY_Q))
    mail = Mail()
    res = _run(tree, mail)
    assert res["limited_seats"] == ["pm-nod"]
    kinds = [r["kind"] for r in _log(tree)]
    assert kinds[:3] == ["episode_open", "switch_decision", "seat_limited"]
    opened = _log(tree)[0]
    assert opened["window"] == "weekly" and opened["account"] == "strategysimple"
    assert "account strategysimple" in opened["line"] and "Local" in opened["line"]
    dec = _log(tree)[1]
    assert dec["would_switch"] is True and dec["to"] == "default" and dec["armed"] is False
    assert [(m[0], m[1]) for m in mail.sent] == [("gm", "high")]
    row = json.load(open(tree.seat_rows))["seats"]["pm-nod"]
    assert row["window"] == "weekly" and row["account"] == "strategysimple"


def test_five_hour_spend_capped_mails_once_per_account_per_month(tree):
    mail = Mail()
    _add(tree.transcript, _err_row("2026-11-25T20:16:31Z", FIVE_Q))
    _run(tree, mail)
    q2 = dict(FIVE_Q, resetsAt=1795659600)
    _add(tree.transcript, _err_row("2026-11-26T01:00:00Z", q2))
    _run(tree, mail, now=1795641600 - 60)
    assert len([m for m in mail.sent if m[0] == "gm"]) == 1
    assert [r["kind"] for r in _log(tree)].count("episode_open") == 2


def test_plain_five_hour_is_logged_and_on_the_seat_row_but_not_mailed(tree):
    mail = Mail()
    q = dict(FIVE_Q, overageStatus="allowed", overageDisabledReason=None)
    _add(tree.transcript, _err_row("2026-11-25T20:16:31Z", q, text="You've hit your session limit"))
    _run(tree, mail)
    assert mail.sent == []
    assert "pm-nod" in json.load(open(tree.seat_rows))["seats"]


def test_recovery_by_effect_clears_the_row_and_closes_the_episode(tree):
    _add(tree.transcript, _err_row("2026-11-25T18:00:00Z", WEEKLY_Q))
    _run(tree)
    _add(tree.transcript, _prompt_row("2026-11-25T18:09:00Z"), _ok_row("2026-11-25T18:10:00Z"))
    _run(tree)
    assert json.load(open(tree.seat_rows))["seats"] == {}
    kinds = [r["kind"] for r in _log(tree)]
    assert "seat_recovered" in kinds and kinds[-1] == "episode_closed"


def test_recovery_when_the_reset_time_passes(tree):
    _add(tree.transcript, _err_row("2026-11-25T20:16:31Z", FIVE_Q))
    _run(tree, now=1795641600 - 600)
    _run(tree, now=1795641600 + 600)
    assert json.load(open(tree.seat_rows))["seats"] == {}
    assert any(r.get("how") == "reset time passed" for r in _log(tree))


def test_first_sight_starts_at_end_so_old_rows_do_not_flood(tree):
    _add(tree.transcript, _err_row("2026-11-20T18:00:00Z", WEEKLY_Q))
    _run(tree, backfill=False)
    assert _log(tree) == []
    _add(tree.transcript, _err_row("2026-11-25T18:00:00Z", WEEKLY_Q))
    _run(tree, backfill=False)
    assert [r["kind"] for r in _log(tree)][0] == "episode_open"


def test_codex_credits_tells_order_owners_and_reroutes_when_configured_then_says_back(tree):
    with open(tree.failover_cfg, "w") as f:
        json.dump({"codex_fallback": {"codex-gm": "orchestra-builder"}, "reroute": True}, f)
    _add(tree.rollout, {"timestamp": "2026-11-25T17:45:19Z", "payload": {
        "type": "token_count", "rate_limits": {"limit_id": "premium", "credits": {"has_credits": False},
                                               "rate_limit_reached_type": "workspace_owner_credits_depleted"}}})
    orders = [{"id": "msg_1", "type": "task", "from_agent": "pm-nod", "subject": "Review PR #9", "body": "pls"},
              {"id": "msg_2", "type": "status", "from_agent": "gm", "subject": "fyi"}]
    mail = Mail()
    _run(tree, mail, inbox=lambda agent: orders if agent == "codex-gm" else [])
    to = [(m[0], m[1]) for m in mail.sent]
    assert ("orchestra-builder", "high") in to          # the order, re-routed with context
    assert ("pm-nod", "high") in to                     # its owner told: window + reset named
    rerouted = [m for m in mail.sent if m[0] == "orchestra-builder"][0]
    assert "HANDOFF_codex-gm-next.md" in rerouted[3] and "pls" in rerouted[3]
    owner_note = [m for m in mail.sent if m[0] == "pm-nod"][0]
    assert "out of credits" in owner_note[3] and "no reset time given" in owner_note[2]
    assert not any(m[0] == "gm" for m in mail.sent)     # the status mail is not an order
    _add(tree.rollout, {"timestamp": "2026-11-25T20:38:30Z", "payload": {
        "type": "token_count", "rate_limits": {"limit_id": "codex",
                                               "primary": {"used_percent": 1.0, "window_minutes": 300,
                                                           "resets_at": 1795657035},
                                               "credits": {"has_credits": False}}}})
    mail2 = Mail()
    _run(tree, mail2)
    assert [(m[0], m[2].split(":")[0]) for m in mail2.sent] == [("pm-nod", "back")]


def test_sidecar_has_initiatives_shape_and_marks_the_limited_owners_items(tree):
    _add(tree.transcript, _err_row("2026-11-25T18:00:00Z", WEEKLY_Q))
    _run(tree)
    side = json.load(open(tree.sidecar))
    keys = {"id", "owner", "owed", "due", "status", "last_check", "note"}
    assert all(set(i) == keys for i in side["items"])
    ids = {i["id"] for i in side["items"]}
    assert {"limit-pm-nod-weekly", "nod-review"} <= ids and "other" not in ids
    nod = [i for i in side["items"] if i["id"] == "nod-review"][0]
    assert nod["note"].startswith("blocked: weekly limit, resets ") and nod["note"].endswith(" Local") and nod["status"] == "open"
    assert json.load(open(tree.initiatives))["items"][0]["note"] == ""   # main file untouched




# --- review of #636 (Claude reviewer, codex out of credits): H2, M2, M4, M5 ----------------------

def test_h2_the_active_login_comes_from_the_ledger_not_the_switch_log(tree):
    # the switch log's last row says strategysimple; the ledger says default holds the shared dir now
    (tmp := os.path.join(tree.orch, "state", "account_locations.json"))
    with open(tmp, "w") as f:
        json.dump({"accounts": {"default": {"where": "shared", "at": "2026-11-25T22:58:59+00:00"},
                                "strategysimple": {"where": "vault", "at": "2026-11-25T22:58:59+00:00"}}}, f)
    assert LW.shared_holder(tree) == "default"
    seen = {}

    class D:
        def usage(self, a):
            seen.setdefault("active", a)
            return None

        def enrolled(self):
            return []
    LW.run_once(tree, now=NOW, start_at_end=False, switch_deps=D())
    assert seen["active"] == "default"


def test_h2_no_ledger_falls_back_to_the_switch_log(tree):
    assert LW.shared_holder(tree) is None
    assert LW.claude_account_at(tree, None) == "strategysimple"


def test_m2_a_switch_error_keeps_the_watchers_own_state(tree):
    _add(tree.transcript, _err_row("2026-11-25T17:50:00.000Z", WEEKLY_Q))

    class D:
        def usage(self, a):
            raise RuntimeError("boom")
    LW.run_once(tree, now=NOW, start_at_end=False, switch_deps=D())
    assert os.path.exists(tree.cursors) and os.path.exists(tree.episodes)
    assert any(r["kind"] == "switch_error" and "boom" in r["error"] for r in _log(tree))


def test_m4_a_line_longer_than_a_chunk_is_skipped_not_frozen(tree, monkeypatch):
    monkeypatch.setattr(LW, "MAX_READ", 64)
    p = str(tree.transcript)
    with open(p, "w") as f:
        f.write("x" * 500 + "\n" + "after\n")
    cur = {p: 0}
    assert LW.new_lines(p, cur, start_at_end=False) == []
    assert cur[p] == 501
    assert LW.new_lines(p, cur, start_at_end=False) == ["after"]


def test_m4_a_short_unfinished_line_waits(tree):
    p = str(tree.transcript)
    with open(p, "w") as f:
        f.write("partial")
    cur = {p: 0}
    assert LW.new_lines(p, cur, start_at_end=False) == [] and cur[p] == 0


def test_rr2_shared_holder_takes_the_first_shared_row_like_account_switch(tree):
    with open(os.path.join(tree.orch, "state", "account_locations.json"), "w") as f:
        json.dump({"accounts": {"strategysimple": {"where": "shared", "at": "2026-11-25T10:00:00+00:00"},
                                "default": {"where": "shared", "at": "2026-11-25T22:00:00+00:00"}}}, f)
    assert LW.shared_holder(tree) == "strategysimple"


# --- 11-26 replay: gm sat on the Fable banner 09:55Z-19:16Z while default's 5-hour read 1% --------------

def test_replay_11_26_the_fable_row_drops_the_seat_to_opus_and_keeps_the_login(tree, monkeypatch):
    """The day itself (gm order msg_f495a92c): the run logged "no switch: default 5-hour 1% < 95%" and
    the Fable seats sat dead for nine hours. The operator's rule (apr_bafb66c3): the seat drops to Opus 5.5."""
    t = 1795658143                                                   # 2026-11-26T09:55:43Z
    monkeypatch.setenv("LIMIT_SWITCH_PREFER_WEEKLY", "0")            # the banner leg alone must switch
    _add(os.path.join(tree.orch, "logs", "account-switch.jsonl"),     # the real day: default since 22:58Z 11-25
         {"ts": "2026-11-25T22:58:59+00:00", "seat": "*shared*", "to": "default", "status": "switched",
          "from": "strategysimple"})
    with open(os.path.join(tree.orch, "state", "account_locations.json"), "w") as f:
        json.dump({"accounts": {"default": {"where": "shared"}, "strategysimple": {"where": "vault"}}}, f)
    for acct, five, week in (("default", 1.0, 73.0), ("strategysimple", 5.0, 61.0)):
        (lambda p: open(p, "w").write(json.dumps({"as_of": t - 60, "windows": {
            "5h": {"used_pct": five, "resets_at": t + 3600}, "weekly": {"used_pct": week, "resets_at": t + 5 * 86400}}})))(
            os.path.join(tree.usage_dir, f"api-claude-{acct}.json"))
    _add(tree.transcript, {"type": "assistant", "isApiErrorMessage": True, "apiError": "model_requires_usage_credits",
                           "error": "rate_limit", "quotaLimits": None, "timestamp": "2026-11-26T09:55:43Z",
                           "sessionId": SID_C, "message": {"model": "<synthetic>", "content": [{"type": "text",
                           "text": "You've reached your Fable limit. Run /usage-credits to continue or switch models with /model."}]}})

    class D:
        switched = []

        def usage(self, a):
            return json.load(open(os.path.join(tree.usage_dir, f"api-claude-{a}.json")))

        def enrolled(self):
            return ["default", "strategysimple"]

        def claude_seats(self):
            return [("pm-nod", "pm-nod")]

        def attached_sessions(self):
            return set()

        def human_typing(self, s, attached=None):
            return None

        def pane_text(self, s):
            return "  ⎿  You've reached your Fable limit. Run /usage-credits to continue or switch models with /model."

        def switch(self, a):
            D.switched.append(a)
            return {"status": "switched", "followers": 1}

        def nudge(self, s, text):
            return 0

        def mail(self, *a):
            pass

    class Drop:
        switched, nudged, mails = [], [], []

        def sessions(self):
            return {"pm-nod": "pm-nod"}

        def seat_model(self, session):
            return "claude-fable-5-1[1m]"

        def usage(self, account):
            return None

        def claude_seats(self):
            return [("pm-nod", "pm-nod")]

        def switch_model(self, session, model):
            Drop.switched.append((session, model))
            return 200, {"ok": True, "switching": "switched"}

        def attached_sessions(self):
            return set()

        def pane_text(self, s):
            return "  ⎿  You've reached your Fable limit. Run /usage-credits to continue or switch models with /model."

        def nudge(self, s, text):
            Drop.nudged.append(s)
            return 0

        def mail(self, *a):
            Drop.mails.append(a)

    LW.run_once(tree, now=t + 30, start_at_end=False, switch_deps=D(), drop_deps=Drop())
    log = _log(tree)
    assert any(r["kind"] == "episode_open" and r["window"] == "credits" for r in log)
    assert D.switched == []                                          # the login stays (operator, apr_bafb66c3)
    assert Drop.switched == [("pm-nod", "claude-opus-5-5[1m]")] and Drop.nudged == ["pm-nod"]
    assert any(r["kind"] == "model_dropped" and r["seat"] == "pm-nod" for r in log)
    assert any(m[0] == "gm" and "pm-nod" in m[3] for m in Drop.mails)
    st = json.load(open(os.path.join(tree.state_dir, "model-drop.json")))
    LW.run_once(tree, now=t + 150, start_at_end=False, switch_deps=D(), drop_deps=Drop())
    assert Drop.switched == [("pm-nod", "claude-opus-5-5[1m]")]     # once per episode
    assert st["drops"]


def test_f7_a_second_credits_hit_the_same_day_reopens_the_episode(tree):
    row = lambda ts: {"type": "assistant", "isApiErrorMessage": True, "apiError": "model_requires_usage_credits",
                      "error": "rate_limit", "quotaLimits": None, "timestamp": ts, "sessionId": SID_C,
                      "message": {"content": [{"type": "text", "text": "You've reached your Fable limit."}]}}
    # all after the fixture's 17:46:32Z switch, so every row is on the same login
    _add(tree.transcript, row("2026-11-25T17:47:00Z"))
    _run(tree, now=NOW - 3000)
    _add(tree.transcript, _ok_row("2026-11-25T17:52:00Z"))
    _run(tree, now=NOW - 2400, backfill=False)
    eps = json.load(open(tree.episodes))
    assert not [e for k, e in eps.items() if not k.startswith("_") and e.get("open")]
    _add(tree.transcript, row("2026-11-25T17:58:00Z"))
    _run(tree, now=NOW, backfill=False)
    eps = json.load(open(tree.episodes))
    ep = [e for k, e in eps.items() if not k.startswith("_") and e.get("window") == "credits"][0]
    assert ep["open"] is True and ep["last_seen"] == "2026-11-25T17:58:00Z"


def test_f1_a_reset_less_seat_row_expires_after_12h_without_a_new_row(tree):
    _add(tree.transcript, {"type": "assistant", "isApiErrorMessage": True, "apiError": "model_requires_usage_credits",
                           "error": "rate_limit", "quotaLimits": None, "timestamp": "2026-11-25T17:00:00Z",
                           "sessionId": SID_C, "message": {"content": [{"type": "text", "text": "x"}]}})
    assert _run(tree, now=NOW)["limited_seats"] == ["pm-nod"]
    assert _run(tree, now=NOW + 13 * 3600, backfill=False)["limited_seats"] == []


# --- 11-26 miss: Claude Code 2.1.284 limit notes, grace allowance, row order ---------------------

def _note_row(ts):
    return {"type": "user", "isMeta": True, "usageLimitNote": "wrap_up", "timestamp": ts, "sessionId": SID_C,
            "message": {"role": "user", "content": "[Usage limit reached; a short grace allowance remains, "
                                                   "then this turn is cut off without warning.]"}}


def _info_row(ts, when="4:20pm"):
    return {"type": "system", "subtype": "informational", "level": "notice", "isMeta": False, "timestamp": ts,
            "sessionId": SID_C, "content": f"Usage limit reached · continuing automatically at {when} · esc to cancel"}


@pytest.fixture
def ny(monkeypatch):
    import time
    monkeypatch.setenv("TZ", "America/New_York")        # the CLI and the watcher share the host zone
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _five_hour_full(tree):
    (open(os.path.join(tree.usage_dir, "api-claude-strategysimple.json"), "w")).write(json.dumps(
        {"as_of": NOW - 60, "windows": {"5h": {"used_pct": 100.0, "resets_at": FIVE_Q["resetsAt"]},
                                        "weekly": {"used_pct": 44.0, "resets_at": NOW + 5 * 86400}}}))


def test_g1_a_wrap_up_note_and_the_informational_row_are_a_5_hour_limit(tree, ny):
    _five_hour_full(tree)
    _add(tree.transcript, _note_row("2026-11-25T17:55:00Z"), _info_row("2026-11-25T17:55:15Z"))
    _run(tree)
    seats = json.load(open(tree.seat_rows))["seats"]
    assert seats["pm-nod"]["key"] == f"claude|strategysimple|5-hour|{FIVE_Q['resetsAt']}"
    opened = [r for r in _log(tree) if r["kind"] == "episode_open"]
    assert len(opened) == 1 and opened[0]["window"] == "5-hour"


def test_g2_without_a_usage_reading_the_note_is_an_unknown_window_with_the_rows_reset(tree, ny):
    _add(tree.transcript, _info_row("2026-11-25T17:55:15Z"))
    _run(tree)
    row = json.load(open(tree.seat_rows))["seats"]["pm-nod"]
    assert row["window"] == "unknown" and row["reset_at"] == FIVE_Q["resetsAt"]


def test_g3_grace_allowance_rows_are_not_a_recovery_a_reply_to_a_new_prompt_is(tree, ny):
    _five_hour_full(tree)
    _add(tree.transcript, _note_row("2026-11-25T17:55:00Z"), _ok_row("2026-11-25T17:55:05Z"),
         _info_row("2026-11-25T17:55:15Z"))
    _run(tree)
    _add(tree.transcript, _ok_row("2026-11-25T17:56:00Z"))       # still the grace turn's tail
    _run(tree)
    assert "pm-nod" in json.load(open(tree.seat_rows))["seats"]
    _add(tree.transcript, _prompt_row("2026-11-25T18:06:24Z", "The shared Claude login was switched. Continue."),
         _ok_row("2026-11-25T18:06:30Z"))
    _run(tree)
    assert json.load(open(tree.seat_rows))["seats"] == {}
    assert [r["how"] for r in _log(tree) if r["kind"] == "seat_recovered"] == ["healthy record"]


def test_g4_a_healthy_row_before_the_limit_row_in_the_same_run_is_no_recovery(tree):
    _add(tree.transcript, _ok_row("2026-11-25T17:59:00Z"), _err_row("2026-11-25T18:00:00Z", FIVE_Q))
    _run(tree)
    assert "pm-nod" in json.load(open(tree.seat_rows))["seats"]
    assert not [r for r in _log(tree) if r["kind"] == "seat_recovered"]


def test_g5_other_informational_rows_and_meta_rows_are_no_limit(tree):
    _add(tree.transcript,
         {"type": "system", "subtype": "informational", "timestamp": "2026-11-25T17:55:00Z", "sessionId": SID_C,
          "content": "Auto-update installed · restart to apply"},
         {"type": "user", "isMeta": True, "timestamp": "2026-11-25T17:55:01Z", "sessionId": SID_C,
          "message": {"role": "user", "content": "[Earlier usage-limit notes no longer apply.]"}})
    _run(tree)
    assert json.load(open(tree.seat_rows))["seats"] == {}


def test_g6_replay_11_27_the_three_invisible_seats_exact_rows(tree, ny):
    """gm msg_9dca7384: tonight's exact wrap_up + informational rows (cwd neutralised) for the three seats
    main never saw. Each seat also writes a grace-allowance reply between its note and its banner row."""
    fx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "limit_notes_2026-11-27.jsonl")
    rows = [json.loads(l) for l in open(fx)]
    db = sqlite3.connect(tree.registry_db)
    for seat, sid in {(r["_seat"], r["sessionId"]) for r in rows}:
        db.execute("INSERT INTO generations (root, session_id) VALUES (?, ?)", (seat, sid))
    db.commit()
    db.close()
    reset = 1795752600                                   # 23:10 Tulum, the strategysimple 5-hour reset
    now = 1795741560                                     # 2026-11-27T01:06Z
    (open(os.path.join(tree.usage_dir, "api-claude-strategysimple.json"), "w")).write(json.dumps(
        {"as_of": now - 120, "windows": {"5h": {"used_pct": 100.0, "resets_at": reset},
                                         "weekly": {"used_pct": 44.0, "resets_at": now + 4 * 86400}}}))
    proj = os.path.dirname(str(tree.transcript))
    for r in rows:
        path = os.path.join(proj, r["sessionId"] + ".jsonl")
        _add(path, {k: v for k, v in r.items() if k != "_seat"})
        if r["type"] == "user":                          # the seat keeps working on its grace allowance
            _add(path, {"type": "assistant", "timestamp": r["timestamp"], "sessionId": r["sessionId"],
                        "message": {"content": [{"type": "text", "text": "stopping: usage limit"}]}})
        os.utime(path, (now - 60, now - 60))
    _run(tree, now=now)
    seats = json.load(open(tree.seat_rows))["seats"]
    assert sorted(seats) == ["arturo-latency-dev", "arturo-receipt-dev", "avatar-escape-dev"]
    assert {s["key"] for s in seats.values()} == {f"claude|strategysimple|5-hour|{reset}"}
    assert not [r for r in _log(tree) if r["kind"] == "seat_recovered"]
