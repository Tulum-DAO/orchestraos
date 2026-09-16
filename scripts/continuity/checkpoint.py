"""Continuity v4 — Phase A checkpoint store (SPEC_continuity-v4-implementation-plan.md §3.2).

Semantic continuity maintained DURING the run, never reconstructed at death.
Additive `cv4_*` tables inside the existing tasks.db (mail lives there, so the
eventual Phase-E cutover can move identity+work+mail in ONE transaction). No
existing table is touched.

The validation gates ARE the product: an active mission without WHY + success
criteria, a task without why/done_when/next_action, a decision without
rationale + authority, an obligation without owner + transfer_policy is
REFUSED before any connection is opened — a refusal leaves the store
byte-identical. That refusal discipline is what makes the compiled packet
trustworthy enough to replace transcript archaeology on rotation.

Ruling context: the operator 2026-08-20 ("Begin all of Step 1"); builder =
lineage-v3-auditor (author!=verifier waived by the operator for this lane).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import sys as _sys
_scripts_dbc = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dbc not in _sys.path:
    _sys.path.insert(0, _scripts_dbc)
import db_connect  # B1-thin: shared tasks.db connect (WAL + 30s busy_timeout)

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR",
                                    os.path.expanduser("~/scripts/agent-orchestra")))
DEFAULT_DB = str(ORCHESTRA_DIR / "state" / "tasks.db")

PACKET_CHARS_PER_TOKEN = 4          # conservative estimate used for the budget


class ValidationRefused(RuntimeError):
    """Raised BEFORE any write when a semantic row is missing load-bearing
    fields. NOTHING is written on refusal."""


# --------------------------------------------------------------------- schema
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cv4_missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    why TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    non_goals TEXT,
    guardrails TEXT,
    authority TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    mission_id INTEGER,
    title TEXT NOT NULL,
    why TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    next_action TEXT NOT NULL,
    done_when TEXT NOT NULL,
    blockers TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    authority TEXT NOT NULL,
    alternatives_rejected TEXT,
    consequences TEXT,
    superseded_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_obligations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    owner TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    transfer_policy TEXT NOT NULL,
    due TEXT,
    ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    event_cursor INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cv4_gate_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_id TEXT NOT NULL,
    gate TEXT NOT NULL,                 -- e.g. cold_orient
    ts TEXT NOT NULL,
    packet_hash TEXT NOT NULL,
    packet_tokens INTEGER,
    subagent_tool_uses INTEGER,         -- 0 proves no repo access (cold)
    trap_outcomes_json TEXT,            -- [{trap, honest:bool}]
    verdict TEXT NOT NULL,              -- PASS|FAIL
    transcript_ref TEXT,                -- path/id to the subagent transcript
    packet_text TEXT,                   -- the EXACT bytes oriented from (audit)
    transcript_text TEXT,               -- the subagent's full Q&A (Task jsonl
                                        -- are not durably on disk, so persist
                                        -- inline for independent audit)
    notes TEXT
);
CREATE INDEX IF NOT EXISTS cv4_events_seat ON cv4_events(seat_id, seq);
"""


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
        # additive migration: a cv4_gate_records created before packet_text/
        # transcript_text existed must gain them (ADD COLUMN is safe + additive).
        have = {r[1] for r in con.execute("PRAGMA table_info(cv4_gate_records)")}
        for col in ("packet_text", "transcript_text"):
            if col not in have:
                con.execute(f"ALTER TABLE cv4_gate_records ADD COLUMN {col} TEXT")
        con.commit()
    finally:
        con.close()


# ----------------------------------------------------------------- validation
def _require(fields: dict, required: tuple, what: str) -> None:
    """Refuse BEFORE any connection — a refusal must leave the store
    byte-identical."""
    missing = [k for k in required
               if not str(fields.get(k) or "").strip()]
    if missing:
        raise ValidationRefused(
            f"{what} REFUSED — missing load-bearing field(s) {missing}. "
            f"A {what} without them is exactly the hollow state that forces a "
            f"successor back into transcript archaeology. NOTHING was written.")


def _event(con: sqlite3.Connection, seat: str, kind: str, payload: dict) -> None:
    con.execute("INSERT INTO cv4_events (seat_id, ts, kind, payload_json) "
                "VALUES (?,?,?,?)",
                (seat, _now(), kind, json.dumps(payload, sort_keys=True)))


