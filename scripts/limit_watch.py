#!/usr/bin/env python3
"""limit_watch: name every usage-limit hit, tell the right party, keep a dry-run record (cron */2).

Reads only NEW bytes since the last run (cursor per file) from:
  * Claude transcripts  <claude config>/projects/*/*.jsonl : API-error rows (structured quotaLimits)
  * Codex rollouts      <codex home>/sessions/**/rollout-*.jsonl : rate_limits records + task errors
and parses them with limit_event (the one parser). Per run it writes:
  * logs/limit-events.jsonl          one line per new episode, per newly limited seat, per recovery,
                                     and the switch decision the auto-switch WOULD take (dry run)
  * state/usage/limits.json          the seat rows: who is limited now, window, reset (operator's zone), account
  * state/initiatives.sidecar.json   items in initiatives.json's shape (gm merges; never the main file)
With --mail it also sends:
  * weekly / monthly-spend limits (and the first spend-capped 5-hour hit per account per month):
    one HIGH mail to gm per episode;
  * a Codex seat limited: one mail per order owner (pending task/review mail addressed to the seat),
    naming the window + reset and the configured Claude fallback seat; when the episode ends, a
    "back" mail to the same owners. With "reroute": true in state/limit_failover.json the order is
    also forwarded to the fallback seat with the limited seat's baton path.
5-hour hits are logged and shown on the seat row only (gm ruling).

--codex-watch tracks whether Codex serves (codex_watch.py) and flips the reviewer rule both ways.

--auto-switch arms the fleet-wide Claude login switch (limit_switch.py: trigger, human-typing
hold, 10-minute gap, no switching back, one-line undo mail, nudge of banner-stuck seats, refusal
mail when the other login has no headroom). Without it the log carries `switch_decision` lines
only (what it would do, and why).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_SCRIPTS)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
if _REPO not in sys.path:
    sys.path.append(_REPO)
import limit_event as LE  # noqa: E402

FRESH_SECONDS = 15 * 60       # only files written in the last 15 min are opened
NO_RESET_ROW_TTL = 12 * 3600  # a limit with no reset time (a credits cap) leaves the seat rows after 12 h quiet
MAX_READ = 4 * 1024 * 1024    # per file per run; a cursor never re-reads old bytes
_UUID_TAIL = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
_GEN_SUFFIX = re.compile(r"-g\d+$")


def _data_dir() -> str:
    env = os.environ.get("ORCHESTRA_DIR")
    if env:
        return os.path.expanduser(env)
    try:
        from orchestra_cli.settings import data_dir
        return str(data_dir())
    except Exception:  # noqa: BLE001
        return os.path.expanduser("~/.orchestra")


class Paths:
    def __init__(self, orch=None, claude_dir=None, codex_home=None):
        self.orch = orch or _data_dir()
        self.claude_projects = os.path.join(
            claude_dir or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")
        self.codex_sessions = os.path.join(
            codex_home or os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")
        st = os.path.join(self.orch, "state")
        self.state_dir = os.path.join(st, "limit_watch")
        self.cursors = os.path.join(self.state_dir, "cursors.json")
        self.episodes = os.path.join(self.state_dir, "episodes.json")
        self.seat_rows = os.path.join(st, "usage", "limits.json")
        self.sidecar = os.path.join(st, "initiatives.sidecar.json")
        self.initiatives = os.path.join(st, "initiatives.json")
        self.registry_db = os.path.join(st, "orchestra-registry.db")
        self.switch_log = os.path.join(self.orch, "logs", "account-switch.jsonl")
        self.profiles = os.path.join(st, "claude_profiles.json")
        self.usage_dir = os.path.join(st, "usage")
        self.failover_cfg = os.path.join(st, "limit_failover.json")
        self.log = os.path.join(self.orch, "logs", "limit-events.jsonl")


# --- small io helpers --------------------------------------------------------------------------

def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def _append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _local(epoch_or_none, fmt="%Y-%m-%dT%H:%M"):
    """Epoch -> the operator's wall clock (initiatives.json keeps operator-local times)."""
    if not epoch_or_none:
        return ""
    return datetime.fromtimestamp(int(epoch_or_none), ZoneInfo(LE.operator_zone()[0])).strftime(fmt)


# --- reading new lines -------------------------------------------------------------------------

