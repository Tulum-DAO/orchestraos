#!/usr/bin/env python3
"""nudge_pane.py — the ONE safe way to type a line into a seat's pane.

    python3 scripts/nudge_pane.py <tmux-session> "<text>"

Why: `tmux send-keys -t S "<text>" Enter` in one call arrives as one burst; Claude Code
treats the burst as a paste and inserts the Enter as a literal newline, so the text sits in
the composer unsubmitted and the router then holds every message to that seat as not-idle
(2026-09-17: ios-watch-dev + oss-arturo-dev, 41 minutes). This helper sends the text, waits,
sends Enter SEPARATELY, re-captures, and exits non-zero if the line did not submit.

Refuses (exit 2) when a client is attached to the session (a human may be mid-read/mid-type)
unless --force, and when the composer already holds typed text that is not ours.
Prefer msg_store for anything durable; this is only the wake.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time


def tmux(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def composer_line(session: str) -> str:
    cap = tmux("capture-pane", "-p", "-t", session)
    if cap.returncode != 0:
        return ""
    lines = [l for l in cap.stdout.splitlines() if "❯" in l or l.lstrip().startswith(">")]
    return lines[-1].split("❯", 1)[-1].strip() if lines else ""


def attached(session: str) -> bool:
    out = tmux("list-clients", "-t", session)
    return out.returncode == 0 and bool(out.stdout.strip())


def nudge(session: str, text: str, *, force: bool = False, settle: float = 0.6) -> int:
    if attached(session) and not force:
        print(f"nudge_pane: a client is attached to {session}; refusing (use --force if you own that client)", file=sys.stderr)
        return 2
    before = composer_line(session)
    if before and before not in text:
        print(f"nudge_pane: composer of {session} already holds text {before[:60]!r}; refusing", file=sys.stderr)
        return 2
    tmux("send-keys", "-t", session, text)
    time.sleep(settle)
    tmux("send-keys", "-t", session, "Enter")
    for _ in range(3):
        time.sleep(1.5)
        line = composer_line(session)
        if not line or line not in text:
            print(f"nudge_pane: submitted to {session}: {text[:70]!r}")
            return 0
        tmux("send-keys", "-t", session, "Enter")          # bare retry, never re-send text
    print(f"nudge_pane: {session} did not submit; text still in composer", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session")
    ap.add_argument("text")
    ap.add_argument("--force", action="store_true", help="nudge even if a client is attached")
    ns = ap.parse_args(argv)
    return nudge(ns.session, ns.text, force=ns.force)


if __name__ == "__main__":
    sys.exit(main())
