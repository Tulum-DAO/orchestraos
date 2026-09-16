import pytest
from unittest.mock import patch, MagicMock

from scripts.lineage_daemon.complete import complete_held_rotation
from scripts.lineage_daemon.adapters.protocol import RetirementReceipt, RepinReceipt
from scripts.continuity.rotation_store import RotationStore, RotationState

@pytest.fixture
def mock_store():
    store = MagicMock(spec=RotationStore)
    store.get_rotation.return_value = {
        "state": RotationState.READY_TO_CUTOVER.value,
        "state_version": 1
    }
    return store

def test_successful_cutover_and_receipts(mock_store):
    def live_sid_fn(alias): return "sid-123"
    def l_start_time_fn(sid): return 100.0
    def readback_mtime_fn(alias): return 200.0
    def comp_mtime_fn(alias): return 200.0
    def prov_sid_fn(alias): return "sid-123"
    def canonical_sids_fn(canary): return ("other", "other", "other")
    def confirm_fn(c, a): return {"outcome": "confirmed"}
    def grade_fn(c, a, s): return {"disposition": "PASS"}
    def safety_fn(c): return ("SUPERSEDED_SAFE", "")
    def grad_fn(a): return True
    def promote_fn(c, a, rotation_store=None, rotation_id=None): 
        if rotation_store and rotation_id:
            rot = rotation_store.get_rotation(rotation_id)
            rotation_store.transition(rotation_id, rot["state_version"], RotationState.CUTOVER_COMMITTED, cutover_receipt_json={"report": {"predecessor_archived": "archive-1"}})
        return {"ok": True, "report": {"predecessor_archived": "archive-1"}}
    def retire_fn(c, a=None, p=None): 
        return {
            "receipt": {
                "postcondition_verified": True, 
                "resolved_pid": 9999, 
                "resolved_sid": "sid-000",
                "requested_target": "archive-1",
                "archive_id": "archive-1",
                "action_result": "killed",
                "reason": "Postconditions verified"
            }
        }
    def execute_tmux_repin_fn(c, a, s, armed=False):
        return {
            "receipt": {
                "verified_after_rename": True,
                "successor_sid": "sid-123",
                "previous_alias": a,
                "canonical_name": c,
                "pane_id": "unknown",
                "error": None
            }
        }
    
    hold_row = {"successor": "gen-1", "created_at": 50.0}
    
    trace = complete_held_rotation(
        "canary", "gen-1", hold_row, now=300.0,
        live_sid_fn=live_sid_fn, l_start_time_fn=l_start_time_fn,
        readback_mtime_fn=readback_mtime_fn, comprehension_mtime_fn=comp_mtime_fn,
        provenance_sid_fn=prov_sid_fn, canonical_store_sids_fn=canonical_sids_fn,
        confirm_fn=confirm_fn, grade_fn=grade_fn, safety_fn=safety_fn,
        graduation_fn=grad_fn, promote_fn=promote_fn, retire_fn=retire_fn,
        rotation_store=mock_store, rotation_id="rot-1",
        execute_tmux_repin_fn=execute_tmux_repin_fn
    )
    
    assert trace["status"] == "completed"
    assert mock_store.transition.call_count == 4
    # Call 1: CUTOVER_COMMITTED
    # Call 2: RETIRE_COMMITTED
    # Call 3: CANONICAL_REPINNED
    # Call 4: COMPLETE

