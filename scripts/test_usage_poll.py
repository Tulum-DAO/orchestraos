"""usage_poll: the provider usage calls are cached host-wide, backed off, and never leak a token.

No test makes a network call: every run gets a fake http_get that returns a scripted HttpResult or
raises. Response bodies follow the shapes Claude Code 2.1.x and codex 0.153.4 parse (see the
usage_poll docstring); the numbers are synthetic.
"""
import base64
import hashlib
import json
import os
import socket

import pytest

import scripts.usage_meter as UM
import scripts.usage_poll as P

NOW = 1792170500
CLAUDE_TOKEN = "sk-ant-oat01-FIXTURE-TOKEN-must-never-be-written"
CODEX_ACCT = "acct-FIXTURE-ID-must-never-be-written"
EMAIL = "fixture-person@example.com"


def _jwt(exp):
    seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{seg({'alg': 'none'})}.{seg({'exp': exp, 'email': EMAIL})}.FIXTURE-CODEX-SIG"


CODEX_TOKEN = _jwt(NOW + 86400)
CLAUDE_BODY = {"five_hour": {"utilization": 42.0, "resets_at": "2026-10-16T19:00:00Z"},
               "seven_day": {"utilization": 71, "resets_at": "2026-10-20T00:00:00+00:00"},
               "seven_day_opus": None, "extra_usage": {"is_enabled": False}}
CODEX_BODY = {"plan_type": "team", "rate_limit": {
    "allowed": True, "limit_reached": False,
    "primary_window": {"used_percent": 12, "limit_window_seconds": 18000, "reset_after_seconds": 600,
                       "reset_at": 1792185347},
    "secondary_window": {"used_percent": 33, "limit_window_seconds": 604800,
                         "reset_after_seconds": 9000}},
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"}}
ON = {"poll": True, "poll_s": 300}


class FakeHttp:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers), timeout))
        r = self.results.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def ok(body):
    return P.HttpResult(200, {}, json.dumps(body).encode())


@pytest.fixture
def homes(tmp_path):
    claude = tmp_path / "claude-home"
    claude.mkdir()
    (claude / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
        "accessToken": CLAUDE_TOKEN, "refreshToken": "REFRESH-FIXTURE-never-used",
        "expiresAt": (NOW + 30 * 86400) * 1000, "subscriptionType": "max"}}))
    codex = tmp_path / "codex-home"
    codex.mkdir()
    (codex / "auth.json").write_text(json.dumps({"tokens": {
        "access_token": CODEX_TOKEN, "refresh_token": "REFRESH-FIXTURE-never-used",
        "account_id": CODEX_ACCT, "id_token": _jwt(NOW + 86400)}}))
    data = tmp_path / "data"
    data.mkdir()
    return {"claude": str(claude), "codex": str(codex), "data": data, "locks": tmp_path / "locks"}


def _claude_target(h):
    return [("claude", "default", h["claude"])]


def _run(h, http, now=NOW, targets=None, config=ON, data=None):
    return P.run(data or h["data"], now=now, http_get=http, config=config, locks=h["locks"],
                 targets=targets if targets is not None else _claude_target(h))


def _state(h):
    return json.loads((h["data"] / "state" / "usage" / "poll-state.json").read_text())


def _no_secret_written(*roots):
    """No written file holds the fixture token, account id or e-mail, nor ANY 12-character piece
    of them (a truncated or sliced token is still a leak)."""
    secrets = (CLAUDE_TOKEN, CODEX_TOKEN, CODEX_ACCT, EMAIL, "REFRESH-FIXTURE-never-used")
    pieces = {s[i:i + 12] for s in secrets for i in range(len(s) - 11)}
    for root in roots:
        for dirpath, _d, files in os.walk(root):
            for f in files:
                blob = open(os.path.join(dirpath, f), "rb").read()
                for piece in pieces:
                    assert piece.encode() not in blob, f"a 12-char piece of a secret was written to {f}"


# ---- 200 ---------------------------------------------------------------------------------------

