import os
import json
import time
import pytest
from datetime import datetime, timezone
from scripts.continuity.rotation_store import RotationStore, RotationState, StateTransitionError
from scripts.lineage_daemon import complete as C
from scripts.lineage_daemon.adapters.protocol import (
    ProviderAdapter, ProcessReceipt, TranscriptReceipt, DeliveryReceipt, 
    RetirementReceipt, ContextObservation, ProviderIdentity, CommandSpec
)

# ==============================================================================
# FIXTURES & HELPERS
# ==============================================================================

@pytest.fixture
def db_path(tmp_path):
    db_file = tmp_path / "test_tasks.db"
    return str(db_file)

@pytest.fixture
def store(db_path):
    return RotationStore(db_path)


def update_rotation_fields(store, rotation_id, **fields):
    """Helper to update standard (non-JSON-serialized) columns directly in SQLite."""
    set_clauses = []
    values = []
    for k, v in fields.items():
        set_clauses.append(f"{k} = ?")
        values.append(v)
    query = f"UPDATE cv4_rotation_lifecycle SET {', '.join(set_clauses)} WHERE rotation_id = ?"
    with store._get_conn() as conn:
        conn.execute(query, tuple(values) + (rotation_id,))
        conn.commit()


# ==============================================================================
# STEP 30: FAULT INJECTION MATRIX
# ==============================================================================

def test_cas_collision_optimistic_locking(store):
    """1. Optimistic locking / CAS collision when multiple daemon beats attempt state transitions simultaneously."""
    rot_id = "canary-collision"
    store.create_rotation(rot_id, "lineage-root")
    
    # Initial state is HEALTHY, state_version = 1
    rot = store.get_rotation(rot_id)
    assert rot["state"] == RotationState.HEALTHY.value
    assert rot["state_version"] == 1
    
    # Beat 1 successfully transitions from v1 to SOFT_AUTHORING
    store.transition(rot_id, 1, RotationState.SOFT_AUTHORING)
    
    # Beat 2 attempts to transition from v1 to SOFT_READY simultaneously -> fails
    with pytest.raises(StateTransitionError):
        store.transition(rot_id, 1, RotationState.SOFT_READY)


def test_predecessor_dies_during_orientation_pending(store):
    """2. Predecessor dies unexpectedly during ORIENTATION_PENDING (recovery reconciles and cleans up)."""
    rot_id = "canary-predecessor-dies"
    store.create_rotation(rot_id, "lineage-root", predecessor_alias="pred-agent")
    
    # Transition to ORIENTATION_PENDING (state_version 1 -> 2)
    store.transition(rot_id, 1, RotationState.ORIENTATION_PENDING)
    
    # Mock ProviderAdapter to return predecessor process is dead
    class MockDeadPredecessorAdapter(ProviderAdapter):
        def observe_context(self, seat: str) -> ContextObservation:
            return ContextObservation(token_count=100, context_pct=0.1, is_near_limit=False)
        def spawn_alias(self, seat: str, successor_alias: str) -> ProcessReceipt:
            return None
        def resolve_declared_identity(self, alias: str) -> ProviderIdentity:
            return ProviderIdentity(session_id="mock_sid", alias=alias)
        def locate_transcript(self, identity: ProviderIdentity) -> TranscriptReceipt:
            return None
        def resume_command(self, identity: ProviderIdentity) -> CommandSpec:
            return None
        def deliver(self, alias_or_sid: str, payload: str, idempotency_key: str) -> DeliveryReceipt:
            return None
        def observe_process(self, alias_or_sid: str) -> ProcessReceipt:
            # Predecessor is dead!
            return ProcessReceipt(
                provider="mock", runtime="python", session_id="pred-sid", pid=1001,
                tmux_session="pred-tmux", cwd="/tmp", model="claude-3-5-sonnet",
                observed_at=datetime.now(timezone.utc), is_alive=False
            )
        def retire_process(self, archive_identity: str) -> RetirementReceipt:
            return None
            
    adapter = MockDeadPredecessorAdapter()
    
    # Reconciliation / recovery routine
    def reconcile_orientation(store, rot_id, adapter):
        rot = store.get_rotation(rot_id)
        if rot["state"] == RotationState.ORIENTATION_PENDING.value:
            pred_alias = rot["predecessor_alias"]
            proc = adapter.observe_process(pred_alias)
            if not proc.is_alive:
                # Predecessor died unexpectedly during orientation!
                # Transition to RECOVERY and release lease/cleanup
                update_rotation_fields(
                    store, rot_id,
                    last_error="Predecessor died unexpectedly during ORIENTATION_PENDING"
                )
                store.transition(rot_id, rot["state_version"], RotationState.RECOVERY)
                return True
        return False
        
    res = reconcile_orientation(store, rot_id, adapter)
    assert res is True
    
    # Verify state updated to RECOVERY
    updated_rot = store.get_rotation(rot_id)
    assert updated_rot["state"] == RotationState.RECOVERY.value
    assert "Predecessor died unexpectedly" in updated_rot["last_error"]


