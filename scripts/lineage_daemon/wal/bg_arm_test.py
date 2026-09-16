"""RED-first tests for bg_arm — the async Blue-Green ORCHESTRATOR (stage-3 final).

Ties decide_bg (transition decision) + bg_state (state machine/arming) +
swap_executor (atomic cutover) into one per-lineage beat step, with ALL external
effects dependency-INJECTED (spawn/verify/hydrate/swap/reap) so this is testable
now with pure fakes and wires to real seams at stage-4/cutover (DP-S3-4).

Invariants under test:
  - ARMING PRECONDITION: bg_enabled is a STRICT SUBSET of cutover-active. arm()
    REFUSES LOUDLY if bg_enabled is set while cutover is inactive (no swap can
    fire pre-cutover — the DB is absent by design). [store-builder agreement]
  - INERT: with bg_enabled absent, a beat step is a no-op (no spawn/swap).
  - PREWARM at ctx≥0.70: spawns Green (setsid-detached seam) AND registers a
    provisional Green into the store at prewarm (invariant #2 — the */10
    reconcile must never see a prewarming Green as absent).
  - READY: Green shadow-verified (wal probe seam) → READY.
  - SWAP at ctx≥0.80 OR death:*: swap_executor fires; death fires IMMEDIATELY
    even if Green unverified (death never gated).
  - reason-token drives everything (gating keys on reason, not action).
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_state import BgStateStore, is_armed  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm, ArmRefused  # noqa: E402


class _Seams:
    """Pure-fake injected seams recording calls."""
    def __init__(self, verify_ok=True, swap_status="complete",
                 project_now_raises=False, produce_raises=False):
        self.calls = []
        self._verify_ok = verify_ok
        self._swap_status = swap_status
        self._project_now_raises = project_now_raises
        self._produce_raises = produce_raises
        self.green_registered = False

    def spawn(self, root, green_alias):
        self.calls.append(("spawn", root, green_alias))
        return {"alias": green_alias, "pid": 4242, "detached": True}

    def register_provisional(self, root, green_alias, generation=None, model=None):
        # mirror the real seam signature; RECORD the threaded generation so a
        # regression that drops it (the live-fire NULL-generation bug) is caught
        # even at the fake-seam layer.
        self.green_registered = True
        self.registered_generation = generation
        self.calls.append(("register_provisional", root, green_alias, generation))

    def project_now(self, root):
        # F3 read-your-writes seam: synchronous reproject; raises => abort-prewarm.
        self.calls.append(("project_now", root))
        if self._project_now_raises:
            raise RuntimeError("projection failed — abort before spawn")

    def verify(self, root, green_alias):
        self.calls.append(("verify", root, green_alias))
        return self._verify_ok

    def hydrate(self, root, green_alias, since_seq):
        self.calls.append(("hydrate", root, green_alias, since_seq))

    def produce(self, root, green_alias):
        # gm Option-1: bg_arm invokes the green-boot producer AFTER hydrate each
        # PREWARMING beat. Fail-safe: a raise here must NOT abort the beat.
        self.calls.append(("produce", root, green_alias))
        if self._produce_raises:
            raise RuntimeError("producer hiccup (msg_store read failed)")

    def swap(self, root, green, blue_generation_id):
        self.calls.append(("swap", root, green, blue_generation_id))
        return type("O", (), {"status": self._swap_status, "swap_id": 1,
                              "green_generation_id": 7, "needs_degraded":
                              self._swap_status == "swap-timeout"})()

    def reap(self, root, blue):
        self.calls.append(("reap", root, blue))

    def _kinds(self):
        return [c[0] for c in self.calls]


def _arm(tmp_path, seams, armed=True, cutover=True):
    wal = str(tmp_path)
    if armed:
        (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    return BgArm(wal, "ios-watch-dev", seams=seams, blue_wal_event_count_fn=lambda: 1,
                 cutover_active=lambda: cutover)


def _obs(ctx_pct=0.0, death=None, blue_gen_id=6):
    return {"root": "ios-watch-dev", "runtime": "claude", "ctx_pct": ctx_pct,
            "death": death, "ceiling_calibrated": True,
            "blue_generation_id": blue_gen_id,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


# ---- arming precondition (bg_enabled ⊂ cutover) ----

def test_arm_refused_when_bg_enabled_but_cutover_inactive(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams, armed=True, cutover=False)
    with pytest.raises(ArmRefused):
        arm.beat(_obs(ctx_pct=0.85))       # would swap, but cutover OFF -> refuse
    assert seams.calls == []               # nothing fired


def test_inert_when_not_armed(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams, armed=False, cutover=True)
    arm.beat(_obs(ctx_pct=0.85))
    assert seams.calls == []               # disarmed = no-op


def test_global_bg_disabled_makes_inert(tmp_path):
    seams = _Seams()
    (tmp_path / "ios-watch-dev.bg_enabled").write_text("")
    (tmp_path / "BG_DISABLED").write_text("")
    arm = BgArm(str(tmp_path), "ios-watch-dev", seams=seams, blue_wal_event_count_fn=lambda: 1,
                cutover_active=lambda: True)
    arm.beat(_obs(ctx_pct=0.85))
    assert seams.calls == []


# ---- prewarm ----

def test_prewarm_spawns_and_registers_provisional_green(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))
    assert "spawn" in seams._kinds()
    # invariant #2: provisional Green registered at prewarm (reconcile-safe)
    assert seams.green_registered is True
    # the green GENERATION (obs['green']['generation']=7) is threaded into the
    # register seam — the fix for the live-fire NULL-generation IntegrityError.
    assert seams.registered_generation == 7
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "PREWARMING"


def test_prewarm_then_verify_advances_to_ready(tmp_path):
    seams = _Seams(verify_ok=True)
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))                 # -> PREWARMING (spawn)
    arm.beat(_obs(ctx_pct=0.74))                 # -> verify -> READY
    assert "verify" in seams._kinds()
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "READY"


def test_prewarm_produces_probe_answer_after_hydrate(tmp_path):
    """gm Option-1 (bar#4 seq-A): each PREWARMING beat invokes the green-boot
    producer AFTER hydrate — it reads the JUST-DELIVERED ingested_view row, so a
    spawn-time one-shot (running before any hydrate row exists) is wrong. Verified
    ordering: verify -> hydrate -> produce."""
    seams = _Seams(verify_ok=False)              # stay PREWARMING
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))                 # SOLO -> PREWARMING (spawn)
    arm.beat(_obs(ctx_pct=0.74))                 # PREWARMING: verify -> hydrate -> produce
    kinds = [c[0] for c in seams.calls]
    assert "produce" in kinds
    assert kinds.index("hydrate") < kinds.index("produce")


def test_prewarm_produce_failure_is_fail_safe(tmp_path):
    """A producer failure must NOT abort the beat (verify then fail-closes the swap
    safely with no probe.json — no worse than drill-inject-off)."""
    seams = _Seams(verify_ok=False, produce_raises=True)
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))
    arm.beat(_obs(ctx_pct=0.74))                 # produce raises internally
    # beat completed, no propagation; hydrate still ran; state still PREWARMING
    assert "hydrate" in [c[0] for c in seams.calls]
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "PREWARMING"


def test_prewarm_hydrate_uses_persisted_baseline_not_zero(tmp_path):
    """since_seq flips 0 -> None: hydrate uses the PERSISTED baseline (correct
    delta-hydration + enables the mid-life gate). bg_arm passes since_seq=None."""
    seams = _Seams(verify_ok=False)
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))
    arm.beat(_obs(ctx_pct=0.74))
    hyd = [c for c in seams.calls if c[0] == "hydrate"][0]
    assert hyd[3] is None            # since_seq=None (persisted), not 0


# ---- swap ----

def test_swap_fires_at_080_when_ready(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))   # PREWARMING
    arm.beat(_obs(ctx_pct=0.75))   # READY
    arm.beat(_obs(ctx_pct=0.83))   # SWAP
    assert "swap" in seams._kinds()
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "DRAINED"


def test_death_swaps_immediately_even_if_unverified(tmp_path):
    seams = _Seams(verify_ok=False)   # Green never verifies
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))                 # PREWARMING (spawn, no verify yet)
    arm.beat(_obs(ctx_pct=0.10, death="court"))  # death dominates -> swap NOW
    assert "swap" in seams._kinds()              # swapped despite no READY


def test_swap_timeout_sets_degraded_not_drained(tmp_path):
    seams = _Seams(swap_status="swap-timeout")
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))
    arm.beat(_obs(ctx_pct=0.75))
    arm.beat(_obs(ctx_pct=0.83))   # swap -> timeout (#11)
    st = BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"]
    assert st == "DEGRADED"        # half-swap routes to DEGRADED, not DRAINED


def test_noop_below_prewarm_stays_solo(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.40))
    assert seams.calls == []
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "SOLO"


# ---- F3 read-your-writes: project_now BETWEEN register and spawn ----

def test_prewarm_projects_between_register_and_spawn(tmp_path):
    """F3 (G7 live catch): after register-provisional-to-DB the arm must
    synchronously reproject BEFORE spawn, so spawn-agent reads a fresh registry,
    not a stale one inside the projector debounce window (U16 refusal race)."""
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))
    kinds = [c[0] for c in seams.calls]
    assert kinds.index("register_provisional") < kinds.index("project_now") \
        < kinds.index("spawn")


def test_prewarm_aborts_when_project_now_raises(tmp_path):
    """Fail-closed: a projection failure ABORTS prewarm — no spawn into an
    unprojected state, no PREWARMING transition (stays SOLO to retry)."""
    seams = _Seams(project_now_raises=True)
    arm = _arm(tmp_path, seams)
    with pytest.raises(RuntimeError):
        arm.beat(_obs(ctx_pct=0.72))
    kinds = [c[0] for c in seams.calls]
    assert "spawn" not in kinds                 # never spawned
    assert BgStateStore(str(tmp_path), "ios-watch-dev").read()["state"] == "SOLO"


def test_death_cold_spawn_also_projects_before_spawn(tmp_path):
    """The death cold-spawn path has the same register->spawn race; project_now
    must guard it too."""
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.05, death="oom"))
    kinds = [c[0] for c in seams.calls]
    assert kinds.index("register_provisional") < kinds.index("project_now") \
        < kinds.index("spawn")
