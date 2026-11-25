#!/usr/bin/env python3
"""chrome_orphans.py: find browser-plugin Chrome whose MCP parent is gone.

The browser plugin's MCP server (node .../superpowers-chrome/.../mcp/dist/index.js) starts Chrome
detached, with --user-data-dir under the plugin's profile root, and has no stop action. When the
seat's session ends, the MCP dies and its Chrome is reparented to pid 1 or `systemd --user`, where it
lingers. Those are the only processes this reports.

A main Chrome process (chrome-family executable, no --type=, profile under a plugin root) is:
  ORPHAN when its parent is pid 1, is gone, or is `systemd --user` (or an extra adopter pid);
  SPARED when its parent is the live plugin MCP, or any other live process (that is someone's
  own Chrome, never ours to end).
Chrome outside the plugin roots is never reported.

Usage: chrome_orphans.py            one line per orphan: pid<TAB>profile<TAB>why
       chrome_orphans.py --all      every plugin Chrome with its verdict and parent chain

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


def _is_chrome(comm):
    return comm.startswith("chrome") or comm.startswith("chromium") or "headless" in comm


def _profile(argv):
    for a in argv:
        if a.startswith("--user-data-dir="):
            return a.split("=", 1)[1]
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


def _chain(pid, procs, depth=6):
    names = []
    while pid in procs and depth:
        ppid, comm, _ = procs[pid]
        names.append(f"{pid}:{comm}")
        pid, depth = ppid, depth - 1
    return " <- ".join(names)


def main(argv):
    procs = read_procs(os.environ.get("CHROME_REAPER_PROC", "/proc"))
    adopters = {int(p) for p in os.environ.get("CHROME_REAPER_ADOPTERS", "").split(",") if p.strip()}
    rows = classify(procs, roots(), adopters)
    for pid, prof, verdict, why in rows:
        if "--all" in argv:
            print(f"{verdict}\t{pid}\t{prof}\t{why}\t{_chain(pid, procs)}")
        elif verdict == "orphan":
            print(f"{pid}\t{prof}\t{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
