import os
import time
import pytest
from scripts.continuity.rotation_store import RotationStore, RotationState, StateTransitionError, reconcile_open_rotations

@pytest.fixture
def db_path(tmp_path):
    db_file = tmp_path / "test_tasks.db"
    return str(db_file)

@pytest.fixture
def store(db_path):
    return RotationStore(db_path)

def test_create_and_get_rotation(store):
    rot_id = "root:old_sid:new_sid"
    rot = store.create_rotation(rot_id, "root")
    assert rot["rotation_id"] == rot_id
    assert rot["state"] == RotationState.HEALTHY.value
    assert rot["state_version"] == 1
    
    # Idempotency check
    rot2 = store.create_rotation(rot_id, "root")
    assert rot2["rotation_id"] == rot_id

def test_lease_acquisition_and_expiry(store):
    rot_id = "root:old:new"
    store.create_rotation(rot_id, "root")
    
    # Acquire lease successfully
    assert store.acquire_lease(rot_id, "worker1", timeout_sec=1.0) is True
    
    # Second worker fails to acquire
    assert store.acquire_lease(rot_id, "worker2", timeout_sec=1.0) is False
    
    # Wait for expiry
    time.sleep(1.1)
    
    # Worker 2 should now acquire
    assert store.acquire_lease(rot_id, "worker2", timeout_sec=1.0) is True

def test_cas_transitions(store):
    rot_id = "root:old:new"
    store.create_rotation(rot_id, "root")
    
    # Successful transition
    rot = store.transition(rot_id, 1, RotationState.SOFT_AUTHORING, grade_receipt={"score": 100})
    assert rot["state"] == RotationState.SOFT_AUTHORING.value
    assert rot["state_version"] == 2
    assert "grade_receipt_json" in rot
    assert '"score": 100' in rot["grade_receipt_json"]
    
    # Conflicting transition (optimistic locking)
    with pytest.raises(StateTransitionError):
        store.transition(rot_id, 1, RotationState.SOFT_READY)
        
    # Correct version transition
    rot = store.transition(rot_id, 2, RotationState.SOFT_READY)
    assert rot["state"] == RotationState.SOFT_READY.value
    assert rot["state_version"] == 3

def test_cas_with_lease(store):
    rot_id = "root:old:new"
    store.create_rotation(rot_id, "root")
    
    store.acquire_lease(rot_id, "worker1", timeout_sec=10.0)
    
    # Transition with correct lease
    rot = store.transition(rot_id, 1, RotationState.SOFT_AUTHORING, lease_id="worker1")
    assert rot["state_version"] == 2
    
    # Transition with wrong lease fails
    with pytest.raises(StateTransitionError):
        store.transition(rot_id, 2, RotationState.SOFT_READY, lease_id="worker2")

def test_reconcile_open_rotations(db_path, store):
    store.create_rotation("rot1", "root")
    store.transition("rot1", 1, RotationState.HEALTHY)  # stays healthy, v=2
    
    store.create_rotation("rot2", "root")
    store.transition("rot2", 1, RotationState.COMPLETE) # terminal
    
    store.create_rotation("rot3", "root")
    store.transition("rot3", 1, RotationState.ESCALATED) # terminal
    
    store.create_rotation("rot4", "root")
    store.transition("rot4", 1, RotationState.READY_TO_CUTOVER) # non-terminal
    
    open_rots = reconcile_open_rotations(db_path)
    open_ids = [r["rotation_id"] for r in open_rots]
    
    assert len(open_ids) == 2
    assert "rot1" in open_ids
    assert "rot4" in open_ids
    assert "rot2" not in open_ids
    assert "rot3" not in open_ids
