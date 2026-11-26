#!/usr/bin/env python3
"""chrome_orphans.py: find browser-plugin Chrome whose MCP parent is gone.

The browser plugin's MCP server (node .../superpowers-chrome/.../mcp/dist/index.js) starts Chrome
detached, with --user-data-dir under the plugin's profile root, and has no stop action. When the
seat's session ends, the MCP dies and its Chrome is reparented to pid 1 or `systemd --user`, where it
lingers. Those are the only processes this reports.

A main Chrome process (chrome-family executable, no --type=, profile under a plugin root) is:
  ORPHAN when its parent is pid 1, is gone, or is `systemd --user` (or an extra adopter pid);
  SPARED when its parent is the live plugin MCP, or any other live process (never orphan-reaped).
Chrome outside the plugin roots is never reported. protected() is narrower: only a plugin Chrome whose
plugin MCP is alive (parent, or ancestor through a wrapper) is exempt from the age rule.

Usage: chrome_orphans.py              one line per orphan: pid<TAB>profile<TAB>why
       chrome_orphans.py --all        every plugin Chrome with its verdict and parent chain
       chrome_orphans.py --protected  pids the age rule must spare: live-parent plugin Chrome + subtree
       chrome_orphans.py --kill-plan  orphans + subtrees with start times: O/T<TAB>pid<TAB>start[...]
       chrome_orphans.py --still P:S  of pid:start pairs, the pids that are still that same browser

Environment (tests): CHROME_REAPER_PROFILE_ROOTS (colon-separated roots, replaces the defaults),
CHROME_REAPER_ADOPTERS (extra pids that count as adopters, like a test that is a subreaper),
CHROME_REAPER_PROC (proc root, default /proc).
"""
import os
import sys

DEFAULT_ROOTS = ("~/.cache/superpowers/browser-profiles",
                 "~/snap/chromium/common/superpowers-browser-profiles")
MCP_MARKERS = ("superpowers-chrome", "mcp/dist/index.js")


def roots():
    raw = os.environ.get("CHROME_REAPER_PROFILE_ROOTS")
    items = raw.split(":") if raw else DEFAULT_ROOTS
    # both spellings of one root (a symlink and its target) resolve to the same real path
    return sorted({os.path.realpath(os.path.expanduser(r)) for r in items if r})


def read_procs(proc="/proc"):
    """{pid: (ppid, comm, argv list)} for every readable process."""
    out = {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "stat"), encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
            with open(os.path.join(proc, name, "cmdline"), "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except OSError:
            continue
        # comm is in parentheses and may itself contain spaces or ")"
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        out[int(name)] = (ppid, comm, argv)
    return out


def identity(pid, proc="/proc"):
    """(comm, start time in clock ticks since boot) of a live process, else None. The start time
    (stat field 22) tells a process apart from a later one that reuses its pid."""
    try:
        with open(os.path.join(proc, str(pid), "stat"), encoding="utf-8", errors="replace") as fh:
            stat = fh.read()
    except OSError:
        return None
    rest = stat[stat.rindex(")") + 2:].split()       # rest[0] is field 3 (state)
    if rest[0] in ("Z", "X"):
        return None
    return stat[stat.index("(") + 1:stat.rindex(")")], int(rest[19])


def still_same(entries, proc="/proc"):
    """Of "pid:start" entries, the pids that are still THAT process (same start time) and a browser."""
    out = []
    for e in entries:
        pid, _, start = e.partition(":")
        if not (pid.isdigit() and start.isdigit()):
            continue
        ident = identity(int(pid), proc)
        if ident and ident[1] == int(start) and _is_chrome(ident[0]):
            out.append(int(pid))
    return out


def kill_plan(procs, rts, adopters=(), proc="/proc"):
    """Orphans and their subtrees, each with its start time, read BEFORE any signal: once a browser
    exits its renderers are reparented and can no longer be found by walking down from it.
    Rows: ("O", pid, start, profile, why) for an orphan, ("T", pid, start) for a descendant."""
    kids = {}
    for pid, (ppid, _comm, _argv) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    rows = []
    for pid, prof, verdict, why in classify(procs, rts, adopters):
        if verdict != "orphan":
            continue
        ident = identity(pid, proc)
        if not ident:
            continue
        rows.append(("O", pid, ident[1], prof, why))
        todo = list(kids.get(pid, ()))
        while todo:
            k = todo.pop()
            ki = identity(k, proc)
            if ki:
                rows.append(("T", k, ki[1]))
            todo.extend(kids.get(k, ()))
    return rows


def _is_chrome(comm):
    return comm.startswith("chrome") or comm.startswith("chromium") or "headless" in comm


def _profile(argv):
    """The --user-data-dir value, or None. Only an absolute path with no control characters counts:
    a relative or ~ path would be resolved in the reaper's cwd, not Chrome's, and a newline or tab
    would let a crafted argument forge an extra output row (a kill of another pid)."""
    for a in argv:
        if a.startswith("--user-data-dir="):
            v = a.split("=", 1)[1]
            if not v.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in v):
                return None
            return v
    return None