# --------------------------------------------------------------------- writes
def mission_set(seat: str, *, db_path: str = DEFAULT_DB, objective=None,
                why=None, success_criteria=None, non_goals=None,
                guardrails=None, authority=None) -> int:
    f = dict(objective=objective, why=why, success_criteria=success_criteria)
    _require(f, ("objective", "why", "success_criteria"), "mission")
    con = _connect(db_path)
    try:
        now = _now()
        con.execute("UPDATE cv4_missions SET status='superseded', updated_at=? "
                    "WHERE seat_id=? AND status='active'", (now, seat))
        cur = con.execute(
            "INSERT INTO cv4_missions (seat_id, objective, why, "
            "success_criteria, non_goals, guardrails, authority, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?, 'active', ?, ?)",
            (seat, objective, why, success_criteria, non_goals, guardrails,
             authority, now, now))
        _event(con, seat, "mission_set",
               {"id": cur.lastrowid, "objective": objective, "why": why})
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def work_set(seat: str, *, db_path: str = DEFAULT_DB, title=None, why=None,
             next_action=None, done_when=None, blockers=None,
             mission_id=None) -> int:
    f = dict(title=title, why=why, next_action=next_action,
             done_when=done_when)
    _require(f, ("title", "why", "next_action", "done_when"), "work item")
    con = _connect(db_path)
    try:
        now = _now()
        cur = con.execute(
            "INSERT INTO cv4_work_items (seat_id, mission_id, title, why, "
            "state, next_action, done_when, blockers, created_at, updated_at) "
            "VALUES (?,?,?,?, 'active', ?,?,?,?,?)",
            (seat, mission_id, title, why, next_action, done_when, blockers,
             now, now))
        _event(con, seat, "work_set",
               {"id": cur.lastrowid, "title": title, "why": why,
                "next_action": next_action})
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def work_update(seat: str, work_id: int, *, db_path: str = DEFAULT_DB,
                state=None, next_action=None, blockers=None) -> None:
    con = _connect(db_path)
    try:
        now = _now()
        sets, vals = ["updated_at=?"], [now]
        for col, v in (("state", state), ("next_action", next_action),
                       ("blockers", blockers)):
            if v is not None:
                sets.append(f"{col}=?")
                vals.append(v)
        vals += [seat, work_id]
        con.execute(f"UPDATE cv4_work_items SET {', '.join(sets)} "
                    f"WHERE seat_id=? AND id=?", vals)
        _event(con, seat, "work_update",
               {"id": work_id, "state": state, "next_action": next_action})
        con.commit()
    finally:
        con.close()


def decision_add(seat: str, *, db_path: str = DEFAULT_DB, decision=None,
                 rationale=None, authority=None, alternatives_rejected=None,
                 consequences=None) -> int:
    f = dict(decision=decision, rationale=rationale, authority=authority)
    _require(f, ("decision", "rationale", "authority"), "decision")
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO cv4_decisions (seat_id, decision, rationale, "
            "authority, alternatives_rejected, consequences, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (seat, decision, rationale, authority, alternatives_rejected,
             consequences, _now()))
        _event(con, seat, "decision_add",
               {"id": cur.lastrowid, "decision": decision,
                "rationale": rationale})
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def obligation_add(seat: str, *, db_path: str = DEFAULT_DB, kind=None,
                   description=None, owner=None, transfer_policy=None,
                   due=None, ref=None) -> int:
    f = dict(kind=kind, description=description, owner=owner,
             transfer_policy=transfer_policy)
    _require(f, ("kind", "description", "owner", "transfer_policy"),
             "obligation")
    con = _connect(db_path)
    try:
        now = _now()
        cur = con.execute(
            "INSERT INTO cv4_obligations (seat_id, kind, description, owner, "
            "state, transfer_policy, due, ref, created_at, updated_at) "
            "VALUES (?,?,?,?, 'open', ?,?,?,?,?)",
            (seat, kind, description, owner, transfer_policy, due, ref,
             now, now))
        _event(con, seat, "obligation_add",
               {"id": cur.lastrowid, "kind": kind, "description": description})
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def obligation_close(seat: str, obligation_id: int, *,
                     db_path: str = DEFAULT_DB, state: str = "done") -> None:
    con = _connect(db_path)
    try:
        con.execute("UPDATE cv4_obligations SET state=?, updated_at=? "
                    "WHERE seat_id=? AND id=?",
                    (state, _now(), seat, obligation_id))
        _event(con, seat, "obligation_close",
               {"id": obligation_id, "state": state})
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------- reads
def _rows(con, sql, args) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args)]


