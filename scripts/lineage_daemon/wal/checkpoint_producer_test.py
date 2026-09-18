"""RED (BG leg-(ii) M1a, Part B — the BUILD-CHECKPOINT producer).

The genuinely-new design (spec §3). Option (a): at the swap boundary the driver injects
Blue a bounded "author your BUILD-CHECKPOINT to state/agent-handoffs/{root}.build-checkpoint.json"
and bounded-waits for the file. Per gm's consensus condition it must be IDEMPOTENT +
bounded-wait with a HARD auto-fallback to (b) WAL-derived synthesis, so a NON-RESPONSIVE
Blue never stalls the swap.

Hermetic: inject + clock are dependency-injected (no live pane, no real sleep).

RED until scripts/lineage_daemon/wal/checkpoint_producer.py exists.
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import checkpoint_producer as cp  # noqa: E402
from lineage_daemon.wal import continue_capsule as _cc  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

ROOT = "second-brain-dev"


def _resolver(bodies):
    return lambda ref: bodies.get(ref, "")


def _prompt(store, seq_chars, body_ref, ts):
    store.append(ts=ts, lineage_root=ROOT, generation=2, sid="b", runtime="claude",
                 kind="prompt", summary=f"text ({seq_chars} chars)", body_ref=body_ref,
                 source_path="x")


def _seed_directive(tmp_path):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    _prompt(store, 40, "t.jsonl:real", ts=100)
    return store, _resolver({"t.jsonl:real": "Finish the M1a checkpoint producer and wire it"})


def _fake_clock():
    t = [0.0]
    def now():
        t[0] += 0.6
        return t[0]
    return now


def test_wal_derived_checkpoint_from_directive(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    out = cp.wal_derived_checkpoint(store, ROOT, resolve_body=resolver)
    assert out["source"] == "wal-derived"
    assert out["objective"] == "Finish the M1a checkpoint producer and wire it"


def test_wal_derived_none_when_degraded(tmp_path):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    store.append(ts=1, lineage_root=ROOT, generation=2, sid="b", runtime="claude",
                 kind="response", summary="no directive here", source_path="x")
    assert cp.wal_derived_checkpoint(store, ROOT) is None   # let hydrate degrade itself


def test_produce_uses_blue_authored_when_inject_writes_it(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    def inject_fn(root):
        # Blue authored its OWN (richer) checkpoint
        cp.write_build_checkpoint(str(tmp_path), root,
                                  {"objective": "Blue's explicit objective", "source": "blue"})
    out = cp.produce_checkpoint(ROOT, str(tmp_path), store, inject_fn=inject_fn,
                                resolve_body=resolver, now=_fake_clock(), sleep=lambda s: None)
    assert out["source"] == "blue"
    assert out["objective"] == "Blue's explicit objective"


def test_produce_hard_fallback_on_nonresponsive_blue(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    called = []
    def inject_fn(root):
        called.append(root)   # Blue is asked but NEVER writes the file (non-responsive)
    out = cp.produce_checkpoint(ROOT, str(tmp_path), store, inject_fn=inject_fn,
                                timeout_s=2.0, resolve_body=resolver,
                                now=_fake_clock(), sleep=lambda s: None)
    assert called == [ROOT], "Blue must be asked (option a) before the fallback"
    assert out["source"] == "wal-derived", "a non-responsive Blue HARD-falls-back to (b)"
    # and the fallback was durably written to the convention path
    path = _cc.build_checkpoint_path(str(tmp_path), ROOT)
    assert os.path.exists(path)
    assert json.load(open(path))["objective"] == "Finish the M1a checkpoint producer and wire it"


def test_produce_is_idempotent_reuses_existing(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    cp.write_build_checkpoint(str(tmp_path), ROOT,
                              {"objective": "already authored", "source": "blue"})
    called = []
    out = cp.produce_checkpoint(ROOT, str(tmp_path), store,
                                inject_fn=lambda r: called.append(r),
                                resolve_body=resolver, now=_fake_clock(), sleep=lambda s: None)
    assert called == [], "idempotent: an existing valid checkpoint must NOT re-inject Blue"
    assert out["objective"] == "already authored"


def test_produce_no_inject_no_wal_returns_none(tmp_path):
    # neither Blue (no inject_fn) nor WAL (degraded) yields a checkpoint -> None
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    store.append(ts=1, lineage_root=ROOT, generation=2, sid="b", runtime="claude",
                 kind="response", summary="nothing", source_path="x")
    assert cp.produce_checkpoint(ROOT, str(tmp_path), store, inject_fn=None,
                                 now=_fake_clock(), sleep=lambda s: None) is None


# ── gm build-conditions 2 (the operator-attachment) + 3 (staleness) ──────────────────

def test_produce_falls_back_when_operator_attached_to_blue(tmp_path):
    """Condition 2 (standing fleet rule): the (a) inject must NOT pane-nudge a seat
    the operator is attached to — if the operator is on Blue's pane, skip (a), go straight to (b)."""
    store, resolver = _seed_directive(tmp_path)
    injected = []
    out = cp.produce_checkpoint(
        ROOT, str(tmp_path), store,
        inject_fn=lambda r: injected.append(r),
        operator_attached_fn=lambda: True,          # the operator is on Blue's pane
        resolve_body=resolver, now=_fake_clock(), sleep=lambda s: None)
    assert injected == [], "must NOT inject Blue when the operator is attached to it"
    assert out["source"] == "wal-derived"       # fell back to (b)


