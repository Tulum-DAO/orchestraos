"""RED-first tests for bg_complete.py — the completion phase so REAP ACTUALLY FIRES
(v2-1, gm bar #5). A reaper that works but is never CALLED is bar #5 (headless/orphan)
failing silently.

The gap (verified by effect at build time): real_seams.make_swap_fn always returns
status='effects-incomplete' (identity committed, sync_effects_owner=False), so
bg_arm._do_swap never hits the status=='complete' branch and never calls reap — it
writes DEGRADED(effects-incomplete) and stops. bg_supervise_fleet only calls
arm.beat, never a completion pass. So reap is dead code without this.

bg_complete runs the async arm's OWN effect layer: for a seat left in
DEGRADED(effects-incomplete), it drives the reap as a BOUNDED effect (the same
_run_effects_bounded runner SwapExecutor uses -> v2-2 timeout for free), and on
success writes DRAINED. It runs INSIDE the beat's per-seat firewall (a completion
throw -> alarm/disarm, Claude-leg build note a) and is idempotent.
"""
import sys

import pytest

sys.path.insert(0, "scripts")
from lineage_daemon.wal import bg_complete  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402


class _EffectsIncompleteSeams:
    """Mirrors real_seams: swap ALWAYS returns effects-incomplete (identity
    committed, effects deferred to the async arm's completion layer)."""
    def __init__(self):
        self.calls = []
        self.reaped = []

    def register_provisional(self, root, alias, generation=None, model=None):
        self.calls.append(("reg", root, generation))
    def project_now(self, root): self.calls.append(("proj", root))
    def spawn(self, root, alias): self.calls.append(("spawn", root))
    def verify(self, root, alias): self.calls.append(("verify", root)); return True
    def hydrate(self, root, alias, since_seq): self.calls.append(("hyd", root))

    def swap(self, root, green, blue_gen_id):
        self.calls.append(("swap", root))
        return type("O", (), {"status": "effects-incomplete", "swap_id": 0,
                              "green_generation_id": 7})()

    def reap(self, root, blue):
        self.reaped.append((root, blue))


def _arm(tmp_path, seams, root="bg-drill-victim"):
    (tmp_path / f"{root}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), root, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True)


def _obs(root="bg-drill-victim", ctx=0.85, blue_gen_id=6):
    return {"root": root, "runtime": "claude", "ctx_pct": ctx, "death": None,
            "ceiling_calibrated": True, "blue_generation_id": blue_gen_id,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def test_swap_leaves_degraded_effects_incomplete_reap_not_yet_fired(tmp_path):
    """Baseline (the dead-code gap): a swap via effects-incomplete seams leaves
    DEGRADED and reap has NOT fired — proving the completion phase is needed."""
    seams = _EffectsIncompleteSeams()
    arm = _arm(tmp_path, seams)
    # prewarm -> ready -> swap
    arm.beat(_obs(ctx=0.72))
    arm.beat(_obs(ctx=0.74))
    arm.beat(_obs(ctx=0.85))
    st = BgStateStore(str(tmp_path), "bg-drill-victim").read()["state"]
    assert st == "DEGRADED"
    assert seams.reaped == []            # <-- reap did NOT fire (the gap)


def test_completion_phase_fires_reap_and_drains(tmp_path):
    """v2-1 / gm bar #5: the completion pass drives the reap (bounded effect) for a
    DEGRADED(effects-incomplete) seat and writes DRAINED."""
    seams = _EffectsIncompleteSeams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx=0.72)); arm.beat(_obs(ctx=0.74)); arm.beat(_obs(ctx=0.85))
    # persist the blue_generation_id the completion pass reaps (bg_arm records it)
    out = bg_complete.complete_swap(
        "bg-drill-victim", wal_dir=str(tmp_path), seams=seams,
        blue_generation_id=6)
    assert out["reaped"] is True
    assert seams.reaped == [("bg-drill-victim", 6)]
    assert BgStateStore(str(tmp_path), "bg-drill-victim").read()["state"] == "DRAINED"


def test_end_to_end_solo_to_drained_reap_fires(tmp_path):
    """THE gm bar #5 end-to-end proof: SOLO -> PREWARMING -> READY -> SWAPPING ->
    effects-incomplete -> completion-pass -> reap -> DRAINED, reap observed."""
    seams = _EffectsIncompleteSeams()
    arm = _arm(tmp_path, seams)
    store = BgStateStore(str(tmp_path), "bg-drill-victim")
    assert store.read()["state"] == "SOLO"
    arm.beat(_obs(ctx=0.72)); assert store.read()["state"] == "PREWARMING"
    arm.beat(_obs(ctx=0.74)); assert store.read()["state"] == "READY"
    arm.beat(_obs(ctx=0.85)); assert store.read()["state"] == "DEGRADED"
    assert seams.reaped == []
    bg_complete.complete_swap("bg-drill-victim", wal_dir=str(tmp_path),
                              seams=seams, blue_generation_id=6)
    assert store.read()["state"] == "DRAINED"
    assert seams.reaped == [("bg-drill-victim", 6)]   # reap ACTUALLY fired


def test_completion_idempotent_on_already_drained(tmp_path):
    """A re-run after DRAINED is a no-op (reap not fired twice)."""
    seams = _EffectsIncompleteSeams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx=0.72)); arm.beat(_obs(ctx=0.74)); arm.beat(_obs(ctx=0.85))
    bg_complete.complete_swap("bg-drill-victim", wal_dir=str(tmp_path),
                              seams=seams, blue_generation_id=6)
    seams.reaped.clear()
    out = bg_complete.complete_swap("bg-drill-victim", wal_dir=str(tmp_path),
                                    seams=seams, blue_generation_id=6)
    assert out["reaped"] is False        # already drained -> no second reap
    assert seams.reaped == []


def test_completion_noop_when_not_degraded(tmp_path):
    """A seat not in a resumable DEGRADED state -> completion is a clean no-op."""
    (tmp_path / "bg-drill-victim.bg_enabled").write_text("")
    BgStateStore(str(tmp_path), "bg-drill-victim").write_state("PREWARMING")
    seams = _EffectsIncompleteSeams()
    out = bg_complete.complete_swap("bg-drill-victim", wal_dir=str(tmp_path),
                                    seams=seams, blue_generation_id=6)
    assert out["reaped"] is False
    assert seams.reaped == []


def test_completion_reap_timeout_raises_bounded(tmp_path):
    """v2-2: the reap runs as a BOUNDED effect — a hanging reap converts to a raise
    (never wedges), so the beat firewall can alarm/disarm."""
    class _HangReap(_EffectsIncompleteSeams):
        def reap(self, root, blue):
            import time
            time.sleep(30)     # hang past the tight timeout

    seams = _HangReap()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx=0.72)); arm.beat(_obs(ctx=0.74)); arm.beat(_obs(ctx=0.85))
    with pytest.raises(bg_complete.CompletionTimeout):
        bg_complete.complete_swap("bg-drill-victim", wal_dir=str(tmp_path),
                                  seams=seams, blue_generation_id=6,
                                  timeout_s=0.5)
