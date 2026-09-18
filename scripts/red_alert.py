#!/usr/bin/env python3
"""red_alert.py — the RED ALERT crash-report standard (CLI + library).

Standard: docs/RED_ALERT.md. One report = one JSON file
    state/red-alert/<UTC ts>-<seat>-<slug>.json
Commissioned by the operator (2026-09-17) after the harness bottom-bar "^Z" button
suspended the gm seat (pid STAT T, not killed) — "LOG EVERYTHING IN A RED ALERT CRASH
REPORT ... a list of errors the system reports, logs, adds to and acts on immediately".

    red_alert.py report --reported-by user|watchdog|agent --channel <where> \
        --severity crash|error|bug|improvement --seat <seat> [--seat ...] \
        --symptom "<the user's words>" [--snapshot <existing pane file>] [--no-capture]
    red_alert.py list [--status open|repairing|awaiting-approval|resolved] [--json]
    red_alert.py show <id>
    red_alert.py update <id> [--status ..] [--diagnosis ..] [--immediate-fix JSON]
                             [--permanent-fix JSON] [--note ..] [--card ..]
    red_alert.py resolve <id> --note "<how it was verified by effect>"
    red_alert.py escalate <id> --reason "<why>"
    red_alert.py classify --seat <seat>          # dry: what class would this seat be now?

`report` auto-captures evidence for each named seat: `tmux capture-pane -S -3000`,
the `ps` state of the pane's process tree (STAT containing T == the ^Z class), the
tail of the seat's log, the registry row (identity store, DB-first) and its sid.
Capture is injectable (`capture=`) so tests never touch tmux.

Every write is a whole-file atomic replace (tmp + os.replace). Nothing here ever
touches a pane — repairs live in red_alert_watch.py.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

# DATA dir (reports, logs, registry) — under `orchestra up` this is ORCHESTRA_DIR; code paths use CODE_ROOT.
ORCH = os.environ.get("ORCHESTRA_DIR") or os.path.expanduser("~/scripts/agent-orchestra")
CODE_ROOT = os.environ.get("ORCHESTRA_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPORTERS = ("user", "watchdog", "agent")
SEVERITIES = ("crash", "error", "bug", "improvement")
STATUSES = ("open", "repairing", "awaiting-approval", "resolved")
SCHEMA_KEYS = (
    "id", "created_at", "reported_by", "channel", "severity", "seats", "symptom",
    "class", "evidence", "diagnosis", "immediate_fix", "permanent_fix", "status",
    "card_id", "repair_attempts", "escalations", "timeline",
)
MUTABLE_KEYS = ("status", "diagnosis", "immediate_fix", "permanent_fix", "card_id", "class", "severity", "watch")

# ---------------------------------------------------------------------------
# The catalogue: the list of errors the system upholds itself to. Each entry names
# the class, how it is detected, the immediate repair the watchdog MAY perform on
# its own (safe, reversible, verified by effect) and the permanent-fix lane.
# Add a class here + a doc row in docs/RED_ALERT.md; tests assert both.
# ---------------------------------------------------------------------------
CATALOGUE = {
    "process_suspended": {
        "severity": "crash",
        "detect": "pane process tree has a STAT containing 'T' (SIGTSTP/^Z), or the screen "
                  "shows 'Claude Code has been suspended'",
        "immediate_fix": {"action": "card_only",
                          "how": "NO KILLS rule (2026-09-18): the process is still present, so the "
                                 "watchdog never touches it. Card + Telegram; a human resumes it (respawn-pane -k + "
                                 "`claude --resume <sid>` by hand — SIGCONT/tcsetpgrp did not stick on gm 04:31Z)"},
        "doc": "^Z from the harness bottom bar or a terminal; the CLI is stopped, not dead",
    },
    "pane_dead": {
        "severity": "crash",
        "detect": "tmux pane_dead=1 or no process on the pane tty",
        "immediate_fix": {"action": "respawn_resume",
                          "how": "tmux respawn-pane -t <seat> '<resume_command>' from the registry row — only when no process remains (never -k)"},
        "doc": "the CLI exited (OOM, crash, kill); the seat name still exists",
    },
    "out_of_usage": {
        "severity": "error",
        "detect": "screen shows 'usage limit' / 'out of usage credits' / 'credits depleted'",
        "immediate_fix": {"action": "switch_provider",
                          "how": "/model in-pane to the next enabled runtime (Opus -> Sonnet -> gemini -> codex); record the switch"},
        "doc": "the provider refuses work; the process is alive and idle",
    },
    "api_error": {
        "severity": "error",
        "detect": "screen shows 'API Error' (529/500/overloaded/rate) in the last screenful",
        "immediate_fix": {"action": "wait_then_retry",
                          "how": "wait 90s; if still on screen, bare Enter retry; if it persists 3x -> switch_provider"},
        "doc": "transient upstream failure; do not switch on the first sight",
    },
    "login_screen": {
        "severity": "crash",
        "detect": "screen shows 'Select login method' / 'Log in' / 'not logged in'",
        "immediate_fix": {"action": "card_only",
                          "how": "auth is the operator's; card + Telegram, never type a login"},
        "doc": "credentials expired or the wrong CLAUDE_CONFIG_DIR",
    },
    "bypass_permissions_dialog": {
        "severity": "error",
        "detect": "screen shows the 'Bypass Permissions mode' accept dialog",
        "immediate_fix": {"action": "accept_dialog",
                          "how": "Down + Enter on the 'Yes, I accept' row (the fleet runs --dangerously-skip-permissions by policy)"},
        "doc": "a fresh spawn/resume stuck on the first-run dialog",
    },
    "composer_stuck": {
        "severity": "bug",
        "detect": "typed text sits at the ❯ line unsubmitted for > 2 scans",
        "immediate_fix": {"action": "bare_enter", "how": "scripts/nudge_pane.py semantics: bare Enter, never re-send text"},
        "doc": "a one-call send-keys paste; the router then holds all mail as not-idle",
    },
    "tmux_server_dead": {
        "severity": "crash",
        "detect": "`tmux list-sessions` fails (no server) while the fleet registry lists online seats",
        "immediate_fix": {"action": "card_only",
                          "how": "one fleet-wide report + card + Telegram; resume is gm's roster-resume-all (each seat: "
                                 "new session + `claude --resume <sid>`). NEVER kill a pid whose argv starts with `tmux` — "
                                 "the server keeps its first client's argv (, 2026-09-18 04:53Z)"},
        "doc": "every pane vanished at once; transcripts intact; nothing else can run until a server exists",
    },
    "green_died": {
        "severity": "error",
        "detect": "a blue-green GREEN pane died while its root's bg_state still expects it "
                  "(state not SOLO and the green is named in meta)",
        "immediate_fix": {"action": "card_only",
                          "how": "NEVER respawn a green — only the blue-green state machine may boot one; a "
                                 "registry respawn manufactures an orphan pane. Blue keeps working; the next beat "
                                 "boots a fresh green. Card so a human knows a green is failing repeatedly"},
        "doc": "an ephemeral successor died (often correctly, e.g. hydrate SeamTimeout under load); not a seat crash",
    },
    "api_health_fail": {
        "severity": "error",
        "detect": "service-watchdog 'HEALTH FAIL: api-server port 8888 failed twice' (HTTP 000) — the API stopped answering",
        "immediate_fix": {"action": "none_needed",
                          "how": "service-watchdog restarts it; file the report with the watchdog-log timestamps, host memory, "
                                 "kernel OOM check and the preserved API stderr (: stderr was NOT preserved — fix #1)"},
        "doc": "a service, not a seat: recurring restarts of the OrchestraOS API; every phone/watch chat + upload 502s while it is down",
    },
    "gateway_unreachable": {
        "severity": "error",
        "detect": "user report from the phone/watch: 'Gateway unreachable'",
        "immediate_fix": {"action": "probe_health", "how": "curl /health on :9091 + the funnel; report which hop failed"},
        "doc": "OrchestraUltra cannot reach :9091 through the Tailscale funnel",
    },
}

# Screen rules match CLI-RENDERED lines only (gm,  false positive 2026-09-18 07:13Z:
# the old regex hit "...given the usage limit" in assistant prose). A "system line" is one the
# harness prints, not the transcript body: a `⎿` result/notice line, a `⚠`/`✗` line, or anything at
# or below the LAST composer prompt (❯) — the status region. Prose lines (`●`, indented text) never
# classify, even when they quote a banner verbatim.
_SYSTEM_LINE = re.compile(r"^\s*[⎿⚠✗]")
_SCREEN_RULES = (
    (re.compile(r"You'?re out of usage credits|You'?ve hit your usage limit|usage limit reached|"
                r"insufficient_quota|credits? (?:depleted|exhausted)|Out of credits", re.I), "out_of_usage"),
    (re.compile(r"^\s*[⎿⚠✗]?\s*API Error\b", re.I), "api_error"),
    (re.compile(r"Select login method|Please log in|Login required|Not logged in\.|OAuth error|authentication[_ ]error", re.I), "login_screen"),
)


def screen_regions(screen: str, tail: int = 40) -> tuple[list[str], list[str]]:
    """(system_lines, status_region) of the last `tail` lines — the only lines a screen rule may see."""
    lines = screen.splitlines()[-tail:]
    last_prompt = max((i for i, l in enumerate(lines) if l.lstrip().startswith("❯")), default=None)
    # no composer prompt on screen = the CLI is not in chat state (login screen, dialog, crashed
    # output): the whole tail is status. With a prompt, only the prompt and what follows it.
    status = lines[last_prompt:] if last_prompt is not None else lines
    system = [l for l in lines if _SYSTEM_LINE.match(l)]
    return system, status


def classify_screen(screen: str) -> tuple[str, str] | None:
    """(class, matched line) for CLI banners; None for prose. Whole-screen dialogs need their companion line."""
    lines = screen.splitlines()[-40:]
    text = "\n".join(lines)
    if re.search(r"Claude Code has been suspended", text) and re.search(r"^\[\d+\]\s*\+?\s*Stopped|Run `fg`", text, re.M):
        return "process_suspended", "Claude Code has been suspended + shell Stopped line"
    if re.search(r"Bypass Permissions mode", text) and re.search(r"Yes, I accept", text):
        return "bypass_permissions_dialog", "Bypass Permissions mode + 'Yes, I accept' row"
    system, status = screen_regions(screen)
    for line in system + [l for l in status if l not in system]:
        # a ⎿ line that is tool OUTPUT quoting a banner (grep results, file excerpts) is still prose:
        # require the banner to START the notice, not sit inside a quoted path/line
        body = re.sub(r"^\s*[⎿⚠✗]?\s*", "", line)
        for rx, cls in _SCREEN_RULES:
            m = rx.search(body)
            if m and (cls != "out_of_usage" or m.start() == 0 or body[:m.start()].strip() == ""):
                return cls, line.strip()
    return None


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
def state_dir() -> str:
    d = os.environ.get("RED_ALERT_STATE_DIR") or os.path.join(ORCH, "state", "red-alert")
    os.makedirs(d, exist_ok=True)
    return d


def log_dir() -> str:
    d = os.environ.get("RED_ALERT_LOG_DIR") or os.path.join(ORCH, "logs", "red-alert")
    os.makedirs(d, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "report"


def _write(path: str, doc: dict) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def _path_of(rid: str) -> str:
    for name in os.listdir(state_dir()):
        if name.endswith(".json"):
            p = os.path.join(state_dir(), name)
            try:
                if json.load(open(p)).get("id") == rid:
                    return p
            except (OSError, ValueError):
                continue
    raise KeyError(f"no RED ALERT report with id {rid}")


def _load(rid: str) -> tuple[str, dict]:
    p = _path_of(rid)
    return p, json.load(open(p))


# ---------------------------------------------------------------------------
# evidence capture (live) — every piece fail-soft so a capture never blocks a report
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover - live only
        return f"<{cmd[0]} failed: {e}>"


def parse_ps(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s+(\S+)\s+(\S+)\s+(.*)$", line)
        if m:
            rows.append({"pid": int(m.group(1)), "stat": m.group(2), "tty": m.group(3), "cmd": m.group(4).strip()})
    return rows


def any_stopped(rows: list[dict]) -> bool:
    return any("T" in r.get("stat", "") for r in rows)


def pane_info(seat: str) -> dict:
    out = _run(["tmux", "list-panes", "-t", seat, "-F", "#{pane_pid} #{pane_tty} #{pane_dead} #{session_attached}"])
    if not out.strip():
        return {"exists": False}
    pid, tty, dead, attached = out.split()[:4]
    return {"exists": True, "pane_pid": int(pid), "tty": tty, "dead": dead == "1", "attached": attached != "0"}


_PS_CACHE: dict = {"at": 0.0, "rows": [], "ppid": {}}


def ps_table(max_age: float = 2.0) -> tuple[list[dict], dict]:
    """One `ps -e` per tick (CPU discipline: 34 seats must not mean 68 ps calls)."""
    if time.time() - _PS_CACHE["at"] > max_age:
        rows, ppid_of = [], {}
        for line in _run(["ps", "-e", "-o", "pid=,ppid=,stat=,tty=,args="]).splitlines():
            m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(.*)$", line)
            if m:
                rows.append({"pid": int(m.group(1)), "stat": m.group(3), "tty": m.group(4), "cmd": m.group(5).strip()})
                ppid_of[int(m.group(1))] = int(m.group(2))
        _PS_CACHE.update(at=time.time(), rows=rows, ppid=ppid_of)
    return _PS_CACHE["rows"], _PS_CACHE["ppid"]


def process_tree(pane_pid: int) -> list[dict]:
    """All descendants of the pane shell (the CLI + its MCP children), with STAT."""
    rows_all, ppid_of = ps_table()
    def under(pid):
        seen = 0
        while pid and pid != 1 and seen < 64:
            if pid == pane_pid:
                return True
            pid = ppid_of.get(pid, 0)
            seen += 1
        return False
    return [r for r in rows_all if r["pid"] != pane_pid and under(r["pid"])]


def registry_row(seat: str) -> dict | None:
    try:
        sys.path.insert(0, CODE_ROOT)
        from scripts.identity_store import resolver
        return resolver.registry_agent_db(ORCH, seat, alarm=lambda m: None)
    except Exception as e:  # pragma: no cover - live only
        return {"_error": repr(e)}


def capture_evidence(seats: list[str], snapshot: str | None = None, lines: int = 3000, to_disk: bool = True) -> dict:
    ev = {"pane_snapshot": {}, "process_state": {}, "pane_dead": {}, "attached": {},
          "screen": {}, "log_excerpt": {}, "sids": {}, "registry_rows": {}, "captured_at": _now()}
    ts = _ts()
    for seat in seats:
        info = pane_info(seat)
        ev["pane_dead"][seat] = (not info.get("exists")) or info.get("dead", False)
        ev["attached"][seat] = info.get("attached", False)
        if snapshot and len(seats) == 1:
            ev["pane_snapshot"][seat] = snapshot
        elif info.get("exists") and to_disk:
            cap = _run(["tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", seat])
            p = os.path.join(log_dir(), f"{seat}-pane-{ts}.txt")
            with open(p, "w") as f:
                f.write(cap)
            ev["pane_snapshot"][seat] = p
        if info.get("exists"):
            ev["screen"][seat] = _run(["tmux", "capture-pane", "-p", "-t", seat])
            ev["process_state"][seat] = process_tree(info["pane_pid"])
        else:
            ev["process_state"][seat] = []
        for cand in (os.path.join(ORCH, "logs", f"{seat}.log"), os.path.join(ORCH, "logs", f"{seat}-watchdog.log")):
            if os.path.exists(cand):
                ev["log_excerpt"][seat] = _run(["tail", "-n", "40", cand])
                break
        row = registry_row(seat)
        ev["registry_rows"][seat] = row
        ev["sids"][seat] = (row or {}).get("session_id")
    return ev


_EXIT_CMD = re.compile(r"<command-name>\s*/(exit|quit)\s*</command-name>")


def _transcript_path(sid: str | None) -> str | None:
    """The live jsonl for a sid, or None. Injected in tests."""
    if not sid:
        return None
    try:
        from scripts import sid_invariants as SI
        path = SI.find_transcript(sid)
    except Exception:
        path = None
    return str(path) if path and os.path.exists(str(path)) else None


def exited_cleanly(sid: str | None, *, tail: int = 5) -> bool:
    """True when the seat's LAST act was typing /exit (or /quit).

    : orchestra-builder-g49 typed /exit at 17:33:00Z; the watchdog saw a dead
    pane 4s later, filed a `pane_dead` crash and carded the operator. A deliberate exit is a
    retirement, not a crash — rotation owns it, RED ALERT does not.
    Only the tail is read: a resumed sid appends, so an /exit from a previous life is
    buried under later turns and must NOT excuse today's crash.
    """
    path = _transcript_path(sid)
    if not path:
        return False
    try:
        with open(path) as fh:
            lines = fh.readlines()[-tail:]
    except OSError:
        return False
    return any(_EXIT_CMD.search(l) for l in lines)


# ---------------------------------------------------------------------------
# classifier
# ---------------------------------------------------------------------------
def classify(ev: dict, seat: str) -> dict | None:
    """Return {class, severity, immediate_fix, signal} or None when the seat looks healthy."""
    def hit(cls, signal):
        e = CATALOGUE[cls]
        return {"class": cls, "severity": e["severity"], "immediate_fix": dict(e["immediate_fix"]), "signal": signal}

    procs = ev.get("process_state", {}).get(seat) or []
    if any_stopped(procs):
        return hit("process_suspended", "ps STAT contains T")
    dead_flag = ev.get("pane_dead", {}).get(seat)
    if dead_flag or (dead_flag is None and procs == [] and seat in ev.get("process_state", {})):
        if exited_cleanly(ev.get("sids", {}).get(seat)):
            return None
        return hit("pane_dead", "pane dead / no process on tty")
    screen = ev.get("screen", {}).get(seat) or ""
    found = classify_screen(screen)
    if found:
        return hit(found[0], f"CLI line: {found[1][:120]}")
    return None


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------
def report(*, reported_by: str, channel: str, severity: str, seats: list[str], symptom: str,
           capture=capture_evidence, snapshot: str | None = None, cls: str | None = None,
           extra: dict | None = None) -> dict:
    if reported_by not in REPORTERS:
        raise ValueError(f"reported_by must be one of {REPORTERS}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    if not seats:
        raise ValueError("at least one --seat")
    ev = capture(seats, snapshot=snapshot) if capture else {}
    guess = None
    for s in seats:
        guess = classify(ev, s) if ev else None
        if guess:
            break
    doc = {
        "id": f"ra_{uuid.uuid4().hex[:8]}",
        "created_at": _now(),
        "reported_by": reported_by,
        "channel": channel,
        "severity": severity,
        "seats": list(seats),
        "symptom": symptom,
        "class": cls or (guess or {}).get("class"),
        "evidence": ev,
        "diagnosis": None,
        "immediate_fix": (guess or {}).get("immediate_fix"),
        "permanent_fix": None,
        "status": "open",
        "card_id": None,
        "repair_attempts": [],
        "escalations": [],
        "timeline": [{"at": _now(), "event": "reported", "by": reported_by, "note": symptom}],
    }
    if extra:
        doc.update({k: v for k, v in extra.items() if k not in doc or k in MUTABLE_KEYS})
        doc.setdefault("kind", None)
    fname = f"{_ts()}-{_slug(seats[0], 24)}-{_slug(doc['class'] or symptom)}.json"
    path = os.path.join(state_dir(), fname)
    _write(path, doc)
    doc["_path"] = path
    return doc


def list_reports(status: str | None = None) -> list[dict]:
    out = []
    for name in sorted(os.listdir(state_dir())):
        if not name.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(state_dir(), name)))
        except ValueError:
            continue
        if status and d.get("status") != status:
            continue
        d["_path"] = os.path.join(state_dir(), name)
        out.append(d)
    return out


def show(rid: str) -> dict:
    p, d = _load(rid)
    d["_path"] = p
    return d


def _timeline(doc: dict, event: str, by: str = "red_alert", note: str | None = None, **kw) -> None:
    row = {"at": _now(), "event": event, "by": by}
    if note:
        row["note"] = note
    row.update(kw)
    doc["timeline"].append(row)


def update(rid: str, *, by: str = "red_alert", note: str | None = None, **fields) -> dict:
    for k in fields:
        if k not in MUTABLE_KEYS:
            raise ValueError(f"not a mutable field: {k} (allowed {MUTABLE_KEYS})")
    if "status" in fields and fields["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    p, d = _load(rid)
    d.update(fields)
    _timeline(d, "updated", by, note, fields=sorted(fields))
    _write(p, d)
    return d


def record_attempt(rid: str, *, action: str, ok: bool, detail: str, by: str = "watchdog") -> dict:
    p, d = _load(rid)
    d["repair_attempts"].append({"at": _now(), "action": action, "ok": ok, "detail": detail, "by": by})
    d["status"] = "resolved" if ok else d["status"]
    _timeline(d, "repair_ok" if ok else "repair_failed", by, detail, action=action)
    _write(p, d)
    return d


def escalate(rid: str, *, reason: str, by: str = "red_alert") -> dict:
    p, d = _load(rid)
    d["escalations"].append({"at": _now(), "reason": reason, "by": by})
    d["status"] = "awaiting-approval"
    _timeline(d, "escalated", by, reason)
    _write(p, d)
    return d


def resolve(rid: str, *, note: str, by: str = "red_alert") -> dict:
    p, d = _load(rid)
    d["status"] = "resolved"
    _timeline(d, "resolved", by, note)
    _write(p, d)
    return d


def surface(d: dict) -> dict:
    """Front door (report button, /redalert): crash/error/bug -> card + Telegram + Arturo via the
    watchdog's post_card; improvement/suggestion -> Telegram + Arturo only (never a card, §4.3)."""
    import subprocess as _sp
    seat = d["seats"][0]
    if d["severity"] in ("crash", "error", "bug"):
        from red_alert_watch import post_card
        card = post_card(d, d.get("evidence") or {}, seat)
        return {"card_id": card, "surfaced": bool(card)}
    here = os.path.dirname(os.path.abspath(__file__))
    kind = d.get("kind") or d["severity"]
    text = f"💡 {d['id']} {kind} on {seat} (from {d['channel']}): {d['symptom'][:400]}\nReport: {d['_path']}"
    tg = _sp.run([os.path.join(here, "tg-notify.sh"), "--from", "RED ALERT", text], capture_output=True, text=True, timeout=60)
    bf = os.path.join(log_dir(), f".{d['id']}-arturo.txt")
    with open(bf, "w") as f:
        f.write(text)
    ms = _sp.run([sys.executable, os.path.join(CODE_ROOT, "msg_store.py"), "send", "--from", "red-alert-builder", "--to", "gm",
                  "--type", "red_alert", "--subject", f"RED ALERT {d['id']}: {kind} on {seat}", "--body-file", bf],
                 capture_output=True, text=True, timeout=60)
    return {"card_id": None, "surfaced": tg.returncode == 0, "arturo": ms.stdout.strip()[-40:]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _fmt(d: dict) -> str:
    return (f"{d['id']}  {d['status']:<17} {d['severity']:<11} {','.join(d['seats']):<22} "
            f"{d.get('class') or '-':<26} {d['created_at']}  {d['symptom'][:60]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report")
    r.add_argument("--reported-by", required=True, choices=REPORTERS)
    r.add_argument("--channel", required=True, help="telegram|arturo|dashboard|harness|watchdog|msg_store|...")
    r.add_argument("--severity", choices=SEVERITIES, help="required unless --kind is given")
    r.add_argument("--seat", action="append", required=True, dest="seats")
    r.add_argument("--symptom", required=True, help="the user's words, verbatim")
    r.add_argument("--snapshot", help="an already-saved pane snapshot to attach (single seat)")
    r.add_argument("--class", dest="cls", choices=sorted(CATALOGUE), help="force the class")
    r.add_argument("--no-capture", action="store_true")
    r.add_argument("--kind", choices=("crash", "bug", "improvement", "suggestion"),
                   help="the report-button type; severity is derived when --severity is omitted")
    r.add_argument("--surface", action="store_true",
                   help="also post the card + Telegram + Arturo row (crash/error/bug) or Telegram + Arturo only "
                        "(improvement/suggestion) — the front-door path used by the report button")

    l = sub.add_parser("list")
    l.add_argument("--status", choices=STATUSES)
    l.add_argument("--json", action="store_true")

    s = sub.add_parser("show"); s.add_argument("id")

    u = sub.add_parser("update"); u.add_argument("id")
    u.add_argument("--status", choices=STATUSES)
    u.add_argument("--diagnosis"); u.add_argument("--immediate-fix", help="JSON")
    u.add_argument("--permanent-fix", help="JSON"); u.add_argument("--card"); u.add_argument("--note")
    u.add_argument("--by", default="red_alert")

    e = sub.add_parser("escalate"); e.add_argument("id"); e.add_argument("--reason", required=True); e.add_argument("--by", default="red_alert")
    v = sub.add_parser("resolve"); v.add_argument("id"); v.add_argument("--note", required=True); v.add_argument("--by", default="red_alert")
    c = sub.add_parser("classify"); c.add_argument("--seat", required=True)

    ns = ap.parse_args(argv)
    if ns.cmd == "report":
        sev = ns.severity or {"crash": "crash", "bug": "bug", "improvement": "improvement", "suggestion": "improvement"}.get(ns.kind)
        if not sev:
            ap.error("--severity or --kind is required")
        d = report(reported_by=ns.reported_by, channel=ns.channel, severity=sev, seats=ns.seats,
                   symptom=ns.symptom, snapshot=ns.snapshot, cls=ns.cls,
                   capture=None if ns.no_capture else capture_evidence,
                   extra={"kind": ns.kind} if ns.kind else None)
        out = {"id": d["id"], "path": d["_path"], "class": d["class"], "severity": sev}
        if ns.surface:
            out.update(surface(d))
        print(json.dumps(out))
        return 0
    if ns.cmd == "list":
        rows = list_reports(ns.status)
        if ns.json:
            print(json.dumps(rows, indent=1))
        else:
            for d in rows:
                print(_fmt(d))
        return 0
    if ns.cmd == "show":
        print(json.dumps(show(ns.id), indent=2)); return 0
    if ns.cmd == "update":
        fields = {}
        if ns.status: fields["status"] = ns.status
        if ns.diagnosis: fields["diagnosis"] = ns.diagnosis
        if ns.immediate_fix: fields["immediate_fix"] = json.loads(ns.immediate_fix)
        if ns.permanent_fix: fields["permanent_fix"] = json.loads(ns.permanent_fix)
        if ns.card: fields["card_id"] = ns.card
        update(ns.id, by=ns.by, note=ns.note, **fields); print(f"{ns.id} updated {sorted(fields)}"); return 0
    if ns.cmd == "escalate":
        escalate(ns.id, reason=ns.reason, by=ns.by); print(f"{ns.id} escalated -> awaiting-approval"); return 0
    if ns.cmd == "resolve":
        resolve(ns.id, note=ns.note, by=ns.by); print(f"{ns.id} resolved"); return 0
    if ns.cmd == "classify":
        ev = capture_evidence([ns.seat])
        print(json.dumps({"seat": ns.seat, "class": classify(ev, ns.seat), "dead": ev["pane_dead"][ns.seat],
                          "attached": ev["attached"][ns.seat],
                          "stopped": any_stopped(ev["process_state"][ns.seat])}, indent=1))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
