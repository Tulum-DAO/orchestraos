"""usage_poll.py: plan usage from the providers' own usage endpoints, cached and backed off.

OFF unless [usage] poll = true in orchestra.toml. When on, one run (cron, every few minutes):

  * for this host's login of each provider (poll_targets):
      claude: GET https://api.anthropic.com/api/oauth/usage  with the Claude Code OAuth access
              token in $CLAUDE_CONFIG_DIR (else ~/.claude)/.credentials.json, claudeAiOauth.accessToken
      codex : GET https://chatgpt.com/backend-api/wham/usage  with the Codex access token in
              $CODEX_HOME (else ~/.codex)/auth.json, tokens.access_token, plus its ChatGPT-Account-Id
  * writes <data dir>/state/usage/api-<provider>-<account>.json (labels and numbers only), then the
    combined <data dir>/state/usage/usage.json (usage_meter.usage_rows: newest reading per window wins, so the
    status-line snapshot and the Codex rollouts stay the zero-call fallback).

Call discipline (the whole host, every caller, every data dir):
  * per LOGIN, not per data dir: the lock and the backoff state live in a host-wide dir
    ($ORCHESTRA_USAGE_LOCK_DIR, else ~/.cache/orchestraos/usage-poll/), in files named by a hash of
    the login file's real path (<hash>.lock, <hash>.json: no token, no e-mail). Two installs that
    share a login share its slot. A login whose lock is held is skipped this run.
  * at most one call per login per poll interval (default 5 min), counted from the last ATTEMPT,
    success or not. The interval has POLL_SLACK_S (30 s) of slack under it, so a cron on the same
    period never finds the slot a few seconds short and skips every other run.
  * one attempt per run, never a retry loop. The attempt is recorded (and saved) BEFORE the request
    goes out and its outcome right after, so a run that dies mid-way still counts it. A failure
    pushes next_allowed out:
      429 / 503 : Retry-After (seconds or an HTTP date) when given, obeyed up to RETRY_AFTER_MAX_S
                  (24 h; anything past BACKOFF_MAX_S is logged); else the exponential step.
      401 / 403 : AUTH_BACKOFF_S. The token is never refreshed here (a refresh rotates the refresh
                  token and signs out every other holder of the login); the CLI refreshes it on its
                  own next use, and the next attempt after the backoff reads the new one.
      timeout, network error, 5xx, unreadable body: the exponential step
                  min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (failures - 1)), floored at the interval.
  * a next_allowed further ahead than now + RETRY_AFTER_MAX_S (the clock went backwards) is
    clamped to now + RETRY_AFTER_MAX_S.
  * an expired token (its own expiry field / JWT exp) is not sent at all: "token expired".
  * redirects are never followed (urllib would forward the Authorization header).

Credentials: the access token is read in-process, sent in one request header, and dropped. It is
never logged, written, or put in an exception message; errors are recorded by class name and HTTP
status only. Nothing here can refresh, renew or re-login.

Response shapes (no live call was made to derive them):
  * claude: Claude Code 2.1.x's own usage reader (binary strings: fetchUtilization -> GET
    /api/oauth/usage; its SDK schema for the same windows: {five_hour, seven_day, seven_day_opus,
    seven_day_sonnet, ...: {utilization: number|null (0-100), resets_at: ISO 8601|null}}).
  * codex : codex 0.153.4's RateLimitStatusPayload {plan_type, rate_limit: {allowed, limit_reached,
    primary_window, secondary_window: {used_percent, limit_window_seconds, reset_after_seconds,
    reset_at}}, credits, ...} from GET /backend-api/wham/usage (binary strings; header
    ChatGPT-Account-Id).
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import usage_meter as UM  # noqa: E402

CLAUDE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_URL = "https://chatgpt.com/backend-api/wham/usage"
USER_AGENT = "orchestraos-usage-meter"

POLL_MIN_S = 5 * 60          # [usage] poll_minutes
POLL_SLACK_S = 30            # the slot opens this much early, so a cron on the same period never skips
BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
RETRY_AFTER_MAX_S = 24 * 3600
AUTH_BACKOFF_S = 30 * 60
HTTP_TIMEOUT_S = 10
CLAUDE_WINDOWS = {"five_hour": "5h", "seven_day": "weekly", "seven_day_opus": "weekly-opus",
                  "seven_day_sonnet": "weekly-sonnet"}


class HttpResult:
    """What one GET came back with: status, headers (lower-cased names), body bytes."""

    def __init__(self, status: int, headers: dict | None = None, body: bytes = b""):
        self.status = status
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.body = body


def _no_redirect_handler():
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        # urllib forwards Authorization to a redirect target; a usage read never follows one
        def redirect_request(self, *a, **k):
            return None
    return _NoRedirect


def urllib_get(url: str, headers: dict, timeout: float, _extra_handlers=()) -> HttpResult:
    """The one live GET. Tests never reach the network: every entry point takes http_get, and the
    redirect test passes a fake transport in _extra_handlers."""
    import urllib.error
    import urllib.request
    opener = urllib.request.build_opener(_no_redirect_handler(), *_extra_handlers)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=timeout) as r:   # noqa: S310 (fixed https URLs)
            return HttpResult(r.status, dict(r.headers), r.read(1 << 20))
    except urllib.error.HTTPError as e:
        return HttpResult(e.code, dict(e.headers or {}), b"")


# ---- config ------------------------------------------------------------------------------------

def load_config(raw: dict | None = None) -> dict:
    """[usage] from orchestra.toml: poll (bool, default False), poll_minutes (default 5)."""
    if raw is None:
        try:
            sys.path.insert(0, os.path.dirname(_SCRIPTS)) if os.path.dirname(_SCRIPTS) not in sys.path else None
            from orchestra_cli.settings import config_path_for, read_toml, repo_root_from_env
            p = config_path_for(repo_root_from_env())
            raw = read_toml(p) if p.is_file() else {}
        except Exception:  # noqa: BLE001 — no config is the inert default
            raw = {}
    sec = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    mins = sec.get("poll_minutes", POLL_MIN_S // 60)
    if isinstance(mins, bool) or not isinstance(mins, (int, float)) or mins < 1:
        mins = POLL_MIN_S // 60
    return {"poll": sec.get("poll") is True, "poll_s": int(mins * 60)}


# ---- tokens (read in-process only) -------------------------------------------------------------

def _jwt_exp(token: str) -> int | None:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        exp = json.loads(base64.urlsafe_b64decode(part)).get("exp")
        return int(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None
    except Exception:  # noqa: BLE001
        return None


def claude_token(config_dir: str, now: float) -> tuple[str | None, str]:
    """(access token, "") or (None, why). Expired = None: never refreshed here."""
    p = Path(os.path.expanduser(config_dir)) / ".credentials.json"
    try:
        oauth = json.loads(p.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return None, "no Claude login in this config dir"
    tok = oauth.get("accessToken")
    if not isinstance(tok, str) or not tok:
        return None, "no Claude login in this config dir"
    exp = oauth.get("expiresAt")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and exp / 1000.0 <= now + 60:
        return None, "token expired (Claude Code refreshes it on its next use)"
    return tok, ""


def codex_token(codex_home: str, now: float) -> tuple[str | None, str | None, str]:
    """(access token, account id, "") or (None, None, why). Expired = None: never refreshed here."""
    p = Path(os.path.expanduser(codex_home)) / "auth.json"
    try:
        toks = json.loads(p.read_text()).get("tokens") or {}
    except (OSError, ValueError, AttributeError):
        return None, None, "no Codex ChatGPT login in this home"
    tok, acct = toks.get("access_token"), toks.get("account_id")
    if not isinstance(tok, str) or not tok:
        return None, None, "no Codex ChatGPT login in this home"
    exp = _jwt_exp(tok)
    if exp is not None and exp <= now + 60:
        return None, None, "token expired (Codex refreshes it on its next use)"
    return tok, acct if isinstance(acct, str) and acct else None, ""


# ---- parsers -----------------------------------------------------------------------------------

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_claude(body: dict, now: float) -> dict:
    """{label: {used_pct, resets_at}} from /api/oauth/usage. Absent or null windows are absent."""
    out = {}
    if not isinstance(body, dict):
        return out
    for key, label in CLAUDE_WINDOWS.items():
        w = body.get(key)
        if not isinstance(w, dict):
            continue
        used = UM._pct(w.get("utilization"))
        if used is None:
            continue
        out[label] = {"used_pct": used, "resets_at": UM._iso_to_epoch(w.get("resets_at"))}
    return out


def _codex_label(secs) -> str | None:
    if not _finite(secs) or secs <= 0 or secs > 10 ** 9:
        return None
    return {18000: "5h", 604800: "weekly"}.get(int(secs), f"{int(secs) // 60}m")


def parse_codex(body: dict, now: float) -> dict:
    out = {}
    rl = body.get("rate_limit") if isinstance(body, dict) else None
    if not isinstance(rl, dict):
        return out
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        used = UM._pct(w.get("used_percent"))
        label = _codex_label(w.get("limit_window_seconds")) or key.split("_")[0]
        if used is None:
            continue
        reset = UM._ts(w.get("reset_at"))
        after = w.get("reset_after_seconds")
        if reset is None and _finite(after) and 0 <= after <= 10 ** 9:
            reset = int(now + after)
        out[label] = {"used_pct": used, "resets_at": reset}
    return out


# ---- backoff -----------------------------------------------------------------------------------

def retry_after_s(value, now: float) -> int | None:
    """Retry-After as seconds from now: delta-seconds or an HTTP date; None when absent/garbled."""
    if value is None:
        return None
    v = str(value).strip()
    if v.isdigit():
        return int(v) if len(v) <= 12 else RETRY_AFTER_MAX_S
    try:
        dt = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if dt is None:
        return None
    return max(0, int(dt.timestamp() - now))


def interval_s(poll_s: int) -> int:
    """The slot length actually used: the configured interval less POLL_SLACK_S (at least 60 s)."""
    return max(60, int(poll_s) - POLL_SLACK_S)


def next_delay(kind: str, failures: int, poll_s: int, retry_after: int | None = None) -> int:
    """Seconds until the next attempt may be made, after an attempt that ended as `kind`."""
    iv = interval_s(poll_s)
    step = min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** min(max(0, failures - 1), 32))
    if kind == "ok":
        return iv
    if kind == "auth":
        return max(iv, AUTH_BACKOFF_S)
    if kind == "rate_limited" and retry_after is not None:
        return max(iv, min(RETRY_AFTER_MAX_S, retry_after))
    return max(iv, step)


# ---- per-login state (host-wide) ---------------------------------------------------------------

def lock_dir() -> Path:
    return Path(os.environ.get("ORCHESTRA_USAGE_LOCK_DIR")
                or os.path.join(os.path.expanduser("~"), ".cache", "orchestraos", "usage-poll"))


def login_file(provider: str, home: str) -> str:
    name = ".credentials.json" if provider == "claude" else "auth.json"
    return os.path.realpath(os.path.join(os.path.expanduser(home), name))


def login_key(provider: str, home: str) -> str:
    """A name for the login's slot: a hash of its login file's real path (never its contents)."""
    return hashlib.sha256(f"{provider}\0{login_file(provider, home)}".encode()).hexdigest()[:16]


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _state_path(data_dir: Path) -> Path:
    return UM.claude_usage_dir(data_dir) / "poll-state.json"


def _load_state(data_dir: Path) -> dict:
    return _read_json(_state_path(data_dir))


def _log(data_dir: Path, entry: dict) -> None:
    try:
        p = UM.claude_usage_dir(data_dir) / "poll.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ---- one account -------------------------------------------------------------------------------

def poll_account(provider: str, account: str, home: str, data_dir: Path, now: float, st: dict,
                 poll_s: int = POLL_MIN_S, http_get=urllib_get, save=lambda: None) -> dict:
    """At most one GET for this login, if its slot is open. Updates the login's state `st` in place,
    calling save() once before the request goes out and once after; returns the outcome
    {key, outcome, status?, wrote?}. Never raises for a vendor failure."""
    key = f"{provider}:{account}"
    st.setdefault("failures", 0)
    na = st.get("next_allowed") or 0
    if not _finite(na):
        na = 0
    if na > now + RETRY_AFTER_MAX_S:            # a clock that went backwards: never wait past the cap
        na = now + RETRY_AFTER_MAX_S
        st["next_allowed"] = na
    if now < na:
        return {"key": key, "outcome": "cached"}
    if provider == "claude":
        tok, why = claude_token(home, now)
        acct_id = None
    else:
        tok, acct_id, why = codex_token(home, now)
    if tok is None:
        # nothing was sent; look again next slot (the CLI may have refreshed it by then)
        st.update(last_status=why, next_allowed=now + interval_s(poll_s))
        save()
        return {"key": key, "outcome": "no-token", "reason": why}
    headers = {"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT, "Accept": "application/json"}
    if provider == "claude":
        headers["anthropic-beta"] = "oauth-2025-04-20"
        url = CLAUDE_URL
    else:
        url = CODEX_URL
        if acct_id:
            headers["ChatGPT-Account-Id"] = acct_id
    del tok
    # The attempt counts before it is made: if this process dies past here, the slot is still used
    # and the next run backs off as after a failure.
    st["failures"] = st.get("failures", 0) + 1
    st.update(last_attempt=now, last_status="in-flight",
              next_allowed=now + next_delay("error", st["failures"], poll_s))
    save()
    try:
        r = http_get(url, headers, HTTP_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — timeout / network: class name only, never the message
        headers = None
        st.update(last_status=f"error:{type(e).__name__}")
        save()
        return {"key": key, "outcome": "error", "error": type(e).__name__}
    headers = None
    if r.status == 200:
        try:
            body = json.loads(r.body or b"{}")
            windows = (parse_claude if provider == "claude" else parse_codex)(body, now) if body else {}
        except (ValueError, TypeError, OverflowError, AttributeError):
            windows = {}
        if not windows:
            st.update(last_status="200:unreadable")
            save()
            return {"key": key, "outcome": "unreadable", "status": 200}
        path = write_snapshot(data_dir, provider, account, windows, now)
        st.update(failures=0, last_ok=now, last_status="200", next_allowed=now + interval_s(poll_s))
        save()
        return {"key": key, "outcome": "ok", "status": 200, "wrote": str(path)}
    if r.status in (401, 403):
        kind = "auth"
    elif r.status in (429, 503):
        kind = "rate_limited"
    else:
        kind = "error"
    ra = retry_after_s(r.headers.get("retry-after"), now) if kind == "rate_limited" else None
    delay = next_delay(kind, st["failures"], poll_s, ra)
    st.update(last_status=str(r.status), next_allowed=now + delay)
    save()
    out = {"key": key, "outcome": kind, "status": r.status}
    if ra is not None and ra > BACKOFF_MAX_S:
        out["retry_after_s"] = ra
        _log(data_dir, {"at": int(now), "key": key, "status": r.status, "retry_after_s": ra,
                        "obeyed_s": delay, "note": "Retry-After past 1 h, obeyed"})
    return out


def write_snapshot(data_dir: Path, provider: str, account: str, windows: dict, now: float) -> Path:
    if not UM._ACCOUNT_RE.match(account):
        raise ValueError("account label must be a plain label")
    return UM._atomic_json(UM.api_snapshot_path(data_dir, provider, account),
                           {"schema": 1, "provider": provider, "account": account,
                            "windows": windows, "as_of": int(now), "source": f"{provider}-api"})


# ---- a run -------------------------------------------------------------------------------------

def poll_targets(env: dict | None = None) -> list[tuple[str, str, str]]:
    """(provider, label, home): this host's one login per provider. Claude: $CLAUDE_CONFIG_DIR, else
    ~/.claude, labelled as the status line labels it (usage_meter.account_label), so both sources
    name the same account. Codex: $CODEX_HOME, else ~/.codex, labelled "default" as the rollout rows are."""
    env = os.environ if env is None else env
    claude_home = env.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    return [("claude", UM.account_label(env.get("CLAUDE_CONFIG_DIR")), claude_home),
            ("codex", "default", env.get("CODEX_HOME") or "~/.codex")]


def run(data_dir: Path, now: float | None = None, http_get=urllib_get, config: dict | None = None,
        targets=None, locks: Path | None = None) -> dict:
    """One poll pass. Per login: take its host-wide lock (skip it if held), load its state, make at
    most one attempt (state saved before and after), release. Returns {ran, outcomes}."""
    import fcntl
    cfg = config if config is not None else load_config()
    if not cfg.get("poll"):
        return {"ran": False, "reason": "off ([usage] poll is not true)"}
    now = time.time() if now is None else now
    locks = Path(locks) if locks is not None else lock_dir()
    locks.mkdir(parents=True, exist_ok=True, mode=0o700)
    UM.claude_usage_dir(data_dir).mkdir(parents=True, exist_ok=True)
    mirror = _load_state(data_dir)
    outcomes = []
    for provider, label, home in (targets if targets is not None else poll_targets()):
        key = login_key(provider, home)
        with open(locks / f"{key}.lock", "a+") as lk:
            try:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                outcomes.append({"key": f"{provider}:{label}", "outcome": "locked"})
                continue
            st_path = locks / f"{key}.json"
            st = _read_json(st_path)

            def save(st=st, st_path=st_path, label=label, provider=provider):
                UM._atomic_json(st_path, dict(st, provider=provider, account=label))
                mirror[f"{provider}:{label}"] = dict(st)
                UM._atomic_json(_state_path(data_dir), mirror)
            try:
                outcomes.append(poll_account(provider, label, home, data_dir, now, st,
                                             cfg.get("poll_s", POLL_MIN_S), http_get, save))
            finally:
                save()
    UM.write_combined(data_dir, UM.usage_rows(data_dir, now=now), now)
    return {"ran": True, "outcomes": outcomes}


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(_SCRIPTS))
    from orchestra_cli.settings import data_dir as _dd
    print(json.dumps(run(_dd())))
