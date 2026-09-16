"""reaper — grandchild-safe Blue reaper for the async Blue-Green swap (gm bar #2).

`kill -pgid` (killpg) alone is INSUFFICIENT: a setsid/new-session grandchild has
its OWN process group, so killpg on the retiring seat's pgid never reaches it —
the setsid-grandchild lesson (memory: reference_pgroup_kill_misses_setsid_
grandchildren). But setsid KEEPS the PPID, so a `ps --ppid` BFS from the root pane
pid still finds the grandchild. This reaper therefore:

  1. SNAPSHOTS the full PPID-descendant set BEFORE signaling (a process that later
     double-forks to init can't be found post-hoc, so snapshot first);
  2. signals the process GROUP of each snapshotted pid AND each pid individually
     (TERM), waits, then escalates to KILL for survivors;
  3. VERIFIES zero survivors and returns the accounting.

Identity resolution is fail-closed: reap receives ``blue_generation_id`` (a
generations-row INT, not a handle). It resolves that to Blue's EXACT tmux session
→ pane pid and RAISES (ReapResolutionError) on any ambiguity/absence — it NEVER
falls back to a root-name glob (which could kill Green or a sibling seat).
"""
import os
import signal
import subprocess
import time


class ReapResolutionError(Exception):
    """blue_generation_id could not be resolved to a UNIQUE live Blue pane. Reap
    fail-closed: raise rather than guess/glob (a wrong reap kills Green/a sibling)."""


# ---- process-tree reaping (given a root pid) --------------------------------

def _children(pid):
    """Direct children of pid via ps --ppid (empty on no children / error)."""
    try:
        r = subprocess.run(["ps", "--ppid", str(pid), "--no-headers", "-o", "pid"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0:
        return []
    out = []
    for tok in r.stdout.split():
        try:
            out.append(int(tok))
        except ValueError:
            continue
    return out


def snapshot_descendants(root_pid):
    """BFS the full PPID-descendant set of root_pid (NOT including root itself).
    Snapshot BEFORE signaling — setsid keeps PPID so BFS finds setsid grandchildren
    (a double-fork-to-init escapee must be caught here, before it reparents)."""
    seen = set()
    frontier = [root_pid]
    while frontier:
        nxt = []
        for pid in frontier:
            for kid in _children(pid):
                if kid not in seen and kid != root_pid:
                    seen.add(kid)
                    nxt.append(kid)
        frontier = nxt
    return seen


def _alive(pid):
    """True iff the pid is a live (non-zombie) process. A SIGKILL'd process that
    is a child of this daemon becomes a ZOMBIE until wait()'d — os.kill(pid,0)
    still succeeds on a zombie, but it is effectively dead (reaped-pending), so we
    read /proc state and treat 'Z' (and 'X' dead) as NOT alive. Without this a
    reaped-pending pane pid would be falsely reported as a survivor."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        # state is the char after the ")" that closes the (comm) field.
        state = data[data.rfind(b")") + 2:data.rfind(b")") + 3]
        return state not in (b"Z", b"X", b"x")
    except FileNotFoundError:
        return False
    except OSError:
        # /proc unreadable — fall back to signal-0 probe.
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


def _signal_pid_and_group(pid, sig):
    """Signal the pid's process GROUP and the pid itself (belt+suspenders — the
    group reaches same-group siblings a setsid escapee left behind; the direct pid
    reaches the setsid escapee whose group killpg would miss)."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    except OSError:
        pgid = None
    if pgid is not None:
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass
    try:
        os.kill(pid, sig)
    except OSError:
        pass


def reap_tree(root_pid, *, timeout_s=10.0):
    """Kill root_pid + its full descendant tree, grandchild-safe. TERM first,
    escalate to KILL for survivors within timeout_s, verify zero survivors.
    Idempotent: an already-dead tree returns {killed: [...], survivors: []}."""
    # 1. snapshot the whole set BEFORE any signal (setsid grandchildren included).
    targets = {root_pid} | snapshot_descendants(root_pid)

    # 2. graceful TERM to each pid + its group.
    for pid in targets:
        _signal_pid_and_group(pid, signal.SIGTERM)

    # 3. wait up to timeout for graceful exit, polling.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not any(_alive(p) for p in targets):
            break
        time.sleep(0.05)

    # 4. escalate: KILL any survivor (re-snapshot in case a survivor spawned more).
    survivors = [p for p in targets if _alive(p)]
    if survivors:
        extra = set()
        for p in survivors:
            extra |= snapshot_descendants(p)
        for pid in set(survivors) | extra:
            _signal_pid_and_group(pid, signal.SIGKILL)
        time.sleep(0.1)
        targets |= extra

    # 5. verify.
    remaining = [p for p in targets if _alive(p)]
    return {"killed": sorted(targets - set(remaining)), "survivors": sorted(remaining)}


# ---- fail-closed identity resolution (blue_generation_id -> pane pid) --------

def _default_pane_pid_fn(session_name):
    """tmux pane pid for window0/pane0 of a session (fleet layout)."""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"{session_name}:0.0",
             "#{pane_pid}"], capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def _resolve_blue_tmux(orchestra_dir, root, blue_generation_id):
    """Resolve blue_generation_id -> Blue's tmux session name via the canonical DB.
    Returns the session string, or None if the mapping is absent/ambiguous. The
    archive row for a retired predecessor lives under key ``<root>-gen<N>``; the
    pre-swap canonical tmux was ``<root>``. We resolve the generations row and its
    recorded tmux, fail-closed if we cannot pin exactly one."""
    import sqlite3
    dbp = os.path.join(orchestra_dir, "state", "orchestra-registry.db")
    if not os.path.exists(dbp):
        return None
    try:
        conn = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return None
    try:
        # The generation row must exist AND belong to this root (guard against a
        # stale/foreign id resolving to another lineage's pane).
        row = conn.execute(
            "SELECT g.root, g.generation FROM generations g WHERE g.id=?",
            (blue_generation_id,)).fetchone()
        if row is None or row[0] != root:
            return None
        # Prefer the archive tmux if the swap already repointed canonical to Green;
        # else the canonical tmux still names Blue. Both are deterministic strings.
        archive_session = f"{root}-gen{row[1]}"
        return archive_session
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def resolve_blue_pane_pid(orchestra_dir, root, blue_generation_id, *,
                          pane_pid_fn=None, tmux_resolver=None):
    """Resolve blue_generation_id to Blue's EXACT live pane pid. RAISE
    ReapResolutionError (fail-closed) on any ambiguity/absence — never glob."""
    if not isinstance(blue_generation_id, int):
        raise ReapResolutionError(
            f"reap refused for {root!r}: blue_generation_id is not an int "
            f"({blue_generation_id!r}) — cannot resolve a unique Blue pane")
    resolver = tmux_resolver or _resolve_blue_tmux
    session = resolver(orchestra_dir, root, blue_generation_id)
    if not session:
        raise ReapResolutionError(
            f"reap refused for {root!r}: blue_generation_id={blue_generation_id} "
            f"does not resolve to a unique Blue tmux session (absent/foreign/"
            f"ambiguous). Refusing to name-glob (would risk Green/sibling).")
    pane_fn = pane_pid_fn or _default_pane_pid_fn
    pid = pane_fn(session)
    if not isinstance(pid, int) or pid <= 0:
        raise ReapResolutionError(
            f"reap refused for {root!r}: Blue session {session!r} has no live "
            f"pane pid — cannot reap a tree I cannot uniquely identify.")
    return pid


