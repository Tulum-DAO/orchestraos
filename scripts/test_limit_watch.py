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
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ_LABEL", "Tulum")


SID_C = "11111111-2222-3333-4444-555555555555"
SID_X = "01a21472-cdaf-7801-a7a1-b706a70c66ce"

NOW = 1795629600            # 2026-11-25 18:00Z, the fixtures' clock
WEEKLY_Q = {"status": "rejected", "resetsAt": NOW + 3 * 86400, "rateLimitType": "seven_day",
            "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled_until"}
FIVE_Q = {"status": "rejected", "resetsAt": 1795641600, "rateLimitType": "five_hour",
          "overageStatus": "rejected", "overageDisabledReason": "org_level_disabled_until"}


def _err_row(ts, quota, text="You've hit your org's monthly spend limit"):
    return {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit", "timestamp": ts,
            "sessionId": SID_C, "message": {"content": [{"type": "text", "text": text}]},
            "quotaLimits": quota}


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
    assert "account strategysimple" in opened["line"] and "Tulum" in opened["line"]
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
    _add(tree.transcript, _ok_row("2026-11-25T18:10:00Z"))
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
    assert nod["note"].startswith("blocked: weekly limit, resets ") and nod["note"].endswith(" Tulum") and nod["status"] == "open"
    assert json.load(open(tree.initiatives))["items"][0]["note"] == ""   # main file untouched


def test_auto_switch_flag_refuses_until_armed(capsys):
    assert LW.main(["--auto-switch"]) == 2
    assert "armed only by an approved card" in capsys.readouterr().err
