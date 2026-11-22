"""usage_meter.py: how much of each provider's plan limit is used, from local files only.

One row per provider and window:
    {provider, account, window, used_pct, resets_at, as_of, stale, source, reason}

This module never makes a vendor API call and never reads a login token. scripts/usage_poll.py
does the (opt-in, cached, backed-off) calls and leaves api-<provider>-<account>.json snapshots in
<data dir>/state/usage/; this module reads them like any other local file. Per provider, account
and WINDOW the newest reading wins, so the local sources below stay the zero-call fallback.

Sources (never a guessed number):
  * codex : the newest `token_count` event that carries `rate_limits` in any recent rollout under
            ~/.codex/sessions. Limits belong to the account, so the freshest event from any thread is
            the reading. primary = the 5 h window, secondary = the weekly one.
  * claude: the `rate_limits` block Claude Code passes its status line (verified by effect on
            2.1.284: five_hour / seven_day, used_percentage + resets_at). hooks/statusline.py
            records it in <data dir>/state/usage/claude-<account>.json; this module only reads it.
            The first redraw of a session carries no block, so an absent block is "unknown",
            never 0.
  * api    : api-claude-<account>.json / api-codex-<account>.json from usage_poll (source
            "claude-api" / "codex-api"), already in window labels ("5h", "weekly").
  * gemini, grok: no local source yet. A row with used_pct None and a plain reason.

A reading older than STALE_S is marked stale: shown greyed with "as of <time>", never as current.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

STALE_S = 30 * 60
FUTURE_SKEW_S = 300                  # an as_of further ahead than this is a wrong clock: stale
AGE_OUT_S = 14 * 24 * 3600           # a login with no reading for this long leaves the view (its file stays)
DROP_GRACE_S = 15 * 60               # a lower reading for the same window is refused only this long
PENDING_REASON = "first reading in a moment"
CODEX_LOOKBACK_S = 8 * 24 * 3600     # a weekly window plus a day: older rollouts cannot be fresher
CODEX_MAX_FILES = 12                 # the newest rollouts; the freshest event among them wins
TAIL_BYTES = 262144
NO_SOURCE = {
    "gemini": "Gemini CLI reports no usage we can read",
    "grok": "Grok reports no usage we can read",
}
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _pct(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if 0 <= v <= 100 else None          # NaN and +-Infinity fail the range


def _ts(v) -> int | None:
    """A positive finite epoch, else None (json reads Infinity and 1e400 as inf: int() would raise)."""
    import math
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return None
    return int(v)


def _row(provider, window, used_pct, resets_at, as_of, source, now, account=None, reason=None):
    stale = as_of is None or now - as_of > STALE_S or as_of - now > FUTURE_SKEW_S
    return {"provider": provider, "account": account, "window": window,
            "used_pct": used_pct, "resets_at": resets_at, "as_of": as_of,
            "stale": bool(stale) if used_pct is not None else None,
            "source": source, "reason": reason}


def _empty(provider, reason, now, source=None):
    return _row(provider, None, None, None, None, source, now, reason=reason)


# ---- codex -------------------------------------------------------------------------------------

def _iso_to_epoch(s) -> int | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def latest_codex_rate_limits(path) -> tuple[dict, int] | None:
    """(rate_limits, event epoch) of the LAST token_count event in the rollout tail that carries
    rate_limits, else None."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode(errors="replace")
    except OSError:
        return None
    best = None
    for line in tail.splitlines():
        if '"rate_limits"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "event_msg":
            continue
        p = d.get("payload")
        if not isinstance(p, dict) or p.get("type") != "token_count":
            continue
        rl = p.get("rate_limits")
        at = _iso_to_epoch(d.get("timestamp"))
        if isinstance(rl, dict) and at is not None:
            best = (rl, at)
    return best


