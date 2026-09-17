"""C-R1: process-bound codex context reader (SPEC_codex-parity-phase2 §C-R1).

Source of truth is the CODEX ROLLOUT JSONL held OPEN by the pane's own codex
process — never pane text, model branding, file mtime, or "most recent session"
(dossier §7: a resumable session id is not a process instance; the wrong-specimen
class). Token events are structured:

    {"type":"event_msg","payload":{"type":"token_count","info":{
        "last_token_usage": {..., "total_tokens": N},
        "model_context_window": M}}}

used_pct = last_token_usage.total_tokens / model_context_window (matches codex's
own footer math, verified against the live seat 2026-08-27).

FAIL-CLOSED contract (returns None => widget renders --/--):
  - no unique codex process on the pane's TTY / no pid resolvable
  - the process holds no open rollout JSONL
  - no token_count event with a valid window in the tail
Perf: reads only the last TAIL_BYTES of the rollout; callers may cache by
(pid, start-ticks, inode, offset) — the widget's 5s refresh must not rescan a
multi-MB transcript.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

TAIL_BYTES = 131072  # token_count events are frequent; 128KB tail is plenty


def _pane_pid(session_name: str) -> int | None:
    """tmux pane pid for window 0 pane 0 of the session (fleet layout)."""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"{session_name}:0.0",
             "#{pane_pid}"],
            capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def _codex_pid_under(pane_pid: int) -> int | None:
    """The codex process under the pane shell. Checks the pane pid itself, then
    children (pgrep -P), matching the executable/cmdline — NEVER assumes the
    wrapper is the process (the wrapper-vs-child specimen class)."""
    def is_codex(pid: int) -> bool:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode(errors="replace")
            return "codex" in cmd.split("\x00")[0].rsplit("/", 1)[-1]
        except OSError:
            return False

    if is_codex(pane_pid):
        return pane_pid
    try:
        r = subprocess.run(["pgrep", "-P", str(pane_pid)],
                           capture_output=True, text=True, timeout=5)
        kids = [int(x) for x in r.stdout.split()] if r.returncode == 0 else []
    except (subprocess.SubprocessError, OSError, ValueError):
        kids = []
    hits = [k for k in kids if is_codex(k)]
    # descend one more level (bash -c wrapper case)
    if not hits:
        for k in kids:
            try:
                r2 = subprocess.run(["pgrep", "-P", str(k)],
                                    capture_output=True, text=True, timeout=5)
                for g in ([int(x) for x in r2.stdout.split()]
                          if r2.returncode == 0 else []):
                    if is_codex(g):
                        hits.append(g)
            except (subprocess.SubprocessError, OSError, ValueError):
                continue
    return hits[0] if len(hits) == 1 else None   # ambiguity => fail closed


def _open_rollout(pid: int) -> str | None:
    """The rollout JSONL this pid holds OPEN (positive pid->session binding)."""
    fd_dir = f"/proc/{pid}/fd"
    try:
        for fd in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if re.search(r"/\.codex/sessions/.+/rollout-.*\.jsonl$", target):
                return target
    except OSError:
        return None
    return None


def last_token_event(rollout_path: str, tail_bytes: int = TAIL_BYTES) -> dict | None:
    """Last token_count payload-info from the rollout tail, else None."""
    try:
        size = os.path.getsize(rollout_path)
        with open(rollout_path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            tail = f.read().decode(errors="replace")
    except OSError:
        return None
    best = None
    for line in tail.splitlines():
        if '"token_count"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        p = d.get("payload") or {}
        if p.get("type") == "token_count" and isinstance(p.get("info"), dict):
            best = p["info"]
    return best


def context_from_info(info: dict) -> tuple[float, int, int] | None:
    """(used_pct, used_tokens, window) from a token_count info dict, else None."""
    window = info.get("model_context_window")
    last = info.get("last_token_usage") or {}
    used = last.get("total_tokens")
    if used is None:
        it, ot = last.get("input_tokens"), last.get("output_tokens")
        if it is not None:
            used = int(it) + int(ot or 0)
    if not isinstance(window, int) or window <= 0 or used is None:
        return None
    used = int(used)
    return (min(100.0, used * 100.0 / window), used, window)


def get_codex_session_context(session_name: str) -> tuple[float, int, int] | None:
    """Widget entry point: (used_pct, tokens, limit) or None (=> --/--)."""
    pane = _pane_pid(session_name)
    if pane is None:
        return None
    pid = _codex_pid_under(pane)
    if pid is None:
        return None
    rollout = _open_rollout(pid)
    if rollout is None:
        return None
    info = last_token_event(rollout)
    if info is None:
        return None
    return context_from_info(info)
