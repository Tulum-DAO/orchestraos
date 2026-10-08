#!/usr/bin/env python3
"""Is an agent CLI running in a seat's pane? Answered from the pane's PROCESS TREE.

Operator finding #10, 2026-10-08: gm's pane ran a nested `tmux attach` over a shell, and two
readers got it wrong. seats._pane_alive counted ANY child of the pane shell as a live seat,
and spawn-agent.sh treated an existing session as "already running" and returned, so a seat
whose CLI had exited could never be relaunched by `orchestra starter` or the dashboard.

A seat's pane is a shell with the CLI typed into it (spawn-agent.sh), so the CLI is a
DESCENDANT of the pane's pid. It is recognised by argv[0]'s basename through
providers.runtime_for_command, the same rule the status detector uses. A nested client the
CLI itself launched (an agent's Bash tool) still has the CLI above it, so that seat is alive.

    python3 scripts/pane_cli.py <session>                 # JSON {exists, runtime, foreground}
    python3 scripts/pane_cli.py --relaunchable <session>  # exit 0 = safe to relaunch, 1 = not
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers  # noqa: E402

# What a pane shows when nothing of the operator's is running in it: the shell its CLI exited
# to, or a nested tmux client over that shell. Anything else (an editor, a build) may be the
# operator's own work and is never relaunched over.
_IDLE_FOREGROUND = {"sh", "bash", "zsh", "dash", "fish", "ksh", "tmux"}


def _panes(session):
    """[(pane_pid, pane_current_command)] for EVERY pane in EVERY window of the session, plus
    the session's creation time; None when there is no such session. `-s` because a CLI in
    window 0 is not in a shell window the operator opened beside it, and the caller kills the
    WHOLE session (review of the first version: one pane checked, the session killed). `=name:`
    is an exact session match: a bare name would prefix-match a neighbour (seat-x -> seat-xy)."""
    try:
        out = subprocess.run(["tmux", "list-panes", "-s", "-t", f"={session}:", "-F",
                              "#{pane_pid}\t#{pane_current_command}\t#{session_created}"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    panes, created = [], 0
    for line in out.stdout.splitlines():
        pid, cmd, born = (line.split("\t") + ["", ""])[:3]
        if pid.isdigit():
            panes.append((int(pid), cmd.strip()))
        if born.isdigit():
            created = int(born)
    return (panes, created) if panes else None


def _runtime_in_tree(root_pid):
    """The runtime of the first agent CLI at or below root_pid, or None."""
    try:
        out = subprocess.run(["ps", "-e", "-ww", "-o", "pid=,ppid=,args="],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    kids, argv0 = {}, {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        pid, ppid = int(parts[0]), int(parts[1])
        kids.setdefault(ppid, []).append(pid)
        argv0[pid] = parts[2].split()[0] if parts[2].split() else ""
    todo, seen = [root_pid], set()
    while todo:
        pid = todo.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        rt = providers.runtime_for_command(argv0.get(pid, ""))
        if rt:
            return rt
        todo.extend(kids.get(pid, []))
    return None


def pane_state(session):
    got = _panes(session)
    if got is None:
        return {"exists": False, "runtime": None, "foreground": None}
    panes, created = got
    runtime = next((rt for rt in (_runtime_in_tree(pid) for pid, _ in panes) if rt), None)
    return {"exists": True, "runtime": runtime, "foreground": panes[0][1] or None,
            "foregrounds": [cmd or None for _, cmd in panes], "created": created}


def relaunchable(state, min_age_s=None):
    """The session exists, no agent CLI is anywhere in ANY of its panes, EVERY pane shows only
    the shell its CLI exited to (or a nested client over one), and the session is older than
    min_age_s. The age floor is the startup gap: spawn-agent.sh creates the session, then types
    the CLI into its shell a second or more later; a second spawn inside that gap (a double
    clicked Resume, a starter re-run) must not kill the first one's session."""
    if min_age_s is None:
        min_age_s = float(os.environ.get("ORCH_RELAUNCH_MIN_AGE_S", "30"))
    fgs = state.get("foregrounds") or [state.get("foreground")]
    return bool(state.get("exists")) and state.get("runtime") is None \
        and all(fg in _IDLE_FOREGROUND for fg in fgs) \
        and time.time() - (state.get("created") or 0) >= min_age_s


def main(argv):
    if argv[:1] == ["--relaunchable"] and len(argv) == 2:
        return 0 if relaunchable(pane_state(argv[1])) else 1
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-2].strip(), file=sys.stderr)
        return 2
    print(json.dumps(pane_state(argv[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
