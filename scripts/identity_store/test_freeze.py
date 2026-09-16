"""Phase-2 item-2 (RED) — freeze/quiesce so ZERO writes are lost at cutover.

Between the final ``migrate`` (capture live JSON -> DB) and the flag flip, a
legacy writer that writes JSON would be lost (not in the DB; the projector then
overwrites the JSON). The freeze barrier closes that window: writers call
``barrier()`` before writing; during the brief freeze they BLOCK, so no write
lands mid-migrate; after unfreeze the cutover flag is active and the same write
goes to the DB.

Cutover sequence: freeze -> (settle) migrate -> arm -> unfreeze. A write is
therefore in EITHER the migrated snapshot OR the DB, never lost.

RED until ``scripts/identity_store/freeze`` exists.
"""
import threading
import time

import pytest

from scripts.identity_store import cutover, freeze


@pytest.fixture
def orchdir(tmp_path):
    (tmp_path / "state").mkdir()
    return str(tmp_path)


def test_not_frozen_by_default(orchdir):
    assert freeze.is_frozen(orchdir) is False


def test_freeze_unfreeze_toggles(orchdir):
    freeze.freeze(orchdir)
    assert freeze.is_frozen(orchdir) is True
    freeze.unfreeze(orchdir)
    assert freeze.is_frozen(orchdir) is False


def test_barrier_passes_immediately_when_not_frozen(orchdir):
    t0 = time.monotonic()
    freeze.barrier(orchdir, timeout=5.0)
    assert time.monotonic() - t0 < 0.5


def test_barrier_blocks_until_unfreeze(orchdir):
    freeze.freeze(orchdir)
    released = {"at": None}

    def waiter():
        freeze.barrier(orchdir, timeout=5.0)
        released["at"] = time.monotonic()

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.3)
    assert released["at"] is None, "barrier must block while frozen"
    unfreeze_at = time.monotonic()
    freeze.unfreeze(orchdir)
    t.join(timeout=5)
    assert released["at"] is not None and released["at"] >= unfreeze_at


def test_barrier_times_out_if_frozen_too_long(orchdir):
    freeze.freeze(orchdir)
    with pytest.raises(freeze.FreezeTimeout):
        freeze.barrier(orchdir, timeout=0.2, poll=0.02)


def test_cutover_window_loses_zero_writes(orchdir):
    """The load-bearing proof. A writer loops barrier->write while the controller
    runs freeze -> settle+snapshot(migrate) -> arm -> unfreeze. Every intended
    write lands in EITHER the pre-cutover JSON snapshot OR the DB — none lost —
    and the freeze window is measured."""
    json_store = {}
    db_store = set()
    intended = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            freeze.barrier(orchdir, timeout=5.0)
            if cutover.is_active(orchdir):
                db_store.add(i)
            else:
                json_store[i] = True
            intended.append(i)
            i += 1
            time.sleep(0.002)

    t = threading.Thread(target=writer)
    t.start()
    time.sleep(0.1)                      # let some legacy JSON writes happen

    t_freeze = time.monotonic()
    freeze.freeze(orchdir)
    time.sleep(0.05)                     # settle: let any in-flight write drain
    snapshot = set(json_store)           # the "migrate" captures a quiescent view
    cutover.arm(orchdir)                 # DB becomes write-truth
    freeze.unfreeze(orchdir)
    t_unfreeze = time.monotonic()

    time.sleep(0.1)                      # let post-cutover writes land in the DB
    stop.set()
    t.join(timeout=5)

    window = t_unfreeze - t_freeze
    assert window < 1.0, f"freeze window should be brief, was {window:.3f}s"
    lost = set(intended) - (snapshot | db_store)
    assert not lost, f"writes lost in the cutover window: {sorted(lost)[:5]}"
    assert db_store, "post-cutover writes should have gone to the DB"
