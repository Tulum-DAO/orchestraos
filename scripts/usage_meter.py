"""usage_meter.py: how much of each provider's plan limit is used, from local files only.

One row per provider and window:
    {provider, account, window, used_pct, resets_at, as_of, stale, source, reason}

Sources (never a vendor API call, never a login token, never a guessed number):
  * codex : the newest `token_count` event that carries `rate_limits` in any recent rollout under
            ~/.codex/sessions. Limits belong to the account, so the freshest event from any thread is
            the reading. primary = the 5 h window, secondary = the weekly one.
  * claude: the `rate_limits` block Claude Code passes its status line (verified by effect on
            2.1.284: five_hour / seven_day, used_percentage + resets_at). hooks/statusline.py
            records it in <data dir>/state/usage/claude-<account>.json; this module only reads it.
            The first redraw of a session carries no block, so an absent block is "unknown",
            never 0.
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
    return float(v) if 0 <= v <= 100 else None


def _ts(v) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None
    return int(v)


def _row(provider, window, used_pct, resets_at, as_of, source, now, account=None, reason=None):
    stale = as_of is None or now - as_of > STALE_S
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


def claude_account() -> str:
    """A plain-words label for this Claude login, never a path: the config dir's name without its
    dots and "claude" ("~/.claude-work" -> "work"); "default" for the usual ~/.claude."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "").rstrip("/")
    name = os.path.basename(cfg).lstrip(".") if cfg else ""
    if name.lower().startswith("claude"):
        name = name[len("claude"):]
    name = name.strip("-_. ")
    return name if name and _ACCOUNT_RE.match(name) else "default"


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


def write_claude_record(record: dict, data_dir: Path, account: str) -> Path:
    import tempfile
    out = claude_usage_dir(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"claude-{account}.json"
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
                rows.append(_row("claude", label, _pct(w["used_pct"]), _ts(w.get("resets_at")), at,
                                 "claude-statusline", now, account=account))
    if not rows:
        return [_empty("claude", "Claude Code has not reported usage yet (it does after a reply, "
                                 "through the OrchestraOS status line)", now, "claude-statusline")]
    return rows


# ---- all ---------------------------------------------------------------------------------------

def usage_rows(data_dir: Path, providers=None, now: float | None = None,
               codex_sessions_dir: Path | None = None) -> list[dict]:
    """Rows for the enabled providers (all four when None), in a stable order."""
    now = time.time() if now is None else now
    wanted = [p for p in ("claude", "codex", "gemini", "grok") if providers is None or p in providers]
    rows = []
    for p in wanted:
        if p == "claude":
            rows += claude_rows(now, data_dir)
        elif p == "codex":
            rows += codex_rows(now, codex_sessions_dir)
        else:
            rows.append(_empty(p, NO_SOURCE[p], now))
    return rows


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from orchestra_cli.settings import data_dir
    print(json.dumps(usage_rows(data_dir()), indent=1))