def test_successor_crashes_before_readback(store):
    """3. Successor crashes or terminates before committing readback (hold remains or expires to re-spawn)."""
    rot_id = "canary-successor-crashed"
    store.create_rotation(rot_id, "lineage-root", successor_alias="succ-agent")
    
    # Transition to SUCCESSOR_SPAWNED
    store.transition(rot_id, 1, RotationState.SUCCESSOR_SPAWNED)
    
    # Successor crashed -> live_sid_fn returns None, readback is missing
    live_sid_fn = lambda a: None
    readback_mtime_fn = lambda a: None
    
    hold_row = {
        "successor": "succ-agent",
        "created_at": 1000,
    }
    
    # Check readiness -> must be False
    is_ready = C.completion_ready(
        "canary", "succ-agent", hold_row, now=5000,
        live_sid_fn=live_sid_fn,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=readback_mtime_fn,
        provenance_sid_fn=lambda a: None
    )
    assert is_ready is False
    
    # Calling complete_held_rotation must return HOLD_NOT_LIVE (hold remains)
    trace = C.complete_held_rotation(
        "canary", "succ-agent", hold_row, now=5000,
        live_sid_fn=live_sid_fn,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=readback_mtime_fn,
        comprehension_mtime_fn=lambda a: None,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: ("pred-sid", "pred-sid", "pred-sid"),
        confirm_fn=lambda c, s: {"outcome": "held"},
        grade_fn=lambda c, s, sid: {"disposition": "FAIL"},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=lambda c, alias: {"ok": False},
        retire_fn=lambda c: None
    )
    assert trace["status"] == C.HOLD_NOT_LIVE
    
    # Recovery daemon checks for dead successor under SUCCESSOR_SPAWNED state to re-spawn
    def check_successor_re_spawn(store, rot_id, live_sid_fn):
        rot = store.get_rotation(rot_id)
        if rot["state"] == RotationState.SUCCESSOR_SPAWNED.value:
            if not live_sid_fn(rot["successor_alias"]):
                # Successor has crashed or terminated before readback.
                # Transition to RECOVERY to trigger re-spawn.
                update_rotation_fields(
                    store, rot_id,
                    last_error="Successor crashed before committing readback"
                )
                store.transition(rot_id, rot["state_version"], RotationState.RECOVERY)
                return True
        return False
        
    assert check_successor_re_spawn(store, rot_id, live_sid_fn) is True
    assert store.get_rotation(rot_id)["state"] == RotationState.RECOVERY.value


