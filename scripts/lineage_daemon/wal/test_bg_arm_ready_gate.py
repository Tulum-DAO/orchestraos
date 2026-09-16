"""RED (BG leg-(ii) P0.5 — READY-gate on the ctx-swap).

Today bg_arm.beat routes action=="swap" straight to _do_swap the instant ctx>=0.80,
with NO check that the Green is verified-READY — so a ctx-swap fires into an UNVERIFIED
(or dead) Green, promoting a successor that never proved it ingested Blue's state (data
loss / dead-green promotion). P0.5: a CTX-triggered swap requires state=="READY"; if not
READY, DEFER (do a prewarm/verify beat instead) so the NEXT beat can swap once verified.
DEATH stays UNGATED forever (never_gated) — a dying seat is rescued regardless of READY.

Drives the REAL BgArm to a state transition (leg-(i) fake-only lesson): with verify_ok=False
the Green never reaches READY, so repeated ctx>=0.80 beats must NEVER swap.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402

ROOT = "ios-watch-dev"


class _Seams:
    def __init__(self, verify_ok=True):
        self.calls = []
        self._verify_ok = verify_ok

    def spawn(self, root, green_alias):
        self.calls.append("spawn")
        return {"alias": green_alias, "pid": 4242, "detached": True}

    def register_provisional(self, root, green_alias, generation=None, model=None):
        self.calls.append("register_provisional")

    def project_now(self, root):
        self.calls.append("project_now")

    def verify(self, root, green_alias):
        self.calls.append("verify")
        return self._verify_ok

    def hydrate(self, root, green_alias, since_seq):
        self.calls.append("hydrate")

    def produce(self, root, green_alias):
        self.calls.append("produce")

    def swap(self, root, green, blue_generation_id):
        self.calls.append("swap")
        return type("O", (), {"status": "complete"})()

    def reap(self, root, blue):
        self.calls.append("reap")


def _arm(tmp_path, seams):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True,
                 blue_pane_pid_fn=lambda root: 4242)


def _obs(ctx_pct=0.0, death=None):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": ctx_pct, "death": death,
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def _state(tmp_path):
    return BgStateStore(str(tmp_path), ROOT).read()["state"]


# ── the decisive gate: a ctx-swap NEVER fires while the Green is not READY ──────

def test_ctx_swap_deferred_while_green_not_ready(tmp_path):
    seams = _Seams(verify_ok=False)          # Green NEVER verifies -> never READY
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))             # SOLO -> PREWARMING (spawn)
    arm.beat(_obs(ctx_pct=0.83))             # ctx>=SWAP but NOT READY -> must DEFER
    arm.beat(_obs(ctx_pct=0.90))             # still not READY -> still DEFER
    assert "swap" not in seams.calls, \
        "P0.5: a ctx-swap must NOT fire into an unverified (not-READY) Green"
    assert _state(tmp_path) != "DRAINED"


def test_ctx_swap_from_solo_at_high_ctx_prewarms_first(tmp_path):
    # a late start already above SWAP: must prewarm+verify to READY BEFORE swapping,
    # never swap straight out of SOLO on ctx.
    seams = _Seams(verify_ok=True)
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.85))             # SOLO + ctx>=SWAP -> prewarm (spawn), NOT swap
    assert "spawn" in seams.calls and "swap" not in seams.calls
    assert _state(tmp_path) == "PREWARMING"


def test_ctx_swap_fires_once_ready(tmp_path):
    seams = _Seams(verify_ok=True)
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))             # PREWARMING
    arm.beat(_obs(ctx_pct=0.75))             # verify -> READY
    assert _state(tmp_path) == "READY"
    arm.beat(_obs(ctx_pct=0.83))             # READY + ctx>=SWAP -> SWAP
    assert "swap" in seams.calls
    assert _state(tmp_path) == "DRAINED"


# ── death stays UNGATED — swaps from a non-READY state regardless ──────────────

def test_death_swaps_from_non_ready_state(tmp_path):
    seams = _Seams(verify_ok=False)          # never READY
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(ctx_pct=0.72))             # PREWARMING
    arm.beat(_obs(ctx_pct=0.05, death="oom"))  # death dominates -> swap NOW despite not READY
    assert "swap" in seams.calls, "death must never be READY-gated"
