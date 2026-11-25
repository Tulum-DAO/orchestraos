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
import json
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
    shell-quoted, validated sink.

    NO `-o`: `pipe-pane -o` is a TOGGLE, not "attach if missing". On a pane that already
    has a pipe it CLOSES it (measured, tmux 3.4: 0 -> 1 -> 0 -> 1). That switched off an
    operator's own pane logger, and on every telemetryd restart it closed the pipes the
    previous run had attached, then recorded them as attached, so a seat's live status
    went dark until its session was recreated. The caller checks #{pane_pipe} first and
    attaches only to an UNPIPED pane (tmux_adapter.Tmux.pipe_pane)."""
    # tmux runs the final arg via `sh -c`; keep it a constant append to a quoted,
    # validated path. No agent-controlled bytes reach the shell.
    pipe_cmd = f"cat >> {shlex.quote(sink)}"
    return ["tmux", "pipe-pane", "-t", session, pipe_cmd]


# The sweep's persisted {tmux session-id: sink} map, beside the sinks. A dotfile: readers
# only ever open `<name>.pipe`, and a session name cannot start with a dot.
BINDINGS_FILE = ".bindings.json"


def _ident(path):
    """(st_dev, st_ino) of the file `path` resolves to (symlinks followed), or None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _proc_writer_inodes(sink_dir, proc_root="/proc"):
    """{(dev, ino)} of every file under `sink_dir` some live process holds open: the pane
    pipes (`cat >> <sink>`). The evidence for a pipe whose session-id no map knows (tmux
    reports only #{pane_pipe}, never the pipe's target). Read-only; never raises."""
    prefix = os.path.realpath(sink_dir) + os.sep
    out = set()
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        fd_dir = os.path.join(proc_root, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            link = os.path.join(fd_dir, fd)
            try:
                if not os.readlink(link).startswith(prefix):
                    continue
                st = os.stat(link)
            except OSError:
                continue
            out.add((st.st_dev, st.st_ino))
    return out


class AttachSweep:
    """Reconciles the set of live tmux sessions against the attached set,
    (re)attaching pipe-pane where needed. `tmux` is injectable: it must provide
    ``list_sessions() -> [(name, session_id)]`` and ``pipe_pane(session, sink)``
    (False = the pane was already piped and was left alone); ``server_id()`` is optional.

    FOLLOW THE PANE, NOT THE NAME. A tmux session-id ($N) is stable across
    `rename-session` for the server's life, and a pane's pipe keeps writing the sink of
    the name it was attached under. A blue-green swap spawns `<root>-gN` (piped into
    `<root>-gN.pipe`) and renames it `<root>`, while the seat's ring reads `<root>.pipe`.
    So the sweep keeps {session-id: sink} and makes `<name>.pipe` RESOLVE to the pane's
    live sink (an atomic symlink swap), so every reader that opens by name gets the
    growing file. The map is persisted (BINDINGS_FILE) because tmux cannot report where
    a pipe goes, so a restart after a rename still knows.

    A session whose pane is piped (pipe_pane False) but whose session-id no map knows is
    adopted only on evidence: a live writer holding `<name>.pipe`, or exactly one orphan
    green sink `<name>-g<N>.pipe` with a live writer and no live session of that name (a
    rename made before this map existed). With no evidence it is UNBOUND: is_unbound()
    tells telemetryd its silence means nothing.
    """

    def __init__(self, tmux, *, sink_dir=SINK_DIR, bindings_path=None, writers=None):
        self._tmux = tmux
        self._sink_dir = sink_dir
        self._bindings_path = bindings_path or os.path.join(sink_dir, BINDINGS_FILE)
        # () -> {(dev, ino)} of sink files held open by a live writer; injectable for tests.
        self._writers = writers or (lambda: _proc_writer_inodes(self._sink_dir))
        self.attached = {}          # name -> tmux session-id currently attached
        self._sinks = None          # session-id -> sink path; loaded lazily (INERT construction)
        self._server = None
        self.unbound = set()        # live names whose pipe has no sink their ring can read
        self.retired = set()        # names whose pane now runs under ANOTHER live name

    def is_retired(self, name):
        """True when `name` is gone because its pane was RENAMED (a green alias after its
        swap), not because it died: the seat is the new name now. A vanished pane is not
        retired (it must still read offline); a name a new session takes is live again."""
        return name in self.retired

    def is_unbound(self, name):
        """True when `name`'s pane is piped, but into no sink `<name>.pipe` resolves to, so
        the seat's ring has no output signal. Callers must not read its silence as idle."""
        return name in self.unbound

    # --- the persisted map ---------------------------------------------------
    def _load(self):
        try:
            with open(self._bindings_path) as fh:
                obj = json.load(fh)
            sinks = obj.get("sinks") or {}
            self._server = obj.get("server")
            self._sinks = {str(k): (v if os.path.isabs(v) else os.path.join(self._sink_dir, v))
                           for k, v in sinks.items() if isinstance(v, str) and v}
        except (OSError, ValueError, AttributeError):
            self._sinks = {}

    def _dump(self):
        def rel(p):
            return os.path.basename(p) if os.path.dirname(p) == self._sink_dir else p
        return {"server": self._server, "sinks": {k: rel(v) for k, v in sorted(self._sinks.items())}}

    def _save(self):
        os.makedirs(self._sink_dir, exist_ok=True)
        tmp = f"{self._bindings_path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump(self._dump(), fh)
        os.replace(tmp, self._bindings_path)          # atomic: a reader never sees a partial

    def _check_server(self):
        """Session-ids restart at $0 on a new tmux server, so a map from another server
        would bind a new pane to a stranger's sink: drop it. A map saved with NO server id
        cannot prove it belongs to this one either: dropped once the server is known. An
        adapter that cannot name the server (None) keeps the map."""
        fn = getattr(self._tmux, "server_id", None)
        try:
            sid = fn() if fn is not None else None
        except Exception:
            sid = None
        if sid is None:
            return
        if self._server != sid:
            self._sinks = {}
            self.attached = {}
        self._server = sid

    # --- binding -------------------------------------------------------------
    def _adopt(self, name, own, claimed, live_names, writers):
        """The sink a piped-but-unmapped pane writes, on evidence only, else None."""
        ident = _ident(own)
        if ident is not None and ident in writers and ident not in claimed:
            return os.path.realpath(own)
        pat = re.compile(re.escape(name) + r"-g\d+\.pipe$")
        found = []
        try:
            names = os.listdir(self._sink_dir)
        except OSError:
            names = []
        for fn in names:
            if not pat.match(fn) or fn[:-len(".pipe")] in live_names:
                continue
            path = os.path.join(self._sink_dir, fn)
            ident = None if os.path.islink(path) else _ident(path)
            if ident is not None and ident in writers and ident not in claimed:
                found.append(path)
        return found[0] if len(found) == 1 else None

    def _point(self, own, sink):
        """Atomically make `own` a symlink to `sink` (symlink to a tmp name + os.replace)."""
        target = os.path.basename(sink) if os.path.dirname(sink) == self._sink_dir else sink
        tmp = f"{own}.{os.getpid()}.rebind"
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        os.symlink(target, tmp)
        os.replace(tmp, own)

    def sweep(self):
        """One reconcile pass. Returns {attached, errors, pruned}. NEVER raises —
        a per-seat failure is isolated into `errors` (fail-isolation)."""
        try:
            current = dict(self._tmux.list_sessions())
        except Exception as e:                      # a lister failure must not wedge the beat
            return {"attached": [], "errors": {"__list__": str(e)}, "pruned": []}
        if self._sinks is None:
            self._load()
        before = self._dump()
        self._check_server()
        live_sids = set(current.values())
        for gone in [x for x in self._sinks if x not in live_sids]:
            del self._sinks[gone]                   # prune: that pane is gone
        attached_now, errors, writers = [], {}, None
        for name, sid in current.items():
            if not _SAFE_SESSION.match(name):
                errors[name] = f"unsafe session name: {name!r}"
                continue
            if sid in self._sinks:                  # known pane (incl. a renamed one): no pipe-pane
                self.attached[name] = sid
                continue
            own = os.path.join(self._sink_dir, f"{name}.pipe")
            try:
                if os.path.islink(own):             # a stale rebind must not route a fresh
                    os.unlink(own)                  # attach into another pane's sink
                # (re)attach when new OR when the tmux identity changed under a stable
                # name (gotcha #1: pipe-pane dropped on session recreation).
                did = self._tmux.pipe_pane(name, own)
            except Exception as e:                  # fail-isolation: skip ONLY this seat
                errors[name] = str(e)
                continue
            self.attached[name] = sid
            if did is not False:
                self._sinks[sid] = own
                attached_now.append(name)
                continue
            if writers is None:
                writers = self._writers()
            claimed = {_ident(p) for s, p in self._sinks.items() if s in live_sids} - {None}
            adopted = self._adopt(name, own, claimed, set(current), writers)
            if adopted is not None:
                self._sinks[sid] = adopted
        # A real file `<name>.pipe` about to be replaced by a rebind may be ANOTHER live
        # pane's sink (rotate_agent renames the blue to an archive name before renaming the
        # green to the root): move it aside, inode intact, so that pane keeps a reachable sink.
        for name, sid in current.items():
            sink = self._sinks.get(sid)
            own = os.path.join(self._sink_dir, f"{name}.pipe")
            if sink is None or sink == own or not _SAFE_SESSION.match(name):
                continue
            if os.path.islink(own) or not os.path.exists(own):
                continue
            for holder, path in list(self._sinks.items()):
                if holder != sid and holder in live_sids and path == own:
                    aside = os.path.join(self._sink_dir,
                                         ".aside-" + re.sub(r"[^A-Za-z0-9]", "", holder) + ".pipe")
                    try:
                        os.rename(own, aside)
                        self._sinks[holder] = aside
                    except OSError as e:
                        errors[name] = str(e)
        unbound = set()
        for name, sid in current.items():
            if name not in self.attached:
                continue
            sink = self._sinks.get(sid)
            own = os.path.join(self._sink_dir, f"{name}.pipe")
            if sink is None:
                unbound.add(name)                   # piped somewhere no ring can read
                continue
            if sink == own:
                continue                            # (a just-attached sink may not exist yet)
            if _ident(sink) is None:
                unbound.add(name)                   # its sink is gone: nothing to point at
                continue
            if _ident(own) != _ident(sink):
                try:
                    self._point(own, sink)
                except OSError as e:
                    errors[name] = str(e)
                    unbound.add(name)
        self.unbound = unbound
        # prune sessions that are gone
        pruned = [n for n in list(self.attached) if n not in current]
        live_now = set(current.values())
        for n in pruned:
            if self.attached[n] in live_now:        # same pane, new name: renamed away
                self.retired.add(n)
            del self.attached[n]
        self.retired -= set(current)
        if self._dump() != before:
            try:
                self._save()
            except OSError as e:
                errors["__bindings__"] = str(e)
        return {"attached": attached_now, "errors": errors, "pruned": pruned}


def _raise_unsafe(name):
    raise UnsafeSessionName(f"unsafe session name: {name!r}")
