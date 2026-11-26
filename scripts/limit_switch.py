"""limit_switch: the fleet-wide Claude login switch, run by limit_watch --auto-switch.

When the ACTIVE shared login is out (a 5-hour, weekly or monthly-spend window exhausted, as
limit_event names it from the transcript rows), move every seat on the shared config dir to the
other enrolled login, the same move the dashboard's account button makes. Conditions, each one a
test (test_limit_switch.py):
  1. trigger, two legs: PROACTIVE, the active login's fresh usage reading is at 95% of its 5-hour
     window (or its weekly window is used up), BEFORE seats hit the banner; REACTIVE, any open limit
     banner episode on the active login (5-hour, weekly, spend, or a limit with no window named),
     whatever the usage reading says. AND the other login has headroom: under 80% on both windows.
     A model's credits cap ("You've reached your Fable limit") is NOT a login limit and never
     switches the login (operator, 2026-11-26, apr_bafb66c3): the seat that shows it drops to
     Opus 5.5 instead (model_drop.py), and a credits episode never keeps a login off the target list;
  2. never while a human is attached to a Claude seat mid-typing (tmux client attached AND the
     composer holds text, or cannot be read): the switch waits for the next run;
  3. at most one switch per 10 minutes, and never back: the login switched away from is parked
     until its window resets;
  4. every switch is logged (before/after login, the triggering window + reset, the seats on the
     dir) and mailed to gm in one line carrying the undo command;
  5. afterwards every Claude seat whose pane shows a limit banner and has no tmux client attached is
     nudged once, naming the window, model, reset and the new login, so it resumes now rather than
     at the reset; gm then hears which of them resumed, or HIGH when none did within 15 minutes;
  6. no headroom on the other login: refused, logged, and mailed to gm HIGH once per episode;
  7. weekly-aware (operator order 2026-11-26): with no trigger, when both logins have 5-hour
     headroom, the shared login moves to the one whose weekly use is at least 5 points LOWER, at
     most once per hour counting every switch, with the same human-typing hold; the old login is not
     parked (it is not out). LIMIT_SWITCH_PREFER_WEEKLY=0 turns this off;
  8. gm is told once per login per week when its weekly use reaches 70%, and again at 85%.
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

TRIGGER_WINDOWS = ("monthly-spend", "weekly", "5-hour", "unknown")   # most severe first; never "credits"
VERIFY_S = 15 * 60
NO_RESET_TRIGGER_S = 2 * 3600     # a reset-less episode (window unknown) triggers only with a row in the last 2 h
NO_RESET_BLOCK_S = 12 * 3600      # ... and keeps a login off the target list for 12 h after its last row
OWN_SWITCH_SLACK_S = 120          # a switch-log row this close to our own switch is ours, not a hand switch            # a switch whose nudged seats have not resumed by then is mailed HIGH
SWITCH_AT_5H_PCT = 95.0       # the operator's rule: switch at 95% of the 5-hour window, before the banner
HEADROOM_PCT = 80.0
MIN_GAP_S = 600
USAGE_FRESH_S = 30 * 60
NUDGE_BUDGET_S = 60           # all nudges of one run together; the cron run is killed at 300 s
MAX_NUDGES = 25
_PARK_FALLBACK = {"5-hour": 5 * 3600, "weekly": 7 * 86400}
WEEKLY_WARN_PCT = (70.0, 85.0)  # gm told once per login per week at each (operator order 2026-11-26)
PREFER_MARGIN_PCT = 5.0         # weekly preference: the other login must be this much lower
PREFER_GAP_S = 3600             # ... and never more than one switch of any kind per hour
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_DIM = re.compile(r"\x1b\[2m.*?(?=\x1b\[|$)")


def _account_switch_cmd():
    """The account switch CLI: $ORCHESTRA_ACCOUNT_SWITCH (the install sets it), else the sibling copy."""
    return os.environ.get("ORCHESTRA_ACCOUNT_SWITCH") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "account_switch.py")


def _nudge_cmd():
    return os.environ.get("LIMIT_SWITCH_NUDGE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "nudge_pane.py")


class _Failed:
    """A subprocess that could not run or timed out reads as a failure, never as an exception."""
    returncode, stdout, stderr = 124, "", ""

    def __init__(self, why):
        self.stderr = why


def _run(argv, timeout, env=None):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return _Failed(f"{type(e).__name__}: {e}"[:300])


def _tmux(*args, timeout=10):
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    return _run(["tmux", *args], timeout, env)


def last_fleet_switch(switch_log):
    """Epoch of the newest completed fleet-wide switch in logs/account-switch.jsonl (the dashboard
    button and the CLI write it too, not only this module), else 0."""
    return last_fleet_switch_row(switch_log)[0]


def last_fleet_switch_row(switch_log):
    """(epoch, login switched to) of the newest completed fleet-wide switch, else (0.0, None)."""
    try:
        with open(switch_log, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 262144))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return (0.0, None)
    best = (0.0, None)
    for line in raw.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("status") == "switched" and r.get("seat") == "*shared*":
            dt = LE._iso_to_dt(r.get("ts"))
            if dt and dt.timestamp() >= best[0]:
                best = (dt.timestamp(), r.get("to"))
    return best


class LiveDeps:
    """The real world. Tests pass a fake with the same methods."""

    def __init__(self, paths, mail_fn):
        self.paths, self.mail = paths, mail_fn

    def refresh_usage(self):
        """Run usage_poll once so the reading is fresh on every watcher run (its own cron is */5)."""
        cmd = os.environ.get("LIMIT_SWITCH_USAGE_POLL")
        if not cmd:
            return
        _run(cmd.split(), 45)

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

    def last_switch(self):
        return last_fleet_switch(self.paths.switch_log)

    def last_switch_row(self):
        return last_fleet_switch_row(self.paths.switch_log)

    def claude_seats(self):
        """[(root, tmux_session)] for online Claude seats (they all share the fleet config dir).
        None when the registry cannot be read: the caller holds, it never reads that as no seats."""
        try:
            c = sqlite3.connect(f"file:{self.paths.registry_db}?mode=ro", uri=True, timeout=5)
            try:
                return [(r, s or r) for r, s in c.execute(
                    "SELECT k.root, k.tmux_session FROM canonical k JOIN lineages l ON l.root = k.root "
                    "WHERE l.runtime = 'claude' AND k.status = 'online'")]
            finally:
                c.close()
        except sqlite3.Error:
            return None

    def attached_sessions(self):
        """Every session with a tmux client on it, or None when tmux cannot be asked."""
        cl = _tmux("list-clients", "-F", "#{client_session}")
        if cl.returncode != 0:
            return None
        return {l.strip() for l in cl.stdout.splitlines() if l.strip()}

    def human_typing(self, session, attached=None):
        """Reason string when a person is attached and mid-typing (or we cannot tell), else None."""
        if attached is None:
            attached = self.attached_sessions()
        if attached is None:
            return f"{session}: cannot list tmux clients"         # cannot tell = hold
        if session not in attached:
            return None                                       # no client: nobody watching
        cap = _tmux("capture-pane", "-p", "-e", "-t", session)
        if cap.returncode != 0:
            return f"{session}: attached, pane unreadable"
        lines = [l for l in cap.stdout.splitlines() if "❯" in l]
        if not lines:
            return f"{session}: attached, no composer on screen"
        typed = _ANSI.sub("", _DIM.sub("", lines[-1].split("❯", 1)[-1])).strip()
        return f"{session}: attached and typing" if typed else None

    def limited_seats(self):
        """Seats limit_watch holds as limited right now (state/usage/limits.json), or None when unreadable."""
        try:
            with open(self.paths.seat_rows) as f:
                return set((json.load(f).get("seats") or {}).keys())
        except (OSError, ValueError, AttributeError):
            return None

    def pane_text(self, session):
        """The bottom of the visible screen only: a banner that scrolled up belongs to a seat that
        already resumed, and nudging it would type a stale line into an idle seat."""
        cap = _tmux("capture-pane", "-p", "-t", session)
        if cap.returncode != 0:
            return ""
        lines = [l for l in cap.stdout.splitlines() if l.strip()]
        return "\n".join(lines[-15:])

    def switch(self, account):
        out = _run([sys.executable, _account_switch_cmd(), "--shared", account], 120)
        try:
            row = json.loads(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            row = {"status": "error", "detail": (out.stderr or out.stdout)[-300:]}
        row["rc"] = out.returncode
        return row

    def nudge(self, session, text):
        return _run([sys.executable, _nudge_cmd(), session, text], 20).returncode


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


def _episode_live(e, now, quiet_s):
    """An open episode still counts: before its reset, or (no reset given) with a row in the last quiet_s.
    A reset-less episode (window unknown) would otherwise count forever after its seat moved on."""
    if not (isinstance(e, dict) and e.get("open")):
        return False
    if e.get("reset_at"):
        return now < int(e["reset_at"])
    seen = LE._iso_to_dt(e.get("last_seen") or e.get("first_seen"))
    return seen is None or now - seen.timestamp() <= quiet_s


def _login_limited(episodes, acct, now):
    """An open, live Claude window episode on this login, within the 12-hour block for reset-less ones.
    A model's credits cap is not one: it is that model's, on whichever login (model_drop.py handles it)."""
    for k, e in (episodes or {}).items():
        if str(k).startswith("_") or not isinstance(e, dict) or e.get("window") == "credits":
            continue
        if e.get("runtime") != "claude" or e.get("account") != acct or not _episode_live(e, now, NO_RESET_BLOCK_S):
            continue
        return True
    return False


def _root(name):
    return re.sub(r"-g(?:en)?\d+$", "", str(name or ""))


def _check_resumed(state, now, log, deps, stamp):
    """After a switch: tell gm which nudged seats resumed (left the limited rows), or HIGH after 15 min."""
    v = state.get("verify")
    if not v or not hasattr(deps, "limited_seats"):
        return
    limited = deps.limited_seats()
    if limited is None:
        return
    lim = {_root(s) for s in limited}
    resumed = [s for s in v.get("seats", []) if _root(s) not in lim]
    if resumed:
        log({"ts": stamp, "kind": "switch_verified", "resumed": resumed, "to": v.get("to")})
        deps.mail("gm", "medium", f"auto-switch: seats resumed on {v.get('to')}",
                  f"Resumed after the switch to {v.get('to')} ({v.get('line')}): {', '.join(resumed)}.\n")
        state.pop("verify", None)
    elif now - float(v.get("since") or now) > VERIFY_S:
        log({"ts": stamp, "kind": "switch_unverified", "seats": v.get("seats"), "to": v.get("to")})
        deps.mail("gm", "high", f"auto-switch did not resume seats on {v.get('to')}",
                  f"15 minutes after the switch to {v.get('to')} ({v.get('line')}) none of the nudged seats "
                  f"left the limit: {', '.join(v.get('seats', []))}. The new login may be out too.\n")
        state.pop("verify", None)


def _told(state):
    """{tag: keep_until or None}. An older state file kept a plain list (kept until its episode closes)."""
    t = state.get("told")
    if isinstance(t, list):
        t = {tag: None for tag in t}
    state["told"] = t if isinstance(t, dict) else {}
    return state["told"]


def _poll_reset(key):
    """The reset epoch carried in a usage-reading trigger key (poll|<login>|<window>|<reset>), else None."""
    try:
        return int(float(key.rsplit("|", 1)[1]))
    except (IndexError, ValueError):
        return None


def _fresh_windows(u, now):
    """(5-hour %, weekly %, weekly reset) of a reading no older than USAGE_FRESH_S, else None."""
    if not u or now - float(u.get("as_of") or 0) > USAGE_FRESH_S:
        return None
    w = u.get("windows") or {}
    five, week = (w.get("5h") or {}).get("used_pct"), (w.get("weekly") or {}).get("used_pct")
    if five is None or week is None:
        return None
    return float(five), float(week), (w.get("weekly") or {}).get("resets_at")


def _weekly_warnings(deps, now, told, log, stamp):
    """gm hears once per login per week at each WEEKLY_WARN_PCT step (the tag lives until that week resets)."""
    for acct in deps.enrolled():
        fw = _fresh_windows(deps.usage(acct), now)
        if fw is None:
            continue
        _, week, reset = fw
        if reset and now >= float(reset):
            continue                     # a reading from before its week turned over says nothing about this week
        for pct in WEEKLY_WARN_PCT:
            tag = f"weekly|{acct}|{pct:.0f}|{reset}"
            if week < pct or tag in told:
                continue
            told[tag] = int(float(reset)) if reset else int(now + _PARK_FALLBACK["weekly"])
            log({"ts": stamp, "kind": "weekly_warn", "account": acct, "weekly": week, "step": pct,
                 "reset_at": reset})
            deps.mail("gm", "high" if pct >= 85 else "medium",
                      f"Claude weekly: {acct} at {week:.0f}% (past {pct:.0f}%)",
                      f"The Claude login {acct} has used {week:.0f}% of its weekly window "
                      f"({LE.reset_label(reset)}). Next step: "
                      + ("85% warning." if pct < 85 else "the window runs out at 100%.") + "\n")


def _prefer_weekly(active, now, log, deps, state, save, stamp, episodes=None):
    """Rule 7. Returns "preferred" | "held" | "failed" | "noop" | None (nothing to do)."""
    if os.environ.get("LIMIT_SWITCH_PREFER_WEEKLY", "1") == "0":
        return None
    me = _fresh_windows(deps.usage(active), now)
    if me is None or me[0] >= HEADROOM_PCT:
        return None
    parked = state.get("parked") or {}
    best = None
    for acct in deps.enrolled():
        if acct == active or acct in parked or _login_limited(episodes, acct, now):
            continue                     # never move to a login with a live limit
        fw = _fresh_windows(deps.usage(acct), now)
        if fw is None or fw[0] >= HEADROOM_PCT or fw[1] >= HEADROOM_PCT:
            continue
        if me[1] - fw[1] >= PREFER_MARGIN_PCT and (best is None or fw[1] < best[1][1]):
            best = (acct, fw)
    if best is None:
        return None
    target, fw = best
    last = max(float(state.get("last_switch") or 0), float(state.get("last_attempt") or 0),
               float(deps.last_switch() if hasattr(deps, "last_switch") else 0))
    if now - last < PREFER_GAP_S:
        return None
    why = f"weekly preference: {active} weekly {me[1]:.0f}% vs {target} {fw[1]:.0f}%, both under {HEADROOM_PCT:.0f}% 5-hour"
    seats = deps.claude_seats()
    attached = deps.attached_sessions() if hasattr(deps, "attached_sessions") else ()
    if seats is None or attached is None:
        log({"ts": stamp, "kind": "switch_held", "reason": "seat registry or tmux clients unreadable", "why": why})
        return "held"
    typing = [r for r in ((deps.human_typing(s, attached) if hasattr(deps, "attached_sessions")
                           else deps.human_typing(s)) for _, s in seats) if r]
    if typing:
        log({"ts": stamp, "kind": "switch_held", "reason": "human mid-typing", "why": why, "detail": typing[:5]})
        return "held"
    state["last_attempt"] = now
    if save:
        save(state)
    res = deps.switch(target)
    if res.get("status") == "noop":
        log({"ts": stamp, "kind": "switch_noop", "from": active, "to": target, "reason": why, "result": res})
        return "noop"
    if res.get("status") != "switched":
        log({"ts": stamp, "kind": "switch_failed", "from": active, "to": target, "reason": why, "result": res})
        deps.mail("gm", "high", f"weekly-preference switch FAILED: {active} -> {target}",
                  f"{why}\nThe switch returned {json.dumps(res)[:400]}. It retries after an hour.\n")
        return "failed"
    state["last_switch"], state["last_to"] = now, target
    if save:
        save(state)
    undo = f"python3 {_account_switch_cmd()} --shared {active}"
    log({"ts": stamp, "kind": "switch_done", "from": active, "to": target, "reason": why,
         "followers": res.get("followers"), "seats": [r for r, _ in seats], "undo": undo})
    deps.mail("gm", "medium", f"weekly preference: Claude login {active} -> {target}",
              f"Moved the shared Claude login {active} -> {target} to spread the weekly window ({why}). "
              f"{res.get('followers', len(seats))} seats follow. Undo: {undo}\n")
    return "preferred"


def tick(paths, episodes, active, now, log, deps, state, save=None):
    """One decision pass. `state` (dict, persisted by the caller) carries last_switch, last_attempt,
    parked, told. `save(state)`, when given, is called the moment a switch has happened, so a later
    failure in the same run (a nudge, the cron timeout) cannot lose last_switch or the parked login."""
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    told = _told(state)
    parked = {a: u for a, u in state.setdefault("parked", {}).items() if u > now}
    state["parked"] = parked
    key = None
    _check_resumed(state, now, log, deps, stamp)

    def once(tag, row, mail=None):
        if tag in told:
            return
        # a usage-reading trigger has no episode to close it: its tags live until the reset in the key
        # (or 5 hours when the reading carried none); an episode's tags live until the episode is pruned
        told[tag] = (max(_poll_reset(key) or int(now + _PARK_FALLBACK["5-hour"]), int(now + 3600))
                     if key and key.startswith("poll|") else None)
        log(dict(row, ts=stamp))
        if mail:
            deps.mail("gm", mail[0], mail[1], mail[2])

    # PROACTIVE leg (the operator, 2026-11-25): the active login's own usage reading. At 95% of its
    # 5-hour window (or a weekly window used up) switch BEFORE any seat hits the banner.
    # REACTIVE leg (gm order 2026-11-26): any open window-limit banner episode on the active login,
    # whatever the reading says. Never a credits cap (TRIGGER_WINDOWS): that seat drops to Opus 5.5.
    if hasattr(deps, "refresh_usage"):
        deps.refresh_usage()
    au = deps.usage(active)
    fresh = bool(au) and now - float(au.get("as_of") or 0) <= USAGE_FRESH_S
    aw = (au or {}).get("windows") or {}
    a5, aweek = (aw.get("5h") or {}).get("used_pct"), (aw.get("weekly") or {}).get("used_pct")
    others = {}
    for acct in deps.enrolled():
        if acct != active:
            ow = (deps.usage(acct) or {}).get("windows") or {}
            others[acct] = {"5h": (ow.get("5h") or {}).get("used_pct"),
                            "weekly": (ow.get("weekly") or {}).get("used_pct"),
                            "weekly-fable": (ow.get("weekly-fable") or {}).get("used_pct")}
    check = {"ts": stamp, "kind": "switch_check", "active": active, "active_fresh": fresh,
             "active_5h": a5, "active_weekly": aweek,
             "active_weekly_fable": (aw.get("weekly-fable") or {}).get("used_pct"), "others": others}
    _weekly_warnings(deps, now, told, log, stamp)
    key = ep = None
    hot = fresh and a5 is not None and (a5 >= SWITCH_AT_5H_PCT or (aweek is not None and aweek >= 100))
    if hot:
        window = "5-hour" if a5 >= SWITCH_AT_5H_PCT else "weekly"
        reset = (aw.get("5h" if window == "5-hour" else "weekly") or {}).get("resets_at")
        if reset and now >= float(reset):
            # a reading taken before its own reset (usage_poll backing off): that window is already new
            check["decision"] = f"no switch: {active} {window} reading predates its reset"
            log(check)
            return None
        key = f"poll|{active}|{window}|{reset}"
        ep = {"window": window, "reset_at": reset, "spend_capped": False}
        check["decision"] = f"trigger: {active} {window} at {a5 if window == '5-hour' else aweek:.0f}%"
    if key is None:
        # REACTIVE leg: a banner on the active login switches whatever the usage reading says
        live = [(k, e) for k, e in episodes.items()
                if not k.startswith("_") and e.get("open") and e.get("runtime") == "claude"
                and e.get("account") == active and e.get("window") in TRIGGER_WINDOWS
                and _episode_live(e, now, NO_RESET_TRIGGER_S)]
        if live:
            live.sort(key=lambda ke: TRIGGER_WINDOWS.index(ke[1]["window"]))
            key, ep = live[0]
            check["decision"] = (f"trigger (banner): {ep['window']}"
                                 + (f" ({ep['model']})" if ep.get("model") else ""))
    if key is None:
        check["decision"] = (f"no switch: {active} 5-hour {a5:.0f}% < {SWITCH_AT_5H_PCT:.0f}%"
                             if fresh and a5 is not None else "no switch: no fresh reading, no banner episode")
        log(check)
        return _prefer_weekly(active, now, log, deps, state, save, stamp, episodes)
    log(check)
    ev = LE.LimitEvent("claude", ep["window"], ep.get("reset_at"), ep.get("spend_capped", False),
                       account=active, model=ep.get("model"))
    line = LE.describe(ev)
    first = state.setdefault("first_trigger", {})
    first.setdefault(key, now)
    for k in [k for k, t in first.items() if now - t > 8 * 86400]:
        del first[k]
    if hasattr(deps, "last_switch_row"):
        ts, to = deps.last_switch_row()
        # our own switch's log row: same destination, written within the slack of our switch. A hand
        # switch back goes to the OTHER login, so it never matches however soon it comes
        ours = (to == state.get("last_to")
                and abs(ts - float(state.get("last_switch") or 0)) <= OWN_SWITCH_SLACK_S)
        if to == active and ts > first[key] and not ours:
            # someone moved the fleet TO this login by hand after it was already hot: their choice stands
            once(f"override|{key}", {"kind": "switch_held", "key": key, "reason": "switched back by hand"},
                 ("medium", f"auto-switch holds: {active} chosen by hand",
                  f"The shared Claude login was switched to {active} by hand while it was out ({line}). "
                  f"The auto-switch leaves it there until {LE.reset_label(ep.get('reset_at'))}.\n"))
            return "held"

    target, reasons = None, []
    for acct in deps.enrolled():
        if acct == active:
            continue
        if acct in parked:
            reasons.append(f"{acct} parked until {LE.reset_label(parked[acct])} (no switching back)")
            continue
        if _login_limited(episodes, acct, now):
            reasons.append(f"{acct}: has a live limit episode of its own")
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
    # the gap counts every switch: this module's, a failed attempt of its own (back off, never re-run
    # the switch every 2 minutes), and one made by hand (the dashboard button, the CLI)
    last = max(float(state.get("last_switch") or 0), float(state.get("last_attempt") or 0),
               float(deps.last_switch() if hasattr(deps, "last_switch") else 0))
    if now - last < MIN_GAP_S:
        once(f"gap|{key}", {"kind": "switch_held", "key": key, "reason": "last switch or attempt under 10 min ago"})
        return "held"
    seats = deps.claude_seats()
    if seats is None:
        log({"ts": stamp, "kind": "switch_held", "key": key, "reason": "seat registry unreadable"})
        return "held"
    attached = deps.attached_sessions() if hasattr(deps, "attached_sessions") else ()
    if attached is None:
        log({"ts": stamp, "kind": "switch_held", "key": key, "reason": "tmux clients unreadable"})
        return "held"
    typing = [r for r in ((deps.human_typing(s, attached) if hasattr(deps, "attached_sessions")
                           else deps.human_typing(s)) for _, s in seats) if r]
    if typing:
        log({"ts": stamp, "kind": "switch_held", "key": key, "reason": "human mid-typing", "detail": typing[:5]})
        return "held"

    state["last_attempt"] = now
    if save:
        save(state)
    res = deps.switch(target)
    if res.get("status") == "noop":
        # the target already holds the shared login: our idea of the active login was wrong
        once(f"noop|{key}", {"kind": "switch_noop", "key": key, "from": active, "to": target, "result": res},
             ("medium", f"auto-switch: {target} already holds the shared login",
              f"The watcher read the active login as {active} ({line}), but {target} already holds it. "
              f"Check state/account_locations.json for more than one shared row.\n"))
        return "noop"
    if res.get("status") != "switched":
        once(f"failed|{key}", {"kind": "switch_failed", "key": key, "from": active, "to": target, "result": res},
             ("high", f"auto-switch FAILED: {active} -> {target}",
              f"{line}\nThe switch to {target} returned {json.dumps(res)[:400]}.\n"))
        return "failed"
    state["last_switch"], state["last_to"] = now, target
    parked[active] = _park_until(ep, now)
    if save:
        save(state)
    undo = f"python3 {_account_switch_cmd()} --shared {active}"
    log({"ts": stamp, "kind": "switch_done", "key": key, "from": active, "to": target,
         "window": ep["window"], "model": ep.get("model"), "reset_at": ep.get("reset_at"), "line": line,
         "headroom": reasons[-1] if reasons else "", "followers": res.get("followers"),
         "seats": [r for r, _ in seats], "parked_until": parked[active], "undo": undo})
    text = (f"[limit-watch] The shared Claude login hit its {ep['window']} limit"
            + (f" ({ep['model']})" if ep.get("model") else "")
            + f" ({LE.reset_label(ep.get('reset_at'))}); it is now {target}. Continue where you left off.")
    t0, nudged = time.monotonic(), []
    for root, s in seats:
        if len(nudged) >= MAX_NUDGES or time.monotonic() - t0 > NUDGE_BUDGET_S:
            log({"ts": stamp, "kind": "nudge_budget_spent", "key": key, "sent": len(nudged)})
            break
        if attached and s in attached:
            continue                     # a person is on that pane: they see the banner and the switch
        tail = deps.pane_text(s)
        if "continuing automatically" in tail.lower() or LE.from_text(tail) is not None:
            rc = deps.nudge(s, text)
            nudged.append(root)
            log({"ts": stamp, "kind": "seat_nudged", "seat": root, "session": s, "rc": rc, "key": key})
    if nudged:
        state["verify"] = {"seats": nudged, "since": now, "to": target, "line": line}
        if save:
            save(state)
    deps.mail("gm", "high", f"auto-switch: Claude login {active} -> {target}",
              f"Switched the shared Claude login {active} -> {target} ({line}; {target} {reasons[-1].split(': ', 1)[-1]}). "
              f"{res.get('followers', len(seats))} seats follow; nudged {', '.join(nudged) or 'none'} "
              f"(gm hears when they resume). Undo: {undo}\n")
    return "switched"


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def prune_told(state, episodes, now=None):
    """Drop a tag once its episode is gone, or, for a usage-reading trigger, once its reset passed."""
    now = time.time() if now is None else now
    keys = {k for k in episodes if not k.startswith("_")}
    state["told"] = {t: until for t, until in _told(state).items()
                     if (until is not None and now < until) or (until is None and t.split("|", 1)[-1] in keys)}
