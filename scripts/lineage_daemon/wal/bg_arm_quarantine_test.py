"""RED (BG leg-(ii) M2 — arm/lift quarantine callers in bg_arm).

bg_quarantine.arm_quarantine/lift_quarantine are fully built + tested but have ZERO
callers (like a fuse with no wiring). M2 wires them into the orchestrator: ARM the
green at its PREWARM spawn (so an autonomous green boots write-contained), LIFT at
swap-wake (the green is now canonical and may mutate). In Target B this is INERT by
construction: the marker only has ENFORCEMENT once the PreToolUse hook is registered
in ~/.claude/settings.json, which stays an A-time gm+the operator gesture (NOT wired here).

Per the leg-(i) fake-only lesson, each decisive test drives the REAL BgArm through a
real state transition and asserts the REAL on-disk marker effect (via the real
bg_quarantine), never a mock of the seam.
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal import bg_quarantine  # noqa: E402

ROOT = "ios-watch-dev"
GREEN_ALIAS = "ios-watch-dev-g7"   # {root}-g{green.generation}


class _Seams:
    """Pure-fake INJECTED external effects (spawn/verify/hydrate/swap/reap/produce);
    the object under test — BgArm + the real bg_quarantine marker — is NOT faked."""
    def __init__(self, verify_ok=True, swap_status="complete"):
        self.calls = []
        self._verify_ok = verify_ok
        self._swap_status = swap_status

    def spawn(self, root, green_alias):
        self.calls.append(("spawn", root, green_alias))
        return {"alias": green_alias, "pid": 4242, "detached": True}

    def register_provisional(self, root, green_alias, generation=None, model=None):
        self.calls.append(("register_provisional", root, green_alias, generation))

    def project_now(self, root):
        self.calls.append(("project_now", root))

    def verify(self, root, green_alias):
        self.calls.append(("verify", root, green_alias))
        return self._verify_ok

    def hydrate(self, root, green_alias, since_seq):
        self.calls.append(("hydrate", root, green_alias, since_seq))

    def produce(self, root, green_alias):
        self.calls.append(("produce", root, green_alias))

    def swap(self, root, green, blue_generation_id):
        self.calls.append(("swap", root, green, blue_generation_id))
        return type("O", (), {"status": self._swap_status, "swap_id": 1,
                              "green_generation_id": 7})()

    def reap(self, root, blue):
        self.calls.append(("reap", root, blue))


def _arm(tmp_path, seams, armed=True):
    wal = str(tmp_path)
    if armed:
        (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(wal, ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True)


def _obs(ctx_pct=0.0, death=None):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": ctx_pct, "death": death,
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def test_prewarm_spawn_arms_the_green(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))   # SOLO -> PREWARMING: spawns the green
    assert ("spawn", ROOT, GREEN_ALIAS) in seams.calls
    # REAL effect: the green is quarantine-armed on disk
    assert bg_quarantine.is_quarantined(str(tmp_path), GREEN_ALIAS), \
        "M2: the green must be quarantine-armed at its PREWARM spawn"


def test_swap_wake_lifts_the_quarantine(tmp_path):
    seams = _Seams(swap_status="complete")
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))   # arm at prewarm
    assert bg_quarantine.is_quarantined(str(tmp_path), GREEN_ALIAS)
    arm.beat(_obs(ctx_pct=0.75))   # verify -> READY (P0.5: ctx-swap now requires READY)
    arm.beat(_obs(ctx_pct=0.85))   # ctx>=0.80 + READY -> swap; on complete -> lift
    assert ("swap", ROOT, _obs(0.85)["green"], 6) in seams.calls
    assert not bg_quarantine.is_quarantined(str(tmp_path), GREEN_ALIAS), \
        "M2: the quarantine must be LIFTED at swap-wake (green is now canonical)"


def test_disarmed_writes_no_marker(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams, armed=False)   # not bg_enabled
    arm.beat(_obs(ctx_pct=0.85))
    assert seams.calls == []                    # INERT: no spawn/swap
    # and no quarantine marker/dir materialized for a disarmed lineage
    assert not bg_quarantine.is_quarantined(str(tmp_path), GREEN_ALIAS)


def test_arm_failure_never_aborts_the_beat(tmp_path, monkeypatch):
    """FAIL-SAFE: a quarantine hiccup must NOT abort the spawn/beat (containment is
    a safety add-on, never a swap blocker). The beat still advances to PREWARMING."""
    seams = _Seams()
    arm = _arm(tmp_path, seams)

    def boom(wal_dir, alias):
        raise OSError("disk full arming quarantine")
    monkeypatch.setattr(bg_quarantine, "arm_quarantine", boom)

    arm.beat(_obs(ctx_pct=0.72))   # must not raise
    from lineage_daemon.wal.bg_state import BgStateStore
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "PREWARMING"


def test_swap_timeout_keeps_the_quarantine(tmp_path):
    """A DEGRADED (half-swapped) green is NOT canonical — it must STAY quarantined.
    LIFT fires ONLY on a committed (complete) swap; a swap-timeout leaves the marker."""
    seams = _Seams(swap_status="swap-timeout")
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))   # arm at prewarm
    assert bg_quarantine.is_quarantined(str(tmp_path), GREEN_ALIAS)
    arm.beat(_obs(ctx_pct=0.75))   # verify -> READY (P0.5: ctx-swap now requires READY)
    arm.beat(_obs(ctx_pct=0.85))   # swap fires but returns swap-timeout -> DEGRADED
    from lineage_daemon.wal.bg_state import BgStateStore
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DEGRADED"
    assert ("reap", ROOT, 6) not in seams.calls          # a timed-out swap does NOT reap
    assert bg_quarantine.is_quarantined(str(tmp_path), GREEN_ALIAS), \
        "M2: a DEGRADED (uncommitted) green must STAY quarantined — never lift on non-complete"


def test_lift_failure_never_aborts_the_swap(tmp_path, monkeypatch):
    """Symmetric fail-safe: a LIFT hiccup at swap-wake must NOT block the swap/reap.
    The swap still completes and reaps the blue."""
    seams = _Seams(swap_status="complete")
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))   # arm
    arm.beat(_obs(ctx_pct=0.75))   # verify -> READY (P0.5: ctx-swap now requires READY)

    def boom(wal_dir, alias):
        raise OSError("disk full lifting quarantine")
    monkeypatch.setattr(bg_quarantine, "lift_quarantine", boom)

    arm.beat(_obs(ctx_pct=0.85))   # swap-complete: lift raises but must be swallowed
    from lineage_daemon.wal.bg_state import BgStateStore
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"
    assert ("reap", ROOT, 6) in seams.calls              # swap completed + reaped despite lift error
