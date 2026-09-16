"""spawn_green — the setsid-detached Green spawn seam for the live-pane drill.

`spawn-agent.sh <green_alias>` does `tmux new-session -d`, which IS the
setsid-detached spawn: the tmux server (not the beat process) owns the pane in its
OWN session/pgid, so the Green survives the beat exiting. bg_arm's
`_register_then_project_then_spawn` already ran register_provisional + project_now
(F3 read-your-writes) before this, so spawn-agent.sh finds the alias registered and
skips auto-register (U16-refusal race handled upstream).

This seam adds the two things the raw script lacks for the async arm:
  * IDEMPOTENCY — a crash-re-entered spawn must NOT create a second live pane for
    one green_alias (tmux has-session guard);
  * REAP REACHABILITY — record the Green pane pid into bg_state so the grandchild-
    safe reaper can PPID-BFS it later.

The spawn is dependency-injected (`spawn_runner`) so tests exercise a real detached
tmux session without launching a full CLI; the live default calls spawn-agent.sh.
"""
import os
import subprocess


# Pre-spawn free-RAM floor (item 2c, gm gate msg_8f6cd0b2). A broad arm spawns MANY
# greens at once; g15's pred4 proof-fire green was OOM-KILLED at boot under swap thrash
# and the seat then silently never rotated. ONE floor constant, in MiB of MemAvailable
# (the kernel's own OOM predictor — accounts for reclaimable cache). A claude/agy green's
# boot RSS spike is ~0.5-1 GiB; below 2 GiB available (esp. with swap already thrashing)
# is the pred4 OOM class. INERT in normal conditions (fleet idle avail ~18 GiB); it only
# refuses when RAM is genuinely too low to boot a green safely.
SPAWN_MIN_AVAIL_MB = 2048


class PreSpawnRAMFloor(RuntimeError):
    """Fail-closed: free RAM is under SPAWN_MIN_AVAIL_MB, so spawn_green refuses to mint
    a green that would likely OOM-die mid-boot (the pred4 class). LOUD — the message names
    the floor and the observed available/swap so the firewall alarm + driver log show WHY."""


def _default_free_ram():
    """Real probe: MemAvailable + SwapFree from /proc/meminfo, in MiB. Injected in tests
    (free_ram_fn) so the floor is exercisable by effect without touching real RAM. Returns
    {'avail_mb': int, 'swap_free_mb': int}. Missing keys read as 0 (fail-closed-friendly:
    an unreadable meminfo looks like no headroom, so the guard refuses rather than guesses)."""
    vals = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in ("MemAvailable", "SwapFree"):
                    vals[parts[0].rstrip(":")] = int(parts[1]) // 1024  # kB -> MiB
    except OSError:
        pass
    return {"avail_mb": vals.get("MemAvailable", 0),
            "swap_free_mb": vals.get("SwapFree", 0)}


def bg_green_env(root, green_alias, wal_dir):
    """The BG-green marker env (P2.7) exported into the green's spawn so it — and its
    SessionStart/PreToolUse hooks — know they belong to a Blue-Green succession:
      * BG_GREEN_ROOT / BG_GREEN_ALIAS  — green_boot_probe + capture_green_sid gate on
        these (INERT for a normal session that has neither);
      * BG_QUARANTINE_ALIAS / BG_QUARANTINE_WALDIR — the M2 containment contract
        (bg_quarantine.py:30-31); WALDIR is ABSOLUTE because the green's cwd is the
        project repo, not the orchestra dir. Pure (no os.environ read) so it is unit-
        testable; spawn_green merges it over the inherited environment."""
    return {
        "BG_GREEN_ROOT": root,
        "BG_GREEN_ALIAS": green_alias,
        "BG_QUARANTINE_ALIAS": green_alias,
        "BG_QUARANTINE_WALDIR": os.path.abspath(wal_dir),
    }


def _tail(text, n_lines=40):
    """Last n_lines of text (may be None/empty) — the diagnostic tail surfaced when a
    spawn produces no reap-reachable pane."""
    if not text:
        return ""
    return "\n".join(text.splitlines()[-n_lines:])


