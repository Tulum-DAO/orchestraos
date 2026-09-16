"""Spawn-time pipe-pane attach (Decision 3) — called by spawn-agent.sh right after
`tmux new-session`. The latency-optimization half of the BOTH-asymmetric placement
(the telemetryd attach-SWEEP is the rotation invariant-keeper). Command-injection
defense + shell-quoting are REUSED from pipe_pane: a validated [A-Za-z0-9_-] token
and a shlex-quoted sink, never raw interpolation. FAIL-SOFT: an attach failure is
reported (return False / exit 1) and NEVER raises into the spawn path.

Run standalone by the shell:  python3 scripts/lineage_daemon/spawn_pipe_attach.py <session>
"""
import os
import subprocess
import sys
import time

# absolute-within-package import so this works both as `-m` and as a direct script
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from lineage_daemon.realtime.pipe_pane import (        # noqa: E402
    SINK_DIR, UnsafeSessionName, attach_command, sink_path)

# spawn-time attach is gated on telemetryd being live: attaching pipe-pane with
# no consumer grows the sink file unbounded. status.json freshness IS the
# liveness signal (the daemon rewrites it every ~1s). This is what keeps the
# spawn-agent.sh change INERT until the operator installs+starts the unit.
DAEMON_FRESH_S = 12.0


def _default_runner(argv):
    p = subprocess.run(argv, capture_output=True, text=True)
    return (p.returncode, p.stdout or "")


def _daemon_live():
    """True iff telemetryd is live (a fresh status.json exists). Fail-safe False."""
    try:
        from lineage_daemon.realtime.snapshot import read_status_snapshot
        snap = read_status_snapshot(os.environ.get("ORCHESTRA_REALTIME_DIR") or None)
        return bool(snap) and (time.time() - float(snap.get("ts", 0))) <= DAEMON_FRESH_S
    except Exception:
        return False


def attach(session, *, runner=_default_runner, sink_dir=SINK_DIR, is_live=None):
    """Attach pipe-pane for `session` to the constant-template sink. Returns True
    on success, False when the daemon is not live (no consumer), on an unsafe
    name, or on ANY runner error (fail-soft — never raises)."""
    live = is_live() if is_live is not None else _daemon_live()
    if not live:
        return False                          # no consumer => do not orphan a growing sink
    try:
        # sink is derived only from the CONSTANT prefix + a VALIDATED token.
        sink = os.path.join(sink_dir, os.path.basename(sink_path(session)))
    except UnsafeSessionName:
        return False                          # refuse; execute nothing
    try:
        os.makedirs(sink_dir, exist_ok=True)
        runner(attach_command(session, sink))
        return True
    except Exception:
        return False                          # wedged/failed tmux never blocks spawn


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        return 1
    return 0 if attach(argv[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
