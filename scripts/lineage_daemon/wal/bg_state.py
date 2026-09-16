"""bg_state — per-lineage Blue-Green state machine + arming + beat supervisor.

This is the INERTNESS ANCHOR of stage-3 (gm emphatic): with ``bg_enabled``
ABSENT (default), the entire machine is a no-op recorder — no swap, no eviction,
no state write on a live beat. Arming is a per-lineage FLAG FILE
(``state/wal/<root>.bg_enabled``, mirroring self_retire_armed ops already know),
NOT a registry field (registry rows get rewritten by promote/repair — an arming
bit must not ride a frequently-rewritten row). A global ``state/wal/BG_DISABLED``
kills all swaps fleet-wide regardless of per-lineage flags (one-tap halt for the
highest-blast-radius op).

The state store uses the salvaged flock + write-temp + atomic-rename discipline
(baseline_store.py) so a beat and a manual op cannot corrupt it.

The beat is the SWAP SUPERVISOR (DEC-1788323461 #12/C2 + gm folds): on observing
SWAPPING it routes by lock + last outcome —
  * lock HELD (executor alive)          -> strict NO-OP (never double-swap);
  * lock FREE + ordinary crash          -> RESUME the idempotent effects (C2);
  * lock FREE + #11 half-swap timeout   -> COMPLETE the reap (NEVER Green-spawn —
    a half-swap needs reap-completion + store-reconcile, not a fresh Green, gm).
"""
import fcntl
import json
import os

STATES = ("SOLO", "PREWARMING", "READY", "SWAPPING", "DRAINED", "DEGRADED",
          # P0.3: the reap-gate refused (canonical did NOT advance to green) — Blue was
          # NOT reaped; the seat awaits manual retirement rather than a blind reap.
          "RETIRE_PENDING")

SUPERVISE_NOOP = "noop"
SUPERVISE_RESUME_EFFECTS = "resume-effects"
SUPERVISE_COMPLETE_REAP = "complete-reap"

# DEGRADED ladder (DP-S3-2): re-page at N beats, hard the operator-card at 2N.
DEGRADED_REPAGE_BEATS = 4
DEGRADED_HARDCARD_BEATS = 8
CTX_FASTPATH = 0.90


def _flag_path(wal_dir, root):
    return os.path.join(wal_dir, f"{root}.bg_enabled")


def _global_disable_path(wal_dir):
    return os.path.join(wal_dir, "BG_DISABLED")


def _telemetry_disable_path(wal_dir):
    """The telemetry daemon's OWN kill-switch (DEC-1788479670). Distinct from
    BG_DISABLED: the arm wants BG_DISABLED PRESENT (arm off) while telemetry — a
    pure observer that rotates nothing — must keep running. Honored by telemetryd
    and by the (telemetry-owned) MultiplexedTailer; NEVER gates the arm."""
    return os.path.join(wal_dir, "TELEMETRY_DISABLED")


def is_armed(wal_dir, root):
    """A lineage is armed ONLY if its flag file exists AND the global kill-switch
    is absent. Default (no flag) = disarmed = the machine is inert."""
    if os.path.exists(_global_disable_path(wal_dir)):
        return False
    return os.path.exists(_flag_path(wal_dir, root))


TERMINAL_STATES = ("DRAINED", "DEGRADED", "RETIRE_PENDING")


def reset_terminal_to_solo(wal_dir, root, reason="arm:terminal-reset"):
    """A stale TERMINAL state (DRAINED/DEGRADED/RETIRE_PENDING) surviving from a prior
    fire must never be carried into a fresh arm — otherwise the beat logs a same-state
    non-transition forever and 'mode=ARMED -> skip:excluded' silently EXCLUDES the seat
    (gm msg_96438e1d). Resets to SOLO with a history breadcrumb; meta (last_valid_ctx_pct
    etc.) is PRESERVED. A LIVE in-flight state (SOLO/PREWARMING/READY/SWAPPING) is left
    untouched — never reset a fire in progress. Returns True iff a reset happened."""
    store = BgStateStore(wal_dir, root)
    state = store.read().get("state")
    if state not in TERMINAL_STATES:
        return False
    store.write_state("SOLO", reason=reason)
    print(f"[bg_state] {root}: terminal state {state} reset to SOLO at arm ({reason})")
    return True


class ArmNotConformant(Exception):
    """The seat's provider adapter did not pass the arm-time conformance gate (DEC v2 §4):
    an unknown runtime, or read_ctx can't return a fresh, in-range, normalized ctx for the
    seat. Fail-closed — the flag is NOT written, so an un-conformed provider can never
    ctx-fire (the unstaged-fixture class). Death remains the safety net regardless."""


