"""RED (BG leg-(ii) M1b-h — VERIFIED_AT(H) gate on the ctx-swap NON-death path).

M1b already ships the final-cut delta (durable delivery) + records H (final_cut_seq)
before the swap. M1b-h is the belt-and-suspenders SUSPENDERS: on the NON-death ctx-swap,
do NOT promote a green unless it has PROVABLY ingested through the final cut H — read the
green's ACTUAL ingested through_seq from its ACTUALLY-DELIVERED lineage_hydrate rows (the
REAL green_boot_probe.collect_ingested_artifact over a REAL tasks.db) and require >= H.

gm gate-criteria rulings (DC — gm verifies-by-effect + casts):
  (1) FAIL-CLOSED-DEFER: if the green has NOT provably ingested H, do NOT swap AND stamp
      the m1bh_verify_pending=H meta alarm (BOTH — the non-death swap has no urgency, so
      deferring until through_seq>=H is lossless; alarm-only would risk a lossy promote).
  (2) REVERT-TO-READY + re-drive next beat (no mid-swap limbo); a never-converging loop
      surfaces via the alarm, never silently spins.
  DEATH (never_gated) skips the gate entirely (durable capsule + record-H sufficient).

Per the leg-(i) fake-only lesson [[reference_bg_realseam_fakeonly_gaps]] the decisive test
drives the REAL BgArm + REAL BgStateStore to a REAL state transition (READY<->DRAINED) with
the REAL collect_ingested_artifact reader over a REAL tasks.db — never a marker on a mock.

RED until _do_swap gates the non-death swap on the green's ingested through_seq >= H.
"""
import os
import sys

sys.path.insert(0, "scripts")
sys.path.insert(0, ".")
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402
from lineage_daemon.wal.green_boot_probe import collect_ingested_artifact  # noqa: E402

ROOT = "ios-watch-dev"
GREEN_ALIAS = "ios-watch-dev-g7"   # {root}-g{green.generation}


