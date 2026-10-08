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


# Interpreters a CLI can be wrapped in (`node …/codex`): the runtime is the script's name.
_INTERPRETERS = {"node", "nodejs", "bun", "deno", "python", "python3"}


def _ps_table():
    """{pid: [children]}, {pid: argv} for every process, or None when ps cannot be read. None
    is UNKNOWN, never "no CLI": a loaded box can time ps out, and reading that as "nothing
    running" would relaunch over a live seat (pm review of 4d750ff)."""
    try:
        res = subprocess.run(["ps", "-e", "-ww", "-o", "pid=,ppid=,args="],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0 or not res.stdout.strip():
        return None
    kids, argv = {}, {}
    for line in res.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not (parts[0].isdigit() and parts[1].isdigit()):
            continue
        pid, ppid = int(parts[0]), int(parts[1])
        kids.setdefault(ppid, []).append(pid)
        argv[pid] = parts[2].split()
    return (kids, argv) if argv else None


def _scan_tree(root_pid, table):
    """(runtime or None, busy) for the process tree at root_pid. busy = anything in it besides
    a shell or a tmux client, so a CLI this file cannot name (a `gemini` under node, a renamed
    binary) still keeps the session."""
    kids, argv = table
    todo, seen, runtime, busy = [root_pid], set(), None, False
    while todo:
        pid = todo.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        av = argv.get(pid) or [""]
        name = os.path.basename(av[0]).lstrip("-")
        rt = providers.runtime_for_command(av[0])
        if not rt and name in _INTERPRETERS:
            script = next((a for a in av[1:] if not a.startswith("-")), "")
            rt = providers.runtime_for_command(script)
        if rt and not runtime:
            runtime = rt
        if name not in _IDLE_FOREGROUND:
            busy = True
        todo.extend(kids.get(pid, []))
    return runtime, busy


def pane_state(session):
    got = _panes(session)
    if got is None:
        return {"exists": False, "runtime": None, "foreground": None}
    panes, created = got
    table = _ps_table()
    runtime, busy = None, False
    if table is not None:
        for pid, _ in panes:
            rt, b = _scan_tree(pid, table)
            runtime = runtime or rt
            busy = busy or b
    return {"exists": True, "runtime": runtime, "foreground": panes[0][1] or None,
            "foregrounds": [cmd or None for _, cmd in panes], "created": created,
            "tree": "ok" if table is not None else "unknown", "busy": busy}


def relaunchable(state, min_age_s=None):
    """The process table was read (unknown is never relaunchable), the session exists, nothing
    but shells and tmux clients runs in ANY of its panes, EVERY pane shows only
    the shell its CLI exited to (or a nested client over one), and the session is older than
    min_age_s. The age floor is the startup gap: spawn-agent.sh creates the session, then types
    the CLI into its shell a second or more later; a second spawn inside that gap (a double
    clicked Resume, a starter re-run) must not kill the first one's session."""
    if min_age_s is None:
        min_age_s = float(os.environ.get("ORCH_RELAUNCH_MIN_AGE_S", "30"))
    fgs = state.get("foregrounds") or [state.get("foreground")]
    return bool(state.get("exists")) and state.get("tree") == "ok" and not state.get("busy") \
        and state.get("runtime") is None \
        and all(fg in _IDLE_FOREGROUND for fg in fgs) \
        and bool(state.get("created")) and time.time() - state["created"] >= min_age_s


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