def test_a_200_becomes_a_snapshot_and_the_newest_rows(homes):
    http = FakeHttp(ok(CLAUDE_BODY))
    out = _run(homes, http)
    assert out["ran"] and out["outcomes"][0]["outcome"] == "ok"
    url, headers, timeout = http.calls[0]
    assert url == P.CLAUDE_URL and headers["anthropic-beta"] == "oauth-2025-04-20" and timeout == P.HTTP_TIMEOUT_S
    rows = UM.usage_rows(homes["data"], providers=["claude"], now=NOW + 1)
    assert [(r["window"], r["used_pct"], r["source"], r["account"], r["stale"]) for r in rows] == [
        ("5h", 42.0, "claude-api", "default", False), ("weekly", 71.0, "claude-api", "default", False)]
    assert rows[0]["resets_at"] == UM._iso_to_epoch("2026-10-16T19:00:00Z")
    combined = json.loads((homes["data"] / "state" / "usage" / "usage.json").read_text())
    assert {r["source"] for r in combined["rows"] if r["provider"] == "claude"} == {"claude-api"}


def test_no_token_or_account_id_or_email_reaches_any_written_file(homes):
    http = FakeHttp(ok(CLAUDE_BODY), ok(CODEX_BODY))
    _run(homes, http, targets=[("claude", "default", homes["claude"]), ("codex", "default", homes["codex"])])
    assert http.calls[1][1]["ChatGPT-Account-Id"] == CODEX_ACCT          # sent, in the header only
    assert (homes["data"] / "state" / "usage" / "api-codex-default.json").is_file()
    _no_secret_written(homes["data"], homes["locks"])


def test_codex_windows_from_wham_usage(homes):
    _run(homes, FakeHttp(ok(CODEX_BODY)), targets=[("codex", "default", homes["codex"])])
    rows = UM.api_rows(NOW + 5, homes["data"], "codex")
    assert [(r["window"], r["used_pct"], r["resets_at"]) for r in rows] == [
        ("5h", 12.0, 1792185347), ("weekly", 33.0, NOW + 9000)]


def test_the_newest_reading_wins_api_or_local(homes):
    _run(homes, FakeHttp(ok(CLAUDE_BODY)))                       # api reading at NOW
    rec = UM.claude_record_from_statusline(
        {"rate_limits": {"five_hour": {"used_percentage": 50, "resets_at": 1},
                         "seven_day": {"used_percentage": 60, "resets_at": 2}}}, NOW + 120)
    UM.write_claude_record(rec, homes["data"], "default")         # status line, newer
    rows = UM.usage_rows(homes["data"], providers=["claude"], now=NOW + 130)
    assert {r["source"] for r in rows} == {"claude-statusline"} and rows[0]["used_pct"] == 50.0
    rec["as_of"] = NOW - 600                                      # status line, older
    UM.write_claude_record(rec, homes["data"], "default")
    rows = UM.usage_rows(homes["data"], providers=["claude"], now=NOW + 130)
    assert {r["source"] for r in rows} == {"claude-api"}


def test_an_unreadable_200_is_a_failure_not_a_zero(homes):
    _run(homes, FakeHttp(P.HttpResult(200, {}, b'{"five_hour": {"utilization": null}}')))
    assert not (homes["data"] / "state" / "usage" / "api-claude-default.json").exists()
    assert _state(homes)["claude:default"]["failures"] == 1


# ---- cache -------------------------------------------------------------------------------------

def test_at_most_one_call_per_poll_interval_host_wide(homes):
    http = FakeHttp(ok(CLAUDE_BODY), ok(CLAUDE_BODY))
    _run(homes, http)
    assert _run(homes, http, now=NOW + 269)["outcomes"][0]["outcome"] == "cached"
    assert len(http.calls) == 1
    # a 5-min cron that fires a few seconds early still gets the slot (30 s of slack)
    assert _run(homes, http, now=NOW + 295)["outcomes"][0]["outcome"] == "ok"
    assert len(http.calls) == 2


def test_the_slot_belongs_to_the_login_not_the_data_dir(homes, tmp_path):
    """Two installs (two data dirs) on one login share one slot: one call, not two."""
    http = FakeHttp(ok(CLAUDE_BODY), ok(CLAUDE_BODY))
    other = tmp_path / "data2"
    other.mkdir()
    _run(homes, http)
    assert _run(homes, http, data=other)["outcomes"][0]["outcome"] == "cached"
    assert len(http.calls) == 1