def _spawn_failure_detail(spawn_result):
    """Format a spawn runner's captured result into a diagnostic suffix for the
    fail-closed RuntimeError. Empty when there was no spawn this call (idempotent
    re-entry) or the runner returned no diagnostics."""
    if not spawn_result:
        return ""
    parts = []
    if spawn_result.get("timed_out"):
        parts.append(
            f"spawn TIMED OUT after {spawn_result.get('timeout_s')}s")
    else:
        parts.append(f"spawn-agent.sh exit={spawn_result.get('returncode')}")
    err = spawn_result.get("stderr_tail")
    out = spawn_result.get("stdout_tail")
    if err:
        parts.append(f"stderr tail:\n{err}")
    if out:
        parts.append(f"stdout tail:\n{out}")
    return " — " + " | ".join(parts)


def _default_spawn_runner(orchestra_dir, timeout_s):
    def run(session, env=None):
        # The real detached spawn — spawn-agent.sh creates the tmux session.
        # env carries the BG green marker vars (bg_green_env); None => inherit only.
        # CAPTURE (do not discard) the exit code + output tails: a spawn that produces
        # no pane must be DIAGNOSABLE (surfaced into the fail-closed RuntimeError +
        # driver log), never blind. A timeout is itself a spawn failure and carries
        # whatever partial output was captured before the deadline.
        try:
            r = subprocess.run(
                [os.path.join(orchestra_dir, "spawn-agent.sh"), session],
                capture_output=True, text=True, timeout=timeout_s, env=env)
            return {"session": session, "returncode": r.returncode,
                    "stderr_tail": _tail(r.stderr), "stdout_tail": _tail(r.stdout)}
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
            return {"session": session, "returncode": None, "timed_out": True,
                    "stderr_tail": _tail(err),
                    "stdout_tail": _tail(out),
                    "timeout_s": timeout_s}
    return run


def _tmux_has_session(session):
    try:
        r = subprocess.run(["tmux", "has-session", "-t", session],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _tmux_pane_pid(session):
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"{session}:0.0",
             "#{pane_pid}"], capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip()) if r.returncode == 0 else None
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def _tmux_pane_id(session):
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"{session}:0.0",
             "#{pane_id}"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    except (subprocess.SubprocessError, OSError):
        return None


def _unlink_stale_pane_event(orchestra_dir, pane_id, spawned_at):
    """Remove a PRE-spawn stale pane-event file (tmux-server-restart %N reuse hazard,
    AGY DEC-1788655588): a stale panes/<N>.json from an OLD session at the same pane
    number could false-PASS liveness. Only remove it if its ts is BEFORE this spawn
    (a fresh event the green itself writes is kept). Best-effort; the ts-fence in
    green_liveness is the primary guard."""
    import json
    if not pane_id:
        return
    path = os.path.join(orchestra_dir, "state", "agent-events", "panes",
                        f"{str(pane_id).lstrip('%')}.json")
    try:
        with open(path) as fh:
            ts = (json.load(fh).get("ts") or 0)
        if ts < spawned_at:
            os.remove(path)
    except (OSError, ValueError):
        return