def _fresh_files(pattern, now):
    out = []
    for p in glob.glob(pattern, recursive=True):
        try:
            if now - os.path.getmtime(p) <= FRESH_SECONDS:
                out.append(p)
        except OSError:
            continue
    return sorted(out)


def new_lines(path, cursors, *, start_at_end):
    """Complete lines written since the cursor. A file seen for the first time starts at its END
    (no flood of old episodes) unless start_at_end is False (backfill)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    pos = cursors.get(path)
    if pos is None:
        pos = size if start_at_end else 0
    if pos > size:              # truncated / replaced
        pos = 0
    if size == pos:
        cursors[path] = pos
        return []
    with open(path, "rb") as f:
        f.seek(pos)
        data = f.read(min(size - pos, MAX_READ))
        last_nl = data.rfind(b"\n")
        if last_nl < 0:
            if len(data) < MAX_READ:
                return []                       # a line still being written: wait for its newline
            # one line longer than a whole chunk: skip it to its newline, or the cursor never moves again
            skipped = pos + len(data)
            while True:
                piece = f.read(1 << 20)
                if not piece:
                    return []                   # no newline yet: next run starts over at pos
                nl = piece.find(b"\n")
                if nl >= 0:
                    cursors[path] = skipped + nl + 1
                    return []
                skipped += len(piece)
    cursors[path] = pos + last_nl + 1
    return data[: last_nl + 1].decode("utf-8", "replace").splitlines()


# --- who and which account ---------------------------------------------------------------------

def seat_for_session(paths, sid):
    if not sid:
        return None
    try:
        c = sqlite3.connect(f"file:{paths.registry_db}?mode=ro", uri=True, timeout=5)
        try:
            row = c.execute("SELECT root FROM generations WHERE session_id=? ORDER BY id DESC LIMIT 1",
                            (sid,)).fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return None
    return _GEN_SUFFIX.sub("", row[0]) if row else None


def shared_holder(paths):
    """The login the fleet's shared config dir holds NOW, by the switch's own ledger
    (state/account_locations.json, the where=="shared" row), else None."""
    acc = _read_json(os.path.join(paths.orch, "state", "account_locations.json"), {}).get("accounts")
    if isinstance(acc, dict):
        for a, r in acc.items():            # the first shared row, exactly as account_switch reads it
            if isinstance(r, dict) and r.get("where") == "shared":
                return a
    return None


def claude_account_at(paths, ts_iso):
    """The shared Claude login in force at ts: the last switch at or before it, else the profile."""
    best = None
    try:
        with open(paths.switch_log) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("status") == "switched" and r.get("seat") == "*shared*" and (
                        not ts_iso or str(r.get("ts", "")) <= _norm_ts(ts_iso)):
                    best = r.get("to")
    except OSError:
        pass
    return best or _read_json(paths.profiles, {}).get("active") or "unknown"


def _norm_ts(ts):
    dt = LE._iso_to_dt(ts)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds") if dt else str(ts)


# --- one pass ----------------------------------------------------------------------------------

def scan(paths, now, cursors, *, start_at_end=True):
    """-> (events: list[LimitEvent with .seat], recoveries: set[(runtime, sid)])"""
    events, recovered = [], set()
    for p in _fresh_files(os.path.join(paths.claude_projects, "*", "*.jsonl"), now):
        for line in new_lines(p, cursors, start_at_end=start_at_end):
            if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            ev = LE.from_claude_row(row)
            sid = row.get("sessionId") or row.get("session_id")
            if ev is None:
                if not row.get("isApiErrorMessage"):
                    recovered.add(("claude", sid))
                continue
            ev.account = claude_account_at(paths, ev.ts)
            ev.seat = seat_for_session(paths, sid) or f"session {str(sid)[:8]}"
            events.append(ev)
    for p in _fresh_files(os.path.join(paths.codex_sessions, "**", "rollout-*.jsonl"), now):
        m = _UUID_TAIL.search(p)
        sid = m.group(1) if m else None
        for line in new_lines(p, cursors, start_at_end=start_at_end):
            if "rate_limits" not in line and "usage_limit_exceeded" not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            payload = obj.get("payload") or {}
            ev = LE.from_codex_payload(payload)
            if ev is None:
                if LE.codex_payload_is_healthy(payload):
                    recovered.add(("codex", sid))
                continue
            ev.ts, ev.session_id, ev.account = obj.get("timestamp"), sid, "codex workspace"
            ev.seat = seat_for_session(paths, sid) or f"session {str(sid)[:8]}"
            events.append(ev)
    return events, recovered


def episode_key(ev):
    when = str(ev.reset_at) if ev.reset_at else (ev.ts or "")[:10]
    return f"{ev.runtime}|{ev.account}|{ev.window}|{when}"


def _mail_worthy(ev, episodes):
    if ev.window in ("weekly", "monthly-spend"):
        return True
    if ev.runtime == "claude" and ev.window == "credits":     # a model's credits cap: no reset comes
        return True
    if ev.runtime == "claude" and ev.spend_capped:
        month = (ev.ts or "")[:7]
        flag = f"spendcap|{ev.account}|{month}"
        if flag not in episodes.get("_spendcap_told", []):
            episodes.setdefault("_spendcap_told", []).append(flag)
            return True
    return False


def switch_decision(paths, ev):
    """What the fleet-wide login switch WOULD do for this Claude episode (dry run, never acts)."""
    if ev.runtime != "claude" or not (ev.window in ("weekly", "monthly-spend", "credits")):
        return None
    profiles = [p.get("id") for p in _read_json(paths.profiles, {}).get("profiles", []) if p.get("enrolled")]
    others = [p for p in profiles if p and p != ev.account]
    best, why = None, "no other enrolled login"
    for acct in others:
        u = _read_json(os.path.join(paths.usage_dir, f"api-claude-{acct}.json"), {})
        w = (u.get("windows") or {})
        five, week = (w.get("5h") or {}).get("used_pct"), (w.get("weekly") or {}).get("used_pct")
        if five is None or week is None:
            why = f"{acct}: no usage reading"
            continue
        if five < 80 and week < 80:         # the armed switch's own headroom rule (limit_switch)
            best, why = acct, f"{acct} has headroom (5-hour {five:.0f}%, weekly {week:.0f}%)"
            break
        why = f"{acct} has no headroom (5-hour {five:.0f}%, weekly {week:.0f}%)"
    return {"would_switch": bool(best), "from": ev.account, "to": best, "reason": why}


def run_once(paths, *, now=None, mail_fn=None, start_at_end=True, inbox_fn=None, switch_deps=None,
             codex_watch=None):
    now = now or time.time()
    cursors = _read_json(paths.cursors, {})
    episodes = _read_json(paths.episodes, {})
    rows = _read_json(paths.seat_rows, {}).get("seats", {})
    cfg = _read_json(paths.failover_cfg, {})
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    events, recovered = scan(paths, now, cursors, start_at_end=start_at_end)

    for ev in events:
        key = episode_key(ev)
        ep = episodes.get(key)
        line = LE.describe(ev)
        if ep is not None and not ep.get("open"):
            # the same window hit again after a recovery (a credits key is per day): the episode is live again
            ep["open"] = True
            _append(paths.log, {"ts": stamp, "kind": "episode_reopen", "key": key, "line": line})
        if ep is None:
            ep = episodes[key] = {"runtime": ev.runtime, "window": ev.window, "reset_at": ev.reset_at,
                                  "account": ev.account, "first_seen": ev.ts or stamp, "seats": [],
                                  "spend_capped": ev.spend_capped, "model": ev.model,
                                  "owners_told": [], "open": True}
            _append(paths.log, {"ts": stamp, "kind": "episode_open", "key": key, "line": line,
                                "runtime": ev.runtime, "window": ev.window, "reset_at": ev.reset_at,
                                "reset_local": _local(ev.reset_at), "account": ev.account,
                                "spend_capped": ev.spend_capped, "model": ev.model, "source": ev.source,
                                "text": ev.text[:300]})
            dec = switch_decision(paths, ev)
            if dec is not None:
                _append(paths.log, dict(dec, ts=stamp, kind="switch_decision", key=key,
                                        armed=switch_deps is not None))
            if mail_fn and _mail_worthy(ev, episodes):
                mail_fn("gm", "high", f"usage limit: {line}",
                        f"{line}\nFirst seen {ev.ts or stamp} on {ev.seat}.\n"
                        + (f"Switch decision (dry run, not armed): {dec['reason']}\n" if dec else ""))
        ep["last_seen"] = ev.ts or stamp
        if ev.seat not in ep["seats"]:
            ep["seats"].append(ev.seat)
            _append(paths.log, {"ts": stamp, "kind": "seat_limited", "key": key, "seat": ev.seat,
                                "session": ev.session_id, "line": line})
            if ev.runtime == "codex" and mail_fn:
                _tell_order_owners(paths, ev, ep, cfg, mail_fn, inbox_fn)
        rows[ev.seat] = {"runtime": ev.runtime, "window": ev.window, "reset_at": ev.reset_at,
                         "reset_label": LE.reset_label(ev.reset_at), "account": ev.account,
                         "since": ep["first_seen"], "line": line, "session": ev.session_id, "key": key,
                         "last_seen": ev.ts or stamp}

    # recovery: by effect (a healthy record / a normal reply on the same session) or the reset passed
    for seat, r in list(rows.items()):
        healed = (r.get("runtime"), r.get("session")) in recovered
        expired = r.get("reset_at") and now >= int(r["reset_at"]) + 120
        seen = LE._iso_to_dt(r.get("last_seen") or r.get("since"))
        quiet = not r.get("reset_at") and seen is not None and now - seen.timestamp() > NO_RESET_ROW_TTL
        if healed or expired or quiet:
            del rows[seat]
            _append(paths.log, {"ts": stamp, "kind": "seat_recovered", "seat": seat, "key": r.get("key"),
                                "how": "healthy record" if healed else
                                       "reset time passed" if expired else "no new limit row for 12 h"})
    for key, ep in episodes.items():
        if key.startswith("_") or not ep.get("open"):
            continue
        if not any(r.get("key") == key for r in rows.values()):
            ep["open"] = False
            _append(paths.log, {"ts": stamp, "kind": "episode_closed", "key": key})
            if mail_fn and ep.get("owners_told"):
                for owner in ep["owners_told"]:
                    mail_fn(owner, "medium", f"back: {', '.join(ep['seats'])} can work again",
                            f"The {ep['window']} limit episode on {ep['account']} is over "
                            f"({', '.join(ep['seats'])}). Orders can go back to the seat.\n")

    _write_json(paths.seat_rows, {"as_of": stamp, "seats": rows})
    _write_sidecar(paths, rows, now)
    _prune(episodes, now)
    _write_json(paths.episodes, episodes)
    _write_json(paths.cursors, {p: o for p, o in cursors.items() if os.path.exists(p)})

    if switch_deps is not None:          # --auto-switch: the fleet-wide login switch (limit_switch.py)
        import limit_switch as LS
        st_path = os.path.join(paths.state_dir, "switch.json")
        st = LS.load_state(st_path)
        active = shared_holder(paths) or claude_account_at(paths, None)
        try:
            LS.tick(paths, episodes, active, now, lambda row: _append(paths.log, row), switch_deps, st,
                    save=lambda s: _write_json(st_path, s))
        except Exception as e:           # the watcher's own state is already written; log and keep the switch state
            _append(paths.log, {"ts": stamp, "kind": "switch_error", "error": f"{type(e).__name__}: {e}"[:300]})
        LS.prune_told(st, episodes, now)
        _write_json(st_path, st)

    if codex_watch is not None:          # --codex-watch: who gates reviews (codex_watch.py); mails always on
        import codex_watch as CW
        try:
            CW.tick(paths, events, now, codex_watch, lambda row: _append(paths.log, row))
        except Exception as e:           # noqa: BLE001 — never takes the limit watcher down with it
            _append(paths.log, {"ts": stamp, "kind": "codex_watch_error", "error": f"{type(e).__name__}: {e}"[:300]})
    return {"events": len(events), "limited_seats": sorted(rows)}


def _tell_order_owners(paths, ev, ep, cfg, mail_fn, inbox_fn):
    root = _GEN_SUFFIX.sub("", ev.seat)
    fallback = (cfg.get("codex_fallback") or {}).get(root)
    pending = [m for m in (inbox_fn or _inbox)(root) if m.get("type") in ("task", "review", "task_request")]
    for m in pending:
        owner = m.get("from_agent")
        if not owner or owner == root:
            continue
        note = (f"{root} is limited: {LE.describe(ev)}.\nYour order {m.get('id')} "
                f"'{str(m.get('subject'))[:80]}' is waiting on it.\n")
        if fallback and cfg.get("reroute"):
            mail_fn(fallback, "high", f"re-routed from {root} (limited): {str(m.get('subject'))[:60]}",
                    f"{root} is limited ({LE.describe(ev)}). This order was sent to it by {owner}; "
                    f"take it with its context: docs/HANDOFF_{root}-next.md.\n\n--- original order "
                    f"{m.get('id')} ---\n{m.get('body') or ''}\n")
            note += f"It was re-routed to {fallback} with the seat's baton. It comes back on reset.\n"
        elif fallback:
            note += f"Configured fallback seat: {fallback}. Re-route it there, or wait for the reset.\n"
        else:
            note += "No fallback seat is configured; re-route it to a Claude seat by hand, or wait.\n"
        if owner not in ep["owners_told"]:
            ep["owners_told"].append(owner)
        mail_fn(owner, "high", f"{root} limited: {ev.window}, {LE.reset_label(ev.reset_at)}", note)


def _inbox(agent):
    try:
        out = subprocess.run([sys.executable, _msg_store(), "inbox", "--agent", agent, "--limit", "50"],
                             capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout or "{}").get("messages", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def _msg_store():
    return os.environ.get("LIMIT_WATCH_MSG_STORE") or os.path.join(_REPO, "msg_store.py")


def send_mail(to, priority, subject, body):
    fd, path = tempfile.mkstemp(prefix="limit-watch-", suffix=".md")
    with os.fdopen(fd, "w") as f:
        f.write(body)
    try:
        subprocess.run([sys.executable, _msg_store(), "send", "--from", "limit-watch", "--to", to,
                        "--type", "status", "--priority", priority, "--subject", subject[:120],
                        "--body-file", path], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)


def _write_sidecar(paths, rows, now):
    """Items in initiatives.json's shape. gm merges this file; nothing here writes the main one."""
    stamp = _local(now)
    items = []
    for seat, r in sorted(rows.items()):
        items.append({"id": f"limit-{seat}-{r['window']}", "owner": seat,
                      "owed": f"resume work after the {r['window']} limit",
                      "due": _local(r.get("reset_at")), "status": "blocked",
                      "last_check": stamp, "note": r["line"]})
    tracked = _read_json(paths.initiatives, {}).get("items", [])
    limited_roots = {_GEN_SUFFIX.sub("", s): r for s, r in rows.items()}
    for it in tracked:
        r = limited_roots.get(_GEN_SUFFIX.sub("", str(it.get("owner", ""))))
        if r and it.get("status") not in ("done", "dropped"):
            items.append({"id": it.get("id"), "owner": it.get("owner"), "owed": it.get("owed"),
                          "due": it.get("due"), "status": it.get("status"), "last_check": stamp,
                          "note": f"blocked: {r['window']} limit, {r['reset_label']}"})
    _write_json(paths.sidecar, {"_doc": "limit_watch sidecar: gm merges into initiatives.json",
                                "updated": stamp, "items": items})


def _prune(episodes, now, keep_days=14):
    cutoff = now - keep_days * 86400
    for k in [k for k, e in episodes.items() if not k.startswith("_") and not e.get("open")]:
        dt = LE._iso_to_dt(episodes[k].get("first_seen"))
        if dt and dt.timestamp() < cutoff:
            del episodes[k]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mail", action="store_true", help="send the mails (default: log only)")
    ap.add_argument("--backfill", action="store_true",
                    help="read fresh files from their start instead of their end on first sight")
    ap.add_argument("--auto-switch", action="store_true",
                    help="ARM the fleet-wide Claude login switch (limit_switch.py; its mails always go to gm)")
    ap.add_argument("--codex-watch", action="store_true",
                    help="track whether Codex serves (codex_watch.py): writes state/codex-availability.json "
                         "and mails the review seats + gm on a flip")
    a = ap.parse_args(argv)
    paths = Paths()
    deps = None
    if a.auto_switch:
        import limit_switch as LS
        deps = LS.LiveDeps(paths, send_mail)
    res = run_once(paths, mail_fn=send_mail if a.mail else None, start_at_end=not a.backfill,
                   switch_deps=deps, codex_watch=send_mail if a.codex_watch else None)
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
