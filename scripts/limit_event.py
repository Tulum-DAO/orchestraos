"""limit_event: the ONE parser for usage-limit signals, for every runtime.

Inputs, best first:
  1. a Claude transcript API-error row (`isApiErrorMessage`), whose `quotaLimits` carries the
     window (`rateLimitType`) and the reset (`resetsAt`, epoch) as data;
  2. a Codex rollout record (`token_count.rate_limits`, or a `task_complete` error);
  3. banner text from a pane, as a fallback (and for runtimes that write no structured row).

Output: a LimitEvent naming the runtime, the window (5-hour | weekly | monthly-spend | credits |
unknown), the reset time (epoch seconds, None when the vendor gave none) and whether the org's
monthly spend cap is also reached (Claude then has no overage to fall back on).

Banner rules match CLI-printed lines only: the phrase must open the line, after whitespace and
the CLI's own markers (⎿ ● ■ ⚠ ✗ • › or a "3. " menu number). Prose that quotes a banner never
counts. Progress readouts ("You've used 99% of your weekly limit", "less than 25% … left") and
recovery lines ("Usage limit reset") open with no limit phrase, so they are never events.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def operator_zone():
    """(IANA zone, label) the operator reads times in. $ORCHESTRA_OPERATOR_TZ (+ _LABEL) wins; else
    the JSON file at $ORCHESTRA_OPERATOR_LOCATION_FILE or <data dir>/state/operator-location.json
    ({"tz": ..., "label": ...}), re-read on every call so a move takes effect at once; else the
    host's zone."""
    tz = os.environ.get("ORCHESTRA_OPERATOR_TZ", "").strip()
    if tz:
        return tz, os.environ.get("ORCHESTRA_OPERATOR_TZ_LABEL", "").strip() or tz
    path = os.environ.get("ORCHESTRA_OPERATOR_LOCATION_FILE") or os.path.join(
        os.path.expanduser(os.environ.get("ORCHESTRA_DIR") or "~/.orchestra"), "state", "operator-location.json")
    try:
        with open(path) as f:
            loc = json.load(f)
        ZoneInfo(loc["tz"])
        return loc["tz"], loc.get("label") or loc["tz"]
    except (OSError, ValueError, KeyError, TypeError, Exception):  # noqa: BLE001 — any bad file => host zone
        pass
    host = datetime.now().astimezone()
    return getattr(host.tzinfo, "key", None) or "UTC", host.strftime("%Z") or "local"

WINDOWS = ("5-hour", "weekly", "monthly-spend", "credits", "unknown")


@dataclass
class LimitEvent:
    runtime: str                  # claude | codex | grok | unknown
    window: str                   # one of WINDOWS
    reset_at: int | None = None   # epoch seconds; None = the vendor gave no reset time
    spend_capped: bool = False    # Claude org monthly spend cap reached (no overage)
    source: str = "pane"          # transcript | rollout | pane
    text: str = ""
    account: str | None = None
    session_id: str | None = None
    ts: str | None = None         # when the signal was written (ISO), when known
    seat: str | None = None       # filled by the caller that knows the seat
    model: str | None = None      # a per-model credits cap ("You've reached your Fable limit")


# --- banner text -----------------------------------------------------------------------------

_PREFIX = r"^[\s⎿●■⚠✗•›❯>]*(?:\d+\.\s+)?"
_WHEN = r"(?:(?P<mon>[A-Za-z]{3,9})\s+(?P<day>\d{1,2}),?\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)"
_TZ = r"\((?P<tz>[A-Za-z_]+/[A-Za-z_/]+)\)"

_SPEND = re.compile(_PREFIX + r"You'?ve hit your org'?s monthly spend limit\b(?P<rest>.*)", re.I)
_SPEND_WINDOW = re.compile(r"your (?P<w>session|weekly) limit resets\s+" + _WHEN + r"\s*" + _TZ, re.I)
_HIT = re.compile(_PREFIX + r"You'?ve hit your (?P<w>session|weekly|usage) limit\b"
                  r"(?:\s*·\s*resets\s+" + _WHEN + r"\s*" + _TZ + r")?", re.I)
_ORG_CAP = re.compile(_PREFIX + r"Your organization'?s usage credit cap is reached", re.I)
_AUTO = re.compile(_PREFIX + r"Usage limit reached\s*·\s*continuing automatically at\s+" + _WHEN, re.I)
_AUTO_RESETS = re.compile(_PREFIX + r"Usage limit reached\s*·\s*limit resets\s+" + _WHEN, re.I)
_CONTINUING = re.compile(_PREFIX + r"Continuing automatically at\s+" + _WHEN, re.I)
_CLAUDE_PIPE = re.compile(_PREFIX + r"Claude AI usage limit reached\b(?:\|(?P<epoch>\d+))?", re.I)
# a per-model credits cap on the login (2026-11-26: Fable): invisible to the 5-hour/weekly readings
# a model FAMILY name only (prose like "your patience limit" is not one), and never after a quote mark
_MODEL_LIMIT = re.compile(r"^[\s⎿●■⚠✗•›❯]*(?:\d+\.\s+)?You'?ve reached your "
                          r"(?P<model>(?:Claude )?(?:Fable|Opus|Sonnet|Haiku|Mythos)(?: [\d.]+)?) limit\b", re.I)
