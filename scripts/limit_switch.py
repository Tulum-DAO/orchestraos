"""limit_switch: the fleet-wide Claude login switch, run by limit_watch --auto-switch.

When the ACTIVE shared login is out (a 5-hour, weekly or monthly-spend window exhausted, as
limit_event names it from the transcript rows), move every seat on the shared config dir to the
other enrolled login, the same move the dashboard's account button makes. Conditions, each one a
test (test_limit_switch.py):
  1. trigger: an open Claude episode on the active login for one of those windows, AND the other
     login's usage reading (usage_poll, fresh) is under 80% on both its 5-hour and weekly windows;
  2. never while a human is attached to a Claude seat mid-typing (tmux client attached AND the
     composer holds text, or cannot be read): the switch waits for the next run;
  3. at most one switch per 10 minutes, and never back: the login switched away from is parked
     until its window resets;
  4. every switch is logged (before/after login, the triggering window + reset, the seats on the
     dir) and mailed to gm in one line carrying the undo command;
  5. afterwards every Claude seat whose pane shows a limit banner is nudged once, naming the
     window, its reset and the new login, so it resumes now rather than at the reset;
  6. no headroom on the other login: refused, logged, and mailed to gm HIGH once per episode.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

import limit_event as LE

TRIGGER_WINDOWS = ("monthly-spend", "weekly", "5-hour")   # most severe first
HEADROOM_PCT = 80.0
MIN_GAP_S = 600
USAGE_FRESH_S = 30 * 60
_PARK_FALLBACK = {"5-hour": 5 * 3600, "weekly": 7 * 86400}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DIM = re.compile(r"\x1b\[2m.*?(?=\x1b\[|$)")


def _account_switch_cmd():
    return os.environ.get("ORCHESTRA_ACCOUNT_SWITCH") or os.path.expanduser(
        "~/scripts/agent-orchestra/scripts/account_switch.py")


def _nudge_cmd():
    return os.environ.get("LIMIT_SWITCH_NUDGE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "nudge_pane.py")


def _tmux(*args, timeout=10):
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=timeout, env=env)


class LiveDeps:
    """The real world. Tests pass a fake with the same methods."""

    def __init__(self, paths, mail_fn):
        self.paths, self.mail = paths, mail_fn

    def usage(self, account):
        try:
            with open(os.path.join(self.paths.usage_dir, f"api-claude-{account}.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def enrolled(self):
        try:
            with open(self.paths.profiles) as f:
                return [p["id"] for p in json.load(f).get("profiles", []) if p.get("enrolled") and p.get("id")]
        except (OSError, ValueError, KeyError):
            return []

    def claude_seats(self):
        """[(root, tmux_session)] for online Claude seats (they all share the fleet config dir)."""
        try:
            c = sqlite3.connect(f"file:{self.paths.registry_db}?mode=ro", uri=True, timeout=5)
            try:
                return [(r, s or r) for r, s in c.execute(
                    "SELECT k.root, k.tmux_session FROM canonical k JOIN lineages l ON l.root = k.root "
                    "WHERE l.runtime = 'claude' AND k.status = 'online'")]
            finally:
                c.close()
        except sqlite3.Error:
            return []

    def human_typing(self, session):
        """Reason string when a person is attached and mid-typing (or we cannot tell), else None."""
        cl = _tmux("list-clients", "-t", session)
        if cl.returncode != 0 or not cl.stdout.strip():
            return None                                       # no client: nobody watching
        cap = _tmux("capture-pane", "-p", "-e", "-t", session)
        if cap.returncode != 0:
            return f"{session}: attached, pane unreadable"
        lines = [l for l in cap.stdout.splitlines() if "❯" in l]
        if not lines:
            return f"{session}: attached, no composer on screen"
        typed = _ANSI.sub("", _DIM.sub("", lines[-1].split("❯", 1)[-1])).strip()
        return f"{session}: attached and typing" if typed else None

    def pane_text(self, session):
        """The bottom of the visible screen only: a banner that scrolled up belongs to a seat that
        already resumed, and nudging it would type a stale line into an idle seat."""
        cap = _tmux("capture-pane", "-p", "-t", session)
        if cap.returncode != 0:
            return ""
        lines = [l for l in cap.stdout.splitlines() if l.strip()]
        return "\n".join(lines[-15:])

    def switch(self, account):
        out = subprocess.run([sys.executable, _account_switch_cmd(), "--shared", account],
                             capture_output=True, text=True, timeout=120)
        try:
            row = json.loads(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            row = {"status": "error", "detail": (out.stderr or out.stdout)[-300:]}
        row["rc"] = out.returncode
        return row

    def nudge(self, session, text):
        out = subprocess.run([sys.executable, _nudge_cmd(), session, text],
                             capture_output=True, text=True, timeout=60)
        return out.returncode


def _park_until(ep, now):
    if ep.get("reset_at"):
        return int(ep["reset_at"])
    if ep.get("window") == "monthly-spend":
        d = datetime.fromtimestamp(now, timezone.utc)
        first = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)
        return int(first.timestamp())
    return int(now + _PARK_FALLBACK.get(ep.get("window"), 5 * 3600))


def _headroom(u, now):
    """(ok, why) for a usage_poll reading."""
    if not u:
        return False, "no usage reading"
    if now - float(u.get("as_of") or 0) > USAGE_FRESH_S:
        return False, "usage reading older than 30 min"
    w = u.get("windows") or {}
    five, week = (w.get("5h") or {}).get("used_pct"), (w.get("weekly") or {}).get("used_pct")
    if five is None or week is None:
        return False, "usage reading lacks the 5-hour or weekly window"
    ok = five < HEADROOM_PCT and week < HEADROOM_PCT
    return ok, f"5-hour {five:.0f}%, weekly {week:.0f}%"


def tick(paths, episodes, active, now, log, deps, state):
    """One decision pass. `state` (dict, persisted by the caller) carries last_switch, parked, told."""
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    told = state.setdefault("told", [])
    parked = {a: u for a, u in state.setdefault("parked", {}).items() if u > now}
    state["parked"] = parked

    def once(tag, row, mail=None):
        if tag in told:
            return
        told.append(tag)
        log(dict(row, ts=stamp))
        if mail:
            deps.mail("gm", mail[0], mail[1], mail[2])

    live = [(k, e) for k, e in episodes.items()
            if not k.startswith("_") and e.get("open") and e.get("runtime") == "claude"
            and e.get("account") == active and e.get("window") in TRIGGER_WINDOWS
            and not (e.get("reset_at") and now >= int(e["reset_at"]))]
    if not live:
        return None
    live.sort(key=lambda ke: TRIGGER_WINDOWS.index(ke[1]["window"]))
    key, ep = live[0]
    ev = LE.LimitEvent("claude", ep["window"], ep.get("reset_at"), ep.get("spend_capped", False),
                       account=active)
    line = LE.describe(ev)

    target, reasons = None, []
    for acct in deps.enrolled():
        if acct == active:
            continue
        if acct in parked:
            reasons.append(f"{acct} parked until {LE.reset_label(parked[acct])} (no switching back)")
            continue
        ok, why = _headroom(deps.usage(acct), now)
        reasons.append(f"{acct}: {why}")
        if ok:
            target = acct
            break
    if target is None:
        why = "; ".join(reasons) or "no other enrolled login"
        once(f"refused|{key}", {"kind": "switch_refused", "key": key, "from": active, "reason": why},
             ("high", f"auto-switch REFUSED: {line}",
              f"The shared Claude login {active} is out ({line}). No other login has headroom: {why}.\n"
              f"Only a refill or the reset helps now; seats wait until {LE.reset_label(ep.get('reset_at'))}.\n"))
        return "refused"
    if now - float(state.get("last_switch") or 0) < MIN_GAP_S:
        once(f"gap|{key}", {"kind": "switch_held", "key": key, "reason": "last switch under 10 min ago"})
        return "held"
    seats = deps.claude_seats()
    typing = [r for r in (deps.human_typing(s) for _, s in seats) if r]
    if typing:
        log({"ts": stamp, "kind": "switch_held", "key": key, "reason": "human mid-typing", "detail": typing[:5]})
        return "held"

    res = deps.switch(target)
    if res.get("status") != "switched":
        once(f"failed|{key}", {"kind": "switch_failed", "key": key, "from": active, "to": target, "result": res},
             ("high", f"auto-switch FAILED: {active} -> {target}",
              f"{line}\nThe switch to {target} returned {json.dumps(res)[:400]}.\n"))
        return "failed"
    state["last_switch"] = now
    parked[active] = _park_until(ep, now)
    undo = f"python3 {_account_switch_cmd()} --shared {active}"
    log({"ts": stamp, "kind": "switch_done", "key": key, "from": active, "to": target,
         "window": ep["window"], "reset_at": ep.get("reset_at"), "line": line,
         "headroom": reasons[-1] if reasons else "", "followers": res.get("followers"),
         "seats": [r for r, _ in seats], "parked_until": parked[active], "undo": undo})
    deps.mail("gm", "high", f"auto-switch: Claude login {active} -> {target}",
              f"Switched the shared Claude login {active} -> {target} ({line}; {target} {reasons[-1].split(': ', 1)[-1]}). "
              f"{res.get('followers', len(seats))} seats follow. Undo: {undo}\n")

    text = (f"[limit-watch] The shared Claude login hit its {ep['window']} limit "
            f"({LE.reset_label(ep.get('reset_at'))}); it is now {target}. Continue where you left off.")
    for root, s in seats:
        tail = deps.pane_text(s)
        if "continuing automatically" in tail.lower() or LE.from_text(tail) is not None:
            rc = deps.nudge(s, text)
            log({"ts": stamp, "kind": "seat_nudged", "seat": root, "session": s, "rc": rc, "key": key})
    return "switched"


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def prune_told(state, episodes):
    keys = {k for k in episodes if not k.startswith("_")}
    state["told"] = [t for t in state.get("told", []) if t.split("|", 1)[-1] in keys]
