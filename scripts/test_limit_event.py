"""limit_event: one parser for every usage-limit signal (transcript row, rollout record, banner).

Every string and record below was copied from a real transcript, rollout or pane on 2026-11-22..25.
"""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import limit_event as LE  # noqa: E402

@pytest.fixture(autouse=True)
def _operator_zone(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ", "America/Cancun")
    monkeypatch.setenv("ORCHESTRA_OPERATOR_TZ_LABEL", "Tulum")


NY = "America/New_York"


def _epoch(s):
    return int(datetime.fromisoformat(s).timestamp())


# --- Claude transcript API-error rows (structured quotaLimits first) -------------------------

def _claude_row(text, quota, ts="2026-11-25T20:16:31.303Z"):
    return {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
            "apiErrorStatus": 429, "timestamp": ts, "sessionId": "sid-test-0001",
            "message": {"model": "<synthetic>", "role": "assistant",
                        "content": [{"type": "text", "text": text}]},
            "quotaLimits": quota}


SPEND_5H = ("You've hit your org's monthly spend limit · run /usage-credits to ask your admin for a "
            "higher limit · your session limit resets 4:20pm (America/New_York)")
SPEND_WEEKLY = ("You've hit your org's monthly spend limit · run /usage-credits to ask your admin for a "
                "higher limit · your weekly limit resets Nov 24, 5am (America/New_York)")
SPEND_WEEKLY_NODATE = ("You've hit your org's monthly spend limit · run /usage-credits to ask your admin "
                       "for a higher limit · your weekly limit resets 5am (America/New_York)")
PLAIN_5H = "You've hit your session limit · resets 7pm (America/New_York)"


def test_claude_row_five_hour_with_spend_cap_uses_quota_limits():
    row = _claude_row(SPEND_5H, {"status": "rejected", "resetsAt": 1795641600,
                                 "unifiedRateLimitFallbackAvailable": False,
                                 "rateLimitType": "five_hour", "overageStatus": "rejected",
                                 "overageDisabledReason": "org_level_disabled_until",
                                 "isUsingOverage": False})
    ev = LE.from_claude_row(row)
    assert ev.runtime == "claude"
    assert ev.window == "5-hour"
    assert ev.spend_capped is True
    assert ev.reset_at == 1795641600          # 2026-11-25 21:20Z = 16:20 Tulum
    assert ev.source == "transcript"
    assert ev.session_id == "sid-test-0001"


def test_claude_row_weekly_from_quota_limits():
    row = _claude_row(SPEND_WEEKLY, {"status": "rejected", "resetsAt": 1795514400,
                                     "rateLimitType": "seven_day", "overageStatus": "rejected",
                                     "overageDisabledReason": "org_level_disabled_until"},
                      ts="2026-11-23T05:13:28.072Z")
    ev = LE.from_claude_row(row)
    assert ev.window == "weekly"
    assert ev.reset_at == 1795514400
    assert ev.spend_capped is True


def test_claude_row_without_quota_limits_falls_back_to_the_banner_text():
    row = _claude_row(SPEND_5H, None)
    ev = LE.from_claude_row(row)
    assert ev.window == "5-hour" and ev.spend_capped is True
    # "4:20pm" New York on the row's day = 21:20Z
    assert ev.reset_at == _epoch("2026-11-25T21:20:00+00:00")


def test_non_error_row_and_non_limit_error_are_not_events():
    assert LE.from_claude_row({"type": "assistant", "message": {"content": [
        {"type": "text", "text": PLAIN_5H}]}}) is None          # prose quoting a banner
    row = _claude_row("API Error: 529 overloaded", None)
    row["error"] = "server_error"
    assert LE.from_claude_row(row) is None


# --- banner text (pane fallback) --------------------------------------------------------------

NOW_PM = datetime(2026, 11, 25, 20, 16, 31, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,runtime,window,spend,reset", [
    (SPEND_5H, "claude", "5-hour", True, "2026-11-25T21:20:00+00:00"),
    ("  ⎿  " + SPEND_5H, "claude", "5-hour", True, "2026-11-25T21:20:00+00:00"),
    (SPEND_WEEKLY_NODATE, "claude", "weekly", True, "2026-11-26T10:00:00+00:00"),
    (PLAIN_5H, "claude", "5-hour", False, "2026-11-26T00:00:00+00:00"),
    ("You've hit your weekly limit · resets Nov 27, 5am (America/New_York)", "claude", "weekly", False,
     "2026-11-27T10:00:00+00:00"),
    ("3. Your organization's usage credit cap is reached for this period. Contact your admin to raise it.",
     "claude", "monthly-spend", True, None),
    ("■ Your workspace is out of credits. Ask your workspace owner to refill in order to continue.",
     "codex", "credits", False, None),
    ("■ Usage limit reached. You've reached your usage limit. Increase your limits to continue using codex.",
     "codex", "unknown", False, None),
    ("You hit your weekly limit.", "grok", "weekly", False, None),
])
def test_banner_lines(text, runtime, window, spend, reset):
    ev = LE.from_text(text, now=NOW_PM)
    assert ev is not None, text
    assert (ev.runtime, ev.window, ev.spend_capped) == (runtime, window, spend)
    assert ev.reset_at == (_epoch(reset) if reset else None)
    assert ev.source == "pane"


