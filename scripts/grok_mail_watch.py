#!/usr/bin/env python3
"""grok_mail_watch.py - instant, mechanical mail wake for a Grok seat (no LLM, no timer wakes).

    python3 scripts/grok_mail_watch.py --agent <seat> [--session <tmux session>]

spawn-agent.sh starts one of these, detached, for every Grok seat it launches (one per seat:
a second copy for the same seat exits at once). It exits by itself once the seat's tmux
session has been gone for --orphan-exit-s, so a killed seat leaves nothing running.

WHY: the router HOLDS every grok row (the Grok composer is not a verified delivery surface,
see runtime_signatures.PROMPT_SIGNATURES["grok"]), so a Grok seat would only learn about mail
when something else poked it. Polling from inside the seat burns model usage. This watcher
costs ZERO model tokens while there is no mail.

HOW:
  1. inotify (via libc, no deps) on the msg_store directory. Any write to tasks.db /
     tasks.db-wal wakes us; we debounce, then COUNT pending rows for --agent
     (indexed: idx_messages_inbox). We never read subject/body and never ack.
  2. If there are pending row ids we have not nudged yet, wait until the pane is idle:
       - the seat's own events.jsonl says the last turn ENDED (grok_context.seat_turn_state)
       - the composer is empty (only Grok's dim placeholder, told apart from real text by
         its colour), no human mid-typing, pane not in copy-mode
       - no client keystrokes in the last --client-quiet seconds
     then type ONE short nudge line and press Enter separately.
  3. Verify by effect: the seat's events.jsonl must record a new turn within --verify-s.
     If not, the ids stay un-nudged and we retry (bounded attempts).
  4. The seat reads + acks its own mail with msg_store (inbox/ack). This watcher only wakes it.

While nothing is pending the process sits in a blocking select() on the inotify fd
(a --safety-s re-count catches any missed event). While mail is pending but the seat is
mid-turn it re-checks the seat every --busy-poll-s seconds with file reads only.

Kill switch: touch ~/runtime/GROK_MAIL_WATCH_DISABLED (checked before every nudge).
State: $XDG_STATE_HOME/grok-mail-watch/<agent>.json (ids already nudged). Log: stdout.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import fcntl
import json
import os
import re
import select
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

IN_MODIFY, IN_CLOSE_WRITE, IN_MOVED_TO, IN_CREATE = 0x2, 0x8, 0x80, 0x100
DB_NAMES = {"tasks.db", "tasks.db-wal"}
KILL = os.path.expanduser("~/runtime/GROK_MAIL_WATCH_DISABLED")
PROMPT = "\u276f"      # composer glyph of the Grok Build TUI (boxed, or bare in --minimal)
BORDER = "\u2502"      # composer box side border


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} [grok-mail-watch] {msg}",
          flush=True)


# ---------------------------------------------------------------- inotify
class Inotify:
    def __init__(self, path: str, mask: int):
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        self.fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 failed")
        if libc.inotify_add_watch(self.fd, path.encode(), mask) < 0:
            raise OSError(ctypes.get_errno(), f"inotify_add_watch {path} failed")

    def wait(self, timeout: float) -> set:
        """Block up to `timeout` s; return the set of file names touched (drained)."""
        r, _, _ = select.select([self.fd], [], [], timeout)
        names = set()
        if not r:
            return names
        while True:
            try:
                buf = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            i = 0
            while i + 16 <= len(buf):
                _wd, _mask, _cookie, ln = struct.unpack_from("iIII", buf, i)
                names.add(buf[i + 16:i + 16 + ln].rstrip(b"\0").decode(errors="replace"))
                i += 16 + ln
        return names


# ---------------------------------------------------------------- msg_store (count only)
_CONN = {}


def pending_ids(db: str, agent: str, tenant: str) -> list:
    """Ids of pending rows for `agent`. Reads ids only - never subject/body.
    One read-only connection is reused (autocommit, so no read txn is held between checks)."""
    conn = _CONN.get(db)
    if conn is None:
        conn = _CONN[db] = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5,
                                           isolation_level=None)
    try:
        return [r[0] for r in conn.execute(
            "SELECT id FROM messages WHERE to_agent=? AND status='pending' AND tenant_id=? "
            "ORDER BY created_at ASC", (agent, tenant))]
    except sqlite3.Error:
        _CONN.pop(db, None)
        conn.close()
        raise


# ---------------------------------------------------------------- pane / seat
def tmux(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


def turn_state(session: str) -> dict:
    try:
        import grok_context
        return grok_context.seat_turn_state(session)
    except Exception as e:  # noqa: BLE001 - a reader fault means "not idle"
        return {"state": f"reader-error:{type(e).__name__}"}


_SGR = re.compile(r"\x1b\[[0-9;]*m")


def composer_index(lines):
    """Index of the composer line: boxed (full TUI) or a bare leading prompt (--minimal)."""
    idx = [i for i, l in enumerate(lines) if PROMPT in l and BORDER in l]
    if not idx:
        idx = [i for i, l in enumerate(lines) if _SGR.sub("", l).lstrip().startswith(PROMPT)]
    return idx[-1] if idx else None


def composer_text(capture: str):
    """Text in the composer, '' if empty, None if no composer is visible."""
    lines = capture.splitlines()
    i = composer_index(lines)
    if i is None:
        return None
    rest = lines[i].split(PROMPT, 1)[1]
    return rest.replace(BORDER, " ").strip()



PLACEHOLDERS = {"Build anything"}
_GRAY = re.compile(r"\x1b\[38;5;(\d+)m")


def composer_is_placeholder(session: str) -> bool:
    """True when the composer shows only Grok's dim placeholder hint (box is empty)."""
    cap = tmux("capture-pane", "-e", "-p", "-t", session)
    if cap.returncode != 0:
        return False
    all_lines = cap.stdout.splitlines()
    i = composer_index(all_lines)
    if i is None:
        return False
    rest = all_lines[i].split(PROMPT, 1)[1]
    plain = _SGR.sub("", rest).replace(BORDER, " ").strip()
    if not plain:
        return True
    if plain in PLACEHOLDERS:
        return True
    # placeholder text is drawn in dim gray (256-color 232-243) right before it starts
    head = rest[:rest.find(plain[0])] if plain[0] in rest else ""
    grays = _GRAY.findall(head)
    return bool(grays) and 232 <= int(grays[-1]) <= 243

