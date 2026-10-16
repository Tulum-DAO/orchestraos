"""usage_meter: plan usage from local files only, never a guessed number.

The Claude inputs are a real Claude Code 2.1.284 status-line capture (session id and paths removed),
taken on a scratch session: the first redraw has no rate_limits block, a later one does. The Codex
line is a real rollout token_count event with its numbers kept and nothing identifying in it.
"""
import json
import os

import pytest

import scripts.usage_meter as U
import scripts.watch_gateway as G
from scripts.test_gateway_scopes import _FakeRequest, _call, _run, _with_store

NOW = 1792170500

CLAUDE_FIRST_REDRAW = {
    "model": {"id": "claude-haiku-4-5-20251001", "display_name": "Haiku 4.5"}, "version": "2.1.284",
    "context_window": {"used_percentage": None, "remaining_percentage": None},
    "exceeds_200k_tokens": False,
}
CLAUDE_WITH_LIMITS = dict(CLAUDE_FIRST_REDRAW, rate_limits={
    "five_hour": {"used_percentage": 40, "resets_at": 1792172400},
    "seven_day": {"used_percentage": 80, "resets_at": 1792490400},
})
CODEX_EVENT = {
    "timestamp": "2026-10-16T17:03:51.207Z", "type": "event_msg",
    "payload": {"type": "token_count", "info": None, "rate_limits": {
        "limit_id": "codex", "limit_name": None,
        "primary": {"used_percent": 1.0, "window_minutes": 300, "resets_at": 1792185347},
        "secondary": {"used_percent": 18.0, "window_minutes": 10080, "resets_at": 1792651095},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "plan_type": "team", "rate_limit_reached_type": None}},
}
CODEX_AT = 1792170231


def _rollout(dirpath, name, events, mtime=None):
    p = dirpath / name
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


# ---- claude ----------------------------------------------------------------------------------

def test_the_first_redraw_has_no_block_and_records_nothing():
    assert U.claude_record_from_statusline(CLAUDE_FIRST_REDRAW, NOW) is None


def test_claude_block_becomes_two_windows(tmp_path):
    rec = U.claude_record_from_statusline(CLAUDE_WITH_LIMITS, NOW)
    U.write_claude_record(rec, tmp_path, "default")
    rows = U.claude_rows(NOW + 60, tmp_path)
    assert [(r["window"], r["used_pct"], r["resets_at"], r["stale"]) for r in rows] == [
        ("5h", 40.0, 1792172400, False), ("weekly", 80.0, 1792490400, False)]
    assert all(r["account"] == "default" and r["source"] == "claude-statusline" for r in rows)


def test_no_claude_reading_is_a_reason_never_a_number(tmp_path):
    rows = U.claude_rows(NOW, tmp_path)
    assert len(rows) == 1 and rows[0]["used_pct"] is None and rows[0]["reason"]


@pytest.mark.parametrize("bad", [None, "40", True, -1, 101, float("nan")])
def test_a_malformed_percentage_is_dropped_not_coerced(bad):
    d = dict(CLAUDE_FIRST_REDRAW, rate_limits={"five_hour": {"used_percentage": bad, "resets_at": 1}})
    assert U.claude_record_from_statusline(d, NOW) is None


def test_two_logins_are_two_accounts(tmp_path):
    rec = U.claude_record_from_statusline(CLAUDE_WITH_LIMITS, NOW)
    U.write_claude_record(rec, tmp_path, "default")
    U.write_claude_record(rec, tmp_path, "work")
    assert {r["account"] for r in U.claude_rows(NOW, tmp_path)} == {"default", "work"}


