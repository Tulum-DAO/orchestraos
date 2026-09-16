"""Seat-builder — the missing glue between the roster/live-tmux and the two
telemetry lanes. Produces realtime Seats (for the B1 overlay: need a root pid) and
durable MuxSeats (for the WAL tailer: need a source path), fail-soft per seat.

The pure `build_seats` takes INJECTED resolvers (unit-tested). `build_live_daemon`
wires the concrete live resolvers (reusing existing fleet code: sid_invariants,
reconcile_fleet_stores, codex_context, run_capture, agent-status hook reader) and
is exercised only in a live run.
"""
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .realtime.daemon import Seat
from .wal.multiplexer import MuxSeat


def _ro_open(db_path):
    """Open a sqlite db READ-ONLY (condition A: never write-lock/create the
    authoritative identity store; a WAL-mode reader never blocks the writer).
    Returns a connection or None (absent/error) — NEVER raises."""
    try:
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        conn.execute("PRAGMA busy_timeout=250")
        return conn
    except Exception:
        return None


def _resolve_generation(record, *, conn):
    """Resolve a seat's generation, NEVER returning None/0/negative (the WAL's
    generation is INTEGER NOT NULL). Order (DEC-1788481319): (a) int-coerce the
    registry value if >0; (b) the AUTHORITATIVE immutable generations table by
    session_id via the READ-ONLY conn (precedes the fallback so a stale-null-on-a-
    rotated seat is not mislabeled); (c) fallback 1 — a never-rotated seat IS gen-1
    by fleet convention (migrate.py:74 'or 1'), the real identity, not a sentinel."""
    g = record.get("generation")
    try:
        if g is not None:
            gi = int(g)
            if gi > 0:                          # gen=0 treated as missing (x or 1 idiom)
                return gi
    except (TypeError, ValueError):
        pass
    sid = record.get("session_id")
    if sid and conn is not None:
        try:
            row = conn.execute(
                "SELECT generation FROM generations WHERE session_id=?", (sid,)).fetchone()
            if row and row[0]:
                return int(row[0])
        except Exception:
            pass                                # no table / query error -> fallback
    return 1


def read_pane_hooks(pane_id, events_dir):
    """Read a claude hook event dict DIRECTLY from a pane_id's event file — the
    cheap half of the per-slow-tick hook refresh, with NO tmux subprocess (the
    batched live_resolver already has the pane_id from ONE list-panes; the naive
    per-seat hooks_for re-runs `tmux display-message` via get_pane_id, ~10x cost).
    Mirrors agent_status._read_hook_event's file logic exactly (pane_id.lstrip('%')
    + '.json', require a dict with 'state'). Returns None on absent/malformed/
    non-claude (no such file) — never raises."""
    if not pane_id:
        return None
    try:
        with open(os.path.join(events_dir, pane_id.lstrip("%") + ".json")) as fh:
            ev = json.load(fh)
        return ev if isinstance(ev, dict) and "state" in ev else None
    except (OSError, ValueError):
        return None


def build_turn_resolver():
    """The E2 turn-completion resolver: (session, runtime, hooks) -> bool|None.
    Derives the claude .jsonl transcript path from the LIVE hook's session_id + cwd
    (the same slug convention as _live_resolvers.source_path) and returns the
    transcript's turn-completion — no extra tmux/registry lookup (the hook, already
    read this tick, carries session_id + cwd). None for non-claude, a hook missing
    those fields, or an absent/unreadable transcript (caller trusts the hook)."""
    from .realtime.transcript_turn import transcript_turn_complete

    def turn_resolver(session, runtime, hooks):
        if runtime != "claude" or not isinstance(hooks, dict):
            return None
        sid = hooks.get("session_id")
        cwd = hooks.get("cwd")
        if not sid or not cwd:
            return None
        slug = re.sub(r"[/.]", "-", cwd)             # matches source_path's claude jsonl slug
        path = os.path.expanduser(f"~/.claude/projects/{slug}/{sid}.jsonl")
        return transcript_turn_complete(path)

    return turn_resolver


