"""run_capture — the READ-ONLY operational runner for stage-1 WAL capture.

Resolves ONE live seat (ios-watch-dev per DP-5) from registry.json + the
claude projects/ layout and drives a WalTailer. This is the ONLY module in the
stage that reads live state, and it reads ONLY (registry lookup + tailing
source streams); it writes exclusively to state/wal/<lineage_root>.db via the
store. Nothing here mutates registry/agent-sessions/state-agents/self_retire/
the beat path.

Usage:
  python3 -m lineage_daemon.wal.run_capture --seat ios-watch-dev [--once]
"""
import argparse
import json
import os
import re
from dataclasses import dataclass

from .store import WalStore
from .wal_tailer import WalTailer, SeatLock

_RESUME_SID_RE = re.compile(r"(?:--resume|--conversation)\s+([0-9a-f-]{36})")


def _proj_slug(cwd):
    """Claude encodes a cwd into projects/ by replacing '/' and '.' with '-'."""
    return re.sub(r"[/.]", "-", cwd or "")


@dataclass
class SeatSpec:
    lineage_root: str
    sid: str
    cwd: str
    generation: int
    source_path: str
    panes_dir: str


def resolve_seat(registry, seat, home, orchestra_dir=None):
    """Read-only resolution of a seat's live capture sources.

    sid: prefer the resume_command --resume sid (authoritative) over the
    periodically-clobbered session_id (the sid-clobber gotcha, per enrich.py).
    """
    a = registry["agents"][seat]
    sid = None
    m = _RESUME_SID_RE.search(a.get("resume_command") or "")
    if m:
        sid = m.group(1)
    sid = sid or a.get("session_id")
    cwd = a.get("cwd")
    generation = int(a.get("generation", 0) or 0)
    source_path = os.path.join(
        home, ".claude", "projects", _proj_slug(cwd), f"{sid}.jsonl")
    orchestra_dir = orchestra_dir or os.path.join(home, "scripts",
                                                  "agent-orchestra")
    panes_dir = os.path.join(orchestra_dir, "state", "agent-events", "panes")
    return SeatSpec(lineage_root=seat, sid=sid, cwd=cwd, generation=generation,
                    source_path=source_path, panes_dir=panes_dir)


def build_tailer(seat, home=None, orchestra_dir=None):
    home = home or os.path.expanduser("~")
    orchestra_dir = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.join(home, "scripts", "agent-orchestra"))
    with open(os.path.join(orchestra_dir, "registry.json")) as fh:
        registry = json.load(fh)
    spec = resolve_seat(registry, seat, home, orchestra_dir)
    wal_dir = os.path.join(orchestra_dir, "state", "wal")
    store = WalStore(os.path.join(wal_dir, f"{spec.lineage_root}.db"))
    tailer = WalTailer(store, spec.source_path, spec.cwd, spec.panes_dir,
                       lineage_root=spec.lineage_root, sid=spec.sid,
                       generation=spec.generation)
    return spec, store, tailer, wal_dir


def _resolve_current_sid(seat, home=None, orchestra_dir=None):
    """Cheap read-only resolution of the seat's CURRENT canonical sid (registry)."""
    home = home or os.path.expanduser("~")
    orchestra_dir = orchestra_dir or os.environ.get(
        "ORCHESTRA_DIR", os.path.join(home, "scripts", "agent-orchestra"))
    with open(os.path.join(orchestra_dir, "registry.json")) as fh:
        registry = json.load(fh)
    return resolve_seat(registry, seat, home, orchestra_dir).sid


def run_capture_loop(seat, *, interval=5.0, build_fn=build_tailer,
                     resolve_sid_fn=None, should_stop=None, sleep=None,
                     home=None, orchestra_dir=None, log=print):
    """Durable capture loop that SELF-HEALS across rotations (DEC-1789348596101795).

    Each iteration re-resolves the seat's canonical sid; if it changed (a rotation),
    rebuild the tailer on the new sid (same per-lineage <root>.db store) BEFORE
    ticking, so capture never stays stuck on a dead sid. Fail-safe: a resolver
    error/None keeps the current tailer (never crashes the daemon)."""
    import time as _time
    sleep = sleep or _time.sleep
    should_stop = should_stop or (lambda: False)
    resolve_sid_fn = resolve_sid_fn or (
        lambda s: _resolve_current_sid(s, home, orchestra_dir))
    spec, store, tailer, wal_dir = build_fn(seat, home=home, orchestra_dir=orchestra_dir)
    current_sid = spec.sid
    while not should_stop():
        try:
            new_sid = resolve_sid_fn(seat)
        except Exception:  # noqa: BLE001 — never crash the daemon on a bad read
            new_sid = None
        if new_sid and new_sid != current_sid:
            spec, store, tailer, wal_dir = build_fn(
                seat, home=home, orchestra_dir=orchestra_dir)
            current_sid = spec.sid
            log(f"[wal] sid changed -> rebuilt tailer on {seat} sid={current_sid}")
        tailer.tick()
        sleep(interval)
    return current_sid


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seat", default="ios-watch-dev")
    ap.add_argument("--once", action="store_true",
                    help="single capture pass then exit (for measurement)")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args(argv)

    spec, store, tailer, wal_dir = build_tailer(args.seat)
    lock = SeatLock(spec.lineage_root, wal_dir)
    if not lock.acquire():
        print(f"[wal] another tailer already owns {spec.lineage_root}; exiting")
        return 1
    try:
        if args.once:
            n = tailer.tick()
            print(f"[wal] captured {n} events; wal seq now {store.max_seq()}")
        else:
            print(f"[wal] capturing {spec.lineage_root} sid={spec.sid} "
                  f"interval={args.interval}s (capture-only, swaps off, "
                  f"re-resolve-on-rotation ON)")
            # SeatLock stays held by THIS process across tailer rebuilds; the loop
            # re-resolves the canonical sid each tick and rebuilds on a rotation.
            run_capture_loop(args.seat, interval=args.interval)
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
