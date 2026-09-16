"""Continuity v4 — Phase B authority store (SHADOW-ONLY).

Ratified schema: .workspace/proposals/continuity-v4-phaseB-authority-schema.md
(DEC-1787252298 CONSENSUS_REACHED — ob+agy APPROVE incl Amendment 1).

Phase-B scope, and its hard limits:
  * additive `cv4_*` tables in tasks.db; NO existing table touched; NO JSON
    plane written (import is READ-ONLY); nothing arms; no authority cutover.
  * import reconstructs seats/runs/process_instances from registry.json +
    agent-sessions.json + state/agents/*.json.
  * divergences are SURFACED to cv4_import_conflicts, never guessed.
  * the partial unique indexes are created by seal_invariants() ONLY at zero
    unresolved conflicts (Q5: a unique index cannot be created over already-
    violating data — enforcement is the graduation, not the starting state).

Identity model (seat/run/process_instance): §3.1 of the plan. conversation_path
is NEVER stored as authority — it is computed from (instance.project_dir,
run.provider_session_id); see projections.py.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

import sys as _sys
_scripts_dbc = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dbc not in _sys.path:
    _sys.path.insert(0, _scripts_dbc)
import db_connect  # B1-thin: shared tasks.db connect (WAL + 30s busy_timeout)

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR",
                                    os.path.expanduser("~/scripts/agent-orchestra")))
DEFAULT_DB = str(ORCHESTRA_DIR / "state" / "tasks.db")

# Fields compared across the 3 planes; a mismatch on any is a surfaced conflict.
_COHERENCE_FIELDS = ("session_id", "generation", "model")

# The JSON `status` vocabulary is NOT the run `state` vocabulary. An "online"
# agent is an "active" run — mapping it through is what makes the one-active-run
# invariant actually bind (else "online" never matches WHERE state IN
# ('active','guarded_active') and the partial index guards nothing).
_STATUS_TO_STATE = {
    "online": "active", "guarded_active": "guarded_active",
    "provisioning": "provisioning", "quiescent": "quiescent",
    "stopped": "retired", "killed": "retired", "retired": "retired",
}


def _status_to_state(status: str | None) -> str:
    return _STATUS_TO_STATE.get((status or "").lower(), "quiescent")


class InvariantsBlocked(RuntimeError):
    """seal_invariants() refused because unresolved import conflicts remain —
    a partial unique index cannot be created over already-violating data."""


# ------------------------------------------------------------------- schema
# NB: NO unique indexes here — those are created by seal_invariants() only when
# cv4_import_conflicts is clean. Creating them at schema time would throw on the
# very split-brain rows the shadow import exists to surface.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv4_seats (
    seat_id TEXT PRIMARY KEY, tier TEXT,
    active_run_id TEXT, generation_counter INTEGER NOT NULL DEFAULT 0,
    epoch INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_runs (
    run_id TEXT PRIMARY KEY,
    seat_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    provider TEXT NOT NULL DEFAULT 'claude',
    provider_session_id TEXT,
    state TEXT NOT NULL,
    predecessor_run_id TEXT,
    model_policy TEXT,
    sid_source TEXT,                       -- operator-asserted|declared|declared-lineage-fallback
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_process_instances (
    instance_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tmux_session TEXT, pane_id TEXT, pid INTEGER,
    project_dir TEXT,                       -- INV3 home; per-instance
    observed_model TEXT,                    -- OBSERVATION ONLY, never authority
    state TEXT NOT NULL,
    started_at TEXT NOT NULL, last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS cv4_transitions (
    transition_id TEXT PRIMARY KEY, seat_id TEXT NOT NULL,
    predecessor_run_id TEXT, successor_run_id TEXT,
    state TEXT NOT NULL, opened_at_epoch INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_transition_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id TEXT NOT NULL, ts TEXT NOT NULL,
    kind TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_import_conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL, field TEXT NOT NULL,
    registry_value TEXT, sessions_value TEXT, state_agents_value TEXT,
    detected_at TEXT NOT NULL, resolved_at TEXT, resolution TEXT
);
"""

