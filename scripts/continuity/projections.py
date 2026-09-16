"""Continuity v4 — Phase B projections + drift report (SHADOW-ONLY).

projections regenerate the legacy JSON identity fields FROM cv4 (the shadow
authority). conversation_path is COMPUTED here, never read as a stored authority
field: cp := instance.project_dir / (run.provider_session_id + '.jsonl').

drift_report diffs projection-vs-live and reports only UNEXPLAINED divergences:
a field already surfaced in cv4_import_conflicts is EXPECTED to differ (the known
split-brain under adjudication) and is counted as explained, never as drift. The
goal of the 7-day soak is `unexplained == []` — where it is not, either the
projection has a gap (a bug) or a NEW divergence appeared that the importer
should have surfaced.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import sys as _sys
_scripts_dbc = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dbc not in _sys.path:
    _sys.path.insert(0, _scripts_dbc)
import db_connect  # B1-thin: shared tasks.db connect (WAL + 30s busy_timeout)

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR",
                                    os.path.expanduser("~/scripts/agent-orchestra")))
DEFAULT_DB = str(ORCHESTRA_DIR / "state" / "tasks.db")

# state -> the JSON status a projection would emit (inverse of store's map)
_STATE_TO_STATUS = {"active": "online", "guarded_active": "online",
                    "provisioning": "provisioning", "quiescent": "quiescent",
                    "retired": "retired"}


def _connect(db_path: str) -> sqlite3.Connection:
    con = db_connect.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def project_seat(*, db_path: str = DEFAULT_DB, seat_id: str) -> dict | None:
    """Regenerate a seat's identity-bearing fields from the shadow authority."""
    con = _connect(db_path)
    try:
        seat = con.execute("SELECT * FROM cv4_seats WHERE seat_id=?",
                           (seat_id,)).fetchone()
        if not seat:
            return None
        run = con.execute("SELECT * FROM cv4_runs WHERE run_id=?",
                          (seat["active_run_id"],)).fetchone()
        if not run:
            return None
        inst = con.execute(
            "SELECT * FROM cv4_process_instances WHERE run_id=? "
            "ORDER BY started_at DESC LIMIT 1", (run["run_id"],)).fetchone()
        project_dir = inst["project_dir"] if inst else None
        sid = run["provider_session_id"]
        cp = (f"{project_dir}/{sid}.jsonl"
              if project_dir and sid else None)
        return {
            "seat_id": seat_id,
            "session_id": sid,
            "generation": run["generation"],
            "model": run["model_policy"],
            "status": _STATE_TO_STATUS.get(run["state"], run["state"]),
            "project_dir": project_dir,
            "conversation_path": cp,
            "conversation_path_derived": True,
        }
    finally:
        con.close()


def _live_planes(orchestra_dir: str) -> dict:
    d = Path(orchestra_dir)

    def _load(p):
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    reg = (_load(d / "registry.json") or {}).get("agents") or {}
    sess = _load(d / "state" / "agent-sessions.json") or {}
    return {"registry": reg, "sessions": sess}


def _surfaced_fields(con) -> set:
    return {(r["seat_id"], r["field"]) for r in con.execute(
        "SELECT seat_id, field FROM cv4_import_conflicts "
        "WHERE resolved_at IS NULL")}


# fields the projection is responsible for reproducing, and where live truth lives
_PROJECTED = ("session_id", "generation", "model", "project_dir",
              "conversation_path")


def drift_report(*, db_path: str = DEFAULT_DB,
                 orchestra_dir: str = str(ORCHESTRA_DIR)) -> dict:
    live = _live_planes(orchestra_dir)
    con = _connect(db_path)
    try:
        surfaced = _surfaced_fields(con)
        seat_ids = [r["seat_id"] for r in con.execute(
            "SELECT seat_id FROM cv4_seats ORDER BY seat_id")]
    finally:
        con.close()

    unexplained, explained = [], 0
    for seat in seat_ids:
        proj = project_seat(db_path=db_path, seat_id=seat)
        if not proj:
            continue
        s = live["sessions"].get(seat) or {}
        r = live["registry"].get(seat) or {}
        for field in _PROJECTED:
            # live truth: sessions carries project_dir/conversation_path; the
            # identity fields live in both — compare against sessions first,
            # then registry, matching the store's import precedence.
            live_val = s.get(field)
            if live_val in (None, "") and field in ("session_id", "generation",
                                                    "model"):
                live_val = r.get(field)
            proj_val = proj.get(field)
            if live_val in (None, ""):
                continue                      # nothing live to reproduce
            if str(proj_val) == str(live_val):
                continue
            if (seat, field) in surfaced:
                explained += 1                # known split-brain, being adjudicated
                continue
            unexplained.append({"seat_id": seat, "field": field,
                                "projected": proj_val, "live": live_val})
    return {"unexplained": unexplained,
            "explained_by_conflict": explained,
            "seats_checked": len(seat_ids)}
