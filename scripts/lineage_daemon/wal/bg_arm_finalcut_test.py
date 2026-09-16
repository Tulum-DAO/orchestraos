"""RED (BG leg-(ii) M1b — final-cut hydrate at the swap boundary).

_do_swap calls seams.swap with NO final hydrate, so a WAL event Blue appends AFTER the
last prewarm/verify beat is never shipped to Green — it is LOST when Blue is reaped
(up-to-one-beat-stale = a lossless-handoff violation). M1b adds ONE final delta hydrate
immediately before the swap (the lossless boundary / final-cut-H) and captures H
(through_seq) for the promote record.

The decisive test drives the REAL BgArm through the REAL state machine (prewarm -> READY
-> swap) with a REAL hydrate_green seam against a REAL WalStore, appends an event AFTER
the last prewarm beat, and asserts the final-cut hydrate DELIVERED it by effect (leg-(i)
lesson: exercise the live path + assert the effect).

RED until _do_swap performs the final-cut hydrate.
"""
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal.bg_arm import BgArm  # noqa: E402
from lineage_daemon.wal import hydrate_green  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402
from lineage_daemon.wal.bg_state import BgStateStore  # noqa: E402

ROOT = "ios-watch-dev"
GREEN_ALIAS = "ios-watch-dev-g7"


def _append(store, ts, summary):
    store.append(ts=ts, lineage_root=ROOT, generation=2, sid="b",
                 runtime="claude", kind="response", summary=summary, source_path="x")


class _RealHydrateSeams:
    """Injected seams: hydrate is the REAL hydrate_green vs a real WalStore (delivery
    captured in `sink`); the other effects record. spawn/verify drive the state
    machine to READY; swap returns complete."""
    def __init__(self, wal_dir, store, sink):
        self.calls = []
        self._wal_dir = wal_dir
        self._store = store
        self.sink = sink

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
        return hydrate_green.hydrate_green(
            root, green_alias, since_seq=since_seq, wal_dir=self._wal_dir,
            store=self._store,
            deliver_fn=lambda r, a, t, iv=None, cap=None: self.sink.append((t, iv)))

    def swap(self, root, green, blue_generation_id):
        self.calls.append("swap")
        return type("O", (), {"status": "complete", "swap_id": 1, "green_generation_id": 7})()

    def reap(self, root, blue):
        self.calls.append("reap")


def _arm(tmp_path, seams):
    (tmp_path / f"{ROOT}.bg_enabled").write_text("")
    # M1b-h: these tests drive a COMPLETE non-death swap, which now VERIFIES the green
    # ingested through the final cut H. They exercise the M1b final-cut DELIVERY (not the
    # M1b-h verify gate), so inject an always-ingested reader; the gate is covered
    # decisively (real reader over a real tasks.db) in bg_arm_m1bh_test.py.
    return BgArm(str(tmp_path), ROOT, seams=seams, blue_wal_event_count_fn=lambda: 1, cutover_active=lambda: True,
                 green_ingested_seq_fn=lambda *a: 10**9)


def _obs(ctx_pct):
    return {"root": ROOT, "runtime": "claude", "ctx_pct": ctx_pct, "death": None,
            "ceiling_calibrated": True, "blue_generation_id": 6,
            "green": {"generation": 7, "model": "claude-opus-4-8[1m]"}}


def test_final_cut_ships_post_prewarm_delta_by_effect(tmp_path):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    for i in range(3):
        _append(store, 1000 + i, f"prewarm-e{i}")   # blue's work before prewarm
    sink = []
    seams = _RealHydrateSeams(str(tmp_path), store, sink)
    arm = _arm(tmp_path, seams)

    arm.beat(_obs(0.72))   # SOLO -> PREWARMING (+ hydrate ships e0..e2)
    arm.beat(_obs(0.72))   # PREWARMING -> verify READY (+ hydrate: nothing new)
    # Blue does MORE work AFTER the last prewarm beat — the event M1b must not lose:
    _append(store, 2000, "post-prewarm-CRITICAL")
    sink.clear()
    arm.beat(_obs(0.85))   # ctx>=0.80 -> swap: the FINAL-CUT hydrate must ship it

    delivered_text = " ".join(t for t, _iv in sink)
    assert "post-prewarm-CRITICAL" in delivered_text, \
        "M1b: the final-cut hydrate must ship the post-prewarm WAL delta before the swap"
    # H captured: the final-cut seq == the WAL head (the critical event's seq)
    assert BgStateStore(str(tmp_path), ROOT).read_meta("final_cut_seq") == store.max_seq()


class _OrderSeams(_RealHydrateSeams):
    """hydrate records only (returns a minimal dict) so we can assert ordering
    without a WAL — proves _do_swap hydrates exactly once, immediately before swap."""
    def hydrate(self, root, green_alias, since_seq):
        self.calls.append("hydrate")
        return {"new_since_seq": 5}


def test_do_swap_hydrates_exactly_once_immediately_before_swap(tmp_path):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    seams = _OrderSeams(str(tmp_path), store, [])
    arm = _arm(tmp_path, seams)
    arm.beat(_obs(0.72))   # prewarm (hydrate #1)
    arm.beat(_obs(0.72))   # verify READY (hydrate #2)
    seams.calls.clear()
    arm.beat(_obs(0.85))   # swap
    assert seams.calls.count("swap") == 1
    i_swap = seams.calls.index("swap")
    assert seams.calls[i_swap - 1] == "hydrate", \
        "M1b: the final-cut hydrate must be the LAST act before the swap"
    assert seams.calls.count("hydrate") == 1   # exactly one final-cut hydrate this beat


class _RaiseHydrateSeams(_OrderSeams):
    """The final-cut hydrate RAISES — proves the guard: a hydrate hiccup must NEVER
    abort the swap (death-swaps cannot wait; worst case is one-beat staleness)."""
    def hydrate(self, root, green_alias, since_seq):
        self.calls.append("hydrate")
        raise OSError("final-cut hydrate delivery failed")


def test_final_cut_hydrate_raise_never_aborts_the_swap(tmp_path):
    # gm merge condition (decisive-safety-untested = leg-(i) fake-only lesson): drive
    # _final_cut_hydrate to RAISE and assert _do_swap STILL swaps + completes.
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    seams = _RaiseHydrateSeams(str(tmp_path), store, [])
    arm = _arm(tmp_path, seams)
    arm._green_alias = GREEN_ALIAS                       # set by beat() live; fix it here
    BgStateStore(str(tmp_path), ROOT).write_state("READY", reason="test-setup")

    arm._do_swap(_obs(0.85), {"reason": "ctx:swap"})     # must NOT raise

    assert "swap" in seams.calls, \
        "M1b: a RAISING final-cut hydrate must NOT abort the swap"
    assert seams.calls.index("hydrate") < seams.calls.index("swap")   # tried the cut, then swapped
    assert BgStateStore(str(tmp_path), ROOT).read()["state"] == "DRAINED"   # swap completed
