#!/usr/bin/env python3
"""PreToolUse(Bash) guard: refuse `git worktree add` with a path INSIDE the live tree (gm msg_72115512 (5)).

WHY: worktrees nested under ~/scripts/agent-orchestra (.workspace/, .claude/worktrees/, a seat's
subdir) are inside every tree-wide edit, grep and sweep run from the live root. On 2026-10-08 a
token-redaction pass from the root rewrote the working copies of ~53 nested worktrees belonging to
other seats, and each nested full checkout was another on-disk copy of every committed secret.
RULE (gm ruling, ADOPTED 2026-10-10): new worktrees live OUTSIDE the live root, under
~/wt/<seat>/<name>. Existing nested ones migrate only when touched.

Exit 2 + a teaching message on refusal; exit 0 (silent) for everything else, including any
command this cannot parse. CEILING: a PreToolUse hook sees an AGENT'S Bash calls only. It does
not see Claude Code's own `isolation: worktree` / EnterWorktree (which create .claude/worktrees/),
cron, daemons or a human shell.
"""
import json
import os
import shlex
import sys

LIVE_ROOT = os.path.realpath(os.environ.get("WORKTREE_GUARD_LIVE_ROOT",
                                            os.path.expanduser("~/scripts/agent-orchestra")))
# `git worktree add` options that take a separate value argument.
_ADD_OPTS_WITH_VALUE = {"-b", "-B", "--reason", "--orphan"}
_SEPARATORS = {"&&", "||", ";", "|", "&"}


def _segments(tokens):
    seg = []
    for t in tokens:
        if t in _SEPARATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg


def _add_targets(seg, cwd):
    """(resolved_path) for each `git [-C dir] worktree add ... <path>` in one command segment."""
    i = 0
    while i < len(seg) and "=" in seg[i] and not seg[i].startswith("-"):
        i += 1                                    # leading VAR=value env assignments
    if i >= len(seg) or os.path.basename(seg[i]) != "git":
        return []
    i += 1
    base = cwd
    while i < len(seg) and seg[i].startswith("-"):
        if seg[i] == "-C" and i + 1 < len(seg):
            base = os.path.join(base, os.path.expanduser(seg[i + 1]))
            i += 2
            continue
        if seg[i] in ("-c", "--git-dir", "--work-tree") and i + 1 < len(seg):
            i += 2
            continue
        i += 1
    if seg[i:i + 2] != ["worktree", "add"]:
        return []
    rest = seg[i + 2:]
    j = 0
    while j < len(rest):
        t = rest[j]
        if t == "--":
            j += 1
            break
        if t in _ADD_OPTS_WITH_VALUE:
            j += 2
            continue
        if t.startswith("-"):
            j += 1
            continue
        break
    if j >= len(rest):
        return []
    path = os.path.expanduser(rest[j])
    return [os.path.realpath(os.path.join(base, path))]


def offending(command, cwd=None):
    """Worktree paths in ``command`` that fall inside LIVE_ROOT (empty list = allow)."""
    cwd = cwd or os.getcwd()
    try:
        tokens = shlex.split(command, comments=True, posix=True)
    except ValueError:
        return []
    bad = []
    for seg in _segments(tokens):
        for p in _add_targets(seg, cwd):
            if p == LIVE_ROOT or p.startswith(LIVE_ROOT + os.sep):
                bad.append(p)
    return bad


def main():
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    if not isinstance(data, dict) or data.get("tool_name") != "Bash":
        return 0
    bad = offending((data.get("tool_input") or {}).get("command", ""), cwd=data.get("cwd") or None)
    if not bad:
        return 0
    seat = os.environ.get("AGENT_ID") or "<seat>"
    sys.stderr.write(
        f"BLOCKED (worktree-path-guard): `git worktree add` into {bad[0]} is INSIDE the live tree "
        f"({LIVE_ROOT}). NOTHING WAS RUN.\n"
        "WHY: nested worktrees sit inside every tree-wide edit/grep/sweep run from the live root. "
        "On 2026-10-08 one redaction pass rewrote the working copies of ~53 other seats' nested "
        "worktrees, and every nested checkout is another on-disk copy of each committed secret.\n"
        f"DO THIS INSTEAD: git worktree add ~/wt/{seat}/<name> <branch>   (rule adopted by gm 2026-10-10).\n"
        "STILL LEGAL: any worktree path outside the live tree, including your session scratchpad "
        "under /tmp. Existing nested worktrees stay until you next touch them.\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