def _reap_recorded(pid, green_pane_pid, alive_fn, reap, timeout_s, root):
    """P0.1: reap the IMMUTABLE blue pane pid recorded at PREWARM, fail-closed.
    Guards (in order): (1) a non-positive-int pid is unresolvable -> RAISE; (2) the
    LOAD-BEARING never-reap-green guard — if the recorded pid IS the live green pane,
    RAISE with ZERO kills (never reap the promoted successor); (3) an already-dead pid
    is a no-op (nothing to kill; a dead blue is the reap's own end-state), NOT a raise
    and NOT a blind reap of a possibly-recycled pid."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ReapResolutionError(
            f"reap refused for {root!r}: recorded blue pid {pid!r} is not a positive "
            f"int — cannot uniquely identify a Blue tree to reap")
    if green_pane_pid is not None and green_pane_pid == pid:
        raise ReapResolutionError(
            f"reap refused for {root!r}: recorded blue pid {pid} == GREEN pane pid — "
            f"refusing (would reap the promoted successor). Zero kills.")
    alive = alive_fn or _alive
    if not alive(pid):
        return {"killed": [], "survivors": [], "already_dead": True, "pid": pid}
    return reap(pid, timeout_s=timeout_s)


def reap_blue_generation(orchestra_dir, root, blue_generation_id, *,
                         timeout_s=10.0, pane_pid_fn=None, tmux_resolver=None,
                         _reap_fn=None, recorded_blue_pid=None, green_pane_pid=None,
                         alive_fn=None):
    """Top-level reap for bg_arm's seam. P0.1: PREFER the immutable pid recorded at
    prewarm (``recorded_blue_pid``) — the archive-name resolver returns ``{root}-gen{N}``
    which does NOT exist for a LIVE blue (pre-swap it sits in ``{root}``/``{root}-g{N}``),
    so the autonomous reap fail-closes without this binding (the dead-pane landmine). When
    a recorded pid is present, reap it under the fail-closed guards in ``_reap_recorded``
    (never-reap-green, invalid, already-dead). When ABSENT, fall back to the legacy
    tmux-name resolution (supervised / back-compat), which RAISES before killing anything
    if Blue cannot be uniquely resolved (zero collateral)."""
    reap = _reap_fn or reap_tree
    if recorded_blue_pid is not None:
        return _reap_recorded(recorded_blue_pid, green_pane_pid, alive_fn, reap,
                              timeout_s, root)
    pid = resolve_blue_pane_pid(
        orchestra_dir, root, blue_generation_id,
        pane_pid_fn=pane_pid_fn, tmux_resolver=tmux_resolver)
    return reap(pid, timeout_s=timeout_s)