def test_rejection_predecessor_pid_alive(mock_store):
    def live_sid_fn(alias): return "sid-123"
    def l_start_time_fn(sid): return 100.0
    def readback_mtime_fn(alias): return 200.0
    def comp_mtime_fn(alias): return 200.0
    def prov_sid_fn(alias): return "sid-123"
    def canonical_sids_fn(canary): return ("other", "other", "other")
    def confirm_fn(c, a): return {"outcome": "confirmed"}
    def grade_fn(c, a, s): return {"disposition": "PASS"}
    def safety_fn(c): return ("SUPERSEDED_SAFE", "")
    def grad_fn(a): return True
    def promote_fn(c, a, rotation_store=None, rotation_id=None): 
        if rotation_store and rotation_id:
            rot = rotation_store.get_rotation(rotation_id)
            rotation_store.transition(rotation_id, rot["state_version"], RotationState.CUTOVER_COMMITTED, cutover_receipt_json={"report": {"predecessor_archived": "archive-1"}})
        return {"ok": True, "report": {"predecessor_archived": "archive-1"}}
    
    # PID remains alive -> postcondition_verified=False
    def retire_fn(c, a=None, p=None): 
        return {
            "receipt": {
                "postcondition_verified": False, 
                "resolved_pid": 9999, 
                "resolved_sid": "sid-000",
                "requested_target": "archive-1",
                "archive_id": "archive-1",
                "action_result": "kill attempted",
                "reason": "PID 9999 is still alive"
            }
        }
        
    hold_row = {"successor": "gen-1", "created_at": 50.0}
    
    trace = complete_held_rotation(
        "canary", "gen-1", hold_row, now=300.0,
        live_sid_fn=live_sid_fn, l_start_time_fn=l_start_time_fn,
        readback_mtime_fn=readback_mtime_fn, comprehension_mtime_fn=comp_mtime_fn,
        provenance_sid_fn=prov_sid_fn, canonical_store_sids_fn=canonical_sids_fn,
        confirm_fn=confirm_fn, grade_fn=grade_fn, safety_fn=safety_fn,
        graduation_fn=grad_fn, promote_fn=promote_fn, retire_fn=retire_fn,
        rotation_store=mock_store, rotation_id="rot-1",
    )
    
    assert trace["status"] == "hold:retire-postcondition-failed"
    # Transition should stop after CUTOVER_COMMITTED
    assert mock_store.transition.call_count == 1
    args, kwargs = mock_store.transition.call_args_list[0]
    assert args[2] == RotationState.CUTOVER_COMMITTED

def test_rejection_tmux_repin_fails(mock_store):
    def live_sid_fn(alias): return "sid-123"
    def l_start_time_fn(sid): return 100.0
    def readback_mtime_fn(alias): return 200.0
    def comp_mtime_fn(alias): return 200.0
    def prov_sid_fn(alias): return "sid-123"
    def canonical_sids_fn(canary): return ("other", "other", "other")
    def confirm_fn(c, a): return {"outcome": "confirmed"}
    def grade_fn(c, a, s): return {"disposition": "PASS"}
    def safety_fn(c): return ("SUPERSEDED_SAFE", "")
    def grad_fn(a): return True
    def promote_fn(c, a, rotation_store=None, rotation_id=None): 
        if rotation_store and rotation_id:
            rot = rotation_store.get_rotation(rotation_id)
            rotation_store.transition(rotation_id, rot["state_version"], RotationState.CUTOVER_COMMITTED, cutover_receipt_json={"report": {"predecessor_archived": "archive-1"}})
        return {"ok": True, "report": {"predecessor_archived": "archive-1"}}
    def retire_fn(c, a=None, p=None): 
        return {
            "receipt": {
                "postcondition_verified": True, 
                "resolved_pid": 9999, 
                "resolved_sid": "sid-000",
                "requested_target": "archive-1",
                "archive_id": "archive-1",
                "action_result": "killed",
                "reason": "Postconditions verified"
            }
        }
        
    # tmux repin fails -> verified_after_rename=False
    def execute_tmux_repin_fn(c, a, s, armed=False):
        return {
            "receipt": {
                "verified_after_rename": False,
                "successor_sid": "sid-123",
                "previous_alias": a,
                "canonical_name": c,
                "pane_id": "unknown",
                "error": "Active session ID sid-999 does not match successor sid-123"
            }
        }
        
    hold_row = {"successor": "gen-1", "created_at": 50.0}
    
    trace = complete_held_rotation(
        "canary", "gen-1", hold_row, now=300.0,
        live_sid_fn=live_sid_fn, l_start_time_fn=l_start_time_fn,
        readback_mtime_fn=readback_mtime_fn, comprehension_mtime_fn=comp_mtime_fn,
        provenance_sid_fn=prov_sid_fn, canonical_store_sids_fn=canonical_sids_fn,
        confirm_fn=confirm_fn, grade_fn=grade_fn, safety_fn=safety_fn,
        graduation_fn=grad_fn, promote_fn=promote_fn, retire_fn=retire_fn,
        rotation_store=mock_store, rotation_id="rot-1",
        execute_tmux_repin_fn=execute_tmux_repin_fn
    )
    
    assert trace["status"] == "hold:repin-failed"
    assert mock_store.transition.call_count == 2
    args, kwargs = mock_store.transition.call_args_list[0]
    assert args[2] == RotationState.CUTOVER_COMMITTED
    args, kwargs = mock_store.transition.call_args_list[1]
    assert args[2] == RotationState.RETIRE_COMMITTED