def pane_blocker(session: str, client_quiet: float):
    """None when the pane is safe to type into, else a short reason."""
    st = turn_state(session)
    if st.get("state") != "idle":
        return f"turn={st.get('state')}"
    mode = tmux("display", "-p", "-t", session, "#{pane_in_mode}")
    if mode.returncode != 0:
        return "pane-unreadable"
    if mode.stdout.strip() not in ("", "0"):
        return "copy-mode"
    cap = tmux("capture-pane", "-p", "-t", session)
    if cap.returncode != 0:
        return "pane-unreadable"
    text = composer_text(cap.stdout)
    if text is None:
        return "no-composer"
    if text and not composer_is_placeholder(session):
        return "composer-has-text"
    lines = cap.stdout.splitlines()
    idx = composer_index(lines)
    tail = "\n".join(lines[max(0, idx - 4):])   # spinner row above the box + footer below
    if "[stop]" in tail or "Ctrl+c:cancel" in tail:
        return "tui-busy-footer"
    cl = tmux("list-clients", "-t", session, "-F", "#{client_activity}")
    if cl.returncode == 0:
        now = time.time()
        for v in cl.stdout.split():
            if v.isdigit() and now - int(v) < client_quiet:
                return "client-typing"
    return None


def nudge_locked(session: str, text: str, verify_s: float, client_quiet: float,
                 lock_wait_s: float = 2.0):
    """The blocker read and the type -> Enter -> verify burst under the ONE cross-process pane lock
    every typer shares (pane_lock.py, gm msg_ede7b24a): the read that says the composer is empty must
    not race a gateway/router burst. Returns (ok, blocker); blocker 'pane-locked' = another sender is
    typing, nothing typed, the caller retries like any other blocker."""
    import pane_lock
    try:
        with pane_lock.pane_lock(session, wait_s=lock_wait_s):
            why = pane_blocker(session, client_quiet)
            if why:
                return False, why
            return nudge(session, text, verify_s), None
    except pane_lock.PaneBusy:
        return False, "pane-locked"