def build_seats(agents, live_names, *, pane_pid, source_path, hooks_for, ring_for,
                resolve_generation):
    """(realtime_seats, mux_seats) for the LIVE subset of the roster. Fail-soft:
    a seat missing its required field (root pid / source path) is skipped, never
    raised. lineage_root falls back to the agent id. generation is RESOLVED (never
    None into the NOT-NULL WAL); a seat whose generation is somehow unresolvable is
    surfaced degraded and its durable MuxSeat is skipped (defensive — resolve
    normally returns >=1)."""
    seats, mux_seats = [], []
    for aid, rec in (agents or {}).items():
        if aid not in live_names:
            continue
        runtime = (rec.get("runtime") or "claude")
        lineage_root = rec.get("lineage_root") or aid
        try:
            gen = resolve_generation(aid, rec)
        except Exception:
            gen = None
        degraded = None if gen is not None else "no-generation"
        try:
            pid = pane_pid(aid)
        except Exception:
            pid = None
        if pid is not None:
            try:
                ring = ring_for(aid)
            except Exception:
                ring = None
            try:
                hooks = hooks_for(aid, runtime)
            except Exception:
                hooks = None
            seats.append(Seat(session=aid, lineage_root=lineage_root, runtime=runtime,
                              root_pid=pid, ring=ring, hooks=hooks, degraded=degraded))
        try:
            src = source_path(aid, rec, pid)
        except Exception:
            src = None
        if src and gen is not None:             # durable capture needs a valid generation
            mux_seats.append(MuxSeat(
                lineage_root=lineage_root, runtime=runtime, source_path=src,
                sid=rec.get("session_id"), generation=gen, cwd=rec.get("cwd")))
    return seats, mux_seats


# --- live wiring ------------------------------------------------------------
# The live path is now assembled from an INJECTABLE resolver bundle so it is
# testable without the live estate (the Gate-2 crash-loop shipped precisely
# because this was pragma-no-cover: an env-string ORCHESTRA_DIR was handed to
# load_stores(), whose first act is `orch / "state"` — a Path op that raises on
# a str). build_live_daemon now normalizes ORCHESTRA_DIR to a Path FIRST.


@dataclass
class _Resolvers:
    """The fleet-reader seam build_live_daemon consumes. Real bundle built by
    `_live_resolvers`; a fake is injected in tests."""
    load_stores: Callable          # (Path) -> (sessions, agents)
    live_sessions: Callable        # () -> set[str] of live tmux session names
    pane_pid: Callable             # (session) -> int|None (build-time, per seat)
    live_resolver: Callable        # (sessions) -> {session: {"pid":int|None,"hooks":dict|None}}
                                   # per-slow-tick BATCHED refresh (one tmux call/tick)
    source_path: Callable          # (session, record, pid) -> str|None
    hooks_for: Callable            # (session, runtime) -> dict|None
    ring_for: Callable             # (session) -> ring|None
    tmux: object                   # AttachSweep tmux adapter (list_sessions/pipe_pane)
    resolve_generation: Callable   # (session, record) -> int>=1 (never None)


