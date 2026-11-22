"""usage_meter: plan usage from local files only, never a guessed number.

The Claude inputs are a real Claude Code 2.1.284 status-line capture (session id and paths removed),
taken on a scratch session: the first redraw has no rate_limits block, a later one does. The Codex
line is a real rollout token_count event with its numbers kept; its clock is a round synthetic one.
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
    "timestamp": "2026-10-16T17:00:00Z", "type": "event_msg",
    "payload": {"type": "token_count", "info": None, "rate_limits": {
        "limit_id": "codex", "limit_name": None,
        "primary": {"used_percent": 1.0, "window_minutes": 300, "resets_at": 1792185347},
        "secondary": {"used_percent": 18.0, "window_minutes": 10080, "resets_at": 1792651095},
        "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
        "plan_type": "team", "rate_limit_reached_type": None}},
}
CODEX_AT = 1792170000


def _rollout(dirpath, name, events, mtime=None):
    from datetime import date
    day = date.fromtimestamp(mtime if mtime is not None else NOW)
    folder = dirpath / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
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


@pytest.mark.parametrize("cfg,label", [
    ("/home/x/.claude-work/", "work"), ("/home/x/.claude", "default"), ("/home/x/claude_alt", "alt"),
    ("/srv/profiles/ops", "ops"), ("/home/x/bad name", "default"), (None, "default"),
])
def test_account_is_a_plain_label_never_a_path(monkeypatch, cfg, label):
    if cfg is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", cfg)
    assert U.claude_account() == label


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


def test_only_event_msg_token_counts_are_readings(tmp_path):
    wrong = json.loads(json.dumps(CODEX_EVENT))
    wrong["type"] = "response_item"
    _rollout(tmp_path, "rollout-a.jsonl", [wrong], mtime=NOW)
    assert U.codex_rows(NOW, tmp_path)[0]["used_pct"] is None


def test_codex_reads_only_the_lookback_day_folders(tmp_path):
    # A rollout filed under an old day folder is never opened, even with a fresh mtime.
    old = tmp_path / "2025" / "01" / "01"
    old.mkdir(parents=True)
    p = old / "rollout-old.jsonl"
    p.write_text(json.dumps(CODEX_EVENT) + "\n")
    os.utime(p, (NOW, NOW))
    assert U.codex_rows(NOW, tmp_path)[0]["used_pct"] is None


def test_the_hook_records_usage_when_claude_code_runs_it(tmp_path):
    # As Claude Code runs it: its own process, another working directory, a bare environment.
    import subprocess, sys
    hook = os.path.join(os.path.dirname(os.path.abspath(U.__file__)), "..", "hooks", "statusline.py")
    r = subprocess.run([sys.executable, os.path.abspath(hook)], input=json.dumps(CLAUDE_WITH_LIMITS),
                       capture_output=True, text=True, cwd=str(tmp_path), timeout=30,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ORCHESTRA_DIR": str(tmp_path)})
    assert r.returncode == 0 and r.stdout.strip(), r.stderr
    assert (tmp_path / "state" / "usage" / "claude-default.json").is_file(), r.stderr


# ---- the gateway runs the poller (on by default) when the dashboard asks for usage ----

def _kick_env(monkeypatch, tmp_path, poll, run=None):
    import scripts.usage_poll as P
    calls = []
    monkeypatch.setattr(P, "load_config", lambda raw=None: {"poll": poll, "poll_s": 300})
    monkeypatch.setattr(P, "run", run or (lambda data_dir, **k: (calls.append(data_dir), {"ran": True, "outcomes": [{"outcome": "ok"}]})[1]))
    monkeypatch.setattr(G, "ORCH_DIR", tmp_path)
    monkeypatch.setattr(G, "_usage_poll_thread", {"t": None, "at": 0.0})
    monkeypatch.setattr(G, "_usage_cache", {"rows": [{"provider": "claude"}], "at": 1.0})
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)   # the guard, lifted for these tests (run is stubbed)
    return calls


def test_a_usage_read_starts_one_background_poll_and_drops_the_cache_after_a_snapshot(monkeypatch, tmp_path):
    calls = _kick_env(monkeypatch, tmp_path, True)
    assert G._kick_usage_poll() is True
    G._usage_poll_thread["t"].join(5)
    assert calls == [tmp_path]
    assert G._usage_cache["rows"] is None, "the next read shows the new reading"


def test_a_run_that_wrote_nothing_keeps_the_cache(monkeypatch, tmp_path):
    """A "cached" outcome (the login's slot is not open yet) must not drop the cache, or every read misses
    and kicks again (the delta review: 10 reads in 0.5 s = 4 threads)."""
    _kick_env(monkeypatch, tmp_path, True, run=lambda d, **k: {"ran": True, "outcomes": [{"outcome": "cached"}]})
    G._kick_usage_poll()
    G._usage_poll_thread["t"].join(5)
    assert G._usage_cache["rows"] == [{"provider": "claude"}]
    _kick_env(monkeypatch, tmp_path, True, run=lambda d, **k: (_ for _ in ()).throw(OSError("x")))
    G._kick_usage_poll()
    G._usage_poll_thread["t"].join(5)
    assert G._usage_cache["rows"] == [{"provider": "claude"}], "a failed run keeps it too"


def test_ten_rapid_reads_start_at_most_one_thread(monkeypatch, tmp_path):
    import threading
    started = []
    gate = threading.Event()

    def slow(d, **k):
        started.append(d)
        gate.wait(5)
        return {"ran": True, "outcomes": []}
    _kick_env(monkeypatch, tmp_path, True, run=slow)
    for _ in range(10):
        G._kick_usage_poll()
    t = G._usage_poll_thread["t"]
    # single-flight on its own: the interval has passed but the run is still alive (and within its deadline)
    monkeypatch.setattr(G, "USAGE_POLL_DEADLINE_S", 600)
    G._usage_poll_thread["at"] -= 400
    G._kick_usage_poll()
    assert len(started) == 1, "single-flight while a run is alive, even past the interval"
    G._usage_poll_thread["at"] += 400
    gate.set()
    t.join(5)
    assert len(started) == 1
    # and after it ends, still none within the poll interval of the last kick
    for _ in range(10):
        G._kick_usage_poll()
    assert len(started) == 1, "rate-limited to the poll interval"
    G._usage_poll_thread["at"] -= 10 ** 6          # the interval has passed
    G._kick_usage_poll()
    G._usage_poll_thread["t"].join(5)
    assert len(started) == 2


def test_a_hung_run_does_not_block_kicks_past_the_deadline(monkeypatch, tmp_path):
    import threading
    started = []
    never = threading.Event()

    def hang(d, **k):
        started.append(d)
        never.wait(30)
        return {"ran": True, "outcomes": []}
    _kick_env(monkeypatch, tmp_path, True, run=hang)
    G._kick_usage_poll()
    hung = G._usage_poll_thread["t"]
    assert hung.is_alive()
    G._kick_usage_poll()
    assert len(started) == 1, "within the deadline: the hung run holds the slot"
    G._usage_poll_thread["at"] -= G.USAGE_POLL_DEADLINE_S + 10 ** 6     # past the deadline (and the interval)
    G._kick_usage_poll()
    assert len(started) == 2, "past the deadline the hung run is abandoned and a new one starts"
    assert G._usage_poll_thread["t"] is not hung and hung.daemon
    never.set()


def test_no_poll_when_it_is_off_or_under_pytest(monkeypatch, tmp_path):
    calls = _kick_env(monkeypatch, tmp_path, False)
    assert G._kick_usage_poll() is False and G._usage_poll_thread["t"] is None
    calls = _kick_env(monkeypatch, tmp_path, True)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
    assert G._kick_usage_poll() is True and G._usage_poll_thread["t"] is None and calls == []


def test_an_unreadable_toml_is_polling_off_with_a_warning(tmp_path, monkeypatch, capsys):
    """Fail safe: the operator may have written poll = false before the file broke. A missing file is the
    defaults (on); a file that does not parse is off."""
    import scripts.usage_poll as P
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "orchestra.toml"))
    assert P.load_config()["poll"] is True, "no file: the defaults"
    (tmp_path / "orchestra.toml").write_text("[usage\npoll = false\n")
    cfg = P.load_config()
    assert cfg["poll"] is False and cfg.get("unreadable") is True
    assert "polling off" in capsys.readouterr().err
    assert P.run(tmp_path, now=1.0, config=cfg)["ran"] is False


def test_rows_say_first_reading_in_a_moment_while_polling_is_on(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(G, "_usage_cache", {"rows": None, "at": 0.0})
    monkeypatch.setattr(G, "_kick_usage_poll", lambda: True)
    monkeypatch.setattr(U, "usage_rows", lambda d, providers=None, polling=False, **k: seen.setdefault("polling", polling) and [])
    G._usage_rows_cached()
    assert seen["polling"] is True