def arm_lineage(wal_dir, root, runtime, seat=None, *,
                register_sid_fn=None, start_capture_fn=None,
                idle=None, retained_ctx_fn=None, **read_ctx_kw):
    """The CONFORMANCE-GATED arm (DEC v2 §4, fail-closed) + GOAL-FINDING #1/#5 arm-time
    mechanics. Writes the ``bg_enabled`` flag ONLY if the provider adapter passes the
    arm-time conformance gate; otherwise raises ``ArmNotConformant`` and writes NOTHING.
    All ctx-fire arming MUST go through this — a bare flag touch bypasses the gate and is
    a defect. ``seat`` defaults to ``root``. Returns the flag path on success.

    GOAL-FINDING #1 (sid-register) + #5 (WAL-capture-at-arm): BEFORE the conformance gate,
    mechanically (a) register the seat's live session_id so its ctx becomes readable and
    (b) start WAL capture WITH backfill so the seat becomes hydratable — an UNSTAGED seat
    (the demo fixture: no sid map, no WAL) becomes fire-able by ARMING ALONE, never by
    hand-staging. Both hooks are injected (the arm command wires the real
    identity_writer.update_session + run_capture one-shot); FAIL-SOFT (a hook error is
    recorded as a durable breadcrumb, never fatal) because the conformance gate below is
    the HARD check — it refuses the arm if the sid-map/read_ctx did not actually take."""
    from .conformance import is_arm_conformant, default_seat_idle
    seat = seat or root
    _st = BgStateStore(wal_dir, root)
    # #1: mechanically map the live sid so read_ctx/conformance can see the seat.
    if register_sid_fn is not None:
        try:
            register_sid_fn(seat)
        except Exception as e:  # noqa: BLE001 — fail-soft; conformance below is the gate
            _st.write_meta("arm_sid_register_error", str(e))
    # #5: mechanically start WAL capture (with backfill) so the seat is hydratable.
    if start_capture_fn is not None:
        try:
            start_capture_fn(seat)
        except Exception as e:  # noqa: BLE001 — fail-soft; the beat's WAL gate is the gate
            _st.write_meta("arm_capture_start_error", str(e))
    # v2.4 idle-retained: supply the seat's idle-state + the retained-ctx reader (bg_state meta,
    # the SAME source the beat's finding #2 uses) so the gate can accept an idle seat's last-valid
    # in-range read (bounded 6h) instead of refusing every stale detector. Live defaults probe the
    # seat's state (agent-status) + read the meta; a caller may inject both (tests / the pre-arm pass).
    _idle = default_seat_idle(seat) if idle is None else idle
    _retained = retained_ctx_fn or (lambda: (_st.read_meta("last_valid_ctx_pct"),
                                             _st.read_meta("last_valid_ctx_ts")))
    ok, reasons = is_arm_conformant(runtime, seat, idle=_idle, retained_ctx_fn=_retained,
                                    **read_ctx_kw)
    if not ok:
        raise ArmNotConformant(
            f"{root} runtime={runtime!r} refused at arm (fail-closed): {reasons}")
    # A terminal state (DRAINED/DEGRADED/RETIRE_PENDING) can never be carried into a
    # fresh arm — reset it BEFORE hooks/flag so a re-armed seat is fire-able again.
    reset_terminal_to_solo(wal_dir, root)
    os.makedirs(wal_dir, exist_ok=True)
    path = _flag_path(wal_dir, root)
    with open(path, "w") as fh:
        fh.write("")
    return path


class BgStateStore:
    def __init__(self, wal_dir, root):
        os.makedirs(wal_dir, exist_ok=True)
        self._path = os.path.join(wal_dir, f"{root}.bg.json")
        self._lock_path = self._path + ".lock"
        self._root = root

    def read(self):
        try:
            with open(self._path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"state": "SOLO", "root": self._root, "history": []}

    def write_state(self, state, reason=None):
        if state not in STATES:
            raise ValueError(f"unknown bg state {state!r}")
        lock_fh = open(self._lock_path, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            data = self.read()
            data["state"] = state
            data.setdefault("root", self._root)
            hist = data.setdefault("history", [])
            hist.append({"state": state, "reason": reason})
            data["history"] = hist[-20:]  # last 20 transitions
            self._atomic_write(data)
            return data
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    def write_meta(self, key, value):
        """Persist a per-seat scalar under ``meta`` WITHOUT touching the state
        machine — the live-pane drill seams need cross-beat metadata (the Green
        pane pid for reap reachability, the last-hydrated WAL seq, the no-WAL-
        progress beat count for the verify stall bound). Same flock + atomic-write
        discipline as write_state."""
        lock_fh = open(self._lock_path, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX)
            data = self.read()
            data.setdefault("root", self._root)
            data.setdefault("meta", {})[key] = value
            self._atomic_write(data)
            return data
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    def read_meta(self, key, default=None):
        return (self.read().get("meta") or {}).get(key, default)

    def _atomic_write(self, data):
        tmp = self._path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)


def supervise(store, lock_held, last_outcome):
    """Beat-as-supervisor routing for a lineage. Returns the action the beat
    should take. Only SWAPPING is actionable; every other state is a no-op here
    (transitions are computed elsewhere by decide v2)."""
    state = store.read()["state"]
    if state != "SWAPPING":
        return SUPERVISE_NOOP
    if lock_held:
        return SUPERVISE_NOOP                      # executor alive (#12)
    if last_outcome == "swap-timeout":
        return SUPERVISE_COMPLETE_REAP             # #11 half-swap (gm criterion)
    if last_outcome == "effects-incomplete":
        return SUPERVISE_RESUME_EFFECTS            # crashed executor (C2)
    return SUPERVISE_NOOP


def degraded_action(beats, ctx_pct):
    """DEGRADED ladder decision. ctx≥0.90 fires the the operator card immediately
    (rescue-before-death); otherwise re-page at N=4, hard card at 2N=8. The
    ladder resets on a state transition (caller resets `beats`)."""
    if ctx_pct >= CTX_FASTPATH:
        return {"repage": True, "shaw_card": True,
                "reason": "ctx-pressure-fastpath"}
    if beats >= DEGRADED_HARDCARD_BEATS:
        return {"repage": True, "shaw_card": True, "reason": "hardcard-2n"}
    if beats >= DEGRADED_REPAGE_BEATS:
        return {"repage": True, "shaw_card": False, "reason": "repage-n"}
    return {"repage": False, "shaw_card": False, "reason": "within-window"}