def _ensure_msg_db(db):
    """Bootstrap the messages/conversations schema in a fresh test tasks.db (mirrors
    green_boot_probe_test._ensure_msg_db — MessageStore does not self-create tables)."""
    import sqlite3
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT,
          task_id TEXT, parent_id TEXT, type TEXT, from_agent TEXT, to_agent TEXT,
          subject TEXT, body TEXT, priority TEXT DEFAULT 'medium', source TEXT DEFAULT 'system',
          status TEXT DEFAULT 'pending', retry_count INTEGER DEFAULT 0, max_retries INTEGER DEFAULT 5,
          metadata TEXT, created_at TEXT, attempted_at TEXT, delivered_at TEXT,
          acknowledged_at TEXT, archived_at TEXT, error TEXT, depends_on TEXT,
          gather_mode TEXT DEFAULT 'gather_all', tenant_id TEXT DEFAULT 'operator');
        CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, subject TEXT,
          participants TEXT, task_id TEXT, created_at TEXT, updated_at TEXT,
          tenant_id TEXT DEFAULT 'operator');
    """)
    conn.close()


def _seed_ingested_row(orchestra_dir, alias, through_seq, since_seq=0):
    """Write a REAL lineage_hydrate row (real MessageStore, real tasks.db) carrying an
    ingested_view scoped to through_seq — the exact shape the REAL green-boot delivery
    lands, so the REAL collect_ingested_artifact reader returns a REAL through_seq."""
    from msg_store import MessageStore
    db = os.path.join(orchestra_dir, "state", "tasks.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    _ensure_msg_db(db)
    MessageStore(db_path=db).send(
        from_agent="lineage-daemon", to_agent=alias, type="lineage_hydrate",
        priority="high", subject="[WAL DELTA] seed", body="x", source="bg-hydrate",
        metadata={"ingested_view": {
            "lineage_root": ROOT,
            "scope": {"since_seq": since_seq, "through_seq": through_seq},
            "events": [{"seq": through_seq, "kind": "response",
                        "summary": "s", "body_ref": None}]}})


def _real_reader(orchestra_dir):
    """The PRODUCTION reader closure: the green's ingested through_seq from its
    ACTUALLY-DELIVERED rows (raises if none delivered -> bg_arm guards -> None)."""
    def read(alias):
        return collect_ingested_artifact(orchestra_dir, alias)[2].get("through_seq")
    return read


class _Seams:
    """Injected effect seams. hydrate optionally DELIVERS a real ingested_view row (so
    the real reader reads it) and returns the recorded H; verify drives to READY."""
    def __init__(self, orchestra_dir, *, final_h, deliver_through=None,
                 hydrate_raises=False):
        self.calls = []
        self._orchestra_dir = orchestra_dir
        self._final_h = final_h
        self._deliver_through = deliver_through
        self._hydrate_raises = hydrate_raises

    def spawn(self, root, green_alias):
        self.calls.append("spawn")
        return {"alias": green_alias, "pid": 4242, "detached": True}

    def register_provisional(self, root, green_alias, generation=None, model=None):
        self.calls.append("register_provisional")

    def project_now(self, root):
        self.calls.append("project_now")

    def verify(self, root, green_alias):
        self.calls.append("verify")
        return True

    def produce(self, root, green_alias):
        self.calls.append("produce")

    def hydrate(self, root, green_alias, since_seq):
        self.calls.append("hydrate")
        if self._hydrate_raises:
            raise OSError("final-cut hydrate delivery failed")
        if self._deliver_through is not None:
            _seed_ingested_row(self._orchestra_dir, green_alias, self._deliver_through)
        return {"new_since_seq": self._final_h}

    def swap(self, root, green, blue_generation_id):
        self.calls.append("swap")
        return type("O", (), {"status": "complete", "swap_id": 1,
                              "green_generation_id": 7})()

    def reap(self, root, blue):
        self.calls.append("reap")


def _arm(tmp_path, seams, *, reader):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    return BgArm(str(tmp_path), ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True,
                 green_ingested_seq_fn=reader)


def _obs(ctx_pct, death=None):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": ctx_pct, "death": death,
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def _drive_to_swap(tmp_path, seams):
    arm = _arm(tmp_path, seams, reader=_real_reader(str(tmp_path)))
    arm.beat(_obs(0.72))   # SOLO -> PREWARMING
    arm.beat(_obs(0.74))   # PREWARMING -> verify READY
    arm.beat(_obs(0.85))   # ctx>=0.80 + READY -> swap-or-defer
    return arm


# ---- (a) provably ingested through H -> swaps -------------------------------

def test_swaps_when_green_ingested_through_h(tmp_path):
    # final cut H=5; the delivery lands a real ingested_view row through_seq=5 that the
    # REAL reader reads -> ingested(5) >= H(5) -> the swap fires and completes.
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=5)
    _drive_to_swap(tmp_path, seams)
    assert "swap" in seams.calls, "M1b-h: a green provably ingested >= H must swap"
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"


# ---- (b) ingested < H -> fail-closed defer (revert READY + alarm) ------------

def test_defers_when_green_ingested_below_h(tmp_path):
    # H=5 but the delivered ingested_view only reaches through_seq=3 (lossy channel):
    # the REAL reader returns 3 < 5 -> DEFER: no swap, revert to READY, stamp alarm.
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=3)
    _drive_to_swap(tmp_path, seams)
    assert "swap" not in seams.calls, \
        "M1b-h: a green that has NOT ingested through H must NOT be promoted"
    st = BgStateStore(str(tmp_path), ROOT)
    assert st.read()["state"] == "READY", "gm ruling (2): revert-to-READY, re-drive"
    assert st.read_meta("m1bh_verify_pending") == 5, \
        "gm ruling (1): stamp the m1bh_verify_pending=H alarm on defer"


# ---- (c) reader absent / no delivered row -> fail-closed defer ---------------

def test_defers_when_ingested_unreadable(tmp_path):
    # No lineage_hydrate row was delivered -> collect_ingested_artifact raises ->
    # bg_arm treats it as NOT-verified (None) -> fail-closed defer (never a blind swap).
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=None)
    _drive_to_swap(tmp_path, seams)
    assert "swap" not in seams.calls, \
        "M1b-h: an UNREADABLE ingested through_seq must fail-closed (defer), never swap"
    st = BgStateStore(str(tmp_path), ROOT)
    assert st.read()["state"] == "READY"
    assert st.read_meta("m1bh_verify_pending") == 5


# ---- (d) death is NEVER gated ------------------------------------------------

def test_death_swaps_even_when_ingested_below_h(tmp_path):
    # A dying blue is rescued regardless: never_gated skips the verify gate entirely,
    # so a death-swap fires even though the green has NOT ingested through H.
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=3)
    arm = _arm(tmp_path, seams, reader=_real_reader(str(tmp_path)))
    arm.beat(_obs(0.72))                       # PREWARMING (spawn)
    arm.beat(_obs(0.10, death="court"))        # death dominates -> swap NOW
    assert "swap" in seams.calls, "death must never be M1b-h-gated"


# ---- (e) H unknown (final-cut hydrate raised) -> gate SKIPPED (M1b fallback) --

def test_gate_skipped_when_no_boundary_recorded(tmp_path):
    # If the final-cut hydrate raises, no H boundary is recorded (h=None). With no
    # boundary the suspenders cannot engage, so M1b's guarantee holds: the swap still
    # fires (worst case one-beat staleness) rather than dead-locking. Reader present +
    # low (would-defer) to prove the SKIP is due to h=None, not a passing verify.
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=3, hydrate_raises=True)
    _seed_ingested_row(str(tmp_path), GREEN_ALIAS, 3)   # reader would say 3 (< any H)
    arm = _arm(tmp_path, seams, reader=_real_reader(str(tmp_path)))
    arm._green_alias = GREEN_ALIAS
    BgStateStore(str(tmp_path), ROOT).write_state("READY", reason="test-setup")
    arm._do_swap(_obs(0.85), {"reason": "ctx:swap"})    # non-death, hydrate raises
    assert "swap" in seams.calls, \
        "M1b-h: with NO boundary H recorded, fall back to M1b (swap), never deadlock"
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"


# ---- (f) arm-readiness breadcrumb: h=None gate-skip must be LOUD, not silent ----

def test_h_none_gate_skip_stamps_arm_readiness_breadcrumb(tmp_path):
    # gm ARM-READINESS: a NON-death swap that falls back to M1b because NO boundary H
    # was recorded (final-cut hydrate raised/no-op'd) must stamp a durable
    # m1bh_skipped_no_boundary breadcrumb so a PERSISTENT hydrate-raise-then-unverified-
    # promote is LOUD (fleet 'unknown must surface'), never a silent unverified promote.
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=3, hydrate_raises=True)
    _seed_ingested_row(str(tmp_path), GREEN_ALIAS, 3)
    arm = _arm(tmp_path, seams, reader=_real_reader(str(tmp_path)))
    arm._green_alias = GREEN_ALIAS
    BgStateStore(str(tmp_path), ROOT).write_state("READY", reason="test-setup")
    arm._do_swap(_obs(0.85), {"reason": "ctx:swap"})
    assert "swap" in seams.calls                       # still the M1b fallback swap
    assert BgStateStore(str(tmp_path), ROOT).read_meta("m1bh_skipped_no_boundary") is True


def test_verified_swap_does_not_stamp_breadcrumb(tmp_path):
    # the breadcrumb is SPECIFIC to the no-boundary fallback: a normal VERIFIED swap
    # (h known, ingested>=H) must NOT stamp it.
    seams = _Seams(str(tmp_path), final_h=5, deliver_through=5)
    _drive_to_swap(tmp_path, seams)
    assert "swap" in seams.calls
    assert BgStateStore(str(tmp_path), ROOT).read_meta("m1bh_skipped_no_boundary") is None