def continuity_status(seat: str, *, db_path: str = DEFAULT_DB) -> dict:
    con = _connect(db_path)
    try:
        missions = _rows(con, "SELECT * FROM cv4_missions WHERE seat_id=? "
                              "AND status='active' ORDER BY id", (seat,))
        work = _rows(con, "SELECT * FROM cv4_work_items WHERE seat_id=? "
                          "AND state IN ('active','blocked') ORDER BY id",
                     (seat,))
        decisions = _rows(con, "SELECT * FROM cv4_decisions WHERE seat_id=? "
                               "AND superseded_by IS NULL ORDER BY id",
                          (seat,))
        obligations = _rows(con, "SELECT * FROM cv4_obligations WHERE "
                                 "seat_id=? AND state='open' ORDER BY id",
                            (seat,))
        cursor = con.execute("SELECT COALESCE(MAX(seq),0) FROM cv4_events "
                             "WHERE seat_id=?", (seat,)).fetchone()[0]
        gaps = []
        if not missions:
            gaps.append("no active mission (objective/why/success_criteria)")
        if not work:
            gaps.append("no active work item (title/why/next_action/done_when)")
        return {"seat": seat, "missions": missions, "work_items": work,
                "decisions": decisions, "obligations": obligations,
                "event_cursor": cursor, "gaps": gaps}
    finally:
        con.close()


def delta_since(seat: str, cursor: int, *, db_path: str = DEFAULT_DB) -> list:
    con = _connect(db_path)
    try:
        return [{"seq": s, "ts": t, "kind": k, "payload": json.loads(p)}
                for s, t, k, p in con.execute(
                    "SELECT seq, ts, kind, payload_json FROM cv4_events "
                    "WHERE seat_id=? AND seq>? ORDER BY seq", (seat, cursor))]
    finally:
        con.close()


# ---------------------------------------------------- checkpoint + packet
def _runtime_harvest() -> dict:
    """Mechanical, best-effort, never fatal — agents contribute semantics,
    the machine harvests mechanics."""
    out = {}
    try:
        out["head"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ORCHESTRA_DIR,
            capture_output=True, text=True, timeout=5).stdout.strip()
        out["branch"] = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ORCHESTRA_DIR,
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    return out


def record_gate(seat: str, *, db_path: str = DEFAULT_DB, gate: str,
                packet_hash: str, verdict: str, packet_tokens: int = None,
                subagent_tool_uses: int = None, trap_outcomes: list = None,
                transcript_ref: str = None, packet_text: str = None,
                transcript_text: str = None, notes: str = None) -> int:
    """Persist a gate result so a PASS is INDEPENDENTLY auditable, not
    supervisor-trust (gm Phase-B DoD, 2026-08-20). The packet_hash pins WHICH
    bytes; subagent_tool_uses=0 is machine proof of no repo access; and because
    Task subagent jsonl are not durably on disk, packet_text + transcript_text
    are persisted INLINE so an auditor other than the runner can re-check the
    exact packet and the subagent's answers/trap outcomes.

    If packet_text is given, packet_hash is verified against it (a record whose
    stored bytes don't match its own hash is not evidence)."""
    if packet_text is not None:
        actual = hashlib.sha256(packet_text.encode()).hexdigest()
        if packet_hash and packet_hash != actual:
            raise ValidationRefused(
                f"gate record REFUSED: packet_hash {packet_hash!r} does not "
                f"match sha256(packet_text)={actual!r} — a self-inconsistent "
                f"audit record is not evidence. NOTHING was written.")
        packet_hash = packet_hash or actual
    con = _connect(db_path)
    try:
        cur = con.execute(
            "INSERT INTO cv4_gate_records (seat_id, gate, ts, packet_hash, "
            "packet_tokens, subagent_tool_uses, trap_outcomes_json, verdict, "
            "transcript_ref, packet_text, transcript_text, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (seat, gate, _now(), packet_hash, packet_tokens, subagent_tool_uses,
             json.dumps(trap_outcomes or []), verdict, transcript_ref,
             packet_text, transcript_text, notes))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def gate_records(seat: str = None, *, db_path: str = DEFAULT_DB) -> list[dict]:
    con = _connect(db_path)
    try:
        if seat:
            return _rows(con, "SELECT * FROM cv4_gate_records WHERE seat_id=? "
                              "ORDER BY id", (seat,))
        return _rows(con, "SELECT * FROM cv4_gate_records ORDER BY id", ())
    finally:
        con.close()