def test_unkillable_predecessor_escalation(store):
    """4. Un-killable predecessor process during retirement (fail-closed escalation, no silent success)."""
    rot_id = "canary-retire-fails"
    store.create_rotation(rot_id, "lineage-root", predecessor_alias="pred-agent")
    store.transition(rot_id, 1, RotationState.READY_TO_CUTOVER)
    
    hold_row = {
        "successor": "succ-agent",
        "created_at": 1000,
    }
    
    # Unkillable predecessor raises an exception during retirement
    def unkillable_retire(canary, alias=None, promote_res=None):
        raise RuntimeError("SIGKILL failed: predecessor is unkillable (immune to signals)")
        
    # Run completion. When retire fails, we must fail-close and escalate
    try:
        C.complete_held_rotation(
            "canary", "succ-agent", hold_row, now=5000,
            live_sid_fn=lambda a: "succ-sid",
            l_start_time_fn=lambda sid: 1500.0,
            readback_mtime_fn=lambda a: 2000.0,
            comprehension_mtime_fn=lambda a: 2100.0,
            provenance_sid_fn=lambda a: None,
            canonical_store_sids_fn=lambda c: ("pred-sid", "pred-sid", "pred-sid"),
            confirm_fn=lambda c, s: {"outcome": "confirmed"},
            grade_fn=lambda c, s, sid: {"disposition": "PASS"},
            safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
            graduation_fn=lambda alias: True,
            promote_fn=lambda c, alias: {"ok": True},
            retire_fn=unkillable_retire
        )
    except Exception as e:
        # Catch exception and escalate state in RotationStore
        update_rotation_fields(store, rot_id, last_error=f"Fail-closed escalation: {str(e)}")
        store.transition(rot_id, 2, RotationState.ESCALATED)
        
    updated_rot = store.get_rotation(rot_id)
    assert updated_rot["state"] == RotationState.ESCALATED.value
    assert "predecessor is unkillable" in updated_rot["last_error"]


def test_corrupted_handoff_sha_rejection(store):
    """5. Stale or corrupted handoff commit SHA / file hash (rejected before promotion)."""
    rot_id = "canary-corrupt-handoff"
    # Create rotation with an expected handoff commit SHA
    store.create_rotation(rot_id, "lineage-root", handoff_commit_sha="expected-sha-123")
    store.transition(rot_id, 1, RotationState.READY_TO_CUTOVER)
    
    hold_row = {
        "successor": "succ-agent",
        "created_at": 1000,
    }
    
    # Mock Git/Handoff validator
    def check_git_sha(expected_sha):
        # Suppose actual HEAD in successor's workspace is "different-sha-456" (corrupted/stale)
        actual_head = "different-sha-456"
        return expected_sha == actual_head
        
    git_ok = check_git_sha("expected-sha-123")
    confirm_fn = lambda c, s: {"outcome": "confirmed" if git_ok else "held"}
    
    trace = C.complete_held_rotation(
        "canary", "succ-agent", hold_row, now=5000,
        live_sid_fn=lambda a: "succ-sid",
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: 2000.0,
        comprehension_mtime_fn=lambda a: 2100.0,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: ("pred-sid", "pred-sid", "pred-sid"),
        confirm_fn=confirm_fn,
        grade_fn=lambda c, s, sid: {"disposition": "PASS"},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=lambda c, alias: {"ok": True},
        retire_fn=lambda c: None
    )
    
    # Confirm fails, promotion never reached, predecessor safe
    assert trace["status"] == C.HOLD_UNCONFIRMED
    assert trace["promoted"] is False


def test_tmux_session_rename_failure(store):
    """6. Tmux session collision or rename failure."""
    rot_id = "canary-tmux-rename-fail"
    store.create_rotation(rot_id, "lineage-root")
    store.transition(rot_id, 1, RotationState.READY_TO_CUTOVER)
    
    hold_row = {
        "successor": "succ-agent",
        "created_at": 1000,
    }
    
    # Simulate tmux collision or rename failure during promotion
    def promote_with_collision(canary, alias):
        return {"ok": False, "error": "rename failed: session 'gm' already exists"}
        
    trace = C.complete_held_rotation(
        "canary", "succ-agent", hold_row, now=5000,
        live_sid_fn=lambda a: "succ-sid",
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: 2000.0,
        comprehension_mtime_fn=lambda a: 2100.0,
        provenance_sid_fn=lambda a: None,
        canonical_store_sids_fn=lambda c: ("pred-sid", "pred-sid", "pred-sid"),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS"},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=promote_with_collision,
        retire_fn=lambda c: None
    )
    
    assert trace["status"] == C.HOLD_PROMOTE_FAILED
    assert trace["promoted"] is False
    
    # Escalated in store
    update_rotation_fields(
        store, rot_id, 
        last_error=f"Tmux session rename failure: {trace.get('last_error', 'collision')}"
    )
    store.transition(rot_id, 2, RotationState.ESCALATED)
    assert store.get_rotation(rot_id)["state"] == RotationState.ESCALATED.value