_INVARIANT_INDEXES = {
    "cv4_one_active_run":
        "CREATE UNIQUE INDEX cv4_one_active_run ON cv4_runs(seat_id) "
        "WHERE state IN ('active','guarded_active')",
    "cv4_unique_gen":
        "CREATE UNIQUE INDEX cv4_unique_gen ON cv4_runs(seat_id, generation)",
    "cv4_unique_provider_session":
        "CREATE UNIQUE INDEX cv4_unique_provider_session "
        "ON cv4_runs(provider, provider_session_id) "
        "WHERE provider_session_id IS NOT NULL",
    "cv4_one_open_transition":
        "CREATE UNIQUE INDEX cv4_one_open_transition ON cv4_transitions(seat_id) "
        "WHERE state NOT IN ('complete','aborted','rolled_back')",
}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    con = db_connect.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def ensure_schema(db_path: str = DEFAULT_DB) -> None:
    con = _connect(db_path)
    try:
        con.executescript(_SCHEMA)
        con.commit()
    finally:
        con.close()


# --------------------------------------------------------------- read planes
def _load(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _read_planes(orchestra_dir: str) -> dict:
    d = Path(orchestra_dir)
    reg = _load(d / "registry.json") or {}
    sess = _load(d / "state" / "agent-sessions.json") or {}
    agents = {}
    adir = d / "state" / "agents"
    if adir.is_dir():
        for f in adir.glob("*.json"):
            row = _load(f)
            if isinstance(row, dict):
                agents[f.stem] = row
    return {"registry": (reg.get("agents") or {}), "sessions": sess,
            "state_agents": agents}


# ------------------------------------------------------------------- import
def import_json(*, db_path: str = DEFAULT_DB,
                orchestra_dir: str = str(ORCHESTRA_DIR)) -> dict:
    """READ-ONLY reconstruction of seats/runs/process_instances from the 3 JSON
    planes. Divergences on identity-bearing fields are surfaced to
    cv4_import_conflicts, never resolved. Returns a summary."""
    planes = _read_planes(orchestra_dir)
    reg, sess, st = (planes["registry"], planes["sessions"],
                     planes["state_agents"])
    seats = set(reg) | set(sess) | set(st)
    con = _connect(db_path)
    now = _now()
    imported, conflicts = 0, 0
    try:
        for seat in sorted(seats):
            r = reg.get(seat) or {}
            s = sess.get(seat) or {}
            a = st.get(seat) or {}

            # authority for the run's core identity = registry, then sessions.
            sid = r.get("session_id") or s.get("session_id")
            gen = r.get("generation")
            if gen is None:
                gen = s.get("generation")
            model = r.get("model") or s.get("model")

            con.execute(
                "INSERT OR REPLACE INTO cv4_seats (seat_id, tier, "
                "generation_counter, epoch, created_at, updated_at) "
                "VALUES (?,?,?,0,?,?)",
                (seat, r.get("tier"), gen if isinstance(gen, int) else 0,
                 now, now))

            run_id = "run_" + uuid.uuid4().hex[:12]
            con.execute(
                "INSERT INTO cv4_runs (run_id, seat_id, generation, provider, "
                "provider_session_id, state, model_policy, sid_source, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_id, seat, gen if isinstance(gen, int) else 0, "claude",
                 sid, _status_to_state(r.get("status") or s.get("status")),
                 model, None, now, now))
            con.execute("UPDATE cv4_seats SET active_run_id=? WHERE seat_id=?",
                        (run_id, seat))

            # the live process instance carries project_dir (INV3) + observed model
            con.execute(
                "INSERT INTO cv4_process_instances (instance_id, run_id, "
                "tmux_session, project_dir, observed_model, state, started_at, "
                "last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                ("instance_" + uuid.uuid4().hex[:12], run_id,
                 s.get("tmux_session") or r.get("tmux_session"),
                 s.get("project_dir"), s.get("model_observed"),
                 "live", now, now))
            imported += 1

            # surface divergences — NEVER guess a winner. Two shapes:
            #  (1) classic: >1 distinct non-null value across the planes.
            #  (2) null-on-LIVE (gm residual #1 / "no-data is NOT agreement",
            #      23a680c83): a LIVE seat with a store-that-HAS-a-row carrying a
            #      null identity field while another store carries a value. A
            #      scanner reading that null acts on it (the 06:58 second-spawn
            #      hazard). null-EVERYWHERE on a stopped/retired seat is coherent,
            #      so the live gate is what separates signal from a retired row.
            is_live = "online" in (str(r.get("status")).lower(),
                                   str(s.get("status")).lower())
            row_has = {"registry": seat in reg, "sessions": seat in sess,
                       "state_agents": seat in st}
            for field in _COHERENCE_FIELDS:
                vals = {"registry": r.get(field), "sessions": s.get(field)}
                if field != "model":          # state/agents carries no model
                    vals["state_agents"] = a.get(field)
                non_null = {str(v) for v in vals.values() if v not in (None, "")}
                divergent = len(non_null) > 1
                null_on_live = bool(is_live and non_null and any(
                    row_has.get(store) and vals.get(store) in (None, "")
                    for store in vals))
                if divergent or null_on_live:
                    con.execute(
                        "INSERT INTO cv4_import_conflicts (seat_id, field, "
                        "registry_value, sessions_value, state_agents_value, "
                        "detected_at) VALUES (?,?,?,?,?,?)",
                        (seat, field,
                         str(vals["registry"]) if vals.get("registry") is not None else None,
                         str(vals["sessions"]) if vals.get("sessions") is not None else None,
                         str(vals.get("state_agents")) if vals.get("state_agents") is not None else None,
                         now))
                    conflicts += 1
        con.commit()
    finally:
        con.close()
    return {"seats": len(seats), "runs_imported": imported,
            "conflicts": conflicts}


