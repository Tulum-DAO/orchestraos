"""pipe-pane attach mechanics — the invariant-keeper for the real-time lane.

Placement is BOTH, asymmetrically (per the commission): spawn-agent.sh attaches
at session-create (a latency optimization with no daemon dependency) AND the B1
daemon runs this attach-SWEEP reconcile. The sweep is what survives rotations,
crashes and manual spawns — it is the invariant-keeper (the reconciler-pattern
lesson), NOT another cron.

Four gotchas, all handled here:
  #1 pipe-pane DROPS on session recreation (rotation/respawn silently detaches):
     spawn-time attach ALONE is insufficient. The sweep tracks each session's
     tmux IDENTITY (session-id) under its name and RE-ATTACHES when the identity
     changes beneath a stable name.
  #2 sink must be crash-isolated + non-blocking: see ring.py (the sink writes to
     a tiny O_NONBLOCK drop-oldest ring; a wedged sink never stalls the pane).
  #3 the ring consumer is the ONLY parser; iOS consumes the DERIVED stream, not a
     2nd raw attach (safe alongside :8888 /ws/terminal node-pty which is a
     server-side observe, not a client attach).
  #4 the pipe-pane command runs via `sh -c` => a command-injection surface. The
     sink path is a CONSTANT-prefix template over a VALIDATED session token
     (never an agent-controlled string), and it is shell-quoted in the command.

Fail-isolation (HARD constraint): a pipe-pane attach error on one seat skips ONLY
that seat and is recorded — it NEVER propagates to wedge the sweep, the fleet or
the beat (the Gate-3.5 firewall discipline).

INERT: nothing here attaches on import or construction; a caller must invoke
sweep(), and the daemon that calls it is not wired to any cron/systemd unit by
this build.
"""
import os
import re
import shlex

# ~/.orchestra/ = ephemeral hot surfaces (rebuildable from the durable store),
# per the telemetry-v2 runbook §3. Never durable evidence here.
SINK_DIR = os.path.expanduser("~/.orchestra/realtime/panes")

# A tmux session token safe to place in a filesystem path AND a shell command:
# the fleet's session names are [A-Za-z0-9_-]. Anything else is refused (never
# interpolated) — the command-injection defense.
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9_-]+$")


class UnsafeSessionName(ValueError):
    """A session name carrying characters that could escape the sink path / sh -c
    command. Refused rather than sanitized — never interpolate agent-controlled
    strings into a shell command (gotcha #4)."""


def sink_path(session):
    """Constant-prefix sink path for a VALIDATED session token. Raises
    UnsafeSessionName for anything outside [A-Za-z0-9_-]."""
    if not isinstance(session, str) or not _SAFE_SESSION.match(session):
        raise UnsafeSessionName(f"unsafe session name: {session!r}")
    return os.path.join(SINK_DIR, f"{session}.pipe")


def attach_command(session, sink):
    """The tmux argv to attach pipe-pane for one session. `session` is passed as a
    tmux argv element (never through sh -c); the sh -c PIPE body appends to the
    shell-quoted, validated sink. `-o` only pipes when not already piping."""
    # tmux runs the final arg via `sh -c`; keep it a constant append to a quoted,
    # validated path. No agent-controlled bytes reach the shell.
    pipe_cmd = f"cat >> {shlex.quote(sink)}"
    return ["tmux", "pipe-pane", "-o", "-t", session, pipe_cmd]


class AttachSweep:
    """Reconciles the set of live tmux sessions against the attached set,
    (re)attaching pipe-pane where needed. `tmux` is injectable: it must provide
    ``list_sessions() -> [(name, session_id)]`` and ``pipe_pane(session, sink)``.
    """

    def __init__(self, tmux, *, sink_dir=SINK_DIR):
        self._tmux = tmux
        self._sink_dir = sink_dir
        self.attached = {}          # name -> tmux session-id currently attached

    def _attach(self, name):
        sink = os.path.join(self._sink_dir, f"{name}.pipe") if _SAFE_SESSION.match(name) \
            else _raise_unsafe(name)
        self._tmux.pipe_pane(name, sink)

    def sweep(self):
        """One reconcile pass. Returns {attached, errors, pruned}. NEVER raises —
        a per-seat failure is isolated into `errors` (fail-isolation)."""
        try:
            current = dict(self._tmux.list_sessions())
        except Exception as e:                      # a lister failure must not wedge the beat
            return {"attached": [], "errors": {"__list__": str(e)}, "pruned": []}
        attached_now, errors = [], {}
        for name, sid in current.items():
            # (re)attach when new OR when the tmux identity changed under a stable
            # name (gotcha #1: pipe-pane dropped on session recreation).
            if self.attached.get(name) == sid:
                continue
            try:
                self._attach(name)
            except Exception as e:                  # fail-isolation: skip ONLY this seat
                errors[name] = str(e)
                continue
            self.attached[name] = sid
            attached_now.append(name)
        # prune sessions that are gone
        pruned = [n for n in list(self.attached) if n not in current]
        for n in pruned:
            del self.attached[n]
        return {"attached": attached_now, "errors": errors, "pruned": pruned}


def _raise_unsafe(name):
    raise UnsafeSessionName(f"unsafe session name: {name!r}")