def checkpoint_compile(seat: str, *, db_path: str = DEFAULT_DB) -> dict:
    """Pure read: canonical semantic state + deterministic hash + cursor.
    Timestamps of compilation are OUTSIDE the hashed content so identical
    state always hashes identically."""
    st = continuity_status(seat, db_path=db_path)
    content = {k: st[k] for k in ("missions", "work_items", "decisions",
                                  "obligations")}
    blob = json.dumps(content, sort_keys=True, default=str)
    return {"seat": seat,
            "content": content,
            "content_hash": hashlib.sha256(blob.encode()).hexdigest(),
            "event_cursor": st["event_cursor"]}


def checkpoint_save(seat: str, *, db_path: str = DEFAULT_DB) -> dict:
    c = checkpoint_compile(seat, db_path=db_path)
    con = _connect(db_path)
    try:
        con.execute("INSERT INTO cv4_checkpoints (seat_id, ts, content_json, "
                    "content_hash, event_cursor) VALUES (?,?,?,?,?)",
                    (seat, _now(), json.dumps(c["content"], default=str),
                     c["content_hash"], c["event_cursor"]))
        con.commit()
        return c
    finally:
        con.close()


def packet_compile(seat: str, *, db_path: str = DEFAULT_DB,
                   budget_tokens: int = 4000) -> str:
    """The successor-orientation packet. Mission + active work + open
    obligations are MANDATORY and always survive; decisions fill the remaining
    budget newest-first (oldest truncate first). Hard char cap enforced."""
    st = continuity_status(seat, db_path=db_path)
    ck = checkpoint_compile(seat, db_path=db_path)
    cap = budget_tokens * PACKET_CHARS_PER_TOKEN

    lines = [f"# CONTINUITY PACKET — seat {seat}",
             f"checkpoint_hash: {ck['content_hash']}",
             f"event_cursor: {ck['event_cursor']}", ""]
    lines.append("## MISSION")
    for m in st["missions"]:
        lines += [f"- objective: {m['objective']}",
                  f"  why: {m['why']}",
                  f"  success: {m['success_criteria']}"]
        if m.get("non_goals"):
            lines.append(f"  non-goals: {m['non_goals']}")
        if m.get("guardrails"):
            lines.append(f"  guardrails: {m['guardrails']}")
        if m.get("authority"):
            lines.append(f"  authority: {m['authority']}")
    lines.append("")
    lines.append("## ACTIVE WORK")
    if not st["work_items"]:
        # Pilot finding: an empty mandatory section must not read as silence
        # (compiler failure?) — a successor needs to know the absence is real.
        lines.append("- (none recorded — no active task; the mission's success "
                     "criteria define the objective. A rotation with no active "
                     "work item means the next occupant must set one.)")
    for w in st["work_items"]:
        lines += [f"- [{w['state']}] {w['title']}",
                  f"  why: {w['why']}",
                  f"  NEXT ACTION: {w['next_action']}",
                  f"  done when: {w['done_when']}"]
        if w.get("blockers"):
            lines.append(f"  blockers: {w['blockers']}")
    lines.append("")
    lines.append("## OPEN OBLIGATIONS")
    if not st["obligations"]:
        lines.append("- (none recorded)")
    for o in st["obligations"]:
        lines.append(f"- ({o['kind']}, owner {o['owner']}, "
                     f"{o['transfer_policy']}) {o['description']}")
    rt = _runtime_harvest()
    if rt:
        lines += ["", "## RUNTIME",
                  f"- branch {rt.get('branch')} @ {rt.get('head')}"]

    mandatory = "\n".join(lines)
    if len(mandatory) > cap:
        return mandatory[:cap]

    dec_lines = ["", "## DECISIONS (newest first)"]
    body = mandatory
    for d in reversed(st["decisions"]):
        cand = (f"- {d['decision']} | why: {d['rationale']} | "
                f"authority: {d['authority']}")
        if d.get("alternatives_rejected"):
            cand += f" | rejected: {d['alternatives_rejected']}"
        trial = body + "\n".join(dec_lines + [cand])
        if len(trial) > cap:
            break
        dec_lines.append(cand)
    if len(dec_lines) > 2:
        body = body + "\n".join(dec_lines)
    return body[:cap]
