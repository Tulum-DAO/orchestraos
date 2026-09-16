"""P0 guard (gm msg_e40e9b52): a seat's DB lineage `runtime` MUST match the
runtime its LIVE process actually runs.

By effect: demo-gemini-pred's live pane runs `agy` (the Gemini/antigravity CLI) =
runtime gemini, but orchestra-registry.db lineages.runtime was 'claude' (migrate
derived it from model='unknown' -> claude, ignoring the flat 'gemini' label). A
faithful projector then inherits that WRONG label onto every green -> a non-claude
rotation silently spawns Claude. This guard makes the mislabel loud: the stored
runtime must equal the live-process runtime.

LIVE assertion (like M1_live): skips a seat that is not currently live, so it is a
no-op in a hermetic CI with no fleet, and a hard guard on the VPS.
"""
import os
import sqlite3
import subprocess

import pytest

_LIVE = os.path.expanduser("~/scripts/agent-orchestra")
_DB = os.path.join(_LIVE, "state", "orchestra-registry.db")

# the CLI binary a live pane runs -> the runtime it IS (agy is the Gemini CLI).
_BIN_RUNTIME = {"agy": "gemini", "claude": "claude", "codex": "codex"}


def _pane_pids(seat):
    out = subprocess.run(["tmux", "list-panes", "-t", seat, "-F", "#{pane_pid}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [int(p) for p in out.stdout.split()]


def _descendants(pid):
    seen = [pid]
    frontier = [pid]
    while frontier:
        nxt = []
        for p in frontier:
            kids = subprocess.run(["pgrep", "-P", str(p)], capture_output=True, text=True).stdout.split()
            for k in kids:
                k = int(k)
                if k not in seen:
                    seen.append(k)
                    nxt.append(k)
        frontier = nxt
    return seen


def _live_runtime(seat):
    """Resolve the runtime the seat's live pane process actually runs, or None."""
    for pp in _pane_pids(seat):
        for pid in _descendants(pp):
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    argv = fh.read().split(b"\0")
            except OSError:
                continue
            for tok in argv:
                base = os.path.basename(tok.decode("utf-8", "replace")).lower()
                if base in _BIN_RUNTIME:
                    return _BIN_RUNTIME[base]
                # node wrapper: .../claude ; explicit claude in path
                if base == "claude" or base.endswith("/claude"):
                    return "claude"
    return None


def _lineage_runtime(seat):
    con = sqlite3.connect(_DB)
    try:
        row = con.execute("SELECT runtime FROM lineages WHERE root=?", (seat,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _assert_seat(seat):
    if not os.path.exists(_DB):
        pytest.skip("live registry db not present")
    live = _live_runtime(seat)
    if live is None:
        pytest.skip(f"seat {seat} not live (no resolvable CLI process)")
    db = _lineage_runtime(seat)
    assert db == live, (
        f"lineage runtime for {seat!r} is {db!r} but its LIVE process runs "
        f"{live!r} — a mislabeled lineage mints wrong-runtime greens on rotation")


def test_gemini_seat_lineage_runtime_matches_live_agy():
    """demo-gemini-pred runs agy (gemini); its lineage runtime must be 'gemini'."""
    _assert_seat("demo-gemini-pred")


def test_claude_seat_lineage_runtime_matches_live_claude():
    """Regression: a live claude seat's lineage runtime must be 'claude'."""
    _assert_seat("orchestra-builder")
