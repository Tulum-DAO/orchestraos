"""notify_policy.py — may an alert go to a person now? Settings › Notifications (settings spec §4).

The person's choices live in <data dir>/state/users/<user>/prefs.json, written by the API (api/src/lib/settingsRegistry.ts):
  notify_level   "all" | "needs_me" | "nothing"   absent or unknown = "all", what every install did before the page
  notify_agents  {"<agent id>": level}             one agent's own level; it wins over notify_level
  quiet_hours    {"from": "HH:MM", "to": "HH:MM"}  in the install time zone ([operator] timezone; empty = UTC);
                                                   absent, null, or from == to = off. May cross midnight.

Event kinds: "needs_me" (a decision card, a questionnaire, a permission prompt, a human blocker), "finished",
"failed" (failed or stuck) and "missed_call". Only "needs_me" has a sender today (approval_notify.py); the others are
here so a sender added later asks the same question. An unknown kind counts as not waiting on you (All only).

  Nothing   no alert of any kind
  Needs me  only "needs_me"
  All       every kind
  Quiet hours: no alert of any kind while they last (no exceptions).

decide() answers why, so a sender can act on it:
  "ok"          send now
  "suppressed"  the person does not want this one (Nothing, or Needs me for something else). The sender marks it
                handled and never sends it later, so switching back to All does not flood old cards.
  "quiet"       quiet hours: hold it and send it once they end (a sender with many held sends one summary).
The level is checked first: a card from an agent set to Nothing is "suppressed" even inside quiet hours.

An agent's own level matches its rotated names too: an override for "dev-3" applies to "dev-3-gen4" (the exact id
first, then the id with its -genN suffix stripped, as approval.py _canon does).

Whose prefs: the install's operator, `[operator] id` in orchestra.toml (default "operator"). That is the same name the
API files Settings under while it trusts no identity headers (api/src/lib/principal.ts), which is how every install
runs today, so the page and the sender read one file. The server's pushes go to one topic that every subscribed phone
reads; nothing ties a push to one person, so a multi-user install would still use the operator's choices here.

should_alert() is the one decision, pure. allowed() reads the files and never raises: if anything cannot be read it
answers True, which is the behaviour before this page existed. api/src/lib/notifyPolicy.ts is the TypeScript twin;
both run every case in contract/notify-policy-cases.json.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

LEVELS = ("all", "needs_me", "nothing")
DEFAULT_LEVEL = "all"
EVENTS = ("needs_me", "finished", "failed", "missed_call")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,120}$")
_GEN_SUFFIX = re.compile(r"-gen\d+$")          # approval.py _canon
OK, QUIET, SUPPRESSED = "ok", "quiet", "suppressed"
_warned_zones: set = set()


def _level(v) -> str | None:
    return v if isinstance(v, str) and v in LEVELS else None


def level_for(prefs: dict, agent: str | None) -> str:
    """The level that applies to an alert from `agent`: its own, else the main one, else All."""
    prefs = prefs if isinstance(prefs, dict) else {}
    agents = prefs.get("notify_agents")
    if agent and isinstance(agents, dict):
        for name in (agent, _GEN_SUFFIX.sub("", agent.strip())):
            own = _level(agents.get(name))
            if own:
                return own
    return _level(prefs.get("notify_level")) or DEFAULT_LEVEL


def _minutes(hhmm) -> int | None:
    m = _HHMM.match(hhmm) if isinstance(hhmm, str) else None
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _zone(tz: str | None):
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz)
        except Exception as e:  # noqa: BLE001 — an unknown zone reads as UTC, as the app shows times
            if tz not in _warned_zones:
                _warned_zones.add(tz)
                print(f"[notify_policy] time zone {tz!r} unusable ({e}); quiet hours use UTC", file=sys.stderr)
    return timezone.utc


def in_quiet_hours(quiet, now_utc: datetime, tz: str | None) -> bool:
    """True while `now_utc`, in the install's zone, is inside [from, to). Off when unset, malformed or from == to."""
    if not isinstance(quiet, dict):
        return False
    start, end = _minutes(quiet.get("from")), _minutes(quiet.get("to"))
    if start is None or end is None or start == end:
        return False
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local = now_utc.astimezone(_zone(tz))
    t = local.hour * 60 + local.minute
    return start <= t < end if start < end else (t >= start or t < end)


def decide(prefs: dict, event: str, agent: str | None, now_utc: datetime, tz: str | None) -> str:
    """The decision, with its reason. Pure: the person's prefs, what happened, from which agent, when, in the
    install's zone. "ok", "suppressed" or "quiet" (module doc)."""
    prefs = prefs if isinstance(prefs, dict) else {}
    level = level_for(prefs, agent)
    if level == "nothing" or (level == "needs_me" and event != "needs_me"):
        return SUPPRESSED
    return QUIET if in_quiet_hours(prefs.get("quiet_hours"), now_utc, tz) else OK


def should_alert(prefs: dict, event: str, agent: str | None, now_utc: datetime, tz: str | None) -> bool:
    return decide(prefs, event, agent, now_utc, tz) == OK


# ------------------------------------------------------------------------------------------------- reading the files

def _data_dir() -> Path:
    from approval_config import orchestra_dir
    return Path(orchestra_dir())


def _config() -> dict:
    root = Path(os.environ.get("ORCHESTRA_ROOT") or Path(__file__).resolve().parent.parent)
    path = Path(os.environ.get("ORCHESTRA_CONFIG") or root / "orchestra.toml")
    import tomllib
    with path.open("rb") as f:
        return tomllib.load(f)


def install_user_and_zone(raw: dict | None = None) -> tuple[str, str]:
    """(the operator's user name, the install time zone or ""), as the API reads them (lib/config.ts operatorId)."""
    raw = _config() if raw is None else raw
    op = raw.get("operator") if isinstance(raw, dict) else None
    op = op if isinstance(op, dict) else {}
    user = op.get("id") if isinstance(op.get("id"), str) and op.get("id") else "operator"
    tz = op.get("timezone") if isinstance(op.get("timezone"), str) else ""
    return user, tz


def load_prefs(data_dir: Path, user: str) -> dict:
    """A missing or unreadable file is no choices (the defaults); a bad user name too."""
    if not _USER_RE.match(user or "") or ".." in user:
        return {}
    try:
        d = json.loads((Path(data_dir) / "state" / "users" / user / "prefs.json").read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def decision(event: str, agent: str | None, now: datetime | None = None, *, data_dir: Path | None = None,
             config: dict | None = None) -> str:
    """decide() for the install's operator, from the files. Never raises; "ok" when anything is unreadable."""
    try:
        user, tz = install_user_and_zone(config)
        prefs = load_prefs(data_dir if data_dir is not None else _data_dir(), user)
        return decide(prefs, event, agent, now or datetime.now(timezone.utc), tz)
    except Exception as e:  # noqa: BLE001 — a broken read must never silence alerts that went out before
        print(f"[notify_policy] could not read notification settings, alerting as before: {e}", file=sys.stderr)
        return OK


def current_level(agent: str | None, *, data_dir: Path | None = None, config: dict | None = None) -> str:
    """The level that applies to `agent` now, from the files (for the sender's audit stamp). "all" when unreadable."""
    try:
        user, _ = install_user_and_zone(config)
        return level_for(load_prefs(data_dir if data_dir is not None else _data_dir(), user), agent)
    except Exception:  # noqa: BLE001
        return DEFAULT_LEVEL


def allowed(event: str, agent: str | None, now: datetime | None = None, **kw) -> bool:
    return decision(event, agent, now, **kw) == OK