def spawn_green(root, green_alias, *, orchestra_dir, wal_dir, spawn_runner=None,
                has_session_fn=None, pane_pid_fn=None, pane_id_fn=None,
                register_green_sid_fn=None, free_ram_fn=None, now=None,
                timeout_s=120.0):
    """Spawn (or re-use) the detached Green pane for green_alias and record its pane
    pid, pane id, and spawn timestamp into bg_state. Idempotent: if a session already
    exists it is re-used (never a second pane). Returns
    {alias, pane_pid, pane_id, spawned_at, detached, reused}.

    The pane_id + spawned_at feed the Layer-2 green-liveness gate (DEC-1788655588):
    spawned_at is captured BEFORE the spawn as a lower bound, so the green's own
    post-spawn SessionStart/hook events pass the ts-fence while a pre-spawn stale
    pane event (tmux-restart %N reuse) does not."""
    import time
    from .bg_state import BgStateStore

    has_session = has_session_fn or _tmux_has_session
    pane_pid_of = pane_pid_fn or _tmux_pane_pid
    pane_id_of = pane_id_fn or _tmux_pane_id
    now_fn = now or time.time
    free_ram_of = free_ram_fn or _default_free_ram
    runner = spawn_runner or _default_spawn_runner(orchestra_dir, timeout_s)
    store = BgStateStore(wal_dir, root)

    reused = has_session(green_alias)
    spawn_result = None
    if not reused:
        # Pre-spawn free-RAM floor (item 2c): only a NEW spawn is gated (a reused crash
        # re-entry mints no green, so there is no OOM risk to guard). FAIL-CLOSED + LOUD
        # BEFORE the spawn — never mint a green that OOM-dies mid-boot, silently leaving
        # the seat un-rotated (the pred4 class).
        ram = free_ram_of()
        avail = ram.get("avail_mb", 0)
        if avail < SPAWN_MIN_AVAIL_MB:
            raise PreSpawnRAMFloor(
                f"spawn_green: refusing to spawn {green_alias!r} — free RAM under floor "
                f"(MemAvailable {avail} MiB < SPAWN_MIN_AVAIL_MB {SPAWN_MIN_AVAIL_MB} MiB; "
                f"SwapFree {ram.get('swap_free_mb', 0)} MiB). A green would likely OOM-die "
                f"at boot; arm/beat must alarm + hold rather than mint a doomed green.")
        # capture BEFORE the spawn (lower bound for the liveness ts-fence)
        spawned_at = now_fn()
        # Pass the BG green marker env THROUGH to spawn-agent.sh so the green (and its
        # capture_green_sid / green_boot_probe / quarantine hooks) are BG-aware (P2.7).
        spawn_result = runner(
            green_alias, env={**os.environ, **bg_green_env(root, green_alias, wal_dir)})
    else:
        # crash re-entry: keep the ORIGINAL spawn ts (do not move the ts-fence)
        spawned_at = store.read_meta("green_spawned_at", None) or now_fn()

    pane_pid = pane_pid_of(green_alias)
    if not isinstance(pane_pid, int) or pane_pid <= 0:
        # Fail-closed: a spawn that produced no reachable pane is not a success —
        # raise so the firewall alarms + disarms (never advance PREWARMING blind).
        # Surface the runner's exit code + output tails so the driver log shows WHAT
        # broke inside the spawn (a blind fail-closed spawn is itself a defect).
        raise RuntimeError(
            f"spawn_green: no live pane pid for {green_alias!r} after spawn "
            f"(reused={reused}) — refusing to advance without a reap-reachable pid"
            + _spawn_failure_detail(spawn_result))

    pane_id = pane_id_of(green_alias)
    # Record for reap reachability (pane_pid) + the Layer-2 liveness gate (pane_id,
    # spawned_at). Persist across beats/crashes.
    store.write_meta("green_pane_pid", pane_pid)
    if pane_id:
        store.write_meta("green_pane_id", pane_id)
    store.write_meta("green_spawned_at", spawned_at)
    _unlink_stale_pane_event(orchestra_dir, pane_id, spawned_at)

    # (a) GREEN-SIDE #1 (Blocker-1 applied to the green): the pane is reap-reachable, so
    # register the green's LIVE cid/sid into its registry row + flat + .sid BEFORE
    # returning — otherwise verify/(b) watches the wrong (blue) sid and false-stalls. The
    # registrar is INJECTED (provider-agnostic; the runtime adapter resolves the green's
    # cid and does the DB-first write), so this stays zero-runtime-literal. FAIL-SOFT: a
    # not-yet-booted (unresolvable) green must never abort the spawn — record a breadcrumb
    # and return green_sid None; the verify seam fail-closes and (d) prunes.
    green_sid = None
    if register_green_sid_fn is not None:
        try:
            green_sid = register_green_sid_fn(green_alias)
        except Exception as e:  # noqa: BLE001 — fail-soft: never abort spawn on sid-reg
            store.write_meta("green_sid_register_error", str(e))
            green_sid = None
        if green_sid:
            store.write_meta("green_session_id", green_sid)
    return {"alias": green_alias, "pane_pid": pane_pid, "pane_id": pane_id,
            "spawned_at": spawned_at, "detached": True, "reused": reused,
            "green_sid": green_sid}
