"""Bring a new Claude seat over to headless once its TUI is up and idle, and turn its own calls on.

A call from an agent's page is with that agent, and new agents start that way. A seat can only
run headless once it has a session to resume, and spawn-agent.sh starts every seat as a TUI, so this runs right after
a New Agent spawn (api/src/routes/agents-new.ts, detached) and does what an operator would by hand, in order:

  1. wait (up to --timeout s) until the pane runs claude, the registry row has a session id, and the TUI is idle
     (no "esc to interrupt" / "esc to cancel" on screen) on two looks 5 s apart: its first task finishes first;
  2. switch it on (headless_switch.set_switch), type /exit, wait for the pane's shell;
  3. seat_supervisor takeover: the supervisor resumes the same session in the same pane;
  4. agent-calls.json seats.<seat>.on = true (kept options; written atomically).

Any failure after step 2 leaves the switch on: every path that brings a seat back in a pane (agent-recovery.sh, red
alert) asks launch-line first, so it comes back as its supervisor. Non-Claude seats are left alone (they stay on tmux).
Logs one JSON line per step to <data dir>/logs/headless-adopt.log.

    python3 scripts/headless_adopt.py <seat> [--timeout 3600] [--by new-agent]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import headless_switch as HS  # noqa: E402
import seat_supervisor as SS  # noqa: E402

BUSY = ("esc to interrupt", "esc to cancel")
SHELLS = SS.SHELLS


def log(seat: str, step: str, **kw) -> None:
    line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seat": seat, "step": step, **kw})
    try:
        p = HS.data_dir() / "logs" / "headless-adopt.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


def pane_cmd(seat: str) -> str:
    return (tmux("display-message", "-t", f"={seat}:", "-p", "#{pane_current_command}").stdout or "").strip()


def tui_idle(seat: str) -> bool:
    if pane_cmd(seat) != "claude":
        return False
    screen = (tmux("capture-pane", "-p", "-t", f"={seat}:").stdout or "").lower()
    return "❯" in screen and not any(b in screen for b in BUSY)


def set_calls_on(seat: str) -> None:
    p = HS.data_dir() / "state" / "agent-calls.json"
    try:
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get("seats"), dict):
            doc = {"seats": {}}
    except (OSError, ValueError):
        doc = {"seats": {}}
    row = doc["seats"].get(seat)
    row = dict(row) if isinstance(row, dict) else {}
    row["on"] = True
    row.setdefault("ack_after_s", 3)
    doc["seats"][seat] = row
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)


def adopt(seat: str, timeout: float, by: str) -> int:
    orch = HS.data_dir()
    if HS.is_headless(seat):
        set_calls_on(seat)
        log(seat, "already_headless")
        return 0
    deadline = time.time() + timeout
    row, idle_since = {}, None
    while time.time() < deadline:
        row = SS.registry_row(seat, orch) or {}
        if row and (row.get("runtime") or "claude").strip().lower() != "claude":
            log(seat, "skip", reason=f"runtime {row.get('runtime')}")
            return 0
        if row.get("session_id") and tui_idle(seat):
            if idle_since and time.time() - idle_since >= 5:
                break
            idle_since = idle_since or time.time()
        else:
            idle_since = None
        time.sleep(2.5)
    else:
        log(seat, "timeout", waited_s=timeout)
        return 3
    try:
        HS.set_switch(seat, True, runtime="claude", by=by)
    except ValueError as e:
        log(seat, "skip", reason=str(e))
        return 0
    log(seat, "switched_on", session_id=row.get("session_id"))
    tmux("send-keys", "-t", f"={seat}:", "-l", "/exit")
    tmux("send-keys", "-t", f"={seat}:", "Enter")
    for _ in range(40):
        if pane_cmd(seat) in SHELLS:
            break
        time.sleep(0.5)
    else:
        log(seat, "tui_did_not_exit", pane=pane_cmd(seat))
        return 4
    ok, info = SS.takeover(seat)
    log(seat, "takeover", ok=ok, info=info if not ok else "typed")
    if not ok:
        return 5
    for _ in range(30):
        try:
            st = HS.status(seat)
            if st.get("ok", True):
                break
        except HS.SupervisorDown:
            pass
        time.sleep(1)
    else:
        log(seat, "supervisor_not_answering")
        return 6
    set_calls_on(seat)
    log(seat, "done", session_id=st.get("session_id"), state=st.get("state"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="headless_adopt.py")
    ap.add_argument("seat")
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--by", default="new-agent")
    a = ap.parse_args(argv)
    return adopt(a.seat, a.timeout, a.by)


if __name__ == "__main__":
    sys.exit(main())