def nudge(session: str, text: str, verify_s: float) -> bool:
    before = turn_state(session).get("turn_number")
    if tmux("send-keys", "-t", session, "-l", text).returncode != 0:
        return False
    time.sleep(0.5)
    if tmux("send-keys", "-t", session, "Enter").returncode != 0:
        return False
    deadline = time.time() + verify_s
    while time.time() < deadline:
        time.sleep(0.5)
        st = turn_state(session)
        if st.get("state") == "working" or st.get("turn_number") != before:
            return True
    # not submitted: clear OUR line only if it is still sitting in the composer
    cap = tmux("capture-pane", "-p", "-t", session)
    if cap.returncode == 0 and (composer_text(cap.stdout) or "") and \
            (composer_text(cap.stdout) or "") in text:
        tmux("send-keys", "-t", session, "C-u")
    return False


# ---------------------------------------------------------------- state
def load_state(path: str) -> dict:
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, d: dict) -> None:
    if load_state(path) == d:
        return
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)


def default_db() -> str:
    """msg_store's own default: MSG_DB_PATH, else <data dir>/state/tasks.db."""
    if os.environ.get("MSG_DB_PATH"):
        return os.environ["MSG_DB_PATH"]
    try:
        if ROOT not in sys.path:
            sys.path.append(ROOT)
        from orchestra_cli.settings import data_dir
        base = str(data_dir())
    except Exception:  # noqa: BLE001 - fall back to the env the seat was launched with
        base = os.environ.get("ORCHESTRA_DIR") or ROOT
    return os.path.join(base, "state", "tasks.db")


