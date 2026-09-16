import sqlite3
import time
import json
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple

class RotationState(str, Enum):
    HEALTHY = "HEALTHY"
    SOFT_AUTHORING = "SOFT_AUTHORING"
    SOFT_READY = "SOFT_READY"
    HOLD_CREATED = "HOLD_CREATED"
    SUCCESSOR_SPAWNED = "SUCCESSOR_SPAWNED"
    ORIENTATION_PENDING = "ORIENTATION_PENDING"
    READY_TO_CUTOVER = "READY_TO_CUTOVER"
    CUTOVER_COMMITTED = "CUTOVER_COMMITTED"
    PROGRESS_WATCH = "PROGRESS_WATCH"
    RETIRE_COMMITTED = "RETIRE_COMMITTED"
    CANONICAL_REPINNED = "CANONICAL_REPINNED"
    EFFECT_WATCH = "EFFECT_WATCH"
    COMPLETE = "COMPLETE"
    ESCALATED = "ESCALATED"
    HOLD = "HOLD"
    RECOVERY = "RECOVERY"

    @classmethod
    def terminal_states(cls) -> List['RotationState']:
        return [cls.COMPLETE, cls.ESCALATED]

class StateTransitionError(Exception):
    pass

class LeaseAcquisitionError(Exception):
    pass

class RotationStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cv4_rotation_lifecycle (
                    rotation_id TEXT PRIMARY KEY,
                    lineage_root TEXT,
                    state TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    predecessor_alias TEXT,
                    predecessor_archive_id TEXT,
                    predecessor_provider_sid TEXT,
                    predecessor_tmux_session TEXT,
                    successor_alias TEXT,
                    successor_provider_sid TEXT,
                    successor_tmux_session TEXT,
                    canonical_name TEXT,
                    generation INTEGER,
                    handoff_commit_sha TEXT,
                    handoff_path TEXT,
                    detector_snapshot_json TEXT,
                    readback_sha TEXT,
                    grade_receipt_json TEXT,
                    cutover_receipt_json TEXT,
                    continuation_receipt_json TEXT,
                    progress_receipt_json TEXT,
                    retire_receipt_json TEXT,
                    repin_receipt_json TEXT,
                    escalation_receipt_json TEXT,
                    lease_id TEXT,
                    epoch REAL,
                    last_error TEXT,
                    retry_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cv4_transition_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rotation_id TEXT,
                    timestamp REAL,
                    old_state TEXT,
                    new_state TEXT,
                    state_version INTEGER,
                    receipts_json TEXT
                )
            """)
            conn.commit()

    def create_rotation(self, rotation_id: str, lineage_root: str, **kwargs) -> Dict[str, Any]:
        """Creates a new rotation if it doesn't exist (idempotent setup)."""
        now = time.time()
        fields = [
            'rotation_id', 'lineage_root', 'state', 'state_version', 'created_at', 'updated_at',
            'predecessor_alias', 'predecessor_archive_id', 'predecessor_provider_sid',
            'predecessor_tmux_session', 'successor_alias', 'successor_provider_sid',
            'successor_tmux_session', 'canonical_name', 'generation'
        ]
        
        values_dict = {
            'rotation_id': rotation_id,
            'lineage_root': lineage_root,
            'state': RotationState.HEALTHY.value,
            'state_version': 1,
            'created_at': now,
            'updated_at': now,
        }
        
        for k in kwargs:
            if k in fields and k not in values_dict:
                values_dict[k] = kwargs[k]

        cols = ", ".join(values_dict.keys())
        placeholders = ", ".join("?" for _ in values_dict)
        
        with self._get_conn() as conn:
            try:
                conn.execute(f"""
                    INSERT INTO cv4_rotation_lifecycle ({cols})
                    VALUES ({placeholders})
                """, tuple(values_dict.values()))
                conn.commit()
            except sqlite3.IntegrityError:
                # Idempotent: already exists, ignore
                pass

        return self.get_rotation(rotation_id)

    def get_rotation(self, rotation_id: str) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM cv4_rotation_lifecycle WHERE rotation_id = ?", (rotation_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def acquire_lease(self, rotation_id: str, lease_id: str, timeout_sec: float = 60.0) -> bool:
        """Attempts to acquire an exclusive lock/lease using the epoch mechanism."""
        now = time.time()
        with self._get_conn() as conn:
            # We can acquire if lease_id is empty, or epoch + timeout < now (expired lease)
            cur = conn.execute("""
                UPDATE cv4_rotation_lifecycle
                SET lease_id = ?, epoch = ?, updated_at = ?
                WHERE rotation_id = ? AND (lease_id IS NULL OR epoch + ? < ?)
            """, (lease_id, now, now, rotation_id, timeout_sec, now))
            
            success = cur.rowcount > 0
            conn.commit()
            return success

    def transition(self, rotation_id: str, expected_version: int, new_state: RotationState, lease_id: Optional[str] = None, **receipts) -> Dict[str, Any]:
        """Compare-and-Swap (CAS) state transition."""
        now = time.time()
        
        # Serialize receipts
        receipt_updates = []
        receipt_values = []
        for k, v in receipts.items():
            col_name = f"{k}_json"
            receipt_updates.append(f"{col_name} = ?")
            receipt_values.append(json.dumps(v))

        update_query = f"""
            UPDATE cv4_rotation_lifecycle
            SET state = ?, state_version = state_version + 1, updated_at = ?
        """
        if receipt_updates:
            update_query += ", " + ", ".join(receipt_updates)

        update_query += " WHERE rotation_id = ? AND state_version = ?"
        
        params = [new_state.value, now] + receipt_values + [rotation_id, expected_version]
        
        if lease_id:
            update_query += " AND lease_id = ?"
            params.append(lease_id)

        with self._get_conn() as conn:
            cur = conn.execute(update_query, tuple(params))
            
            if cur.rowcount == 0:
                raise StateTransitionError(f"CAS failed for {rotation_id}: expected version {expected_version}")
            
            # Record transition event
            conn.execute("""
                INSERT INTO cv4_transition_events (rotation_id, timestamp, old_state, new_state, state_version, receipts_json)
                VALUES (?, ?, (SELECT state FROM cv4_rotation_lifecycle WHERE rotation_id = ?), ?, ?, ?)
            """, (rotation_id, now, rotation_id, new_state.value, expected_version + 1, json.dumps(receipts)))
            
            conn.commit()
            
        return self.get_rotation(rotation_id)

def reconcile_open_rotations(db_path: str) -> List[Dict[str, Any]]:
    """Loads all non-terminal rotations on startup for reconciliation."""
    terminal = [s.value for s in RotationState.terminal_states()]
    store = RotationStore(db_path)
    with store._get_conn() as conn:
        placeholders = ",".join("?" for _ in terminal)
        query = f"SELECT * FROM cv4_rotation_lifecycle WHERE state NOT IN ({placeholders})"
        cur = conn.execute(query, tuple(terminal))
        return [dict(row) for row in cur.fetchall()]