def test_a_second_run_while_the_lock_is_held_does_nothing(homes):
    import fcntl
    homes["locks"].mkdir(parents=True)
    key = P.login_key("claude", homes["claude"])
    with open(homes["locks"] / f"{key}.lock", "a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        http = FakeHttp()
        out = _run(homes, http)
    assert out["outcomes"] == [{"key": "claude:default", "outcome": "locked"}] and http.calls == []


def test_on_by_default_and_off_with_one_line(homes):
    """No [usage] section = on (the operator's default). poll = false is the one line that turns it off, and then
    no call is made. A wrong type (poll = "yes") is off, never a guess."""
    assert P.load_config({}) == {"poll": True, "poll_s": 300}
    assert P.load_config({"usage": {}})["poll"] is True
    http = FakeHttp()
    off = P.run(homes["data"], now=NOW, http_get=http, config=P.load_config({"usage": {"poll": False}}),
                targets=_claude_target(homes))
    assert off["ran"] is False and "poll = false" in off["reason"]
    assert http.calls == []
    assert P.load_config({"usage": {"poll": True, "poll_minutes": 7}}) == {"poll": True, "poll_s": 420}
    assert P.interval_s(420) == 390 and P.interval_s(300) == 270 and P.interval_s(60) == 60
    assert P.load_config({"usage": {"poll": "yes", "poll_minutes": 0}}) == {"poll": False, "poll_s": 300}


def test_the_targets_are_this_hosts_one_login_per_provider():
    """One login per provider, labelled as the status line and the Codex rollouts label it, so the
    API reading and the local one are the same account's (newest per window wins)."""
    assert P.poll_targets({}) == [("claude", "default", "~/.claude"), ("codex", "default", "~/.codex")]
    env = {"CLAUDE_CONFIG_DIR": "/srv/logins/.claude-work", "CODEX_HOME": "/srv/logins/codex"}
    assert P.poll_targets(env) == [("claude", "work", "/srv/logins/.claude-work"),
                                   ("codex", "default", "/srv/logins/codex")]
    assert P.poll_targets(env)[0][1] == UM.account_label(env["CLAUDE_CONFIG_DIR"])


# ---- 429 / Retry-After -------------------------------------------------------------------------

@pytest.mark.parametrize("header,expect", [
    ("120", 270),            # shorter than the poll interval: the interval
    ("1800", 1800),          # honoured
    ("7200", 7200),          # past 1 h: still honoured (and logged)
    ("999999", 86400),       # capped at 24 h
    (None, 270),             # absent: the exponential step, floored at the interval
])
def test_429_honours_retry_after(homes, header, expect):
    http = FakeHttp(P.HttpResult(429, {"Retry-After": header} if header else {}, b""))
    assert _run(homes, http)["outcomes"][0]["outcome"] == "rate_limited"
    assert _state(homes)["claude:default"]["next_allowed"] == NOW + expect
    assert not (homes["data"] / "state" / "usage" / "api-claude-default.json").exists()


def test_retry_after_as_an_http_date():
    from email.utils import formatdate
    assert P.retry_after_s(formatdate(NOW + 900, usegmt=True), NOW) == 900
    assert P.retry_after_s("garbled", NOW) is None


def test_repeated_429s_back_off_and_never_retry_in_the_same_run(homes):
    http = FakeHttp(*[P.HttpResult(429, {}, b"")] * 8)
    now, gaps = NOW, []
    for _ in range(8):
        _run(homes, http, now=now)
        nxt = _state(homes)["claude:default"]["next_allowed"]
        gaps.append(nxt - now)
        now = nxt
    assert len(http.calls) == 8                                   # one call per run, no more
    assert gaps == [270, 270, 270, 480, 960, 1920, 3600, 3600]


# ---- 401 / timeout / expiry --------------------------------------------------------------------

def test_401_backs_off_and_never_refreshes(homes):
    cred = os.path.join(homes["claude"], ".credentials.json")
    before = hashlib.md5(open(cred, "rb").read()).hexdigest()
    http = FakeHttp(P.HttpResult(401, {}, b'{"error": "expired"}'))
    assert _run(homes, http)["outcomes"][0]["outcome"] == "auth"
    assert _state(homes)["claude:default"]["next_allowed"] == NOW + P.AUTH_BACKOFF_S
    assert len(http.calls) == 1 and all("oauth/token" not in c[0] for c in http.calls)
    assert hashlib.md5(open(cred, "rb").read()).hexdigest() == before
    _no_secret_written(homes["data"], homes["locks"])


def test_a_timeout_is_recorded_by_class_never_by_message(homes):
    http = FakeHttp(socket.timeout(f"timed out sending Bearer {CLAUDE_TOKEN}"))
    out = _run(homes, http)
    assert out["outcomes"][0] == {"key": "claude:default", "outcome": "error", "error": "TimeoutError"}
    assert _state(homes)["claude:default"]["last_status"].startswith("error:")
    assert _state(homes)["claude:default"]["next_allowed"] == NOW + 270
    assert CLAUDE_TOKEN not in json.dumps(out)
    _no_secret_written(homes["data"], homes["locks"])


def test_an_expired_token_is_never_sent(homes):
    cred = os.path.join(homes["claude"], ".credentials.json")
    d = json.load(open(cred))
    d["claudeAiOauth"]["expiresAt"] = (NOW - 10) * 1000
    open(cred, "w").write(json.dumps(d))
    http = FakeHttp()
    assert _run(homes, http)["outcomes"][0]["outcome"] == "no-token"
    assert http.calls == []
    expired_codex = os.path.join(homes["codex"], "auth.json")
    d = json.load(open(expired_codex))
    d["tokens"]["access_token"] = _jwt(NOW - 10)
    open(expired_codex, "w").write(json.dumps(d))
    assert P.codex_token(homes["codex"], NOW)[0] is None


def test_a_missing_login_is_no_call(homes, tmp_path):
    http = FakeHttp()
    assert _run(homes, http, targets=[("claude", "work", str(tmp_path / "nowhere"))])["outcomes"][0]["outcome"] == "no-token"
    assert http.calls == []


# ---- pass 2 (independent review) ---------------------------------------------------------------

def test_retry_after_past_an_hour_is_logged(homes):
    _run(homes, FakeHttp(P.HttpResult(429, {"Retry-After": "7200"}, b"")))
    log = (homes["data"] / "state" / "usage" / "poll.log").read_text()
    assert '"retry_after_s": 7200' in log


def test_a_clock_that_went_backwards_never_waits_past_the_cap(homes):
    _run(homes, FakeHttp(P.HttpResult(429, {"Retry-After": "86400"}, b"")))
    # the clock jumps back 30 days: the stored next_allowed is now 31 days ahead
    http = FakeHttp(ok(CLAUDE_BODY))
    back = NOW - 30 * 86400
    assert _run(homes, http, now=back)["outcomes"][0]["outcome"] == "cached"
    assert _state(homes)["claude:default"]["next_allowed"] == back + P.RETRY_AFTER_MAX_S
    assert _run(homes, http, now=back + P.RETRY_AFTER_MAX_S)["outcomes"][0]["outcome"] == "ok"


def test_the_attempt_is_saved_even_if_the_run_dies(homes):
    class Boom(BaseException):
        pass

    def die(url, headers, timeout):
        raise Boom()                                  # not an Exception: escapes poll_account
    with pytest.raises(Boom):
        _run(homes, die)
    st = _state(homes)["claude:default"]
    assert st["last_attempt"] == NOW and st["next_allowed"] >= NOW + 270
    http = FakeHttp(ok(CLAUDE_BODY))
    assert _run(homes, http, now=NOW + 10)["outcomes"][0]["outcome"] == "cached" and http.calls == []


def test_each_login_is_saved_right_after_its_attempt(homes):
    """The second login's request sees the first login's attempt already on disk."""
    seen = []

    def http(url, headers, timeout):
        seen.append(json.loads((homes["data"] / "state" / "usage" / "poll-state.json").read_text()))
        return ok(CLAUDE_BODY) if "anthropic" in url else ok(CODEX_BODY)
    _run(homes, http, targets=[("claude", "default", homes["claude"]), ("codex", "default", homes["codex"])])
    assert seen[1]["claude:default"]["last_status"] == "200"


@pytest.mark.parametrize("body", [
    '{"rate_limit":{"primary_window":{"used_percent":5,"limit_window_seconds":Infinity}}}',
    '{"rate_limit":{"primary_window":{"used_percent":5,"limit_window_seconds":18000,"reset_at":1e400}}}',
    '{"rate_limit":{"primary_window":{"used_percent":5,"limit_window_seconds":18000,"reset_after_seconds":1e400}}}',
    '{"rate_limit":{"primary_window":{"used_percent":Infinity,"limit_window_seconds":18000}}}',
    '{"rate_limit":{"primary_window":{"used_percent":NaN,"limit_window_seconds":-Infinity}}}',
])
def test_infinite_numbers_never_raise(homes, body):
    P.parse_codex(json.loads(body), NOW)                         # no OverflowError / ValueError
    out = _run(homes, FakeHttp(P.HttpResult(200, {}, body.encode())), targets=[("codex", "default", homes["codex"])])
    assert out["outcomes"][0]["outcome"] in ("ok", "unreadable")
    assert UM._ts(float("inf")) is None and UM._ts(1e400) is None and UM._ts(float("nan")) is None


def test_redirects_are_never_followed(homes):
    """X2: a 302 from the usage URL ends the read; the Authorization header never reaches the
    redirect target. A fake https transport stands in for the network."""
    import io
    import urllib.request
    import urllib.response
    sent = []

    class FakeHttps(urllib.request.HTTPSHandler):
        def https_open(self, req):
            sent.append((req.full_url, req.get_header("Authorization")))
            if len(sent) == 1:
                hdrs = __import__("email.message").message.Message()
                hdrs["Location"] = "https://elsewhere.invalid/steal"
                resp = urllib.response.addinfourl(io.BytesIO(b""), hdrs, req.full_url, 302)
                resp.msg = "Found"
                return resp
            hdrs = __import__("email.message").message.Message()
            resp = urllib.response.addinfourl(io.BytesIO(b"{}"), hdrs, req.full_url, 200)
            resp.msg = "OK"
            return resp
    r = P.urllib_get(P.CLAUDE_URL, {"Authorization": "Bearer X"}, 5, _extra_handlers=(FakeHttps,))
    assert r.status == 302
    assert [u for u, _a in sent] == [P.CLAUDE_URL]


def test_a_partial_token_in_a_file_is_caught(homes):
    """X3: the leak scan itself catches a 12-character slice of the token."""
    (homes["data"] / "slice.txt").write_text("prefix " + CLAUDE_TOKEN[5:19] + " suffix")
    with pytest.raises(AssertionError):
        _no_secret_written(homes["data"])


def test_newest_wins_per_window_not_per_account(homes):
    """Review demo A: a newer status line with only 5 h keeps the API's weekly and weekly-opus."""
    now = 1_800_000_000
    P.write_snapshot(homes["data"], "claude", "default",
                     {"5h": {"used_pct": 50, "resets_at": now + 100}, "weekly": {"used_pct": 90, "resets_at": now + 1000},
                      "weekly-opus": {"used_pct": 95, "resets_at": None}}, now - 60)
    u = homes["data"] / "state" / "usage"
    (u / "claude-default.json").write_text(json.dumps(
        {"windows": {"five_hour": {"used_pct": 51, "resets_at": now + 100}}, "as_of": now - 10}))
    rows = UM.usage_rows(homes["data"], providers=["claude"], now=now)
    assert [(r["window"], r["used_pct"], r["source"]) for r in rows] == [
        ("5h", 51.0, "claude-statusline"), ("weekly", 90.0, "claude-api"), ("weekly-opus", 95.0, "claude-api")]


def test_a_reading_from_the_future_is_stale(homes):
    """Review demo B: an as_of more than 300 s ahead is a wrong clock, never a current reading."""
    now = 1_800_000_000
    P.write_snapshot(homes["data"], "claude", "default", {"5h": {"used_pct": 5}}, now + 86400 * 30)
    assert all(r["stale"] for r in UM.usage_rows(homes["data"], providers=["claude"], now=now))
    P.write_snapshot(homes["data"], "claude", "default", {"5h": {"used_pct": 5}}, now + 299)
    assert not any(r["stale"] for r in UM.usage_rows(homes["data"], providers=["claude"], now=now))


def test_an_older_lower_block_never_overwrites_the_status_line_snapshot(homes):
    """Same resets_at and a LOWER used_pct = an idle seat's older block: the higher one stays."""
    hi = {"windows": {"five_hour": {"used_pct": 60.0, "resets_at": 111}, "seven_day": {"used_pct": 30.0, "resets_at": 222}},
          "as_of": NOW, "version": None}
    UM.write_claude_record(hi, homes["data"], "default")
    lo = {"windows": {"five_hour": {"used_pct": 40.0, "resets_at": 111}, "seven_day": {"used_pct": 20.0, "resets_at": 222}},
          "as_of": NOW + 60, "version": None}
    assert UM.write_claude_record(lo, homes["data"], "default") is None
    p = homes["data"] / "state" / "usage" / "claude-default.json"
    stamped = {"windows": {k: dict(w, as_of=NOW) for k, w in hi["windows"].items()}, "as_of": NOW, "version": None}
    assert json.loads(p.read_text()) == stamped    # each window carries when it was read
    # a new window (another resets_at) is always written, even lower
    new = {"windows": {"five_hour": {"used_pct": 1.0, "resets_at": 999}, "seven_day": {"used_pct": 20.0, "resets_at": 222}},
           "as_of": NOW + 120, "version": None}
    UM.write_claude_record(new, homes["data"], "default")
    got = json.loads(p.read_text())
    assert got["windows"]["five_hour"]["used_pct"] == 1.0 and got["windows"]["seven_day"]["used_pct"] == 30.0
    # the kept window keeps ITS time: never re-stamped as current alongside the fresh one
    assert got["windows"]["seven_day"]["as_of"] == NOW and got["windows"]["five_hour"]["as_of"] == NOW + 120
    assert got["as_of"] == NOW + 120


def _rec(five, week, at, resets=(111, 222)):
    return {"windows": {"five_hour": {"used_pct": five, "resets_at": resets[0]},
                        "seven_day": {"used_pct": week, "resets_at": resets[1]}}, "as_of": at, "version": None}


def test_a_real_drop_in_one_window_is_taken_after_the_grace(homes):
    """A plan upgrade mid-week: weekly 80% -> 20% with the same reset. Refused only within DROP_GRACE_S of the
    high reading; after that the lower reading is written (not held until the window ends)."""
    d = homes["data"]
    p = d / "state" / "usage" / "claude-default.json"
    UM.write_claude_record(_rec(10.0, 80.0, NOW), d, "default")
    # within the grace: refused, and the kept weekly still says it was read at NOW
    UM.write_claude_record(_rec(12.0, 20.0, NOW + 60), d, "default")
    got = json.loads(p.read_text())
    assert got["windows"]["seven_day"]["used_pct"] == 80.0 and got["windows"]["seven_day"]["as_of"] == NOW
    # past the grace: the drop is real
    later = NOW + UM.DROP_GRACE_S + 60
    UM.write_claude_record(_rec(14.0, 20.0, later), d, "default")
    got = json.loads(p.read_text())
    assert got["windows"]["seven_day"] == {"used_pct": 20.0, "resets_at": 222, "as_of": later}
    rows = {r["window"]: r for r in UM.claude_rows(later + 5, d)}
    assert rows["weekly"]["used_pct"] == 20.0 and rows["weekly"]["stale"] is False


def test_a_kept_high_reading_is_shown_at_its_own_age_and_the_poller_wins(homes):
    """The kept 80% is not re-stamped: once STALE_S old it greys out. The poller's newer lower reading (its own
    file) beats it in the merge, so the drop shows at once when the poller is on."""
    d = homes["data"]
    UM.write_claude_record(_rec(10.0, 80.0, NOW), d, "default")
    UM.write_claude_record(_rec(11.0, 20.0, NOW + 600), d, "default")      # 10 min later: within the grace
    weekly = [r for r in UM.claude_rows(NOW + 601, d) if r["window"] == "weekly"][0]
    assert weekly["used_pct"] == 80.0 and weekly["as_of"] == NOW
    assert [r for r in UM.claude_rows(NOW + UM.STALE_S + 1, d) if r["window"] == "weekly"][0]["stale"] is True
    UM._atomic_json(UM.api_snapshot_path(d, "claude", "default"),
                    {"windows": {"weekly": {"used_pct": 20.0, "resets_at": 222}}, "as_of": NOW + 300})
    rows = {r["window"]: r for r in UM.usage_rows(d, providers=["claude"], now=NOW + 601) if r["provider"] == "claude"}
    assert rows["weekly"]["used_pct"] == 20.0 and rows["weekly"]["source"] == "claude-api"
    assert rows["5h"]["used_pct"] == 11.0


def test_concurrent_status_line_writes_are_serialised(homes):
    """The read-modify-write runs under an exclusive flock: a write waits while another holds it."""
    import fcntl
    import threading
    d = homes["data"]
    out = UM.claude_usage_dir(d)
    out.mkdir(parents=True, exist_ok=True)
    done = threading.Event()
    with open(out / ".claude-default.json.lock", "a") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        t = threading.Thread(target=lambda: (UM.write_claude_record(_rec(5.0, 6.0, NOW), d, "default"), done.set()))
        t.start()
        assert not done.wait(0.5), "the write went ahead while the lock was held"
        assert not (out / "claude-default.json").exists()
    t.join(5)
    assert done.is_set() and (out / "claude-default.json").exists()


def test_a_future_dated_reading_never_hides_a_fresh_one(homes):
    """The review's demo: an api snapshot of 5% stamped a day ahead (a clock stepped back after it) next to a
    status-line 80% from 5 s ago. The fresh 80% is shown; the future one only when nothing fresh exists."""
    d = homes["data"]
    UM._atomic_json(UM.api_snapshot_path(d, "claude", "default"),
                    {"windows": {"5h": {"used_pct": 5.0, "resets_at": NOW + 3600}}, "as_of": NOW + 86400})
    UM.write_claude_record({"windows": {"five_hour": {"used_pct": 80.0, "resets_at": NOW + 3600}}, "as_of": NOW - 5,
                            "version": None}, d, "default")
    rows = [r for r in UM.usage_rows(d, providers=["claude"], now=NOW) if r.get("window") == "5h"]
    assert len(rows) == 1 and rows[0]["used_pct"] == 80.0 and rows[0]["stale"] is False
    assert rows[0]["source"] == "claude-statusline"
    # alone, the future-dated reading still shows, greyed
    (d / "state" / "usage" / "claude-default.json").unlink()
    alone = [r for r in UM.usage_rows(d, providers=["claude"], now=NOW) if r.get("window") == "5h"]
    assert alone[0]["used_pct"] == 5.0 and alone[0]["stale"] is True


def test_a_login_with_no_reading_for_14_days_leaves_the_view_but_its_file_stays(homes):
    d = homes["data"]
    old = NOW - UM.AGE_OUT_S - 60
    UM._atomic_json(UM.api_snapshot_path(d, "claude", "work"),
                    {"windows": {"weekly": {"used_pct": 50.0, "resets_at": NOW}}, "as_of": old})
    UM.write_claude_record({"windows": {"seven_day": {"used_pct": 40.0, "resets_at": NOW}}, "as_of": old,
                            "version": None}, d, "work")
    UM.write_claude_record({"windows": {"seven_day": {"used_pct": 7.0, "resets_at": NOW}}, "as_of": NOW - 60,
                            "version": None}, d, "default")
    rows = [r for r in UM.usage_rows(d, providers=["claude"], now=NOW) if r.get("used_pct") is not None]
    assert {r["account"] for r in rows} == {"default"}
    assert UM.api_snapshot_path(d, "claude", "work").exists() and (d / "state" / "usage" / "claude-work.json").exists()


def test_before_the_first_reading_a_polled_provider_says_first_reading_in_a_moment(homes):
    d = homes["data"]
    empty = homes["data"] / "no-codex-sessions"
    on = UM.usage_rows(d, providers=["claude", "codex"], now=NOW, codex_sessions_dir=empty, polling=True)
    assert [(r["provider"], r["pending"], r["reason"]) for r in on] == \
        [("claude", True, UM.PENDING_REASON), ("codex", True, UM.PENDING_REASON)]
    off = UM.usage_rows(d, providers=["claude", "codex"], now=NOW, codex_sessions_dir=empty)
    assert all("pending" not in r and r["reason"] != UM.PENDING_REASON for r in off)


def test_each_reading_says_whether_it_is_the_agents_login(homes, monkeypatch):
    d = homes["data"]
    for acct, pct in (("default", 7.0), ("work", 30.0)):
        UM.write_claude_record({"windows": {"seven_day": {"used_pct": pct, "resets_at": NOW}}, "as_of": NOW - 60,
                                "version": None}, d, acct)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    flags = {r["account"]: r["agents"] for r in UM.usage_rows(d, providers=["claude"], now=NOW)}
    assert flags == {"default": True, "work": False}
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/srv/logins/.claude-work")      # this install runs Claude as "work"
    flags = {r["account"]: r["agents"] for r in UM.usage_rows(d, providers=["claude"], now=NOW)}
    assert flags == {"default": False, "work": True}


def test_the_attempt_is_on_disk_before_the_request_goes_out(homes):
    """If the process is killed mid-request (no finally runs), the slot is still used."""
    seen = []

    def http(url, headers, timeout):
        seen.append(json.loads((homes["data"] / "state" / "usage" / "poll-state.json").read_text()))
        seen.append(json.loads((homes["locks"] / f"{P.login_key('claude', homes['claude'])}.json").read_text()))
        return ok(CLAUDE_BODY)
    _run(homes, http)
    assert seen[0]["claude:default"]["last_status"] == "in-flight"
    assert seen[1]["last_status"] == "in-flight" and seen[1]["next_allowed"] >= NOW + 270
    _no_secret_written(homes["data"], homes["locks"])


# --- a model's own weekly allowance (the Fable limit), from the body's limits[] (measured 2026-11-26) ---------

LIMITS_BODY = {
    "five_hour": {"utilization": 39.0, "resets_at": "2026-11-26T22:40:00.044465+00:00"},
    "seven_day": {"utilization": 68.0, "resets_at": "2026-12-01T10:00:00.044481+00:00"},
    "seven_day_opus": None,
    "weekly_scoped_shares": [{"limit_index": 2, "used_percent_of_weekly": 43, "allowance_percent_of_weekly": 50}],
    "limits": [
        {"kind": "session", "group": "session", "percent": 39, "severity": "normal",
         "resets_at": "2026-11-26T22:40:00.044465+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_all", "group": "weekly", "percent": 68, "severity": "normal",
         "resets_at": "2026-12-01T10:00:00.044481+00:00", "scope": None, "is_active": False},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 86, "severity": "warning",
         "resets_at": "2026-12-01T10:00:00.044627+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": True}],
}