def _codex_rollouts(sessions_dir: Path, now: float) -> list[Path]:
    """The newest rollouts, read from the per-day folders Codex keeps (sessions/YYYY/MM/DD) for the
    lookback only, so a refresh never walks a long-lived sessions tree. Day folders are in local
    time, so one extra day on each side covers any time zone."""
    from datetime import date, timedelta
    days = int(CODEX_LOOKBACK_S // 86400) + 2
    today = date.fromtimestamp(now)
    found = []
    for i in range(-1, days):
        d = today - timedelta(days=i)
        folder = sessions_dir / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("rollout-") and e.name.endswith(".jsonl") and e.is_file():
                try:
                    m = e.stat().st_mtime
                except OSError:
                    continue
                if now - m <= CODEX_LOOKBACK_S:
                    found.append((m, Path(e.path)))
    found.sort(reverse=True)
    return [p for _m, p in found[:CODEX_MAX_FILES]]


def codex_rows(now: float, sessions_dir: Path | None = None) -> list[dict]:
    sessions_dir = sessions_dir or Path(os.path.expanduser("~/.codex/sessions"))
    best = None
    for p in _codex_rollouts(sessions_dir, now):
        r = latest_codex_rate_limits(p)
        if r and (best is None or r[1] > best[1]):
            best = r
    if best is None:
        return [_empty("codex", "no Codex session has reported its limits yet", now, "codex-rollout")]
    rl, at = best
    rows = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        used = _pct(w.get("used_percent"))
        if used is None:
            continue
        mins = w.get("window_minutes")
        window = {300: "5h", 10080: "weekly"}.get(mins, f"{mins}m" if isinstance(mins, int) else key)
        rows.append(_row("codex", window, used, _ts(w.get("resets_at")), at, "codex-rollout", now))
    return rows or [_empty("codex", "Codex reported limits without a usage number", now, "codex-rollout")]


# ---- claude ------------------------------------------------------------------------------------

def claude_usage_dir(data_dir: Path) -> Path:
    return Path(data_dir) / "state" / "usage"


def account_label(config_dir: str | None) -> str:
    """A plain-words label for a Claude login, never a path: the config dir's name without its
    dots and "claude" ("~/.claude-work" -> "work"); "default" for the usual ~/.claude. usage_poll
    labels its snapshots the same way, so the status line and the API name one login alike."""
    cfg = (config_dir or "").rstrip("/")
    name = os.path.basename(cfg).lstrip(".") if cfg else ""
    if name.lower().startswith("claude"):
        name = name[len("claude"):]
    name = name.strip("-_. ")
    return name if name and _ACCOUNT_RE.match(name) else "default"


def claude_account() -> str:
    """This process's Claude login label: account_label($CLAUDE_CONFIG_DIR)."""
    return account_label(os.environ.get("CLAUDE_CONFIG_DIR", ""))


def claude_record_from_statusline(d: dict, now: float) -> dict | None:
    """The record hooks/statusline.py writes, or None when the input carries no usable block."""
    rl = d.get("rate_limits") if isinstance(d, dict) else None
    if not isinstance(rl, dict):
        return None
    windows = {}
    for key in ("five_hour", "seven_day"):
        w = rl.get(key)
        if isinstance(w, dict) and _pct(w.get("used_percentage")) is not None:
            windows[key] = {"used_pct": _pct(w.get("used_percentage")),
                            "resets_at": _ts(w.get("resets_at"))}
    if not windows:
        return None
    return {"windows": windows, "as_of": int(now),
            "version": d.get("version") if isinstance(d.get("version"), str) else None}


def monotonic_record(old, new: dict, grace_s: int = DROP_GRACE_S) -> dict | None:
    """The record to write over `old`. Every window carries its own as_of (when it was read).

    Within one window (same resets_at) usage normally only rises, so a LOWER used_pct for the same
    reset is usually an older block (an idle seat redrawing late): the old window is kept, with its
    OWN as_of, never re-stamped as current, so it greys out as stale on its own clock. A real drop
    (a plan upgrade mid-window, a provider-side reset that keeps resets_at) is refused only while the
    old reading is younger than grace_s; after that the lower reading is taken. None when every window
    of `new` is such a step back: nothing to write."""
    at = _ts(new.get("as_of")) if isinstance(new, dict) else None
    windows = {k: (dict(w, as_of=at) if isinstance(w, dict) and at is not None else w)
               for k, w in ((new.get("windows") or {}) if isinstance(new, dict) else {}).items()}
    new = dict(new, windows=windows)
    if not isinstance(old, dict) or not isinstance(old.get("windows"), dict):
        return new
    old_at = _ts(old.get("as_of"))
    out, fresh = {}, False
    for key, w in windows.items():
        o = old["windows"].get(key)
        o_at = (_ts(o.get("as_of")) or old_at) if isinstance(o, dict) else None
        if isinstance(o, dict) and isinstance(w, dict) and w.get("resets_at") is not None \
                and o.get("resets_at") == w.get("resets_at") \
                and _pct(o.get("used_pct")) is not None and _pct(w.get("used_pct")) is not None \
                and _pct(o["used_pct"]) > _pct(w["used_pct"]) \
                and o_at is not None and (at is None or at - o_at < grace_s):
            out[key] = dict(o, as_of=o_at)
        else:
            out[key] = w
            fresh = True
    if not fresh:
        return None
    return dict(new, windows=out)


def write_claude_record(record: dict, data_dir: Path, account: str) -> Path | None:
    """Write the status-line record atomically; None when monotonic_record says it is a step back.

    Several status lines (one per session) write the same account's file: the read-modify-write runs
    under an exclusive flock on a sidecar lock file (.claude-<account>.json.lock; the data file itself
    is replaced by rename, so its inode cannot carry the lock)."""
    import fcntl
    import tempfile
    out = claude_usage_dir(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"claude-{account}.json"
    with open(out / f".claude-{account}.json.lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = None
        record = monotonic_record(old, record)
        if record is None:
            return None
        fd, tmp = tempfile.mkstemp(prefix=f".claude-{account}.", dir=str(out))
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(record, fh)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    return path


def claude_rows(now: float, data_dir: Path) -> list[dict]:
    rows = []
    for p in sorted(claude_usage_dir(data_dir).glob("claude-*.json")):
        account = p.stem[len("claude-"):]
        if not _ACCOUNT_RE.match(account):
            continue
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        at = _ts(rec.get("as_of")) if isinstance(rec, dict) else None
        windows = rec.get("windows") if isinstance(rec, dict) else None
        if at is None or not isinstance(windows, dict):
            continue
        for key, label in (("five_hour", "5h"), ("seven_day", "weekly")):
            w = windows.get(key)
            if isinstance(w, dict) and _pct(w.get("used_pct")) is not None:
                w_at = min(_ts(w.get("as_of")) or at, at)     # a window kept from an older block keeps its time
                if now - w_at > AGE_OUT_S:
                    continue
                rows.append(_row("claude", label, _pct(w["used_pct"]), _ts(w.get("resets_at")), w_at,
                                 "claude-statusline", now, account=account))
    if not rows:
        return [_empty("claude", "Claude Code has not reported usage yet (it does after a reply, "
                                 "through the OrchestraOS status line)", now, "claude-statusline")]
    return rows


# ---- api snapshots (written by usage_poll) -----------------------------------------------------

API_PROVIDERS = ("claude", "codex")


def api_snapshot_path(data_dir: Path, provider: str, account: str) -> Path:
    return claude_usage_dir(data_dir) / f"api-{provider}-{account}.json"


def api_rows(now: float, data_dir: Path, provider: str) -> list[dict]:
    """Rows from usage_poll's snapshots for one provider: {windows: {label: {used_pct, resets_at}},
    as_of}. A malformed file or window is skipped, never read as 0."""
    rows = []
    for p in sorted(claude_usage_dir(data_dir).glob(f"api-{provider}-*.json")):
        account = p.stem[len(f"api-{provider}-"):]
        if not _ACCOUNT_RE.match(account):
            continue
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        at = _ts(rec.get("as_of")) if isinstance(rec, dict) else None
        windows = rec.get("windows") if isinstance(rec, dict) else None
        if at is None or not isinstance(windows, dict) or now - at > AGE_OUT_S:
            continue
        for label in sorted(windows, key=_window_order):
            w = windows[label]
            if isinstance(label, str) and isinstance(w, dict) and _pct(w.get("used_pct")) is not None:
                rows.append(_row(provider, label, _pct(w["used_pct"]), _ts(w.get("resets_at")), at,
                                 f"{provider}-api", now, account=account))
    return rows


def _window_order(label) -> tuple:
    return ({"5h": 0, "weekly": 1}.get(label, 2), str(label))


def _newest_per_window(*sources: list[dict]) -> list[dict]:
    """Per account AND window, the row with the newest NON-stale reading (as_of); a stale one (too old,
    or stamped in the future by a wrong clock) wins only when there is no fresh one. A newer source
    that has only some windows (a status line with just 5 h) never hides the others (the API's weekly
    rows). A row with no number never hides one that has a number. Accounts keep first-seen order;
    windows run 5 h, weekly, then the rest."""
    rank = lambda r: (not r.get("stale"), r.get("as_of") or 0)  # noqa: E731
    best: dict = {}
    order: list = []
    for rows in sources:
        for r in rows:
            if r.get("used_pct") is None:
                continue
            k = (r.get("account"), r.get("window"))
            if r.get("account") not in order:
                order.append(r.get("account"))
            if k not in best or rank(r) > rank(best[k]):
                best[k] = r
    out = []
    for acct in order:
        out += sorted((r for (a, _w), r in best.items() if a == acct), key=lambda r: _window_order(r.get("window")))
    return out


# ---- all ---------------------------------------------------------------------------------------

def usage_rows(data_dir: Path, providers=None, now: float | None = None,
               codex_sessions_dir: Path | None = None, polling: bool = False,
               agents_account: str | None = None) -> list[dict]:
    """Rows for the enabled providers (all four when None), in a stable order.

    polling: usage_poll is on, so a Claude/Codex row with no reading yet says PENDING_REASON
    ("pending": true) in place of the local source's reason. Every row with a number says whether it is
    the login the agents use ("agents"): for Claude agents_account, else this process's own login
    (claude_account: $CLAUDE_CONFIG_DIR, else ~/.claude = "default"); for Codex "default" ($CODEX_HOME)."""
    now = time.time() if now is None else now
    wanted = [p for p in ("claude", "codex", "gemini", "grok") if providers is None or p in providers]
    rows = []
    for p in wanted:
        if p == "claude":
            local = claude_rows(now, data_dir)
        elif p == "codex":
            local = codex_rows(now, codex_sessions_dir)
            # the rollout reading carries no account; it is the default login's (~/.codex)
            for r in local:
                if r.get("used_pct") is not None and r.get("account") is None:
                    r["account"] = "default"
        else:
            rows.append(_empty(p, NO_SOURCE[p], now))
            continue
        merged = _newest_per_window(api_rows(now, data_dir, p), local)
        if merged:
            agents = (agents_account or claude_account()) if p == "claude" else "default"
            for r in merged:
                r["agents"] = r.get("account") == agents
        elif polling:
            local = [dict(r, reason=PENDING_REASON, pending=True) for r in local]
        rows += merged or local
    return rows


def write_combined(data_dir: Path, rows: list[dict], now: float | None = None) -> Path:
    """<data dir>/state/usage/usage.json: every row, one file, written atomically. Rows hold labels
    and numbers only (no token, no e-mail): that is all usage_rows ever produces."""
    return _atomic_json(claude_usage_dir(data_dir) / "usage.json",
                        {"as_of": int(time.time() if now is None else now), "rows": rows})


def _atomic_json(path: Path, obj) -> Path:
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from orchestra_cli.settings import data_dir
    print(json.dumps(usage_rows(data_dir()), indent=1))
