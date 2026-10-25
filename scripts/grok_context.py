"""Grok Build (``grok`` CLI) seat adapter: read a Grok seat's state.

The ONE place the fleet reads a Grok seat's state. Every consumer that branches on
``runtime == "grok"`` (agent-status, tmux_context_widget, message-router,
ctx_adapters' sid resolver, spawn-agent.sh) calls into this module, so the Grok
file formats are parsed in exactly one place, the codex_context.py pattern.

SOURCE OF TRUTH is Grok's own per-session store, never pane text:

    $GROK_HOME/sessions/<url-encoded-cwd>/<session-id>/
        events.jsonl   runtime events: turn_started / phase_changed / turn_ended ...
        signals.json   contextTokensUsed, contextWindowTokens, turnCount ...
        summary.json   current_model_id, session_kind, info.cwd ...

and the seat is bound to its session by PROCESS TRUTH: the pane's own ``grok``
process carries the session id on its argv because spawn-agent.sh pins it
(``-s <uuid>`` for a new seat, ``-r <uuid>`` for a resume). There is no
"most recent session" fallback anywhere in this module: an unresolvable seat
returns None / 'unknown' (the codex dossier §7 wrong-specimen rule).

FAIL-CLOSED contract:
  * no unique grok process on the pane / no UUID on its argv  -> None
  * zero or more than one session dir for that UUID          -> None
  * events.jsonl with no turn boundary yet                   -> state 'unknown'
  * signals.json missing / window 0                          -> context None
'unknown' is never 'idle': the router holds mail for a seat it cannot read.

What this module does NOT cover (each consumer refuses grok by name): WAL capture into the
lineage store, rotation, approval cards/menus. The dashboard's chat view reads updates.jsonl
itself (api/src/routes/chat-transcript.ts, the TypeScript twin of transcript_path below).

Read-only. Never writes to $GROK_HOME, never touches auth.json, never launches grok.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
import uuid

RUNTIME = "grok"

#: argv[0] basenames that ARE the Grok Build CLI. ``grok`` is the launcher name
#: (~/.local/bin/grok); the installer's real file is grok-linux-x86_64
#: (a static ELF, so argv[0] is the name it was invoked by, not node/python).
GROK_BASENAMES = ("grok", "grok-linux-x86_64")

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

#: events.jsonl tail read; turn events are tiny, MCP lifecycle lines are the bulk.
TAIL_BYTES = 262144

#: Opt-in router arming for Grok delivery. Mail to a grok seat is HELD unless this is armed
#: AND the composer signature is verified (message-router.py). Today it never is: mail
#: reaches a grok seat through scripts/grok_mail_watch.py instead.
DELIVERY_ARM_ENV = "ROUTER_GROK_DELIVERY_ARMED"
DELIVERY_ARM_SENTINEL = os.path.join("runtime", "ROUTER_GROK_DELIVERY_ARMED")


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def grok_home() -> str:
    return os.environ.get("GROK_HOME") or os.path.expanduser("~/.grok")


def sessions_root(home: str | None = None) -> str:
    return os.path.join(home or grok_home(), "sessions")


def is_uuid(value) -> bool:
    return isinstance(value, str) and bool(UUID_RE.match(value))


def new_session_id() -> str:
    """A fresh session UUID for ``grok -s``. Grok itself mints UUIDv7; any valid UUID
    is accepted (grok --help: 'must be a valid UUID and must not already exist')."""
    return str(uuid.uuid4())


def session_dir(sid: str, root: str | None = None) -> str | None:
    """$GROK_HOME/sessions/<encoded-cwd>/<sid>/ for ``sid``, found by sid alone (the
    cwd encoding — percent-encoding, hashed when long — is Grok's business). Exactly
    one match or None."""
    if not is_uuid(sid):
        return None
    hits = [p for p in glob.glob(os.path.join(root or sessions_root(), "*", sid))
            if os.path.isdir(p)]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# process truth: pane -> grok pid -> session id
# ---------------------------------------------------------------------------

def is_grok_argv(argv) -> bool:
    if not argv:
        return False
    return os.path.basename(argv[0]) in GROK_BASENAMES


def sid_from_argv(argv) -> str | None:
    """The session UUID a grok argv is bound to, or None.

    ``-s/--session-id <uuid>`` names the session of a NEW conversation (and, with
    --fork-session, the fork), so it wins. Otherwise ``-r/--resume <uuid>``.
    ``-r`` with no value or with a title, ``-c/--continue``, or no flag at all is
    NOT an identity (it means "whatever Grok picks") -> None, fail closed."""
    if not is_grok_argv(argv):
        return None
    found = {}
    args = list(argv[1:])
    i = 0
    while i < len(args):
        a = args[i]
        for short, long_, key in (("-s", "--session-id", "s"), ("-r", "--resume", "r")):
            if a.startswith(long_ + "="):
                found.setdefault(key, a.split("=", 1)[1])
            elif a in (short, long_):
                nxt = args[i + 1] if i + 1 < len(args) else None
                if nxt is not None and not nxt.startswith("-"):
                    found.setdefault(key, nxt)
        i += 1
    for key in ("s", "r"):
        v = found.get(key)
        if v is not None:
            return v if is_uuid(v) else None
    return None


def _cmdline(pid: int):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    return [p.decode(errors="replace") for p in raw.split(b"\x00") if p]


def _children(pid: int) -> list[int]:
    """Direct children of `pid`, from /proc (Linux). This used to import a `proc_children`
    module that was never published, so on a public install every call raised
    ModuleNotFoundError, seat_turn_state() answered reader-error, and grok_mail_watch held
    every nudge forever."""
    out: list[int] = []
    base = f"/proc/{int(pid)}/task"
    try:
        tids = os.listdir(base)
    except OSError:
        tids = []
    for tid in tids:
        try:
            with open(f"{base}/{tid}/children") as fh:
                out.extend(int(x) for x in fh.read().split())
        except OSError:
            pass
    if out:
        return sorted(set(out))
    # No /proc/<pid>/task/*/children (kernel without CONFIG_PROC_CHILDREN): scan ppids.
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat") as fh:
                ppid = int(fh.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == int(pid):
            out.append(int(name))
    return sorted(out)


def grok_pid_under(pane_pid: int, *, cmdline_fn=None, children_fn=None) -> int | None:
    """The unique grok process at or under the pane shell (two levels, like
    codex_context). Ambiguity (two grok processes) fails closed."""
    cmdline_fn = cmdline_fn or _cmdline
    children_fn = children_fn or _children
    if is_grok_argv(cmdline_fn(pane_pid)):
        return pane_pid
    try:
        kids = list(children_fn(pane_pid))
    except (OSError, ValueError):
        kids = []
    hits = [k for k in kids if is_grok_argv(cmdline_fn(k))]
    if not hits:
        for k in kids:
            try:
                hits.extend(g for g in children_fn(k) if is_grok_argv(cmdline_fn(g)))
            except (OSError, ValueError):
                continue
    return hits[0] if len(hits) == 1 else None


def pane_pid(session_name: str) -> int | None:
    """tmux pane pid for window 0 pane 0 of the session (fleet layout). Read-only query."""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"={session_name}:0.0", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def sid_for_pid(pid: int, *, cmdline_fn=None) -> str | None:
    return sid_from_argv((cmdline_fn or _cmdline)(pid))


def resolve_session(session_name: str, *, pane_pid_fn=None, cmdline_fn=None,
                    children_fn=None, root=None) -> dict | None:
    """tmux session -> {pid, sid, dir}. None unless every link is positive."""
    ppid = (pane_pid_fn or pane_pid)(session_name)
    if not ppid:
        return None
    pid = grok_pid_under(ppid, cmdline_fn=cmdline_fn, children_fn=children_fn)
    if not pid:
        return None
    sid = sid_for_pid(pid, cmdline_fn=cmdline_fn)
    if not sid:
        return None
    d = session_dir(sid, root)
    if not d:
        return None
    return {"pid": pid, "sid": sid, "dir": d}


def _updates_jsonl(sdir: str | None) -> str | None:
    if not sdir:
        return None
    path = os.path.join(sdir, "updates.jsonl")
    return path if os.path.isfile(path) else None


def transcript_path(session_name: str | None = None, sid: str | None = None, *,
                    root=None, pane_pid_fn=None, cmdline_fn=None,
                    children_fn=None) -> dict | None:
    """``{path, sid}`` of the seat's ``updates.jsonl``, or None.

    Process truth wins: pane -> grok pid -> argv UUID -> that session's
    updates.jsonl. ``chat_history.jsonl`` is the post-compaction model context,
    not the conversation, so it is never returned. A live grok process whose
    session has no updates file fails closed (no sid fallback onto a different
    file). The sid fallback runs only when the pane has no readable grok process,
    so a dead seat can still be opened once its session id is known.
    """
    if session_name:
        resolved = resolve_session(
            session_name, root=root, pane_pid_fn=pane_pid_fn,
            cmdline_fn=cmdline_fn, children_fn=children_fn)
        if resolved:
            path = _updates_jsonl(resolved["dir"])
            return {"path": path, "sid": resolved["sid"]} if path else None
    if sid and is_uuid(sid):
        path = _updates_jsonl(session_dir(sid, root))
        if path:
            return {"path": path, "sid": sid}
    return None


# ---------------------------------------------------------------------------
# session-store readers (pure over a session dir)
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def read_signals(sdir: str) -> dict | None:
    return _read_json(os.path.join(sdir, "signals.json"))


def read_summary(sdir: str) -> dict | None:
    return _read_json(os.path.join(sdir, "summary.json"))


def context_from_signals(sig: dict | None):
    """(used_pct 0..100, tokens, window) from signals.json, or None. This is Grok's own
    figure (the TUI footer shows the same "used / window"); usage.json's per-call
    inputTokens measures something else (system prompt + tools) and is not used."""
    if not isinstance(sig, dict):
        return None
    tokens, window = sig.get("contextTokensUsed"), sig.get("contextWindowTokens")
    if not isinstance(tokens, (int, float)) or not isinstance(window, (int, float)):
        return None
    if window <= 0 or tokens < 0:
        return None
    return (min(100.0, 100.0 * tokens / window), int(tokens), int(window))


def _tail_lines(path: str, tail_bytes: int = TAIL_BYTES):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - tail_bytes)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return None
    lines = data.split(b"\n")
    if start > 0 and lines:
        lines = lines[1:]          # first line is partial
    return [l for l in lines if l.strip()]


#: event type -> what it says about the seat. Only TURN BOUNDARIES decide busy/idle;
#: phase_changed is reported as detail (it repeats, and never marks a turn's end).
TURN_START, TURN_END = "turn_started", "turn_ended"


def turn_state(sdir: str | None, *, tail_bytes: int = TAIL_BYTES) -> dict:
    """Busy/idle from events.jsonl turn boundaries.

    {'state': 'working'|'idle'|'unknown', 'ts': <iso of the deciding event>|None,
     'phase': <last phase_changed>|None, 'outcome': <turn_ended.outcome>|None,
     'turn_number': int|None, 'model_id': str|None}

    working = the last turn boundary is turn_started; idle = it is turn_ended;
    unknown = no events file, unreadable, or no turn boundary in the tail (a seat
    still booting, before its first prompt)."""
    out = {"state": "unknown", "ts": None, "phase": None, "outcome": None,
           "turn_number": None, "model_id": None}
    if not sdir:
        return out
    lines = _tail_lines(os.path.join(sdir, "events.jsonl"), tail_bytes)
    if not lines:
        return out
    for raw in lines:
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == TURN_START:
            out.update(state="working", ts=ev.get("ts"), outcome=None, phase=None,
                       turn_number=ev.get("turn_number"),
                       model_id=ev.get("model_id") or out["model_id"])
        elif t == TURN_END:
            out.update(state="idle", ts=ev.get("ts"), outcome=ev.get("outcome"))
        elif t == "phase_changed" and out["state"] == "working":
            out["phase"] = ev.get("phase")
    return out


# ---------------------------------------------------------------------------
# consumer entry points
# ---------------------------------------------------------------------------

def get_grok_session_context(session_name: str, **kw):
    """(pct, tokens, window) for the tmux_context_widget / agent-status, or None."""
    s = resolve_session(session_name, **kw)
    if not s:
        return None
    return context_from_signals(read_signals(s["dir"]))


def seat_turn_state(session_name: str, **kw) -> dict:
    s = resolve_session(session_name, **kw)
    st = turn_state(s["dir"] if s else None)
    st["sid"] = s["sid"] if s else None
    return st


def status_for_pid(pid: int, *, cmdline_fn=None, root=None) -> dict:
    """agent-status row fragment for a live grok pid (already found by the detector).
    Keys: state, activity, model, context_pct_of_window, sid, confidence, phase."""
    sid = sid_for_pid(pid, cmdline_fn=cmdline_fn)
    sdir = session_dir(sid, root) if sid else None
    st = turn_state(sdir)
    summ = read_summary(sdir) if sdir else None
    ctx = context_from_signals(read_signals(sdir)) if sdir else None
    if not sid:
        why = "grok argv carries no session UUID (spawn pins -s/-r; unpinned = unreadable)"
    elif not sdir:
        why = f"no unique session dir for {sid} yet"
    elif st["state"] == "unknown":
        why = "no turn boundary in events.jsonl yet"
    elif st["state"] == "working":
        why = f"Grok turn {st['turn_number']} running" + (f" ({st['phase']})" if st["phase"] else "")
    else:
        why = "Grok turn ended" + (f" ({st['outcome']})" if st["outcome"] else "")
    return {
        "state": st["state"],
        "activity": why,
        "model": (summ or {}).get("current_model_id") or st.get("model_id") or "",
        "context_pct_of_window": int(round(ctx[0])) if ctx else None,
        "context_pct": f"{ctx[0]:.0f}%" if ctx else "",
        "sid": sid,
        "event_ts": st["ts"],
        "phase": st["phase"],
        "confidence": "grok-events" if st["state"] != "unknown" else "process",
    }


def delivery_armed() -> bool:
    """Opt-in arming for router delivery INTO a grok pane (default: held)."""
    if os.environ.get(DELIVERY_ARM_ENV) == "1":
        return True
    try:
        return os.path.exists(os.path.join(os.path.expanduser("~"), DELIVERY_ARM_SENTINEL))
    except OSError:
        return False


def router_idle(session_name: str, *, signature_verified: bool, **kw) -> tuple:
    """(idle: bool, reason: str) for the router's idle gate. Idle ONLY when delivery is
    armed, the composer signature is verified, and events.jsonl says the last
    turn ENDED. Everything else HOLDS (mail stays pending; never dropped)."""
    if not delivery_armed():
        return False, "grok-delivery-unarmed"
    if not signature_verified:
        return False, "grok-composer-signature-unverified"
    st = seat_turn_state(session_name, **kw)
    if st["state"] != "idle":
        return False, f"grok-turn-{st['state']}"
    return True, "grok-turn-ended"


def wait_turn_started(sid: str, *, timeout_s: float = 45.0, poll_s: float = 1.0,
                      root=None, sleep_fn=None, clock=None) -> bool:
    """Spawn's submit-verify BY EFFECT: True once the session's events.jsonl records a
    turn_started (the initial prompt passed on grok's argv was accepted)."""
    sleep_fn = sleep_fn or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + timeout_s
    while True:
        d = session_dir(sid, root)
        if d:
            lines = _tail_lines(os.path.join(d, "events.jsonl")) or []
            for raw in lines:
                try:
                    if json.loads(raw).get("type") == TURN_START:
                        return True
                except (ValueError, AttributeError):
                    continue
        if clock() >= deadline:
            return False
        sleep_fn(poll_s)


def _main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Grok seat reader (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new-sid")
    p = sub.add_parser("context"); p.add_argument("session")
    p = sub.add_parser("state"); p.add_argument("session")
    p = sub.add_parser("wait-turn"); p.add_argument("sid")
    p.add_argument("--timeout", type=float, default=45.0)
    p = sub.add_parser("transcript")
    p.add_argument("session")
    p.add_argument("--sid", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "new-sid":
        print(new_session_id())
        return 0
    if a.cmd == "context":
        print(json.dumps(get_grok_session_context(a.session)))
        return 0
    if a.cmd == "state":
        print(json.dumps(seat_turn_state(a.session)))
        return 0
    if a.cmd == "wait-turn":
        return 0 if wait_turn_started(a.sid, timeout_s=a.timeout) else 1
    if a.cmd == "transcript":
        rec = transcript_path(a.session, a.sid)
        print(json.dumps(rec or {}))
        return 0 if rec else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
