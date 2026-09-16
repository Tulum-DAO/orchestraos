"""bg_arm — the async Blue-Green ORCHESTRATOR (stage-3 final piece).

One ``beat(obs)`` step per armed lineage ties the three built pieces together:
  decide_bg(obs) -> action + reason token
  bg_state       -> current state + arming gate + transition persistence
  swap_executor  -> the atomic cutover (via an injected swap seam)

Everything external is dependency-INJECTED (``seams``): spawn / register_provisional
/ verify / hydrate / swap / reap. Stage-3 tests pass pure fakes; stage-4 injects the
real setsid-detached spawn, the ``wal probe`` verify, the ``wal digest`` hydrate, a
``swap_generation`` wrapper, and the grandchild-safe reap — with NO change to this
orchestration logic (DP-S3-4).

ARMING PRECONDITION (agreed with the identity-store-builder): ``bg_enabled`` is a
STRICT SUBSET of cutover-active. Pre-cutover the identity DB is ABSENT by design,
so a blue-green swap is physically impossible; if a lineage is bg_enabled while
cutover is inactive, ``beat`` RAISES ``ArmRefused`` (loud) rather than attempting a
swap that could never succeed. Disarmed (no flag) or globally BG_DISABLED = inert
no-op.

INVARIANT #2 (from the p4/p5 reconciler round): the provisional Green is
registered into the store AT PREWARM (register-then-spawn), so the out-of-lock
*/10 reconcile never sees a prewarming Green as "absent from store".
"""
import os

from . import bg_quarantine
from .bg_state import BgStateStore, is_armed, degraded_action
from .decide_bg import decide_bg
from .store import WalStore

# GOAL-FINDING #5 (WAL-capture-at-arm): grace beats an armed seat gets for its WAL capture
# to produce events before the beat REFUSES to prewarm. "Zero events after one beat" =>
# refuse. 1 => the first empty beat kicks/awaits capture; the next still-empty beat refuses.
WAL_ARM_MAX_BEATS = 1


class ArmRefused(Exception):
    """bg_enabled is set for a lineage while the store cutover is NOT active.
    A blue-green swap is impossible pre-cutover (no DB); refuse loudly rather
    than fire a doomed swap or silently no-op a mis-armed lineage."""


class WalCaptureNotStarted(Exception):
    """GOAL-FINDING #5: an armed seat whose WAL (state/wal/<root>.db) has ZERO events
    after the grace window — hydrate would have NOTHING to deliver (last_hydrated_seq
    None), so a spawned green could never ingest and READY is unreachable. Refuse to
    prewarm (never spawn an un-hydratable green) and raise LOUD; the beat firewall alarms
    and the anti-orphan disarms-to-legacy after N. Fail-closed by design."""