# ==============================================================================
# STEP 31 & 32: EPHEMERAL INTEGRATION SUITE
# ==============================================================================

def test_pipeline_integration_end_to_end(store):
    """Verify RotationStore + complete.py + ProviderAdapter pipeline end-to-end."""
    
    # 1. Initialize Rotation Lifecycle in store
    rot_id = "canary-e2e-pipeline"
    store.create_rotation(
        rot_id, "lineage-root", 
        predecessor_alias="canary",
        predecessor_provider_sid="pred-sid",
        successor_alias="succ-agent"
    )
    
    # Advance rotation to SUCCESSOR_SPAWNED
    store.transition(rot_id, 1, RotationState.SUCCESSOR_SPAWNED)
    
    # 2. Define concrete mock ProviderAdapter managing mock process environments
    class IntegrationProviderAdapter(ProviderAdapter):
        def __init__(self):
            self.processes = {
                "canary": ProcessReceipt(
                    provider="mock", runtime="python", session_id="pred-sid", pid=1001,
                    tmux_session="canary-tmux", cwd="/tmp", model="claude-3-5-sonnet",
                    observed_at=datetime.now(timezone.utc), is_alive=True
                )
            }
            
        def observe_context(self, seat: str) -> ContextObservation:
            return ContextObservation(token_count=120000, context_pct=0.12, is_near_limit=False)
            
        def spawn_alias(self, seat: str, successor_alias: str) -> ProcessReceipt:
            receipt = ProcessReceipt(
                provider="mock", runtime="python", session_id="succ-sid", pid=1002,
                tmux_session="succ-tmux", cwd="/tmp", model="claude-3-5-sonnet",
                observed_at=datetime.now(timezone.utc), is_alive=True
            )
            self.processes[successor_alias] = receipt
            return receipt
            
        def resolve_declared_identity(self, alias: str) -> ProviderIdentity:
            return ProviderIdentity(session_id=self.processes[alias].session_id, alias=alias)
            
        def locate_transcript(self, identity: ProviderIdentity) -> TranscriptReceipt:
            return TranscriptReceipt(
                provider="mock", session_id=identity.session_id,
                transcript_path=f"/tmp/{identity.session_id}/transcript.jsonl",
                token_count=120000, context_pct=0.12, is_fresh=True
            )
            
        def resume_command(self, identity: ProviderIdentity) -> CommandSpec:
            return CommandSpec(command="agy", env={})
            
        def deliver(self, alias_or_sid: str, payload: str, idempotency_key: str) -> DeliveryReceipt:
            return DeliveryReceipt(
                provider="mock", target_alias_or_sid=alias_or_sid,
                payload_hash="payload-sha256", delivered_at=datetime.now(timezone.utc),
                delivery_method="stdio", verified_ack=True
            )
            
        def observe_process(self, alias_or_sid: str) -> ProcessReceipt:
            return self.processes.get(alias_or_sid)
            
        def retire_process(self, archive_identity: str) -> RetirementReceipt:
            # Terminate predecessor process
            if archive_identity in self.processes:
                self.processes[archive_identity].is_alive = False
            return RetirementReceipt(
                requested_target=archive_identity, archive_id="arch_1",
                resolved_sid="pred-sid", resolved_pid=1001, action_result="killed",
                postcondition_verified=True, reason="rotation completion"
            )
            
    adapter = IntegrationProviderAdapter()
    
    # 3. Successor is spawned via ProviderAdapter
    proc_receipt = adapter.spawn_alias("canary", "succ-agent")
    assert proc_receipt.is_alive is True
    assert proc_receipt.session_id == "succ-sid"
    
    # Store transition to ORIENTATION_PENDING
    update_rotation_fields(
        store, rot_id,
        successor_provider_sid="succ-sid",
        successor_tmux_session="succ-tmux"
    )
    store.transition(rot_id, 2, RotationState.ORIENTATION_PENDING)
    
    # Successor completes work and commits its readback & comprehension
    # Simulated file modified times (mtimes)
    now_ts = 5000.0
    readback_mtime = 2000.0
    comprehension_mtime = 2100.0
    
    hold_row = {
        "successor": "succ-agent",
        "created_at": 1000.0,
    }
    
    # Verify that successor is now completion_ready
    is_ready = C.completion_ready(
        "canary", "succ-agent", hold_row, now=now_ts,
        live_sid_fn=lambda a: adapter.observe_process(a).session_id if adapter.observe_process(a) else None,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: readback_mtime,
        provenance_sid_fn=lambda a: "succ-sid"
    )
    assert is_ready is True
    
    # Transition to READY_TO_CUTOVER in RotationStore
    store.transition(rot_id, 3, RotationState.READY_TO_CUTOVER)
    
    # 4. Integrate complete_held_rotation with RotationStore & ProviderAdapter
    promoted_calls = []
    retired_calls = []
    
    def pipeline_promote(canary, alias):
        # Perform CAS transition in RotationStore to CUTOVER_COMMITTED
        rot = store.get_rotation(rot_id)
        store.transition(rot_id, rot["state_version"], RotationState.CUTOVER_COMMITTED)
        promoted_calls.append(alias)
        return {"ok": True}
        
    def pipeline_retire(canary, alias=None, promote_res=None):
        # Retire the predecessor process using ProviderAdapter
        adapter.retire_process(canary)
        # Perform transition in RotationStore to COMPLETE
        rot = store.get_rotation(rot_id)
        store.transition(rot_id, rot["state_version"], RotationState.COMPLETE)
        retired_calls.append(canary)
        return {"retired": True}
        
    trace = C.complete_held_rotation(
        "canary", "succ-agent", hold_row, now=now_ts,
        live_sid_fn=lambda a: adapter.observe_process(a).session_id if adapter.observe_process(a) else None,
        l_start_time_fn=lambda sid: 1500.0,
        readback_mtime_fn=lambda a: readback_mtime,
        comprehension_mtime_fn=lambda a: comprehension_mtime,
        provenance_sid_fn=lambda a: "succ-sid",
        canonical_store_sids_fn=lambda c: ("pred-sid", "pred-sid", "pred-sid"),
        confirm_fn=lambda c, s: {"outcome": "confirmed"},
        grade_fn=lambda c, s, sid: {"disposition": "PASS"},
        safety_fn=lambda c: ("SUPERSEDED_SAFE", "ok"),
        graduation_fn=lambda alias: True,
        promote_fn=pipeline_promote,
        retire_fn=pipeline_retire
    )
    
    # 5. Verify entire pipeline outputs are perfectly coherent
    assert trace["status"] == C.COMPLETED
    assert trace["promoted"] is True
    assert trace["retired"] is True
    
    # Verify promote strictly before retire execution order
    assert trace["steps"].index("promote") < trace["steps"].index("retire")
    
    # Verify predecessor process was retired (observe_process returns alive=False)
    assert adapter.observe_process("canary").is_alive is False
    
    # Verify RotationStore contains final terminal COMPLETE state!
    final_rot = store.get_rotation(rot_id)
    assert final_rot["state"] == RotationState.COMPLETE.value
    assert final_rot["state_version"] == 6  # HEALTHY(1) -> ORIENT_PENDING(2) -> READY_CUTOVER(3) -> CUTOVER_COMMITTED(4) -> COMPLETE(5) -> +1 from transition = 6
