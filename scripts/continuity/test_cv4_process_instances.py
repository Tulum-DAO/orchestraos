"""RED-first: CV4 durable process-instance placement (rung-4, codex §2).

Two append-only tables in the authority DB + a rotation join row:
  * cv4_process_instances     — one immutable row per captured InstanceRecord,
                                keyed by instance_id. Re-persisting the SAME
                                instance_id is idempotent; a DIFFERENT identity
                                tuple under the same id is REFUSED (an observation
                                may add facts but never overwrite the identity).
  * cv4_instance_observations — append-only observation ledger (many per instance).
  * cv4_rotation              — predecessor_instance_id + successor_instance_id +
                                lease_token + epoch; instance_id is the join/fence
                                key.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "scripts"))

from continuity import process_fence as pf  # noqa: E402
from continuity import cv4_process_instances as cpi  # noqa: E402


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "authority.db")
    cpi.ensure_process_instance_schema(p)
    return p


def _rec(**over):
    base = dict(pid=555, starttime=42, boot_id="boot-A", provider="codex",
                provider_sid="sid-1", tmux_session="s", tmux_pane_id="%1",
                host_id="h", observed_at="2026-08-31T05:00:00Z")
    base.update(over)
    return pf.InstanceRecord(**base)


def test_schema_is_idempotent(db):
    cpi.ensure_process_instance_schema(db)  # second call must not raise


def test_persist_and_load_roundtrips_by_instance_id(db):
    rec = _rec()
    iid = cpi.persist_instance(db, rec)
    assert iid == rec.instance_id
    loaded = cpi.load_instance(db, rec.instance_id)
    assert loaded == rec


def test_persist_same_instance_id_is_idempotent(db):
    rec = _rec()
    cpi.persist_instance(db, rec)
    cpi.persist_instance(db, rec)  # identical -> no error, no duplicate
    assert cpi.load_instance(db, rec.instance_id) == rec


def test_identity_is_immutable_conflicting_tuple_refused(db):
    rec = _rec()
    cpi.persist_instance(db, rec)
    # a DIFFERENT identity tuple stored under the SAME instance_id must be refused
    # — an observation may add facts, never overwrite the identity. (Forcing the id
    # explicitly simulates a forged/colliding write.)
    forged = _rec(host_id="different-host")
    with pytest.raises(cpi.InstanceIdentityConflict):
        cpi.persist_instance(db, forged, instance_id=rec.instance_id)


def test_observations_are_append_only(db):
    rec = _rec()
    cpi.persist_instance(db, rec)
    cpi.append_observation(db, rec.instance_id, is_alive=True, observed_at="t1")
    cpi.append_observation(db, rec.instance_id, is_alive=True, observed_at="t2")
    obs = cpi.load_observations(db, rec.instance_id)
    assert len(obs) == 2
    assert [o["observed_at"] for o in obs] == ["t1", "t2"]


def test_rotation_row_joins_pred_and_succ_by_instance_id(db):
    pred = _rec(provider_sid="pred-sid")
    succ = _rec(provider_sid="succ-sid", pid=999)
    cpi.persist_instance(db, pred)
    cpi.persist_instance(db, succ)
    cpi.record_rotation(db, rotation_id="rot-1",
                        predecessor_instance_id=pred.instance_id,
                        successor_instance_id=succ.instance_id,
                        lease_token="lease-1", epoch=3)
    row = cpi.load_rotation(db, "rot-1")
    assert row["predecessor_instance_id"] == pred.instance_id
    assert row["successor_instance_id"] == succ.instance_id
    assert row["lease_token"] == "lease-1"
    assert row["epoch"] == 3


def test_rotation_refuses_unknown_instance_id(db):
    # the join key must reference a persisted instance (fence integrity)
    with pytest.raises(cpi.UnknownInstance):
        cpi.record_rotation(db, rotation_id="rot-x",
                            predecessor_instance_id="deadbeef" * 8,
                            successor_instance_id="deadbeef" * 8,
                            lease_token="l", epoch=1)
