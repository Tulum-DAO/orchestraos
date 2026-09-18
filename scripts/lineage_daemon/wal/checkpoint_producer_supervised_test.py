"""RED (BG leg-(ii) (ii-B) — the SUPERVISED produce_checkpoint wrapper).

(ii-B) is Target-B's finishing touch (gm PART-C=(ii), msg_d26e36d8; approved msg_9e76717b):
wire the M1a producer (produce_checkpoint, landed @9ca96dd92e / @99aee1ffb1) into the
SUPERVISED driver bg_live_beat.py. The wrapper adds the TWO gm-required caller conditions
ON TOP of produce_checkpoint's own operator-gate + bounded-wait→(b) fallback:

  cond 1 — the operator attached to Blue's pane  -> SKIP the (a) inject -> (b) WAL-derived
  cond 2 — blue_pid DEAD (/proc/{pid})   -> SKIP the (a) inject -> (b) (don't burn timeout)

i.e. real_inject_fn is passed through ONLY when (alive and not operator); else None.

RED-first + real-object: every decisive case drives the REAL produce_checkpoint against a
REAL WalStore + REAL continue_capsule (never a mock of the seam). inject/clock/alive are
dependency-injected so no live pane / real sleep is needed.

RED until checkpoint_producer.produce_checkpoint_supervised exists.
"""
import json
import os
import sys

sys.path.insert(0, "scripts")
from lineage_daemon.wal import checkpoint_producer as cp  # noqa: E402
from lineage_daemon.wal import continue_capsule as _cc  # noqa: E402
from lineage_daemon.wal.store import WalStore  # noqa: E402

ROOT = "second-brain-dev"
BLUE_SESSION = ROOT


def _resolver(bodies):
    return lambda ref: bodies.get(ref, "")


def _seed_directive(tmp_path):
    store = WalStore(str(tmp_path / f"{ROOT}.db"))
    store.append(ts=100, lineage_root=ROOT, generation=2, sid="b", runtime="claude",
                 kind="prompt", summary="text (40 chars)", body_ref="t.jsonl:real",
                 source_path="x")
    return store, _resolver({"t.jsonl:real": "Finish the (ii-B) supervised checkpoint wire"})


def _fake_clock():
    t = [0.0]

    def now():
        t[0] += 0.6
        return t[0]
    return now


def _kwargs():
    return dict(now=_fake_clock(), sleep=lambda s: None)


# ── cond 2: blue_pid DEAD -> skip (a) inject -> (b) ─────────────────────────────

def test_supervised_dead_blue_skips_inject_falls_back(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    injected = []
    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=999999, blue_session=BLUE_SESSION,
        real_inject_fn=lambda r: injected.append(r),
        alive_fn=lambda pid: False,          # cond 2: Blue's pid is dead
        resolve_body=resolver, **_kwargs())
    assert injected == [], "a DEAD Blue must NOT be injected (don't burn the timeout)"
    assert out["source"] == "wal-derived", "dead Blue -> hard (b)"
    # durably written to the convention path
    assert os.path.exists(_cc.build_checkpoint_path(str(tmp_path), ROOT))


# ── cond 1: the operator attached to Blue -> skip (a) inject -> (b) ─────────────────────

def test_supervised_operator_attached_skips_inject_falls_back(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    injected = []
    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=1, blue_session=BLUE_SESSION,
        real_inject_fn=lambda r: injected.append(r),
        alive_fn=lambda pid: True,           # Blue alive...
        operator_attached_fn=lambda: True,       # ...but the operator is on the pane (cond 1)
        resolve_body=resolver, **_kwargs())
    assert injected == [], "must NOT pane-nudge a seat the operator is attached to"
    assert out["source"] == "wal-derived", "the operator-attached -> (b)"


# ── alive + not-operator + Blue writes -> blue-authored ────────────────────────────

def test_supervised_alive_and_blue_writes_uses_blue(tmp_path):
    store, resolver = _seed_directive(tmp_path)

    def inject_fn(root):
        cp.write_build_checkpoint(str(tmp_path), root,
                                  {"objective": "Blue's explicit objective", "source": "blue"})
    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=1, blue_session=BLUE_SESSION,
        real_inject_fn=inject_fn,
        alive_fn=lambda pid: True,
        operator_attached_fn=lambda: False,
        resolve_body=resolver, **_kwargs())
    assert out["source"] == "blue"
    assert out["objective"] == "Blue's explicit objective"


# ── alive + not-operator + Blue NON-responsive -> hard (b) ─────────────────────────

def test_supervised_alive_but_nonresponsive_hard_fallback(tmp_path):
    store, resolver = _seed_directive(tmp_path)
    called = []
    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=1, blue_session=BLUE_SESSION,
        real_inject_fn=lambda r: called.append(r),   # asked but never writes
        alive_fn=lambda pid: True,
        operator_attached_fn=lambda: False,
        timeout_s=2.0, resolve_body=resolver, **_kwargs())
    assert called == [ROOT], "an ALIVE non-the operator Blue IS asked (option a) first"
    assert out["source"] == "wal-derived", "non-responsive Blue HARD-falls-back to (b)"
    path = _cc.build_checkpoint_path(str(tmp_path), ROOT)
    assert os.path.exists(path)
    assert json.load(open(path))["objective"] == "Finish the (ii-B) supervised checkpoint wire"


# ── default alive_fn is /proc-based (real, no injection) ───────────────────────

def test_supervised_default_alive_fn_uses_proc(tmp_path):
    """With no alive_fn injected, aliveness is the REAL /proc check. pid 1 (init) is
    always alive on Linux; a the operator-attached gate then still forces (b) deterministically."""
    store, resolver = _seed_directive(tmp_path)
    injected = []
    out = cp.produce_checkpoint_supervised(
        ROOT, str(tmp_path), store,
        blue_pid=1, blue_session=BLUE_SESSION,
        real_inject_fn=lambda r: injected.append(r),
        operator_attached_fn=lambda: True,       # force (b) regardless
        resolve_body=resolver, **_kwargs())
    assert injected == []
    assert out["source"] == "wal-derived"