_CODEX_CREDITS = re.compile(_PREFIX + r"Your workspace is out of credits\b", re.I)
_CODEX_LIMIT = re.compile(_PREFIX + r"Usage limit reached\.\s*You'?ve reached your usage limit", re.I)
_GROK_WEEKLY = re.compile(_PREFIX + r"You hit your weekly limit\b", re.I)
_REACHED = re.compile(_PREFIX + r"(?:Usage limit reached|You'?ve reached your usage limit)\b", re.I)
_CREDITS = re.compile(_PREFIX + r"(?:You'?re out of usage credits|Out of credits\b|"
                      r"(?:API )?Error\b[^\n]*?\b(?:insufficient_quota|credits? (?:depleted|exhausted))\b)", re.I)

_WINDOW_WORD = {"session": "5-hour", "weekly": "weekly", "usage": "unknown"}


def _local_tz(host_tz):
    if host_tz:
        return ZoneInfo(host_tz)
    return datetime.now().astimezone().tzinfo


def _reset_epoch(m, tz, now: datetime) -> int | None:
    """Turn a matched WHEN (+ zone) into epoch seconds: the first such moment after `now`."""
    try:
        hour = int(m.group("h")) % 12 + (12 if m.group("ap")[:1].lower() == "p" else 0)
        minute = int(m.group("m") or 0)
        local_now = now.astimezone(tz)
        if m.group("mon"):
            month = datetime.strptime(m.group("mon")[:3].title(), "%b").month
            dt = datetime(local_now.year, month, int(m.group("day")), hour, minute, tzinfo=tz)
            if dt < local_now - timedelta(days=1):          # Jan 2 seen on Dec 30
                dt = dt.replace(year=dt.year + 1)
        else:
            dt = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= local_now:
                dt += timedelta(days=1)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _zone(m):
    try:
        return ZoneInfo(m.group("tz"))
    except Exception:  # noqa: BLE001 — unknown zone name => no reset, never a guess
        return None


def _line_event(line: str, now: datetime, host_tz) -> LimitEvent | None:
    if not line.strip():
        return None
    m = _SPEND.match(line)
    if m:
        w = _SPEND_WINDOW.search(m.group("rest"))
        if w:
            tz = _zone(w)
            return LimitEvent("claude", _WINDOW_WORD[w.group("w").lower()],
                              _reset_epoch(w, tz, now) if tz else None, True, text=line.strip())
        return LimitEvent("claude", "monthly-spend", None, True, text=line.strip())
    m = _HIT.match(line)
    if m:
        reset = None
        if m.group("h"):
            tz = _zone(m)
            reset = _reset_epoch(m, tz, now) if tz else None
        return LimitEvent("claude", _WINDOW_WORD[m.group("w").lower()], reset, text=line.strip())
    if _ORG_CAP.match(line):
        return LimitEvent("claude", "monthly-spend", None, True, text=line.strip())
    m = _CLAUDE_PIPE.match(line)      # headless stream-json result: "…reached|<epoch>"
    if m:
        epoch = int(m.group("epoch") or 0)
        return LimitEvent("claude", "unknown", epoch if epoch > 10**9 else None, text=line.strip())
    m = _AUTO.match(line) or _AUTO_RESETS.match(line) or _CONTINUING.match(line)
    if m:
        return LimitEvent("claude", "unknown", _reset_epoch(m, _local_tz(host_tz), now), text=line.strip())
    m = _MODEL_LIMIT.match(line)
    if m:
        return LimitEvent("claude", "credits", None, text=line.strip(), model=m.group("model"))
    if _CODEX_CREDITS.match(line):
        return LimitEvent("codex", "credits", None, text=line.strip())
    if _CODEX_LIMIT.match(line):
        return LimitEvent("codex", "unknown", None, text=line.strip())
    if _GROK_WEEKLY.match(line):
        return LimitEvent("grok", "weekly", None, text=line.strip())
    if _REACHED.match(line):
        runtime = "codex" if line.lstrip().startswith("■") else "claude"
        return LimitEvent(runtime, "unknown", None, text=line.strip())
    if _CREDITS.match(line):
        return LimitEvent("unknown", "credits", None, text=line.strip())
    return None


def from_text(text: str, *, now: datetime | None = None, host_tz: str | None = None) -> LimitEvent | None:
    """The LAST limit banner in `text` (a pane capture or one line), else None."""
    now = now or datetime.now(timezone.utc)
    last = None
    for line in (text or "").splitlines():
        ev = _line_event(line, now, host_tz)
        if ev is not None:
            last = ev
    return last


def parse_reset(text: str, *, now: datetime | None = None) -> int | None:
    """Any 'resets <when> (<zone>)' clause in text, as epoch seconds. Readouts included: a
    '99% of your weekly limit · resets Sep 20, 7am (…)' line still carries a useful reset."""
    now = now or datetime.now(timezone.utc)
    m = re.search(r"resets\s+" + _WHEN + r"\s*" + _TZ, text or "", re.I)
    if not m:
        return None
    tz = _zone(m)
    return _reset_epoch(m, tz, now) if tz else None


