"""Projector regenerator — Option A: cutover-only STANDALONE DAEMON (DEC-1788371192).

Under cutover the writers write the DB (single write-truth); the ~130 legacy readers
(dashboard/api/phone/watch) keep reading registry.json / agent-sessions.json /
state/agents/*.json, which must be REGENERATED from the store or they freeze at the
flip. The U12 monitor pages if the projection sidecar ages past ``max_age_s`` (120s).

Consensus (all 3 legs, over the writer-triggered Option B): a SINGLE standalone daemon
detects EVERY write by construction via ``PRAGMA data_version`` — a counter SQLite bumps
whenever another connection commits — so there is no per-writer seam to miss (the §B-gap
lesson: a hook you forget to add is a silent gap). It covers swap_generation, the async
blue-green arm, and any future writer for free. The daemon:

  * WRITE-DETECT: poll ``PRAGMA data_version``; on change, mark dirty.
  * DEBOUNCE: coalesce a burst into <=1 ``project_faithful`` per ``debounce_s`` window.
  * FORCE-FLOOR (< U12 ``max_age_s``): re-project on an idle floor so the sidecar stays
    fresh with zero writes (else an idle fleet spuriously pages).
  * SINGLE-INSTANCE: a liveness-checked lock so two daemons never fight (stale-pid
    takeover after a crash/reboot).
  * CUTOVER-ONLY: it is NOT a standing fleet daemon — it runs only while the cutover flag
    is active and EXITS when disarmed. INERT: flag OFF = the daemon is not running and
    ``run_once``/``tick`` are no-ops.

Lock contention is a non-issue: the daemon only READS (post-COMMIT snapshots), the
factory sets ``busy_timeout=10000``, and the debounce bounds read frequency. The async
bg-arm must NEVER self-reproject — the daemon OWNS all reprojection.
"""
import json
import os
import time

from scripts.identity_store import cutover

# Debounce: coalesce a write burst (e.g. a rotation's ~10 writes) into <=1 projection.
_DEFAULT_DEBOUNCE_S = 2.0
# Idle force-floor: re-project at least this often. MUST be < the U12 monitor's
# max_age_s (120s) so an idle fleet never pages before the floor refreshes the sidecar.
_DEFAULT_FORCE_FLOOR_S = 30.0
# How often the loop wakes to poll data_version (cheap: a single PRAGMA).
_DEFAULT_POLL_S = 1.0
_LOCK_REL = os.path.join("state", "identity-store-regenerator.lock")


def _db_path(orchestra_dir):
    return os.path.join(orchestra_dir, "state", "orchestra-registry.db")


def read_data_version(conn) -> int:
    """The SQLite data_version — bumps whenever ANOTHER connection commits a write to
    the database file. The cross-process write detector Option A is built on."""
    return conn.execute("PRAGMA data_version").fetchone()[0]


def _project(orchestra_dir, conn, now):
    from scripts.identity_store import projector
    return projector.project_faithful(conn, orchestra_dir, now=now)


class RegenState:
    """Pure daemon-tick state (no I/O of its own) — deterministic + unit-testable."""

    def __init__(self, *, debounce_s=_DEFAULT_DEBOUNCE_S,
                 force_floor_s=_DEFAULT_FORCE_FLOOR_S):
        self.debounce_s = debounce_s
        self.force_floor_s = force_floor_s
        self.last_data_version = None
        self.last_project_at = None
        self._dirty = False

    def decide(self, data_version, now) -> bool:
        """Decide whether to project THIS tick. Returns True iff a projection should run.
        - a data_version change marks dirty (a write happened since last look);
        - project a dirty state once the debounce window has elapsed (coalescing);
        - else force a projection on the idle floor (keepalive) / the very first tick.
        On a True return the caller projects, then calls ``mark_projected``."""
        if self.last_data_version is None:
            # first observation after (re)start: establish baseline + do an initial
            # projection so the copies/sidecar are immediately fresh.
            self.last_data_version = data_version
            return True
        if data_version != self.last_data_version:
            self.last_data_version = data_version
            self._dirty = True
        if self._dirty:
            if self.last_project_at is None or (now - self.last_project_at) >= self.debounce_s:
                return True
            return False  # coalesce within the debounce window
        # clean (no pending write): keep the sidecar fresh on the idle floor.
        if self.last_project_at is None or (now - self.last_project_at) >= self.force_floor_s:
            return True
        return False

    def mark_projected(self, now):
        self.last_project_at = now
        self._dirty = False


