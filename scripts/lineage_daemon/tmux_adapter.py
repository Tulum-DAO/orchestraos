"""Concrete tmux adapter for pipe_pane.AttachSweep.

AttachSweep is injected with a `tmux` object providing exactly:
  list_sessions() -> [(name, session_id)]
  pipe_pane(session, sink)
Only test fakes existed in-tree; this is the production adapter. The subprocess
runner is injectable (dependency injection) so parsing + argv are unit-tested
without a live tmux server. Command-injection defense is REUSED from pipe_pane:
the session name is validated against the fleet charset and the sink is
shell-quoted inside the attach command body (never interpolated raw).
"""
import subprocess

from .realtime.pipe_pane import UnsafeSessionName, _SAFE_SESSION, attach_command

_LIST_FMT = "#{session_name} #{session_id}"
# window 0 / pane 0 is the fleet's agent pane (mirrors codex_context._pane_pid's
# `{session}:0.0`). ONE list-panes -a fans the whole fleet in a single subprocess,
# carrying BOTH the pane_pid (root pid resolve) and the pane_id (the join key for
# the hook event file) so the per-slow-tick refresh needs NO per-seat subprocess.
_PANES_FMT = "#{session_name} #{window_index} #{pane_index} #{pane_id} #{pane_pid}"


def _default_runner(argv):
    """Run argv, return (returncode, stdout). Never raises on a tmux non-zero
    exit (no server running is a normal empty result)."""
    p = subprocess.run(argv, capture_output=True, text=True)
    return (p.returncode, p.stdout or "")


class Tmux:
    def __init__(self, runner=_default_runner):
        self._run = runner

    def list_sessions(self):
        """[(name, session_id)] for live sessions. Empty when no server is
        running (tmux exits non-zero) — never raises. Malformed lines skipped."""
        rc, out = self._run(["tmux", "list-sessions", "-F", _LIST_FMT])
        if rc != 0:
            return []
        pairs = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2:                 # exactly name + session-id
                pairs.append((parts[0], parts[1]))
        return pairs

    def pane_map(self):
        """{session_name: (pane_id, pane_pid)} for window 0 / pane 0 of every live
        session, in ONE `tmux list-panes -a` subprocess (the batched per-slow-tick
        resolve — NOT one display-message per seat). pane_id is the join key for the
        hook event file; pane_pid is the root pid. Empty when no server is running
        (non-zero exit) — never raises. Malformed lines / non-integer pids skipped;
        only the 0.0 pane is kept (the fleet's agent pane)."""
        rc, out = self._run(["tmux", "list-panes", "-a", "-F", _PANES_FMT])
        if rc != 0:
            return {}
        panes = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            name, win, pane, pane_id, pid = parts
            if win != "0" or pane != "0":       # only the agent pane (window0/pane0)
                continue
            try:
                panes[name] = (pane_id, int(pid))
            except ValueError:
                continue
        return panes

    def pipe_pane(self, session, sink):
        """Attach pipe-pane for one session to `sink`. Refuses an unsafe session
        name (command-injection defense — reused from pipe_pane) BEFORE running
        anything; the sink is shell-quoted by attach_command."""
        if not isinstance(session, str) or not _SAFE_SESSION.match(session):
            raise UnsafeSessionName(f"unsafe session name: {session!r}")
        self._run(attach_command(session, sink))