# --- Claude transcript rows ------------------------------------------------------------------

_CLAUDE_WINDOW = {"five_hour": "5-hour"}


def _row_text(row) -> str:
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content or "")


def _iso_to_dt(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def from_claude_row(row: dict) -> LimitEvent | None:
    """A Claude Code transcript row -> LimitEvent, only for API-error rows."""
    if not isinstance(row, dict) or not row.get("isApiErrorMessage"):
        return None
    text = _row_text(row)
    now = _iso_to_dt(row.get("timestamp")) or datetime.now(timezone.utc)
    q = row.get("quotaLimits")
    ev = None
    if row.get("apiError") == "model_requires_usage_credits":
        # the model's credits cap: no quotaLimits, no reset; the banner text names the model
        named = from_text(text, now=now)
        ev = LimitEvent("claude", "credits", None, text=text,
                        model=named.model if named is not None else None)
    elif isinstance(q, dict) and q.get("rateLimitType"):
        rlt = str(q.get("rateLimitType"))
        window = _CLAUDE_WINDOW.get(rlt) or ("weekly" if rlt.startswith("seven_day") else "unknown")
        reset = q.get("resetsAt")
        spend = (q.get("overageStatus") == "rejected"
                 and "org_level" in str(q.get("overageDisabledReason") or "")) \
            or "monthly spend limit" in text
        ev = LimitEvent("claude", window, int(reset) if reset else None, bool(spend), text=text)
    else:
        ev = from_text(text, now=now)
        if ev is None:
            return None
        ev.runtime = "claude"
    ev.source = "transcript"
    ev.session_id = row.get("sessionId") or row.get("session_id")
    ev.ts = row.get("timestamp")
    return ev


# --- Codex rollout records -------------------------------------------------------------------

def _codex_windows(rl):
    out = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if isinstance(w, dict):
            out.append(w)
    return out


def _codex_window_name(minutes):
    return {300: "5-hour", 10080: "weekly"}.get(minutes, "unknown")


def from_codex_payload(payload: dict) -> LimitEvent | None:
    """A Codex rollout `payload` (token_count with rate_limits, or a task error) -> LimitEvent."""
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict) and err.get("codex_error_info") == "usage_limit_exceeded":
        msg = str(err.get("message") or "")
        ev = from_text(msg) or LimitEvent("codex", "unknown", None, text=msg)
        ev.runtime, ev.source = "codex", "rollout"
        return ev
    rl = payload.get("rate_limits")
    if not isinstance(rl, dict):
        return None
    reached = str(rl.get("rate_limit_reached_type") or "")
    credits = rl.get("credits") or {}
    credits_out = (rl.get("limit_id") == "premium" and credits.get("has_credits") is False
                   and not credits.get("unlimited")) or "credit" in reached
    if credits_out:
        return LimitEvent("codex", "credits", None, source="rollout", text=reached or "premium:has_credits=false")
    full = [w for w in _codex_windows(rl) if (w.get("used_percent") or 0) >= 100]
    if full:
        w = max(full, key=lambda x: x.get("window_minutes") or 0)
        return LimitEvent("codex", _codex_window_name(w.get("window_minutes")), w.get("resets_at"),
                          source="rollout", text=reached or "used_percent>=100")
    if reached:
        resets = [w.get("resets_at") for w in _codex_windows(rl) if w.get("resets_at")]
        return LimitEvent("codex", "unknown", resets[0] if resets else None, source="rollout", text=reached)
    return None


def codex_payload_is_healthy(payload: dict) -> bool:
    """True for a rate_limits record that shows the seat can work (the end of an episode)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("rate_limits"), dict):
        return False
    return from_codex_payload(payload) is None


# --- the report line -------------------------------------------------------------------------

_LABEL = {"5-hour": "5-hour limit", "weekly": "weekly limit", "monthly-spend": "monthly spend limit",
          "credits": "out of credits", "unknown": "usage limit (window not shown)"}


def reset_label(epoch: int | None, tz: str | None = None, label: str | None = None) -> str:
    if not epoch:
        return "no reset time given"
    if tz is None:
        tz, label = operator_zone()
    dt = datetime.fromtimestamp(int(epoch), ZoneInfo(tz))
    return f"resets {dt:%a %m-%d %H:%M} {label}"


def describe(ev: LimitEvent) -> str:
    """One line: runtime, window, reset in the operator's zone, account."""
    parts = [f"{ev.runtime}: {_LABEL.get(ev.window, ev.window)}"
             + (f" ({ev.model})" if getattr(ev, "model", None) else "")]
    if ev.spend_capped and ev.window != "monthly-spend":
        parts.append("org monthly spend cap also reached, so no overage")
    parts.append(reset_label(ev.reset_at))
    parts.append(f"account {ev.account or 'unknown'}")
    return " · ".join(parts)