def _under(path, rts):
    real = os.path.realpath(os.path.expanduser(path))
    return any(real == r or real.startswith(r + os.sep) for r in rts)


def _is_mcp(entry):
    _ppid, comm, argv = entry
    joined = " ".join(argv)
    return comm.startswith("node") and all(m in joined for m in MCP_MARKERS)


def classify(procs, rts, adopters=()):
    """[(pid, profile, verdict, why)] for every main plugin Chrome. verdict = 'orphan' | 'spared'."""
    res = []
    for pid, (ppid, comm, argv) in sorted(procs.items()):
        if not _is_chrome(comm) or any(a.startswith("--type=") for a in argv):
            continue
        prof = _profile(argv)
        if not prof or not _under(prof, rts):
            continue
        parent = procs.get(ppid)
        if ppid <= 1:
            res.append((pid, prof, "orphan", "reparented to pid 1"))
        elif parent is None:
            res.append((pid, prof, "orphan", f"parent {ppid} is gone"))
        elif parent[1] == "systemd" and "--user" in parent[2]:
            res.append((pid, prof, "orphan", f"reparented to systemd --user ({ppid})"))
        elif ppid in adopters:
            res.append((pid, prof, "orphan", f"reparented to adopter {ppid}"))
        elif _is_mcp(parent):
            res.append((pid, prof, "spared", f"live plugin MCP parent {ppid}"))
        else:
            res.append((pid, prof, "spared", f"live parent {ppid} ({parent[1]})"))
    return res


def _has_live_mcp_ancestor(pid, procs, depth=8):
    """True when a live plugin MCP is the parent, or an ancestor through wrapper processes."""
    ppid = procs[pid][0]
    while depth and ppid > 1 and ppid in procs:
        if _is_mcp(procs[ppid]):
            return True
        ppid, depth = procs[ppid][0], depth - 1
    return False


def protected(procs, rts):
    """Pids the AGE rule must not touch: every plugin Chrome whose plugin MCP is alive (as its parent or
    an ancestor through a wrapper), and everything under it (renderers, GPU, utility, a crashpad handler
    when it sits under it). A plugin Chrome with some OTHER live parent is not orphan-reaped, but keeps
    the age rule, as before: an MCP that died behind a surviving wrapper must not leak a browser forever."""
    kids = {}
    for pid, (ppid, _comm, _argv) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = set(), [pid for pid, _prof, verdict, _why in classify(procs, rts)
                        if verdict == "spared" and _has_live_mcp_ancestor(pid, procs)]
    while todo:
        pid = todo.pop()
        if pid not in out:
            out.add(pid)
            todo.extend(kids.get(pid, ()))
    return out


def _chain(pid, procs, depth=6):
    names = []
    while pid in procs and depth:
        ppid, comm, _ = procs[pid]
        names.append(f"{pid}:{comm}")
        pid, depth = ppid, depth - 1
    return " <- ".join(names)


def main(argv):
    proc = os.environ.get("CHROME_REAPER_PROC", "/proc")
    if argv[:1] == ["--still"]:
        for pid in still_same(argv[1:], proc):
            print(pid)
        return 0
    procs = read_procs(proc)
    adopters = {int(p) for p in os.environ.get("CHROME_REAPER_ADOPTERS", "").split(",") if p.strip()}
    if "--kill-plan" in argv:
        for row in kill_plan(procs, roots(), adopters, proc):
            print("\t".join(str(x) for x in row))
        return 0
    if "--protected" in argv:
        for pid in sorted(protected(procs, roots())):
            print(pid)
        return 0
    rows = classify(procs, roots(), adopters)
    for pid, prof, verdict, why in rows:
        if "--all" in argv:
            print(f"{verdict}\t{pid}\t{prof}\t{why}\t{_chain(pid, procs)}")
        elif verdict == "orphan":
            print(f"{pid}\t{prof}\t{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
