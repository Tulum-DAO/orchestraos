"""Stage 4 part B: what a green is TOLD about the blue it replaces (DEC-1791324686371842, v2).

green_brief_addendum() returns text for the green's hydration brief:
  * the Level 7 cut point: which of the blue's turns starts the work that is still live, so the
    green keeps that in detail and summarizes the rest (one Jev call);
  * the uncommitted work the blue left in its tree (code only, no Jev), reported as a TOTAL plus
    the paths changed during the blue's session, so 3 real edits and 12,914 inherited dirty paths
    can never read the same (orchestraos-builder's build note on the v2 vote).

CONTRACT (the consensus condition): best-effort and strictly non-blocking. The Jev call is bounded
(3 s, one attempt); git is bounded (5 s). Any failure drops that part, logs it, and the function
still returns -- possibly "". It never raises, so it can never hold a green out of READY or
delay, cancel, or gate a swap. Callers do not need to wrap it.
"""
from __future__ import annotations

import calendar
import json
import os
import subprocess
import time
import traceback
from typing import Callable, List, Optional

from . import audit
from .client import JevClient, JevError
from .compact import cut_point_instructions, cut_point_question, read_transcript, transcript_snapshot

CUT_POINT_TIMEOUT_MS = 3000
GIT_TIMEOUT_S = 5
MAX_LISTED_PATHS = 20


def _bounded_decide(state, questions):
    return JevClient(timeout_ms=CUT_POINT_TIMEOUT_MS, retry_delay_ms=0, max_attempts=1).system_one(state, questions)


def cut_point_line(transcript_path: str, decide: Callable = _bounded_decide) -> Optional[str]:
    """The cut-point instruction, or None when there is nothing to cut or the call failed."""
    try:
        turns = transcript_snapshot(read_transcript(transcript_path))["turns"]
    except OSError as err:
        audit.record("error", {"hook": "handoff.cut_point", "error": f"transcript: {type(err).__name__}: {err}"})
        return None
    if len(turns) < 2:
        return None
    try:
        result = decide({"turns": turns}, cut_point_question(turns))
    except JevError as err:
        audit.record("error", {"hook": "handoff.cut_point", "error": f"{type(err).__name__}: {err}"})
        return None
    cut = cut_point_instructions(turns, result["answers"]["live_from"])
    return None if cut["live_from"] is None else cut["instructions"]


def _porcelain_path(line: str) -> str:
    path = line[3:]
    if " -> " in path:  # rename: report the new name
        path = path.split(" -> ", 1)[1]
    return path.strip().strip('"')


def uncommitted_line(tree: str, session_start: Optional[float]) -> Optional[str]:
    """Total uncommitted paths in `tree`, and which of them changed after `session_start`."""
    try:
        p = subprocess.run(["git", "-C", tree, "status", "--porcelain", "--untracked-files=normal"],
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as err:
        audit.record("error", {"hook": "handoff.uncommitted", "error": f"{type(err).__name__}: {err}"})
        return None
    if p.returncode != 0:
        audit.record("error", {"hook": "handoff.uncommitted", "error": f"git rc {p.returncode}: {p.stderr.strip()[:200]}"})
        return None
    paths = [_porcelain_path(line) for line in p.stdout.splitlines() if len(line) > 3]
    if not paths:
        return None
    total = len(paths)
    plural = "s" if total != 1 else ""
    if session_start is None:
        listed = ", ".join(paths[:MAX_LISTED_PATHS])
        return (f"The tree {tree} has {total} uncommitted path{plural} (the blue's session start is unknown, "
                f"so these may include inherited drift); first {min(total, MAX_LISTED_PATHS)}: {listed}.")
    recent: List[str] = []
    for path in paths:
        try:
            if os.path.getmtime(os.path.join(tree, path)) >= session_start:
                recent.append(path)
        except OSError:
            continue  # deleted paths have no mtime; they are still in the total
    if not recent:
        return (f"The tree {tree} has {total} uncommitted path{plural}, none changed during the blue's session: "
                "all of it predates the blue. Do not go looking for its unfinished edits there.")
    shown = ", ".join(recent[:MAX_LISTED_PATHS])
    more = f" (+{len(recent) - MAX_LISTED_PATHS} more)" if len(recent) > MAX_LISTED_PATHS else ""
    return (f"The blue left uncommitted work: {len(recent)} of the {total} uncommitted paths in {tree} changed "
            f"during its session: {shown}{more}. Check them (git diff) before you start; commit or finish them.")


def session_start_of(transcript_path: str) -> Optional[float]:
    """Epoch of the blue session's first timestamped transcript entry, or None."""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    ts = json.loads(line).get("timestamp")
                except (ValueError, AttributeError):
                    continue
                if ts:
                    # The timestamp is UTC ("...Z"). timegm, not mktime - timezone: the VPS clock is
                    # US Eastern, and mktime's DST shift put the start an hour early (test caught it).
                    return float(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")))
    except OSError:
        return None
    return None


def green_brief_addendum(transcript_path: Optional[str], tree: Optional[str],
                         decide: Callable = _bounded_decide) -> str:
    """Text to append to a green's brief; "" when there is nothing useful. Never raises."""
    parts: List[str] = []
    try:
        start = session_start_of(transcript_path) if transcript_path else None
        if transcript_path:
            line = cut_point_line(transcript_path, decide)
            if line:
                parts.append(line)
        if tree:
            line = uncommitted_line(tree, start)
            if line:
                parts.append(line)
    except Exception as err:  # noqa: BLE001 -- the CONTRACT is never-raise (READY must not depend on this)
        audit.record("error", {"hook": "handoff.green_brief_addendum", "bug": True,
                               "error": f"{type(err).__name__}: {err}",
                               "trace": traceback.format_exc(limit=4)})
    if not parts:
        return ""
    return "From the blue you replace:\n" + "\n".join(f"- {p}" for p in parts)