def unresolved_conflicts(db_path: str = DEFAULT_DB) -> list[dict]:
    con = _connect(db_path); con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM cv4_import_conflicts WHERE resolved_at IS NULL "
            "ORDER BY id")]
    finally:
        con.close()


def resolve_conflict(*, db_path: str = DEFAULT_DB, seat_id: str, field: str,
                     resolution: str) -> None:
    con = _connect(db_path)
    try:
        con.execute("UPDATE cv4_import_conflicts SET resolved_at=?, "
                    "resolution=? WHERE seat_id=? AND field=? "
                    "AND resolved_at IS NULL", (_now(), resolution, seat_id,
                                                field))
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------- seal (Q5 gate)
def seal_invariants(*, db_path: str = DEFAULT_DB) -> dict:
    """Create the partial unique indexes — the DB-level enforcement — ONLY when
    zero unresolved conflicts remain. Refuses otherwise: a unique index cannot
    be created over already-violating import data, and refusing is the honest
    state (the split-brain must be adjudicated first, not indexed away)."""
    open_c = unresolved_conflicts(db_path)
    if open_c:
        raise InvariantsBlocked(
            f"seal REFUSED: {len(open_c)} unresolved import conflict(s) "
            f"({', '.join(sorted({c['seat_id']+'.'+c['field'] for c in open_c}))}). "
            f"A partial unique index cannot be created over violating data; "
            f"adjudicate the conflicts first. NOTHING was created.")
    con = _connect(db_path)
    created = []
    try:
        existing = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        for name, ddl in _INVARIANT_INDEXES.items():
            if name in existing:
                continue
            con.execute(ddl)          # throws IntegrityError if data still violates
            created.append(name)
        con.commit()
    finally:
        con.close()
    return {"sealed": True, "indexes_created": created}