def test_auto_continue_two_line_form_seen_2026_11_25():
    screen = ("  ⚠ Usage limit reached · limit resets 9:30pm\n"
              "    Continuing automatically at 9:30pm · esc to cancel\n")
    ev = LE.from_text(screen, now=NOW_PM, host_tz="America/Cancun")
    assert ev.runtime == "claude" and ev.window == "unknown"
    assert ev.reset_at == _epoch("2026-11-26T02:30:00+00:00")
    assert LE.from_text("  ⚠ Usage limit reached · limit resets 9:30pm", now=NOW_PM,
                        host_tz="America/Cancun").reset_at == _epoch("2026-11-26T02:30:00+00:00")


def test_headless_result_pipe_form_carries_the_reset_epoch():
    ev = LE.from_text("Claude AI usage limit reached|1762300800")
    assert (ev.runtime, ev.window, ev.reset_at) == ("claude", "unknown", 1762300800)


def test_auto_continue_line_names_no_window_but_keeps_the_reset():
    # the CLI prints this in the HOST's zone; the VPS runs America/Cancun (UTC-5)
    ev = LE.from_text("● Usage limit reached · continuing automatically at 4:20pm · esc to cancel",
                      now=NOW_PM, host_tz="America/Cancun")
    assert ev.runtime == "claude" and ev.window == "unknown"
    assert ev.reset_at == _epoch("2026-11-25T21:20:00+00:00")


@pytest.mark.parametrize("text", [
    "You've used 99% of your weekly limit · resets Sep 20, 7am (America/New_York)",
    "⚠ Heads up, you have less than 25% of your weekly limit left. Run /status for a breakdown.",
    "• You have 1 usage limit reset available. Run /usage to use one.",
    "● Usage limit reset · continuing automatically",
    "I think we stopped because of the usage limit reached earlier today.",          # prose
    "● The banner said: You've hit your session limit · resets 7pm (America/New_York)",  # quoted
    "",
])
def test_progress_readouts_prose_and_recovery_are_not_events(text):
    assert LE.from_text(text, now=NOW_PM) is None


def test_last_banner_on_the_screen_wins():
    screen = "\n".join([
        "■ Your workspace is out of credits. Add credits to continue.",
        "› retry",
        "■ Usage limit reached. You've reached your usage limit. Increase your limits to continue using codex.",
    ])
    assert LE.from_text(screen, now=NOW_PM).window == "unknown"


def test_reset_rolls_to_tomorrow_when_the_clock_time_already_passed():
    ev = LE.from_text("You've hit your session limit · resets 6:20am (America/New_York)",
                      now=datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc))
    assert ev.reset_at == _epoch("2026-11-26T11:20:00+00:00")


def test_dated_reset_in_january_seen_in_december_lands_next_year():
    ev = LE.from_text("You've hit your weekly limit · resets Jan 2, 5am (America/New_York)",
                      now=datetime(2026, 12, 30, 12, 0, tzinfo=timezone.utc))
    assert ev.reset_at == _epoch("2027-01-02T10:00:00+00:00")


# --- Codex rollout records --------------------------------------------------------------------