class BgArm:
    def __init__(self, wal_dir, root, seams, cutover_active,
                 effect_timeout_s=120.0, forced=False, blue_pane_pid_fn=None,
                 green_ingested_seq_fn=None, blue_wal_event_count_fn=None,
                 green_wake_fn=None, green_quota_fn=None):
        self._wal_dir = wal_dir
        # Vendor-quota gate (gm msg_7b3ffa11): fn(green_alias) -> {exhausted, kind, limit_id,
        # reset_at} or None (unknown). None (legacy callers) => never gates.
        self._green_quota_fn = green_quota_fn
        self._root = root
        self._seams = seams
        # GOAL-FINDING #5: how many WAL events blue has captured, for the WAL-at-arm gate.
        # Default (None) = the real WalStore reader (fail-closed: absent/empty => refuse).
        # Injected in unit tests that exercise OTHER invariants and don't seed a WAL.
        self._blue_wal_event_count_fn = blue_wal_event_count_fn
        # GOAL-FINDING #4: wakes the idle green to ingest its delivered hydrate digest
        # (green_wake.wake_green_to_ingest wired over the provider adapter's inject_text +
        # transcript-started probe + Enter-resend) -> returns True iff the ingest turn
        # STARTED by effect. None (default) = legacy PREWARM->READY (verify only), so the
        # 200+ unit tests are unchanged; the arm command wires the real waker for a fire.
        self._green_wake_fn = green_wake_fn
        self._cutover_active = cutover_active
        # M1b-h (leg-(ii)): resolves the GREEN's ACTUAL ingested through_seq from its
        # ACTUALLY-DELIVERED lineage_hydrate rows, so the non-death ctx-swap can VERIFY
        # the green ingested through the final cut H before promoting it. Injected (a
        # closure over collect_ingested_artifact keyed on orchestra_dir+green_alias);
        # bg_arm has no orchestra_dir of its own. If neither this fn NOR a seams-level
        # `green_ingested_seq` is wired, the reader returns None => fail-closed defer.
        self._green_ingested_seq_fn = green_ingested_seq_fn
        # P0.1: resolves BLUE's live pane pid so it can be recorded at PREWARM (the
        # immutable reap binding). Default = the live blue seat's pane (session == root,
        # matching bg_live_beat's BLUE_SESSION). Injected in tests.
        self._blue_pane_pid_fn = blue_pane_pid_fn or self._default_blue_pane_pid_fn
        # SUPERVISED hand-drive (leg (i) — bg_live_beat.py --manual-override): when
        # forced, this arm bypasses its own is_armed guard so the ONE named seat acts
        # WITH the global BG_DISABLED kill-switch present. Set per-beat by
        # bg_supervise_fleet from force_roots; the autonomous callers never force, so
        # BG_DISABLED still keeps them inert. force NEVER bypasses the cutover gate.
        self._forced = forced
        self._store = BgStateStore(wal_dir, root)
        # The Green alias MUST match the projector's provisional-alias convention
        # `{root}-g{N}` (projector._alias_payload) — spawn-agent/verify/hydrate key
        # on the projected registry name, so a literal `-g-green` would spawn/probe
        # an UNREGISTERED name (auto-register-refuse under cutover). Derived per-beat
        # from obs['green']['generation'] (blue+1); a placeholder until beat() sets it.
        self._green_alias = f"{root}-g-green"

    def _green_alias_for(self, obs):
        gen = (obs.get("green") or {}).get("generation")
        return f"{self._root}-g{gen}" if gen is not None else f"{self._root}-g-green"

    def beat(self, obs):
        """One supervised step for this lineage. No-op unless armed; raises
        ArmRefused if armed-without-cutover."""
        if not self._forced and not is_armed(self._wal_dir, self._root):
            return  # inert: disarmed or globally BG_DISABLED (forced overrides for
            #        the ONE supervised hand-driven seat only)

        # Resolve the Green alias to the PROJECTED provisional name `{root}-g{N}`
        # (matches projector._alias_payload) so spawn/verify/hydrate key on the
        # registered alias, not the unregistered literal `-g-green`.
        self._green_alias = self._green_alias_for(obs)

        # bg_enabled ⊂ cutover: armed but cutover inactive = refuse loudly.
        if not self._cutover_active():
            raise ArmRefused(
                f"{self._root} is bg_enabled but the store cutover is inactive; "
                f"a blue-green swap is impossible pre-cutover (DB absent)")

        decision = decide_bg(obs)
        action = decision["action"]
        state = self._store.read()["state"]

        # leg (i): a swap SUPPRESSED because blue's live occupant != the canonical seat
        # (a fresh successor already took the seat) must never be silent — record a
        # durable breadcrumb (the offending live sid) for gm/telemetry. decide_bg has
        # already returned noop, so the swap simply does not fire; this only logs it.
        if decision.get("reason") == "suppress:live-sid-mismatch":
            self._store.write_meta("swap_suppressed_live_mismatch",
                                   obs.get("blue_sid_used") or True)

        if action == "swap":
            # P0.5 READY-gate: a CTX-triggered swap (never_gated=False) may fire ONLY
            # when the Green is verified-READY — otherwise it would promote an unverified
            # (or dead) successor that never proved it ingested Blue's state. When not
            # READY, DEFER: do a prewarm/verify beat so the NEXT beat can swap once READY.
            # DEATH (never_gated=True) is NEVER gated — a dying seat is rescued regardless.
            if decision.get("never_gated"):

                # death is NEVER gated (a dead blue beats no seat) — breadcrumb only, so the

                # successor's first turn is known-blocked.

                self._green_quota_hold("death-swap")

                self._do_swap(obs, decision)

            elif state == "READY":

                # seam 3 (gm msg_7b3ffa11): re-read the green's quota AT FIRE TIME; a READY

                # green that has since hit its limit holds (stays READY, no prune).

                if self._green_quota_hold("swap-gate"):

                    return

                self._do_swap(obs, decision)

            else:

                self._do_prewarm_or_verify(state, obs)
        elif action == "prewarm":
            self._do_prewarm_or_verify(state, obs)
        # noop / uncalibrated-solo-alarm: nothing to orchestrate here (the alarm
        # is surfaced by decide_bg's reason token; the beat records no transition)

    def _register_then_project_then_spawn(self, green):
        """Invariant #2 + F3 read-your-writes: register the provisional Green in
        the store, SYNCHRONOUSLY reproject so spawn-agent reads a fresh registry
        (not a stale one inside the projector debounce window — the U16 refusal
        race caught live at G7), THEN spawn. project_now RAISES on failure ->
        we abort BEFORE spawn (fail-closed: never spawn into an unprojected
        state, never advance state; the beat retries next tick).

        The Green GENERATION + model come from obs['green'] (blue+1, set by
        build_obs) and MUST be threaded into register_provisional — the real
        identity_writer.register_provisional INSERTs a NOT-NULL generations row, so
        a missing generation is a live IntegrityError (the fake-only gap the unit
        seams hid; caught on the watched first fire)."""
        self._seams.register_provisional(
            self._root, self._green_alias,
            generation=green.get("generation"), model=green.get("model"))
        self._seams.project_now(self._root)   # raises -> caller aborts pre-spawn
        self._seams.spawn(self._root, self._green_alias)
        # M2 (leg-(ii)): ARM the green's quarantine the instant it exists, so an
        # autonomous green boots write-CONTAINED (read-only) until swap-wake. INERT
        # in Target B: the marker only ENFORCES once the PreToolUse hook is
        # registered in ~/.claude/settings.json (an A-time gm+the operator gesture, NOT here).
        self._arm_quarantine()

    @staticmethod
    def _default_blue_pane_pid_fn(root):
        """Resolve the LIVE blue seat's pane pid (session == root). Lazy import so
        bg_arm stays importable on a base without reaper wired."""
        from . import reaper
        return reaper._default_pane_pid_fn(root)

    def _record_blue_pane_pid(self):
        """P0.1: capture blue's live pane pid AT PREWARM and persist it to bg_state meta
        `blue_pane_pid` (the immutable reap binding the reap seam reads). FAIL-SAFE — a
        resolver hiccup must NEVER abort the prewarm beat; a None result records nothing
        (the reap then fail-closes on the legacy resolver rather than a wrong pid)."""
        try:
            pid = self._blue_pane_pid_fn(self._root)
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                self._store.write_meta("blue_pane_pid", pid)
        except Exception:  # noqa: BLE001 — capture is a safety add-on, never a blocker
            pass

    def _blue_wal_event_count(self, blue_sid=None):
        """GOAL-FINDING #5: how many WAL events blue has captured (state/wal/<root>.db).
        FAIL-CLOSED: an absent/unreadable db counts as ZERO (an un-hydratable seat), so a
        capture that never started is caught, never assumed healthy. An injected fn (unit
        tests of other invariants) overrides the real reader."""
        if self._blue_wal_event_count_fn is not None:
            try:
                return int(self._blue_wal_event_count_fn())
            except Exception:  # noqa: BLE001 — fail-closed
                return 0
        try:
            store = WalStore(os.path.join(self._wal_dir, f"{self._root}.db"))
            try:
                # freshness (a PM-seat class): only events keyed to the LIVE blue sid count;
                # a prior capture's events under an old sid are not hydratable evidence.
                return store.count_events(self._root, sid=blue_sid)
            finally:
                store.close()
        except Exception:  # noqa: BLE001 — fail-closed: unreadable WAL => treat as empty
            return 0

    def _wal_ready_for_prewarm(self, blue_sid=None):
        """GOAL-FINDING #5 gate. Returns True when blue's WAL has events (spawn may
        proceed) or False to DEFER this beat (stay SOLO, benign — capture may produce
        events by next beat). Raises WalCaptureNotStarted (LOUD) when the WAL is STILL
        empty after the grace window (WAL_ARM_MAX_BEATS) — refuse the un-hydratable green.
        A non-empty WAL clears the empty-beat counter."""
        if self._blue_wal_event_count(blue_sid=blue_sid) > 0:
            self._store.write_meta("wal_empty_beats", 0)
            return True
        n = (self._store.read_meta("wal_empty_beats") or 0) + 1
        self._store.write_meta("wal_empty_beats", n)
        if n > WAL_ARM_MAX_BEATS:
            self._store.write_meta("wal_empty_at_arm_alarm", True)
            raise WalCaptureNotStarted(
                f"{self._root} is armed but its WAL (state/wal/{self._root}.db) has ZERO "
                f"events after {n} beats; refusing to spawn an un-hydratable green "
                f"(arming must start WAL capture keyed on the live sid, with backfill)")
        return False  # within the grace window: DEFER the spawn (stay SOLO), no alarm

    def _wake_and_verify_ingest(self):
        """GOAL-FINDING #4: WAKE the idle green to ingest its delivered hydrate digest
        (via the injected green_wake_fn = green_wake.wake_green_to_ingest over the provider
        adapter, which injects + confirms the turn STARTED by effect / #3), then verify
        ingestion by effect (through_seq >= delivered H). Returns True only when the green
        provably ingested. LOUD + NOT-READY when it never woke; fail-soft on a hook error
        (stay PREWARMING, retry next beat). Never advances READY on delivery/verify alone."""
        from .green_wake import ingestion_verified
        try:
            woke = bool(self._green_wake_fn(self._green_alias))
        except Exception as e:  # noqa: BLE001 — a wake hiccup defers, never crashes the beat
            self._store.write_meta("green_wake_error", str(e))
            return False
        if not woke:
            self._store.write_meta("green_wake_failed_alarm", True)
            return False  # LOUD: the green never took its ingest turn -> not READY
        if self._store.read_meta("green_wake_failed_alarm"):
            # episode over (gm msg_86bcc168 item 1(b)): a later successful wake clears the
            # flag so the beat's once-per-episode page does not outlive the stall.
            self._store.write_meta("green_wake_failed_alarm", False)
        h = self._store.read_meta("last_hydrated_seq")
        if h is None:
            h = self._store.read_meta("final_cut_seq")
        return ingestion_verified(self._green_ingested_through, self._green_alias, h)

    def _green_quota_hold(self, seam):
        """Vendor-quota gate (gm msg_7b3ffa11). True => HOLD this seam: the green is
        quota-limited (credits depleted / window hit) by its runtime's own truth source.
        Breadcrumb green_quota_limited {kind, limit_id, reset_at, seam, ts} + a deduped alarm
        key (kind:reset_at) in green_quota_alarm so the beat pages once per episode. Never
        prunes/respawns. None/unknown/any error => False (proceed as today)."""
        if self._green_quota_fn is None:
            return False
        try:
            q = self._green_quota_fn(self._green_alias)
        except Exception:  # noqa: BLE001 — unknown => proceed
            return False
        if not q or not q.get("exhausted"):
            return False
        import time as _t
        crumb = {"kind": q.get("kind"), "limit_id": q.get("limit_id"),
                 "reset_at": q.get("reset_at"), "detail": q.get("detail"),
                 "seam": seam, "ts": _t.time()}
        self._store.write_meta("green_quota_limited", crumb)
        key = f"{q.get('kind')}:{q.get('reset_at')}"
        if self._store.read_meta("green_quota_alarm") != key:
            self._store.write_meta("green_quota_alarm", key)
        return True

    def _do_prewarm_or_verify(self, state, obs):
        # P0.1: record blue's live pane pid while blue is still canonical (before any
        # swap renames/repoints). Idempotent across prewarm beats (blue's pid is stable).
        self._record_blue_pane_pid()
        if state in ("SOLO",):
            # GOAL-FINDING #5 (WAL-at-arm, fail-closed): NEVER spawn a green when blue's
            # WAL is empty — it could never be hydrated (hydrate has nothing to deliver),
            # so READY is unreachable and the swap silently never fires. Give capture a
            # grace window (WAL_ARM_MAX_BEATS) to produce events, then REFUSE + alarm LOUD.
            if not self._wal_ready_for_prewarm(blue_sid=obs.get("blue_sid_used") or None):
                return  # defer: WAL empty within grace, stay SOLO, retry next beat
            # register provisional -> project_now -> spawn (invariant #2 + F3),
            # then advance. A project_now raise propagates: no spawn, no state
            # change, retry next beat.
            self._register_then_project_then_spawn(obs["green"])
            self._store.write_state("PREWARMING", reason="ctx:prewarm")
        elif state == "PREWARMING":
            # seams 1+2 (gm msg_7b3ffa11): a quota-limited green is never woken and never
            # written READY — hold PREWARMING (no prune) until its runtime says otherwise.
            if self._green_quota_hold("prewarm"):
                return
            if self._green_wake_fn is not None:
                # GOAL-FINDING #4 (lossless seam): hydrate DELIVERS the digest, but
                # msg_store never WAKES the idle green -> it never ingests -> READY is
                # unreachable. So deliver, then WAKE the green to ingest (reusing #3
                # confirm-started), and gate READY on ingestion PROVED by effect
                # (through_seq >= delivered H) — never on verify/delivery alone.
                self._seams.hydrate(self._root, self._green_alias, since_seq=None)
                if self._wake_and_verify_ingest() and \
                        self._seams.verify(self._root, self._green_alias):
                    self._store.write_state("READY", reason="ingest-verified")
            else:
                # legacy path (unchanged): shadow-verify (wal probe seam); advance to
                # READY on success. Verify reads the probe answer PRODUCED at the end of
                # the PRIOR beat. Then keep hydrating deltas (staleness ≤ one beat).
                if self._seams.verify(self._root, self._green_alias):
                    self._store.write_state("READY", reason="shadow-verified")
                self._seams.hydrate(self._root, self._green_alias, since_seq=None)
            # gm Option-1 (bar#4 seq-A): invoke the green-boot producer AFTER hydrate
            # so it reads the JUST-DELIVERED ingested_view row (a spawn-time one-shot
            # would run before any hydrate row exists). FAIL-SAFE: a producer error
            # never aborts the beat — verify then fail-closes the swap safely (no
            # probe.json), no worse than drill-inject-off.
            self._produce_probe_answer()
        # READY: nothing to do until the swap threshold/death

    def _arm_quarantine(self):
        """M2: write the green's ACTIVE quarantine marker at spawn. Guarded —
        containment is a safety add-on, NEVER a swap blocker: a hiccup (bad alias,
        disk) must not abort the spawn/beat. INERT until the A-time PreToolUse hook
        reads the marker to block a shadow green's writes."""
        try:
            bg_quarantine.arm_quarantine(self._wal_dir, self._green_alias)
        except Exception:  # noqa: BLE001 — fail-safe: containment never blocks the beat
            pass

    def _final_cut_hydrate(self):
        """M1b: one last delta hydrate before the swap. since_seq=None ships only the
        events after the persisted last-hydrated seq (the post-prewarm delta). Records
        the delivered through_seq H (final_cut_seq) for the promote record. Guarded —
        containment of the lossless boundary must never block the cutover."""
        try:
            res = self._seams.hydrate(self._root, self._green_alias, since_seq=None)
            h = res.get("new_since_seq") if isinstance(res, dict) else None
            if h is not None:
                self._store.write_meta("final_cut_seq", h)
            return h
        except Exception:  # noqa: BLE001 — fail-safe: never abort the swap on hydrate
            return None

    def _green_ingested_through(self, green_alias):
        """M1b-h: the GREEN's ACTUAL ingested through_seq, read from its ACTUALLY-
        DELIVERED lineage_hydrate rows (green_boot_probe.collect_ingested_artifact).
        Prefers the injected constructor closure; falls back to a seams-level
        `green_ingested_seq` (real_seams_for wires it — it holds orchestra_dir).
        FAIL-CLOSED: unwired / raises / absent / non-int => None (NOT-verified), so a
        blind (un-provable) promote is never taken."""
        fn = self._green_ingested_seq_fn or getattr(self._seams, "green_ingested_seq", None)
        if fn is None:
            return None
        try:
            v = fn(green_alias)
            return int(v) if v is not None else None
        except Exception:  # noqa: BLE001 — fail-closed: an unreadable ingest defers
            return None

    def _lift_quarantine(self):
        """M2: remove the marker at swap-wake (the green is now canonical and may
        mutate). Idempotent + guarded (same fail-safe discipline as arm)."""
        try:
            bg_quarantine.lift_quarantine(self._wal_dir, self._green_alias)
        except Exception:  # noqa: BLE001 — fail-safe: never block the swap
            pass

    def _produce_probe_answer(self):
        """Invoke the injected produce seam (green_boot_probe.produce_probe_answer),
        guarded — the orchestrator must never abort a beat on a producer hiccup."""
        produce = getattr(self._seams, "produce", None)
        if produce is None:
            return  # older seam set without the producer wired: inert (drill/tests)
        try:
            produce(self._root, self._green_alias)
        except Exception:  # noqa: BLE001 — fail-safe: a producer error is not fatal
            pass

    def _do_swap(self, obs, decision):
        # death is never gated: swap fires even from SOLO/PREWARMING (unverified
        # Green + WAL beats a dead canonical). If no Green exists yet, spawn one
        # first (cold-spawn path) so the swap has a target.
        state = self._store.read()["state"]
        if state == "SOLO":
            # death cold-spawn carries the same register->spawn race; guard it
            # with the F3 read-your-writes project_now (abort before spawn on raise).
            self._register_then_project_then_spawn(obs["green"])
        self._store.write_state("SWAPPING", reason=decision["reason"])

        # M1b (leg-(ii)): FINAL-CUT hydrate — ship Blue's last WAL delta to Green ONE
        # more time immediately before the atomic swap, so a response Blue appended
        # AFTER the last prewarm/verify beat is delivered (durably) before Blue is
        # reaped (the lossless boundary). Captures H (through_seq) for the promote
        # record. Fail-safe: a hydrate hiccup must NEVER abort the swap (death-swaps
        # cannot wait) — worst case is one-beat staleness, never a blocked cutover.
        h = self._final_cut_hydrate()

        # M1b-h (leg-(ii)): VERIFIED_AT(H) gate on the NON-death ctx-swap. M1b's durable
        # delivery + record-H is already sufficient; this is the SUSPENDERS: do NOT
        # promote a green unless it has PROVABLY ingested through the final cut H. The
        # gate engages ONLY when a boundary H was recorded this beat (an int) — if the
        # final-cut hydrate raised / recorded nothing (h is None) there is no boundary
        # to verify, so we keep M1b's guarantee (swap, worst-case one-beat staleness)
        # rather than introduce a deadlock. DEATH (never_gated) skips the gate entirely
        # (a dying seat is rescued regardless; durable capsule + record-H suffice).
        if not decision.get("never_gated"):
            if h is None:
                # ARM-READINESS breadcrumb (gm): a NON-death swap with NO recorded
                # final-cut boundary (the final-cut hydrate raised / no-op'd) falls back
                # to M1b's swap-anyway — the verify suspenders cannot engage without an
                # H. Stamp a durable meta so a PERSISTENT hydrate-raise-then-unverified-
                # promote is LOUD (fleet 'unknown must surface' — P0.6 unknown->alarm,
                # M3h silent-null->LOUD), never a silent unverified promote.
                self._store.write_meta("m1bh_skipped_no_boundary", True)
            else:
                ingested = self._green_ingested_through(self._green_alias)
                if ingested is None or ingested < h:
                    # gm rulings: (1) FAIL-CLOSED-DEFER — do NOT swap AND stamp the
                    # m1bh_verify_pending=H alarm (both; the non-death swap has no
                    # urgency, so deferring until through_seq>=H is lossless).
                    # (2) REVERT-TO-READY + re-drive next beat (no mid-swap limbo); it
                    # converges as the green ingests H, and a never-converging loop
                    # surfaces via the alarm — never a silent spin, never a lossy
                    # promote of a not-yet-ingested green.
                    self._store.write_meta("m1bh_verify_pending", h)
                    self._store.write_state("READY", reason="m1bh-verify-pending")
                    return

        outcome = self._seams.swap(self._root, obs["green"],
                                   obs.get("blue_generation_id"))
        if getattr(outcome, "status", None) == "complete":
            # M2 (leg-(ii)): swap-wake — the green is now canonical, so LIFT its
            # quarantine (idempotent) before reap. Fail-safe: never blocks the swap.
            self._lift_quarantine()
            self._seams.reap(self._root, obs.get("blue_generation_id"))
            self._store.write_state("DRAINED", reason="swap-complete")
        elif getattr(outcome, "status", None) == "swap-timeout":
            # #11 half-swap: identity coherent, effects hung -> DEGRADED (needs
            # reap-COMPLETION by the beat supervisor, NOT a cold Green-spawn).
            self._store.write_state("DEGRADED", reason="swap-timeout")
        else:  # effects-incomplete: resumable by the beat supervisor (C2)
            self._store.write_state("DEGRADED", reason="effects-incomplete")