def tick(orchestra_dir, conn, state: RegenState, now=None) -> bool:
    """One daemon step against an OPEN connection. Reads data_version, and if the state
    machine says so, runs project_faithful. Returns True iff it projected."""
    now = time.time() if now is None else now
    dv = read_data_version(conn)
    if state.decide(dv, now):
        _project(orchestra_dir, conn, now)
        state.mark_projected(now)
        return True
    return False


# --- single-instance liveness lock -----------------------------------------

def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_singleton(orchestra_dir) -> bool:
    """Best-effort single-instance guard: write our pid into the lockfile unless a LIVE
    pid already holds it (liveness-checked so a crashed/rebooted holder is taken over).
    Returns True iff we now own it. (A full flock would also work; this pid-liveness form
    survives reboot cleanly — a stale pid from before reboot is not alive.)"""
    path = os.path.join(orchestra_dir, _LOCK_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        try:
            holder = int(open(path).read().strip() or "0")
        except (OSError, ValueError):
            holder = 0
        if holder and holder != os.getpid() and _pid_alive(holder):
            return False  # a live daemon already owns it
    tmp = path + f".{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(str(os.getpid()))
    os.replace(tmp, path)
    return True


def release_singleton(orchestra_dir):
    path = os.path.join(orchestra_dir, _LOCK_REL)
    try:
        if int(open(path).read().strip() or "0") == os.getpid():
            os.remove(path)
    except (OSError, ValueError):
        pass


def run(orchestra_dir, *, debounce_s=_DEFAULT_DEBOUNCE_S,
        force_floor_s=_DEFAULT_FORCE_FLOOR_S, poll_s=_DEFAULT_POLL_S,
        max_iters=None) -> str:
    """The daemon loop. CUTOVER-ONLY: returns immediately if the flag is inactive, and
    EXITS when the cutover is disarmed (rollback stops the daemon). Holds the
    single-instance lock for its lifetime. ``max_iters`` bounds the loop for tests.
    Returns the exit reason ('inactive' | 'lock-held' | 'disarmed' | 'max-iters')."""
    if not cutover.is_active(orchestra_dir):
        return "inactive"
    if not acquire_singleton(orchestra_dir):
        return "lock-held"
    from scripts.identity_store import orchestra_db
    conn = orchestra_db.get_connection(_db_path(orchestra_dir))
    state = RegenState(debounce_s=debounce_s, force_floor_s=force_floor_s)
    iters = 0
    try:
        while True:
            if not cutover.is_active(orchestra_dir):
                return "disarmed"
            tick(orchestra_dir, conn, state)
            iters += 1
            if max_iters is not None and iters >= max_iters:
                return "max-iters"
            time.sleep(poll_s)
    finally:
        conn.close()
        release_singleton(orchestra_dir)


def start_if_armed(orchestra_dir) -> bool:
    """REBOOT-RECOVERY hook. The cutover flag SURVIVES a reboot but the daemon does
    NOT — so a post-reboot recovery path (and the flip sequence itself) must (re)start
    the daemon whenever the cutover is active. Idempotent: the single-instance lock
    makes a redundant call a no-op (a live holder declines). Returns True iff a daemon
    was spawned. Keyed ONLY on ``cutover.is_active`` so it is a pure no-op flag-off
    (INERT) — nothing spawns, nothing imports beyond the flag check."""
    if not cutover.is_active(orchestra_dir):
        return False
    if not acquire_singleton(orchestra_dir):
        return False  # already running
    release_singleton(orchestra_dir)  # the spawned child re-acquires for its lifetime
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ)
    env["ORCHESTRA_DIR"] = orchestra_dir
    subprocess.Popen(
        [sys.executable, "-m", "scripts.identity_store.regenerator", "run"],
        cwd=repo, env=env, start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="identity-store projector regenerator (Option A daemon)")
    p.add_argument("cmd", choices=["run", "start-if-armed"], nargs="?", default="run")
    p.add_argument("--orchestra-dir",
                   default=os.environ.get("ORCHESTRA_DIR",
                                          os.path.expanduser("~/scripts/agent-orchestra")))
    args = p.parse_args(argv)
    if args.cmd == "start-if-armed":
        started = start_if_armed(args.orchestra_dir)
        print(json.dumps({"started": started}))
        return
    reason = run(args.orchestra_dir)
    print(json.dumps({"exit": reason}))


if __name__ == "__main__":
    _main()