def test_codex_credits_depleted_token_count():
    payload = {"type": "token_count", "rate_limits": {
        "limit_id": "premium", "primary": None, "secondary": None,
        "credits": {"has_credits": False, "unlimited": False, "balance": None},
        "plan_type": "team", "rate_limit_reached_type": "workspace_owner_credits_depleted"}}
    ev = LE.from_codex_payload(payload)
    assert (ev.runtime, ev.window, ev.reset_at, ev.source) == ("codex", "credits", None, "rollout")


def test_codex_task_complete_error_is_credits():
    payload = {"type": "task_complete", "error": {
        "message": "Your workspace is out of credits. Add credits to continue.",
        "codex_error_info": "usage_limit_exceeded"}}
    assert LE.from_codex_payload(payload).window == "credits"


def test_codex_window_hit_is_named_from_window_minutes():
    payload = {"type": "token_count", "rate_limits": {
        "limit_id": "codex",
        "primary": {"used_percent": 100.0, "window_minutes": 300, "resets_at": 1795657035},
        "secondary": {"used_percent": 36.0, "window_minutes": 10080, "resets_at": 1796065761},
        "credits": {"has_credits": False, "unlimited": False}, "rate_limit_reached_type": None}}
    ev = LE.from_codex_payload(payload)
    assert (ev.window, ev.reset_at) == ("5-hour", 1795657035)
    payload["rate_limits"]["secondary"]["used_percent"] = 100.0
    payload["rate_limits"]["primary"]["used_percent"] = 10.0
    ev = LE.from_codex_payload(payload)
    assert (ev.window, ev.reset_at) == ("weekly", 1796065761)


def test_healthy_codex_record_is_not_an_event_but_is_a_recovery():
    payload = {"type": "token_count", "rate_limits": {
        "limit_id": "codex",
        "primary": {"used_percent": 0.0, "window_minutes": 300, "resets_at": 1795657035},
        "secondary": {"used_percent": 36.0, "window_minutes": 10080, "resets_at": 1796065761},
        "credits": {"has_credits": False, "unlimited": False}, "rate_limit_reached_type": None}}
    assert LE.from_codex_payload(payload) is None
    assert LE.codex_payload_is_healthy(payload) is True
    assert LE.codex_payload_is_healthy({"type": "agent_message"}) is False


# --- the report line --------------------------------------------------------------------------

def test_describe_names_window_reset_in_tulum_and_account():
    ev = LE.LimitEvent(runtime="claude", window="5-hour", reset_at=1795641600, spend_capped=True,
                       source="transcript", text=SPEND_5H, account="strategysimple")
    s = LE.describe(ev)
    assert "5-hour limit" in s
    assert "resets Wed 11-25 16:20 Tulum" in s
    assert "account strategysimple" in s
    assert "monthly spend cap" in s


def test_describe_without_reset_says_so():
    ev = LE.LimitEvent(runtime="codex", window="credits", reset_at=None, source="rollout",
                       text="", account="codex workspace")
    assert "no reset time given" in LE.describe(ev)


def test_operator_zone_follows_the_location_file_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCHESTRA_OPERATOR_TZ")
    loc = tmp_path / "loc.json"
    loc.write_text('{"tz": "America/New_York", "label": "ET"}')
    monkeypatch.setenv("ORCHESTRA_OPERATOR_LOCATION_FILE", str(loc))
    assert LE.operator_zone() == ("America/New_York", "ET")
    assert LE.reset_label(1795641600) == "resets Wed 11-25 16:20 ET"
    loc.write_text('{"tz": "America/Cancun", "label": "Tulum"}')      # a move: no restart needed
    assert LE.reset_label(1795641600) == "resets Wed 11-25 16:20 Tulum"
    loc.write_text("not json")
    assert LE.operator_zone()[0]                                       # host zone, never a crash


def test_m5_credit_words_mid_line_in_tool_output_are_not_a_limit():
    assert LE.from_text("  ⎿  scripts/x.py:12:    if code == 'insufficient_quota':") is None
    assert LE.from_text("  ⎿  grep: credits depleted handling in billing.py") is None
    ev = LE.from_text("API Error: 429 {\"error\":{\"type\":\"insufficient_quota\"}}")
    assert ev is not None and ev.window == "credits"