def session_alive(session: str) -> bool:
    return tmux("has-session", "-t", f"={session}").returncode == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="instant mechanical mail wake for a Grok seat")
    ap.add_argument("--agent", required=True)
    ap.add_argument("--session", default=None, help="tmux session (default: --agent)")
    ap.add_argument("--tenant", default=os.environ.get("ORCHESTRA_TENANT") or "operator")
    ap.add_argument("--db", default=None, help="msg_store db (default: MSG_DB_PATH or <data dir>/state/tasks.db)")
    ap.add_argument("--orphan-exit-s", type=float, default=120.0,
                    help="exit once the tmux session has been gone this long (0 = never)")
    ap.add_argument("--debounce-s", type=float, default=0.3)
    ap.add_argument("--min-interval-s", type=float, default=1.0,
                    help="at most one db count per this many seconds (the WAL is written ~7x/s)")
    ap.add_argument("--busy-poll-s", type=float, default=2.0)
    ap.add_argument("--safety-s", type=float, default=120.0)
    ap.add_argument("--client-quiet-s", type=float, default=4.0)
    ap.add_argument("--verify-s", type=float, default=15.0)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--once", action="store_true", help="one check, then exit (tests)")
    a = ap.parse_args(argv)
    a.db = a.db or default_db()
    session = a.session or a.agent
    # NOT under state/: that is the watched dir, and our own writes would wake us.
    sdir = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                        "grok-mail-watch")
    os.makedirs(sdir, exist_ok=True)
    state_path = os.path.join(sdir, f"{a.agent}.json")
    # One watcher per seat: spawn starts one on every launch and resume, so a second copy
    # for a seat that already has one exits here (the lock dies with its holder).
    lock_fh = open(os.path.join(sdir, f"{a.agent}.lock"), "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(f"another watcher already holds {a.agent}; exiting")
        return 0
    st = load_state(state_path)
    nudged = set(st.get("nudged", []))
    attempts = int(st.get("attempts", 0))

    ino = Inotify(os.path.dirname(a.db), IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE)
    log(f"watching {a.db} for {a.agent} -> tmux {session} (pid {os.getpid()})")
    last_blocker = None
    waiting = False                     # un-nudged mail exists, seat not yet idle
    last_check = 0.0
    gone_since = None
    while True:
        if not a.once and a.orphan_exit_s > 0:
            if session_alive(session):
                gone_since = None
            elif gone_since is None:
                gone_since = time.monotonic()
            elif time.monotonic() - gone_since >= a.orphan_exit_s:
                log(f"tmux session {session} gone for {a.orphan_exit_s:.0f}s; exiting")
                return 0
        if not a.once:
            idle_wait = a.safety_s if gone_since is None else min(a.safety_s, max(1.0, a.orphan_exit_s / 4))
            names = ino.wait(a.busy_poll_s if waiting else idle_wait)
            if names and not (names & DB_NAMES) and not waiting:
                continue
            if names:
                # coalesce: debounce the burst, and never count more than once per min-interval
                time.sleep(max(a.debounce_s, a.min_interval_s - (time.monotonic() - last_check)))
                ino.wait(0)             # drain the burst
        last_check = time.monotonic()
        try:
            ids = pending_ids(a.db, a.agent, a.tenant)
        except sqlite3.Error as e:
            log(f"db read failed: {e}")
            waiting = False
            if a.once:
                return 1
            continue
        nudged &= set(ids)              # forget rows that left 'pending'
        fresh = [i for i in ids if i not in nudged]
        if not fresh:
            if waiting:
                log("pending mail cleared before a nudge was needed")
            waiting, attempts, last_blocker = False, 0, None
            save_state(state_path, {"nudged": sorted(nudged), "attempts": 0})
            if a.once:
                return 0
            continue
        waiting = True
        if os.path.exists(KILL):
            if last_blocker != "kill-switch":
                log(f"{len(fresh)} new pending; kill switch {KILL} present, not nudging")
                last_blocker = "kill-switch"
            if a.once:
                return 0
            continue
        # An early, unlocked read: it can only SKIP a nudge. The read that decides to type is the one
        # nudge_locked repeats under the pane lock.
        why = pane_blocker(session, a.client_quiet_s)
        if why:
            if why != last_blocker:
                log(f"{len(fresh)} new pending for {a.agent}; holding nudge ({why})")
                last_blocker = why
            if a.once:
                return 0
            continue
        n = len(ids)
        text = (f"[fleet-mail] {n} pending message{'s' if n != 1 else ''} for {a.agent} in msg_store. "
                f"Read: python3 {os.path.join(ROOT, 'msg_store.py')} inbox --agent {a.agent} - "
                f"then reply/ack each.")
        t0 = time.time()
        ok, why = nudge_locked(session, text, a.verify_s, a.client_quiet_s)
        if why:                                   # blocked under the lock: hold, not a failed attempt
            if why != last_blocker:
                log(f"{len(fresh)} new pending for {a.agent}; holding nudge ({why})")
                last_blocker = why
            if a.once:
                return 0
            continue
        if ok:
            nudged |= set(ids)
            attempts = 0
            log(f"nudged {session}: {n} pending ({len(fresh)} new), turn started in "
                f"{time.time() - t0:.1f}s")
        else:
            attempts += 1
            log(f"nudge to {session} did not start a turn (attempt {attempts}/{a.max_attempts})")
            if attempts >= a.max_attempts:
                log("max attempts reached; marking these ids nudged so we stop typing "
                    "(new mail will retry)")
                nudged |= set(ids)
                attempts = 0
        last_blocker = None
        waiting = bool([i for i in ids if i not in nudged])
        save_state(state_path, {"nudged": sorted(nudged), "attempts": attempts})
        if a.once:
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
