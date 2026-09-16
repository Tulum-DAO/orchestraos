"""RED (BG arm-readiness leg (i) — suppress the autonomous swap on live!=canonical).

gm ruling (baton §1i): when build_obs's `blue_sid_live_mismatch` is true, blue's LIVE pane
occupant sid != the canonical seat sid => a FRESH successor already took blue's seat
out-of-band. ANY swap now (ctx OR death) would promote our prewarmed green OVER that live
successor = a clobber (the promote-races-auto-respawner class). Safe-direction: a MISSED
swap is harmless, a WRONG swap clobbers a live seat. So decide_bg SUPPRESSES the swap
(action=noop, reason=suppress:live-sid-mismatch) and bg_arm records the skip (durable
breadcrumb). Adopts blocker_surface_watchdog's R2 skip-on-mismatch (which does NOT exempt
death — a dying blue whose seat is already succeeded has nothing left to rescue here).

Per the leg-(i) lesson [[reference_bg_realseam_fakeonly_gaps]] the by-effect tests drive the
REAL BgArm + REAL BgStateStore and assert the REAL swap seam is NOT called. RED until
decide_bg gates on the flag + bg_arm records the skip.

INERT: state/wal/BG_DISABLED gates the whole path; this only changes the DECISION, never arms.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.decide_bg import decide_bg  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "ios-watch-dev"


def _obs(ctx_pct=0.0, death=None, mismatch=False):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": ctx_pct, "death": death,
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"},
            "blue_sid_used": "live-occupant-sid", "blue_sid_live_mismatch": mismatch}


# ---- decide_bg (pure) ---------------------------------------------------------

def test_decide_suppresses_ctx_swap_on_mismatch():
    d = decide_bg(_obs(ctx_pct=0.85, mismatch=True))
    assert d["action"] == "noop", "a live-sid mismatch must suppress the ctx swap"
    assert d["reason"] == "suppress:live-sid-mismatch"


def test_decide_suppresses_death_swap_on_mismatch():
    d = decide_bg(_obs(ctx_pct=0.10, death="oom", mismatch=True))
    assert d["action"] == "noop", \
        "a mismatch must suppress even a DEATH swap — the seat is already succeeded"
    assert d["reason"] == "suppress:live-sid-mismatch"


def test_decide_swaps_ctx_when_no_mismatch():
    assert decide_bg(_obs(ctx_pct=0.85, mismatch=False))["action"] == "swap"


def test_decide_death_swaps_when_no_mismatch():
    assert decide_bg(_obs(ctx_pct=0.10, death="oom", mismatch=False))["action"] == "swap"


def test_decide_absent_flag_swaps_no_regression():
    o = _obs(ctx_pct=0.85)
    del o["blue_sid_live_mismatch"]              # legacy obs w/o the flag
    assert decide_bg(o)["action"] == "swap"


# ---- bg_arm.beat (real-object, by-effect) -------------------------------------

class _Seams:
    def __init__(self, verify_ok=True):
        self.calls = []
        self._verify_ok = verify_ok

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
        return type("O", (), {"status": "complete", "swap_id": 1,
                              "green_generation_id": 7})()

    def reap(self, root, blue):
        self.calls.append(("reap", root, blue))

    def _kinds(self):
        return [c[0] for c in self.calls]


def _arm(tmp_path, seams):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True)


def test_beat_suppresses_ctx_swap_on_mismatch(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))                     # SOLO -> PREWARMING
    arm.beat(_obs(ctx_pct=0.75))                     # -> READY
    arm.beat(_obs(ctx_pct=0.85, mismatch=True))      # ctx>=0.80 BUT mismatch -> NO swap
    assert "swap" not in seams._kinds(), \
        "a live-sid mismatch must suppress the ctx swap (fresh successor holds the seat)"
    # the skip is LOGGED (durable breadcrumb), never silent
    assert BgStateStore(str(tmp_path), ROOT).read_meta("swap_suppressed_live_mismatch") \
        is not None


def test_beat_suppresses_death_swap_on_mismatch(tmp_path):
    seams = _Seams(verify_ok=False)                  # green never verifies
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))                     # PREWARMING (spawn)
    arm.beat(_obs(ctx_pct=0.10, death="court", mismatch=True))  # death + mismatch
    assert "swap" not in seams._kinds(), \
        "a mismatch must suppress even a death swap — nothing to rescue into a taken seat"


def test_beat_swaps_when_no_mismatch(tmp_path):
    seams = _Seams()
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))
    arm.beat(_obs(ctx_pct=0.75))
    arm.beat(_obs(ctx_pct=0.85, mismatch=False))     # no mismatch -> swaps (no regression)
    assert "swap" in seams._kinds()
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"