def test_the_fable_weekly_allowance_is_read_from_limits():
    w = P.parse_claude(LIMITS_BODY, NOW)
    assert w["weekly-fable"] == {"used_pct": 86.0, "resets_at": UM._iso_to_epoch("2026-12-01T10:00:00.044627+00:00"),
                                 "allowance_pct_of_weekly": 50.0, "severity": "warning"}
    assert w["5h"]["used_pct"] == 39.0 and w["weekly"]["used_pct"] == 68.0


def test_a_scoped_limit_never_overwrites_a_named_window_and_junk_is_skipped():
    body = {"seven_day_opus": {"utilization": 10.0, "resets_at": None}, "limits": [
        {"kind": "weekly_scoped", "percent": 90, "scope": {"model": {"display_name": "Opus"}}},
        {"kind": "weekly_scoped", "percent": 200, "scope": {"model": {"display_name": "Sonnet"}}},
        {"kind": "weekly_scoped", "percent": 50, "scope": {"model": {"display_name": "../x y"}}},
        {"kind": "weekly_scoped", "percent": 50, "scope": None},
        "junk", {"kind": "session", "percent": 5}]}
    w = P.parse_claude(body, NOW)
    assert w["weekly-opus"]["used_pct"] == 10.0
    assert "weekly-sonnet" not in w and "weekly-x-y" in w
    assert set(w) == {"weekly-opus", "weekly-x-y"}


def test_limits_that_is_not_a_list_is_ignored():
    assert P.parse_claude({"limits": {"kind": "weekly_scoped"}}, NOW) == {}


def test_the_fable_window_reaches_the_usage_rows_with_its_extras_and_goes_stale_on_its_own_clock(tmp_path):
    P.write_snapshot(tmp_path, "claude", "default", P.parse_claude(LIMITS_BODY, NOW), NOW)
    rows = [r for r in UM.api_rows(NOW + 60, tmp_path, "claude") if r["window"] == "weekly-fable"]
    assert len(rows) == 1 and rows[0]["used_pct"] == 86.0 and rows[0]["stale"] is False
    assert rows[0]["allowance_pct_of_weekly"] == 50.0 and rows[0]["severity"] == "warning"
    old = [r for r in UM.api_rows(NOW + 3 * 3600, tmp_path, "claude") if r["window"] == "weekly-fable"][0]
    assert old["stale"] is True and old["as_of"] == NOW and old["used_pct"] == 86.0      # stale, never 0
