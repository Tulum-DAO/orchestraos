"""profile_switcher.py — auto-switch algorithm for Claude Code "profiles".

Ported from hamed-elfayome/Claude-Usage-Tracker (MenuBarManager.swift
checkAutoSwitchIfNeeded / switchToNextProfile, and TerminalLauncherService's
use of CLAUDE_CONFIG_DIR as the per-profile login boundary). Claude Code
keeps exactly ONE login per CLAUDE_CONFIG_DIR, so a "profile" here is just a
config directory plus bookkeeping about when it was last seen exhausted.

INERT BY DEFAULT: every function in this module only reads/writes its own
JSON state file (state/claude_profiles.json) and its own log file
(logs/profile-switcher.log) under the given orchestra_dir. Nothing here
touches ~/.claude, ~/.claude.json, environment variables of the running
process, spawn-agent.sh, or any other live file. `env_for_active()` is the
only function a caller is meant to consume, and even that just RETURNS a
dict — it never calls os.environ or exec's anything.

FLEET SAFETY RULE: this module never alters a live process's auth. A later,
separately-gated spawn caller is responsible for applying
CLAUDE_CONFIG_DIR (from env_for_active) at spawn time only. Wiring this
into the live fleet is congruence-gated and out of scope for this module.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_ORCHESTRA_DIR = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/orchestra")


def _state_path(orchestra_dir: str) -> str:
    return os.path.join(orchestra_dir, "state", "claude_profiles.json")


def _log_path(orchestra_dir: str) -> str:
    return os.path.join(orchestra_dir, "logs", "profile-switcher.log")


def load_state(orchestra_dir: str) -> dict:
    path = _state_path(orchestra_dir)
    if not os.path.exists(path):
        return {"enabled": False, "active": None, "profiles": []}
    with open(path, "r") as f:
        return json.load(f)


def save_state(orchestra_dir: str, state: dict) -> None:
    path = _state_path(orchestra_dir)
    dir_ = os.path.dirname(path)
    os.makedirs(dir_, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".claude_profiles.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _find_profile(state: dict, profile_id):
    for p in state.get("profiles", []):
        if p["id"] == profile_id:
            return p
    return None


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def is_exhausted(profile: dict, now: datetime) -> bool:
    eu = profile.get("exhausted_until")
    if eu is None:
        return False
    return now < _parse_iso(eu)


def mark_exhausted(orchestra_dir: str, profile_id: str, reset_at_iso, now: datetime) -> dict:
    state = load_state(orchestra_dir)
    p = _find_profile(state, profile_id)
    if p is None:
        return state
    if reset_at_iso is None:
        reset_at_iso = (now + timedelta(days=7)).isoformat()
    p["exhausted_until"] = reset_at_iso
    save_state(orchestra_dir, state)
    return state


def clear_if_recovered(orchestra_dir: str, now: datetime) -> dict:
    state = load_state(orchestra_dir)
    changed = False
    for p in state.get("profiles", []):
        eu = p.get("exhausted_until")
        if eu is not None and _parse_iso(eu) <= now:
            p["exhausted_until"] = None
            p["auto_switched"] = False
            changed = True
    if changed:
        save_state(orchestra_dir, state)
    return state


def next_available(state: dict, after_profile_id, now: datetime):
    profiles = state.get("profiles", [])
    n = len(profiles)
    if n < 2:
        return None
    ids = [p["id"] for p in profiles]
    try:
        idx = ids.index(after_profile_id)
    except ValueError:
        idx = -1
    for i in range(1, n + 1):
        cand = profiles[(idx + i) % n]
        if not is_exhausted(cand, now):
            return cand
    return None


def _log(orchestra_dir: str, decision: dict) -> None:
    path = _log_path(orchestra_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(decision) + "\n")


def check_auto_switch(orchestra_dir: str, current_profile_id: str, exhausted: bool, now: datetime, reset_at_iso=None) -> dict:
    state = load_state(orchestra_dir)

    if not state.get("enabled"):
        decision = {"action": "noop", "reason": "disabled"}
        _log(orchestra_dir, decision)
        return decision

    if len(state.get("profiles", [])) < 2:
        decision = {"action": "noop", "reason": "single-profile"}
        _log(orchestra_dir, decision)
        return decision

    current = _find_profile(state, current_profile_id)
    if current is None:
        decision = {"action": "noop", "reason": "unknown-profile"}
        _log(orchestra_dir, decision)
        return decision

    if not exhausted:
        if current.get("auto_switched"):
            current["auto_switched"] = False
            save_state(orchestra_dir, state)
        decision = {"action": "noop", "reason": "not-exhausted"}
        _log(orchestra_dir, decision)
        return decision

    if current.get("auto_switched"):
        decision = {"action": "noop", "reason": "already-switched"}
        _log(orchestra_dir, decision)
        return decision

    mark_exhausted(orchestra_dir, current_profile_id, reset_at_iso, now)
    state = load_state(orchestra_dir)
    current = _find_profile(state, current_profile_id)
    current["auto_switched"] = True
    save_state(orchestra_dir, state)

    nxt = next_available(state, current_profile_id, now)
    if nxt is None:
        decision = {"action": "stay", "reason": "all-exhausted", "active": current_profile_id}
        _log(orchestra_dir, decision)
        return decision

    state["active"] = nxt["id"]
    save_state(orchestra_dir, state)
    decision = {"action": "switch", "from": current_profile_id, "to": nxt["id"], "config_dir": nxt["config_dir"]}
    _log(orchestra_dir, decision)
    return decision


def env_for_active(orchestra_dir: str) -> dict:
    state = load_state(orchestra_dir)
    if not state.get("enabled") or not state.get("active"):
        return {}
    p = _find_profile(state, state["active"])
    if p is None:
        return {}
    return {"CLAUDE_CONFIG_DIR": p["config_dir"]}


# --- exhaustion text detection -----------------------------------------
#
# Claude Code pane text falls into three shapes we care about:
#   1. "You've used 99% of your weekly limit"      -> NOT exhausted yet.
#      Any "<N>% of your <weekly|daily|session> limit" phrasing is a
#      progress readout, not a hard stop.
#   2. "Usage limit reached" / "You've reached your usage limit" ->
#      exhausted, no reset time given in-line.
#   3. "weekly limit · resets Sep 20, 7am (America/New_York)" -> exhausted,
#      with a parseable reset time. The presence of a "resets <date>,
#      <time> (<tz>)" clause (once the percent-progress case above has
#      been ruled out) itself signals the limit was hit — Claude Code only
#      shows a reset countdown once a limit is reached.
_NOT_EXHAUSTED_RE = re.compile(r"\b\d{1,3}%\s+of\s+your\s+(?:weekly|daily|session)\s+limit\b", re.I)
_REACHED_RES = [
    re.compile(r"usage limit reached", re.I),
    re.compile(r"reached (?:your|the) usage limit", re.I),
    re.compile(r"\blimit reached\b", re.I),
]
_RESETS_RE = re.compile(
    r"resets\s+([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*\(([^)]+)\)",
    re.I,
)


def detect_exhaustion_text(pane_text: str):
    """Return (exhausted: bool, reset_at_iso: str|None).

    reset_at is parsed UNCONDITIONALLY (a pre-limit status line like
    "You've used 99% … · resets Sep 20, 7am" still carries a useful reset time);
    a not-exhausted match ("NN% of your … limit", NN<100) only forces
    exhausted=False — it never short-circuits the reset parse."""
    not_exhausted = _NOT_EXHAUSTED_RE.search(pane_text) is not None
    reached = (not not_exhausted) and any(r.search(pane_text) for r in _REACHED_RES)

    reset_iso = None
    m = _RESETS_RE.search(pane_text)
    if m:
        month_str, day_str, hour_str, min_str, ampm, tzname = m.groups()
        try:
            month = datetime.strptime(month_str[:3], "%b").month
            day = int(day_str)
            hour = int(hour_str)
            minute = int(min_str) if min_str else 0
            if ampm.lower() == "pm" and hour != 12:
                hour += 12
            if ampm.lower() == "am" and hour == 12:
                hour = 0
            tz = ZoneInfo(tzname.strip())
            year = datetime.now(tz).year
            dt = datetime(year, month, day, hour, minute, tzinfo=tz)
            reset_iso = dt.isoformat()
        except Exception:
            reset_iso = None

    # Exhaustion is decided ONLY by explicit "limit reached" phrasing. A bare
    # "resets <date>" clause is NOT proof: Claude Code shows the reset countdown
    # BEFORE the limit is hit (by effect: "You've used 99% of your weekly limit ·
    # resets Sep 20, 7am (America/New_York)" appeared on a still-working pane).
    # Treating it as exhausted would false-switch accounts. The clause is used
    # only to supply reset_at, returned in either case so a caller can pre-plan.
    return (reached, reset_iso)


def main():
    parser = argparse.ArgumentParser(prog="profile_switcher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("env")
    check_p = sub.add_parser("check")
    check_p.add_argument("--profile", required=True)
    check_p.add_argument("--exhausted", action="store_true")
    check_p.add_argument("--reset-at", default=None)

    args = parser.parse_args()
    orchestra_dir = DEFAULT_ORCHESTRA_DIR

    if args.cmd == "status":
        print(json.dumps(load_state(orchestra_dir), indent=2))
    elif args.cmd == "env":
        for k, v in env_for_active(orchestra_dir).items():
            print(f"{k}={v}")
    elif args.cmd == "check":
        now = datetime.utcnow()
        decision = check_auto_switch(orchestra_dir, args.profile, args.exhausted, now, args.reset_at)
        print(json.dumps(decision))


if __name__ == "__main__":
    main()