def test_account_label_comes_from_the_config_dir(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/home/x/.claude-work/")
    assert U.claude_account() == ".claude-work"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/home/x/bad name")
    assert U.claude_account() == "default"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert U.claude_account() == "default"


def test_an_old_reading_is_stale_not_current(tmp_path):
    rec = U.claude_record_from_statusline(CLAUDE_WITH_LIMITS, NOW)
    U.write_claude_record(rec, tmp_path, "default")
    rows = U.claude_rows(NOW + U.STALE_S + 1, tmp_path)
    assert all(r["stale"] is True and r["as_of"] == NOW for r in rows)


def test_the_statusline_hook_writes_the_record(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "statusline_hook", os.path.join(os.path.dirname(U.__file__), "..", "hooks", "statusline.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setenv("ORCHESTRA_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert hook.write_usage(CLAUDE_FIRST_REDRAW, NOW) is None
    assert not (tmp_path / "state" / "usage").exists()       # nothing written for an absent block
    path = hook.write_usage(CLAUDE_WITH_LIMITS, NOW)
    assert path == str(tmp_path / "state" / "usage" / "claude-default.json")
    assert json.loads(open(path).read())["windows"]["seven_day"]["used_pct"] == 80.0


# ---- codex -----------------------------------------------------------------------------------

def test_codex_rate_limits_become_5h_and_weekly(tmp_path):
    _rollout(tmp_path, "rollout-a.jsonl", [CODEX_EVENT], mtime=NOW)
    rows = U.codex_rows(NOW, tmp_path)
    assert [(r["window"], r["used_pct"], r["resets_at"], r["as_of"]) for r in rows] == [
        ("5h", 1.0, 1792185347, CODEX_AT), ("weekly", 18.0, 1792651095, CODEX_AT)]


def test_codex_freshest_event_wins_across_rollouts(tmp_path):
    older = json.loads(json.dumps(CODEX_EVENT))
    older["timestamp"] = "2026-10-16T12:00:00Z"
    older["payload"]["rate_limits"]["primary"]["used_percent"] = 77.0
    # The newer FILE (by mtime) holds the older event: the event's own time decides.
    _rollout(tmp_path, "rollout-new-file.jsonl", [older], mtime=NOW)
    _rollout(tmp_path, "rollout-old-file.jsonl", [CODEX_EVENT], mtime=NOW - 100)
    assert U.codex_rows(NOW, tmp_path)[0]["used_pct"] == 1.0


def test_codex_without_any_reading_is_a_reason(tmp_path):
    other = {"timestamp": "2026-10-16T17:00:00Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {}}}
    _rollout(tmp_path, "rollout-a.jsonl", [other], mtime=NOW)
    rows = U.codex_rows(NOW, tmp_path)
    assert len(rows) == 1 and rows[0]["used_pct"] is None and rows[0]["reason"]
    assert U.codex_rows(NOW, tmp_path / "missing")[0]["used_pct"] is None


def test_a_rollout_older_than_the_lookback_is_not_read(tmp_path):
    _rollout(tmp_path, "rollout-a.jsonl", [CODEX_EVENT], mtime=NOW - U.CODEX_LOOKBACK_S - 10)
    assert U.codex_rows(NOW, tmp_path)[0]["used_pct"] is None


# ---- all providers ---------------------------------------------------------------------------

def test_gemini_and_grok_say_why_they_have_no_number(tmp_path):
    rows = U.usage_rows(tmp_path, providers=["gemini", "grok"], now=NOW)
    assert [r["provider"] for r in rows] == ["gemini", "grok"]
    assert all(r["used_pct"] is None and r["reason"] for r in rows)


def test_only_enabled_providers_are_listed(tmp_path):
    rows = U.usage_rows(tmp_path, providers=["claude"], now=NOW, codex_sessions_dir=tmp_path)
    assert {r["provider"] for r in rows} == {"claude"}


# ---- gateway ---------------------------------------------------------------------------------

def test_usage_route_needs_the_usage_verb(monkeypatch, tmp_path):
    assert G.ROUTE_SCOPES[("GET", "/usage")] == "usage"
    _, _, read_only = _with_store(monkeypatch, tmp_path, ["read"])
    resp, reached, _ = _call("GET", "/usage", {"Authorization": f"Bearer {read_only}"})
    assert resp.status == 403 and not reached
    _, _, with_usage = _with_store(monkeypatch, tmp_path, ["usage"])
    resp, reached, _ = _call("GET", "/usage", {"Authorization": f"Bearer {with_usage}"})
    assert reached
    resp, reached, _ = _call("GET", "/usage", {"Authorization": "Bearer fleet-token-value"})
    assert reached                                                  # the fleet token holds '*'


def _agents_body(monkeypatch, scopes):
    monkeypatch.setattr(G, "_agents_cache", {"data": [{"id": "gm"}], "at": 1.0, "reg_mtime": None,
                                             "event_mtime": None, "refreshing": False})
    monkeypatch.setattr(G, "_registry_mtime", lambda: None)
    monkeypatch.setattr(G, "_newest_event_mtime", lambda: None)
    monkeypatch.setattr(G, "AGENTS_CACHE_S", 10 ** 9)
    monkeypatch.setattr(G, "_usage_rows_cached", lambda: [{"provider": "codex", "used_pct": 1.0}])
    req = _FakeRequest("GET", "/agents")
    req["principal"] = {"id": "d", "label": "", "scopes": scopes}
    return json.loads(_run(G.handle_agents(req)).body)


def test_agents_carries_usage_only_for_a_caller_holding_usage(monkeypatch):
    assert "usage" not in _agents_body(monkeypatch, ["read"])
    assert _agents_body(monkeypatch, ["read", "usage"])["usage"] == [{"provider": "codex", "used_pct": 1.0}]
    assert "usage" in _agents_body(monkeypatch, ["*"])