def test_produce_reproduces_a_stale_existing_checkpoint(tmp_path):
    """Condition 3: a checkpoint older than the current prewarm baseline (fresh_since_seq)
    is STALE — do NOT reuse it; re-produce a fresh (b) with authored_at_seq==head."""
    store, resolver = _seed_directive(tmp_path)   # store.max_seq() == 1 here
    # a STALE checkpoint from an earlier cycle (authored_at_seq below the baseline)
    cp.write_build_checkpoint(str(tmp_path), ROOT,
                              {"objective": "OLD stale objective", "source": "blue",
                               "lineage_root": ROOT, "authored_at_seq": 0})
    called = []
    out = cp.produce_checkpoint(
        ROOT, str(tmp_path), store, inject_fn=lambda r: called.append(r),
        fresh_since_seq=store.max_seq(),         # require freshness at/after the baseline
        operator_attached_fn=lambda: True,           # force (b) so the test is deterministic
        resolve_body=resolver, now=_fake_clock(), sleep=lambda s: None)
    assert out["objective"] != "OLD stale objective", "a stale checkpoint must NOT be reused"
    assert out["source"] == "wal-derived"
    assert out["authored_at_seq"] == store.max_seq()


def test_produce_reuses_a_FRESH_existing_checkpoint(tmp_path):
    """A fresh existing checkpoint (lineage match + authored_at_seq >= baseline) IS reused
    idempotently — no re-inject."""
    store, resolver = _seed_directive(tmp_path)
    cp.write_build_checkpoint(str(tmp_path), ROOT,
                              {"objective": "fresh objective", "source": "blue",
                               "lineage_root": ROOT, "authored_at_seq": store.max_seq()})
    called = []
    out = cp.produce_checkpoint(
        ROOT, str(tmp_path), store, inject_fn=lambda r: called.append(r),
        fresh_since_seq=store.max_seq(), resolve_body=resolver,
        now=_fake_clock(), sleep=lambda s: None)
    assert called == [], "a FRESH checkpoint must be reused without re-injecting"
    assert out["objective"] == "fresh objective"


def test_wal_derived_stamps_lineage_and_seq(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    out = cp.wal_derived_checkpoint(store, ROOT, resolve_body=resolver)
    assert out["lineage_root"] == ROOT
    assert out["authored_at_seq"] == store.max_seq()
