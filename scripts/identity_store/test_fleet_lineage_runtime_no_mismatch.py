"""P0 fleet guard (gm msg_f9546702 step 5): NO canonical-live lineage may carry a
DB `runtime` that disagrees with the seat's TRUTH — so the stale-claude mislabel
class (a wrong-runtime green + a ctx:unknown production beat) is caught by CI, not
by a failed fire.

TRUTH = the live pane process signature when the seat has a live tmux session
(agy=>gemini, codex=>codex, claude=>claude); else the flat registry.json label.

RED-first: the live fleet had 55 such mismatches (all DB=stale-claude) when this
landed. After the reconcile batch + demo-codex-pred, the only remaining mismatch is
holistic-intent (a model-vs-process conflict held for gm's decision), captured in
EXPECTED_PENDING. The test FAILS on any NEW mismatch or if a pending one changes
shape — that is the CI guard.

LIVE assertion (reads the live registry + processes): skips cleanly when the live
stores are absent (hermetic CI with no fleet).
"""
import json
import os
import subprocess

import pytest

_LIVE = os.path.expanduser("~/scripts/agent-orchestra")
_DB = os.path.join(_LIVE, "state", "orchestra-registry.db")
_REG = os.path.join(_LIVE, "registry.json")

_BIN = {"agy": "gemini", "claude": "claude", "codex": "codex"}

# Roots whose mismatch is KNOWN and deliberately not yet reconciled. Keep minimal;
# adding a root here documents a held exception, removing it re-arms the guard.
# holistic-intent was reconciled (gm-approved gemini-3.7-flash: runtime + model +
# flat, all consistent) so the fleet is now truly zero-mismatch.
EXPECTED_PENDING = set()


def _descendants(pid):
    seen, frontier = [pid], [pid]
    while frontier:
        nxt = []
        for p in frontier:
            for k in subprocess.run(["pgrep", "-P", str(p)], capture_output=True, text=True).stdout.split():
                k = int(k)
                if k not in seen:
                    seen.append(k)
                    nxt.append(k)
        frontier = nxt
    return seen


def _live_runtime(session, live_sessions):
    if session not in live_sessions:
        return None
    pids = subprocess.run(["tmux", "list-panes", "-t", session, "-F", "#{pane_pid}"],
                          capture_output=True, text=True).stdout.split()
    for pp in pids:
        for pid in _descendants(int(pp)):
            try:
                argv = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            except OSError:
                continue
            for tok in argv:
                b = os.path.basename(tok.decode("utf-8", "replace")).lower()
                if b in _BIN:
                    return _BIN[b]
                if b == "claude" or b.endswith("/claude"):
                    return "claude"
    return None


def _mismatches():
    import sqlite3
    con = sqlite3.connect(_DB)
    con.row_factory = sqlite3.Row
    lineages = {r["root"]: r["runtime"] for r in con.execute("SELECT root, runtime FROM lineages")}
    canon = {r["root"]: dict(r) for r in con.execute(
        "SELECT c.root, c.tmux_session, g.retired_at FROM canonical c "
        "JOIN generations g ON g.id = c.generation_id")}
    con.close()
    flat = json.load(open(_REG)).get("agents", {})
    live_sessions = set(subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}"],
                                       capture_output=True, text=True).stdout.split())
    bad = {}
    for root, dbrt in lineages.items():
        c = canon.get(root)
        # canonical-live seats only: a canonical row whose generation is not retired.
        if not c or c.get("retired_at") is not None:
            continue
        session = c.get("tmux_session") or root
        truth = _live_runtime(session, live_sessions) or (flat.get(root) or {}).get("runtime")
        if truth is not None and dbrt != truth:
            bad[root] = (dbrt, truth)
    return bad


def test_no_canonical_live_lineage_runtime_mismatch():
    if not (os.path.exists(_DB) and os.path.exists(_REG)):
        pytest.skip("live identity stores not present")
    bad = _mismatches()
    unexpected = {r: v for r, v in bad.items() if r not in EXPECTED_PENDING}
    assert not unexpected, (
        f"{len(unexpected)} canonical-live lineage(s) have a DB runtime != truth "
        f"(stale-mislabel class -> wrong-runtime green + ctx:unknown): {unexpected}")