def _live_resolvers(orchestra_dir):  # pragma: no cover — touches the live estate
    """Build the REAL fleet resolvers, reusing existing fleet code. `orchestra_dir`
    is a Path. Inserts scripts/ on sys.path and lazy-imports the fleet modules."""
    import importlib.util
    from .realtime.pipe_pane import sink_path
    from .pane_sink_tailer import PaneSinkTailer
    from .tmux_adapter import Tmux

    scripts = str(orchestra_dir / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import sid_invariants
    import reconcile_fleet_stores as rfs
    import codex_context

    spec = importlib.util.spec_from_file_location(
        "agent_status_live", str(orchestra_dir / "scripts" / "agent-status.py"))
    agent_status = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent_status)

    def source_path(session, record, pid):
        runtime = (record.get("runtime") or "claude")
        sid = record.get("session_id")
        cwd = record.get("cwd")
        try:
            if runtime == "codex":
                return codex_context._open_rollout(pid) if pid else None
            if runtime == "gemini":
                gsid = rfs.get_gemini_live_sid(str(pid)) if pid else None
                if not gsid:
                    return None
                p = os.path.expanduser(
                    f"~/.gemini/antigravity-cli/conversations/{gsid}.db")
                return p if os.path.exists(p) else None
            slug = __import__("re").sub(r"[/.]", "-", cwd or "")   # claude jsonl
            p = os.path.expanduser(f"~/.claude/projects/{slug}/{sid}.jsonl")
            return p if os.path.exists(p) else None
        except Exception:
            return None

    def hooks_for(session, runtime):
        if runtime != "claude":
            return None                    # codex/gemini have no hook layer
        try:
            return agent_status._read_hook_event(session)
        except Exception:
            return None

    def ring_for(session):
        try:
            return PaneSinkTailer(sink_path(session))
        except Exception:
            return None

    # Condition A: open the authoritative identity store READ-ONLY, ONCE, reused
    # across all seats (never write-lock/create it; a pure observer).
    _gen_conn = _ro_open(str(orchestra_dir / "state" / "orchestra-registry.db"))

    def resolve_generation(session, record):
        return _resolve_generation(record, conn=_gen_conn)

    _tmux = Tmux()

    def live_resolver(sessions):
        # ONE batched `tmux list-panes -a` for the whole fleet per slow tick -> each
        # session's (pane_id, pane_pid); then hooks are read DIRECTLY from the
        # pane_id's event file (NO per-seat get_pane_id subprocess — the naive
        # per-seat hooks_for path cost ~10x CPU). A non-claude session has no claude
        # hook event file -> hooks None (matching hooks_for's None-for-non-claude). A
        # session absent from list-panes is omitted -> caller frozen-fallback.
        pane_map = _tmux.pane_map()            # {session: (pane_id, pane_pid)}
        out = {}
        for s in sessions:
            pane = pane_map.get(s)
            if pane is None:
                continue                       # absent -> frozen fallback in the daemon
            pane_id, pid = pane
            out[s] = {"pid": pid,
                      "hooks": read_pane_hooks(pane_id, agent_status.EVENTS_DIR)}
        return out

    return _Resolvers(
        load_stores=sid_invariants.load_stores,
        live_sessions=rfs.get_live_tmux_sessions,
        pane_pid=codex_context._pane_pid, live_resolver=live_resolver,
        source_path=source_path, hooks_for=hooks_for, ring_for=ring_for,
        tmux=_tmux, resolve_generation=resolve_generation)


def build_live_daemon(orchestra_dir=None, *, resolvers=None):
    """Assemble a TelemetryDaemon against the live fleet. `orchestra_dir` is
    normalized to a Path FIRST (the Gate-2 fix: load_stores does `orch / "state"`).
    `resolvers` is injectable for tests; None => the real live bundle."""
    from .realtime.lineage_flag import LineageFlagStore
    from .realtime.pipe_pane import AttachSweep, SINK_DIR
    from .realtime.proc_sampler import ProcSampler
    from .telemetryd import TelemetryDaemon
    from .wal.multiplexer import MultiplexedTailer

    orchestra_dir = Path(orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra")))
    r = resolvers or _live_resolvers(orchestra_dir)

    _sessions, agents = r.load_stores(orchestra_dir)      # Path, not str (the fix)
    live = r.live_sessions()
    seats, mux_seats = build_seats(agents, live, pane_pid=r.pane_pid,
                                   source_path=r.source_path, hooks_for=r.hooks_for,
                                   ring_for=r.ring_for, resolve_generation=r.resolve_generation)

    wal_dir = str(orchestra_dir / "state" / "wal")
    mux = MultiplexedTailer(wal_dir=wal_dir, lock_dir=wal_dir)
    for ms in mux_seats:
        try:
            mux.register(ms)
        except Exception:
            pass
    # the flag store reads the denylist FILE, never the wal_dir (absent file =>
    # fail-closed = block, the safe INERT default until the detection path writes it)
    flags_path = os.path.join(wal_dir, "lineage_flags.json")
    return TelemetryDaemon(
        seats=seats, mux=mux, sweep=AttachSweep(r.tmux, sink_dir=SINK_DIR),
        sampler=ProcSampler(), flag_store=LineageFlagStore(flags_path),
        snapshot_base=None, wal_dir=wal_dir,
        lock_path=os.path.join(wal_dir, "telemetryd.lock"),
        # per-SLOW-tick refresh: re-resolve live root_pid + hooks instead of the
        # fields frozen at build_seats (post-rotation offline_crashed flap + frozen
        # stalled/waiting_permission staleness). ONE batched list-panes call/tick.
        live_resolver=r.live_resolver,
        # E2: transcript turn-completion vetoes a stale-by-emission working hook
        # (Claude fires no Stop on interrupt/kill) -> idle, while a real mid-tool
        # hang (pending tool_use) stays stalled.
        turn_resolver=build_turn_resolver())
