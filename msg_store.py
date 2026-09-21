#!/usr/bin/env python3
"""
msg_store.py — SQLite-backed inter-agent message store.

Replaces both file-based inbox/outbox AND message_bus.py JSONL.
Single source of truth for all inter-agent communication.

Usage:
    from msg_store import MessageStore
    store = MessageStore()

    # Send a message
    msg_id = store.send(
        from_agent="gm", to_agent="pm-products",
        type="route", subject="Build the UI",
        body="Full description...", priority="high"
    )

    # Read inbox (pending messages for an agent)
    messages = store.inbox("pm-products")

    # Claim a message for processing (atomic)
    claimed = store.claim(msg_id)

    # Mark as delivered
    store.deliver(msg_id)

    # Mark as acknowledged (agent confirmed receipt)
    store.acknowledge(msg_id)

    # Archive (done processing)
    store.archive(msg_id)

    # Mark as failed (will retry)
    store.fail(msg_id, error="Agent not running")

    # Query messages
    messages = store.query(to_agent="gm", type="task_request", status="pending")

    # Get conversation thread
    thread = store.thread(conversation_id)

    # CLI mode
    python3 msg_store.py send --from gm --to pm-products --type route --subject "Build UI" --body "..."
    python3 msg_store.py inbox --agent pm-products
    python3 msg_store.py get --id msg_xxx
    python3 msg_store.py ack --id msg_xxx
    python3 msg_store.py stats
"""

import sqlite3
import json
import os
import sys
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ORCHESTRA_DIR = Path(os.environ.get("ORCHESTRA_DIR") or os.environ.get("ORCH_DIR") or os.path.expanduser("~/orchestra"))
DB_PATH = ORCHESTRA_DIR / "state" / "tasks.db"


def _now():
    return datetime.now(tz=timezone.utc).isoformat()


def _gen_id(prefix="msg"):
    import random
    return f"{prefix}_{random.randrange(16**8):08x}_{int(datetime.now(tz=timezone.utc).timestamp()*1000) % 10**8}"



# --- leg-4 act 3: send-time addressability (DEC-1787052528 CONSENSUS_REACHED) ---
# Mail addressed to an identity that cannot receive it used to be discovered 34
# hours later, or never. The sender now learns in milliseconds. SHIPS SHADOW:
# ADDRESSABILITY_REFUSE defaults OFF and arming is gm+the operator's (gm bind a).
#
# FAIL OPEN, deliberately the inverse of leg-3's fail-closed delivery guard:
#
#     A DELIVERY GUARD THAT FAILS CLOSED **DELAYS** A MESSAGE;
#     A SEND REFUSAL THAT FAILS CLOSED **DESTROYS** IT AT THE SOURCE.
#
# (quoted verbatim per gm msg_26a4171f — that asymmetry is the thing a future
# agent will otherwise "fix" into symmetry). Any classifier error allows the send.
ADDRESSABILITY_KILL_FILE = Path.home() / "runtime" / "ADDRESSABILITY_DISABLED"
# Env-overridable so a TEST RUN can never write into the production evidence file.
# Found the hard way: my own pytest runs put 47 fixture lines into the real shadow
# log within minutes, and that log is exactly what gm reads to decide arming — a
# census polluted by test senders is worse than no census.
ADDRESSABILITY_LOG_FILE = Path(
    os.environ.get("ADDRESSABILITY_LOG_FILE")
    or (Path(__file__).resolve().parent / "logs" / "addressability-shadow.log"))


class ImpersonatedSender(RuntimeError):
    """Armed refusal: the calling process is not the agent it claims to be."""


class SelfAddressedRow(RuntimeError):
    """Armed refusal: sender and recipient are identical. initiative-architect's
    framing — 'a self-addressed row is not a delivery, it is a contradiction' — and
    the history agrees: 16 such rows ever, ZERO with a body over 200 chars, 15 of
    them spacers. The single substantive one deliberately planted a stale row to
    verify the queue-drain hook by effect, which is why the escape exists."""


def sender_identity():
    """The calling process's OWN agent id, or None when it cannot be established.

    Derived from the tmux pane the caller runs in. None for cron jobs, daemons and
    hooks — which legitimately send under other identities (message-router,
    lineage-daemon, approval-loop), so an unknown caller must never be treated as a
    spoof."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        import subprocess
        out = subprocess.run(["tmux", "display-message", "-p", "-t", pane,
                              "#{session_name}"], capture_output=True, text=True,
                             timeout=5)
        name = (out.stdout or "").strip()
        return name or None
    except Exception:      # noqa: BLE001 — identity is best-effort, never blocking
        return None


TRIVIAL_BODY_CHARS = 3


# --------------------------------------------------------------- G3 source gate
# the operator directive 2026-08-19: reactive dispatch drags lanes off course and burns
# fleet context. The brake belongs on the SENDER, before the row exists — a
# receiver cannot un-read a lane it was pulled into.
#
# DRIVE_TYPES is enumerated from the types ACTUALLY in the store, not imagined:
# reply(261) task_request(92) decision(47) message(25) report(16) ruling(8)...
# Only the ones that push WORK INTO a lane are drive-class.
#
# The must-not-fire case IS the design: a `reply` answering a blocked agent must
# never be braked. A brake that throttles answers starves exactly the agents
# waiting on a ruling — it converts a flood into a deadlock, which is worse.
DRIVE_TYPES = frozenset({
    "task_request", "ruling", "directive", "dispatch", "commission", "request",
})
BRAKE_UNACKED_MAX = 2


# ---------------------------------------------- G3 leg 1 contract: the bootstrap
# orchestra-builder, 2026-08-19: a successor's DURABLE SPAWN ORDERS are
# `task_request` — drive-class. And ORIENTATION MAIL CANNOT CARRY A LANE TAG BY
# NATURE, because the recipient does not yet know its lane. So a charter-based
# gate holds precisely the message that would tell a newborn what its charter is.
# That is a bootstrap paradox, not an edge case.
#
# It is also the SECOND mechanism aimed at the same victim: the router already
# holds all inbound to a busy agent, and a successor is busy from birth — ob's own
# spawn orders sat PENDING and it only had them because it polled the store. A
# charter gate would add a second, independent reason the same mail never lands.
#
# This predicate is the CONTRACT that G3 leg 1 (charter check) must honour. It is
# defined here, with tests, so leg 1 cannot be built without tripping over it.
ORIENTATION_TYPES = frozenset({
    "spawn_orders", "orientation", "handoff", "handoff_confirmation",
    "lineage_soft_handoff", "canary", "canaries",
})


def charter_exempt(msg_type, to_agent, *, recipient_has_charter=True):
    """Is this send exempt from a charter check? Orientation always is.

    Two independent grounds, either sufficient:
      1. the type is orientation/spawn mail — it CREATES the lane it would be
         judged against;
      2. the recipient has no charter yet — a newborn cannot match a predicate
         over a field it does not have, and refusing it is unverifiable-as-refusal.
    """
    t = str(msg_type or "").strip().lower()
    if t in ORIENTATION_TYPES:
        return {"exempt": True, "reason": f"orientation type {t!r} — creates the lane"}
    if not recipient_has_charter:
        return {"exempt": True,
                "reason": f"{to_agent!r} has no charter yet — a newborn cannot be "
                          f"judged against a lane it has not been told"}
    return {"exempt": False, "reason": "charter check applies"}


def outbound_brake(from_agent, to_agent, msg_type, *, rows=None):
    """Would a new DRIVE-class send pile onto a lane that has not read me yet?

    Returns a verdict dict; NEVER raises and never blocks on its own. Arming is a
    separate decision, because a first-version guard is broken in the negative
    direction and this one would sit in the path of every send in the fleet.

    `rows` (iterable of (to_agent, type, status)) is injectable so the predicate
    can be measured against live history without touching the store.
    """
    verdict = {"drive": False, "unacked": 0, "would_hold": False, "reason": None}
    try:
        if str(msg_type or "").strip().lower() not in DRIVE_TYPES:
            verdict["reason"] = f"not drive-class ({msg_type!r}) — never braked"
            return verdict
        verdict["drive"] = True
        if rows is None:
            conn = MessageStore()._conn()
            try:
                rows = conn.execute(
                    "SELECT to_agent, type, status FROM messages "
                    "WHERE from_agent = ? AND status = 'pending'",
                    (from_agent,)).fetchall()
            finally:
                conn.close()
        n = sum(1 for r in rows
                if (r[0] == to_agent
                    and str(r[1] or "").strip().lower() in DRIVE_TYPES
                    and str(r[2] or "") == "pending"))
        verdict["unacked"] = n
        if n >= BRAKE_UNACKED_MAX:
            verdict["would_hold"] = True
            verdict["reason"] = (
                f"{n} unacked drive-class send(s) from {from_agent!r} already "
                f"sitting in {to_agent!r}'s lane — it has not read the last one. "
                f"Wait for an ack, or fold this into the outstanding thread.")
        else:
            verdict["reason"] = f"{n} unacked drive-class send(s) — under the brake"
    except Exception:          # noqa: BLE001 — fail OPEN, never block a send
        verdict["reason"] = "brake unavailable — failing open"
    return verdict


_UNSET = object()


def sender_verdict(from_agent, to_agent, body, caller=_UNSET):
    """Three independently checkable smells on a send (gm's incident msg_b4b49741,
    real rows msg_8e001e7e / msg_4c415da8):

      impersonated   — the caller is identifiable and is NOT from_agent
      self_addressed — from == to (an agent routing mail to itself)
      trivial_body   — a body with no content

    Worth noting which one actually caught gm's rows: the CHEAP ones. Identity
    inference is impossible for daemons and cron, but 'self-addressed with a
    one-character body' needs no inference at all. Prefer the check that works
    where the expensive one cannot."""
    if caller is _UNSET:
        caller = sender_identity()
    impersonated = bool(caller and from_agent and caller != from_agent)
    self_addressed = bool(from_agent and from_agent == to_agent)
    trivial = len(str(body or "").strip()) < TRIVIAL_BODY_CHARS
    return {"caller": caller, "impersonated": impersonated,
            "self_addressed": self_addressed, "trivial_body": trivial,
            # self-addressed is its own refusal shape: it needs no trivial-body
            # qualifier, because a row from an agent to itself is a contradiction
            # rather than a delivery.
            "suspect": bool(impersonated or self_addressed)}


def _impersonation_armed() -> bool:
    if ADDRESSABILITY_KILL_FILE.exists():
        return False
    return os.environ.get("IMPERSONATION_REFUSE", "").strip().lower() in (
        "1", "true", "yes", "on")


class UnaddressableTarget(RuntimeError):
    """Armed refusal: the target cannot receive pane-injected mail. Names the
    class so the sender learns WHY instantly instead of 34 hours later."""


def _addressability_armed() -> bool:
    if ADDRESSABILITY_KILL_FILE.exists():
        return False
    return os.environ.get("ADDRESSABILITY_REFUSE", "").strip().lower() in (
        "1", "true", "yes", "on")


def _addressability_log_path() -> Path:
    override = os.environ.get("ADDRESSABILITY_LOG_FILE")
    return Path(override) if override else ADDRESSABILITY_LOG_FILE


def _addressability_log(line: str) -> None:
    try:
        path = _addressability_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(f"{_now()} {line}\n")
    except OSError:
        pass          # observability must never break the write path


def _addressability_verdict(target: str):
    """None = classification unavailable/disabled -> behave exactly as before."""
    if ADDRESSABILITY_KILL_FILE.exists():
        return None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_addressability",
            Path(__file__).resolve().parent / "scripts" / "addressability.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.classify(target, log=_addressability_log)
    except Exception:      # noqa: BLE001 — fail OPEN (see the asymmetry above)
        return None


class MessageStore:
    def __init__(self, db_path=None):
        # MSG_DB_PATH env mirror (F4 fix, spec §3.1): scopes the message bus the
        # same way APPROVAL_DB_PATH scopes the approval store (approval.py:18).
        # Precedence: explicit db_path arg > MSG_DB_PATH env > live DB_PATH.
        # UNSET (or empty) => DB_PATH => byte-identical to prior behavior. Read at
        # the constructor so ALL no-arg MessageStore() constructions are scoped at
        # once (incl. approval_resume._msg_store, the C5b resume-leak hole).
        self.db_path = str(db_path or os.environ.get("MSG_DB_PATH") or DB_PATH)

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA wal_autocheckpoint=100")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def migrate(self):
        """Add missing columns to messages, conversations, tasks tables; create agent_hierarchy and roadmap tables."""
        conn = self._conn()
        try:
            # Check and add columns to messages
            msg_cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
            if "depends_on" not in msg_cols:
                conn.execute("ALTER TABLE messages ADD COLUMN depends_on TEXT")
            if "gather_mode" not in msg_cols:
                conn.execute("ALTER TABLE messages ADD COLUMN gather_mode TEXT DEFAULT 'gather_all'")
            if "tenant_id" not in msg_cols:
                conn.execute("ALTER TABLE messages ADD COLUMN tenant_id TEXT DEFAULT 'operator'")

            # Check and add columns to conversations
            conv_cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
            if "mode" not in conv_cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN mode TEXT DEFAULT 'fire_and_forget'")
            if "max_iterations" not in conv_cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN max_iterations INTEGER DEFAULT 1")
            if "iteration_count" not in conv_cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN iteration_count INTEGER DEFAULT 0")
            if "completion_condition" not in conv_cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN completion_condition TEXT")
            if "status" not in conv_cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN status TEXT DEFAULT 'open'")
            if "tenant_id" not in conv_cols:
                conn.execute("ALTER TABLE conversations ADD COLUMN tenant_id TEXT DEFAULT 'operator'")

            # Check and add columns to tasks (task-message integration)
            # tasks table may not exist in test DBs that only bootstrap messages/conversations
            task_table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()
            if task_table_exists:
                task_cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
                if "message_id" not in task_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN message_id TEXT")
                if "conversation_id" not in task_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN conversation_id TEXT")
                if "roadmap_phase_id" not in task_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN roadmap_phase_id TEXT")
                if "tenant_id" not in task_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'operator'")

            # Create agent_hierarchy table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_hierarchy (
                    agent_id        TEXT NOT NULL,
                    tenant_id       TEXT NOT NULL DEFAULT 'operator',
                    parent_id       TEXT,
                    project         TEXT,
                    role            TEXT,
                    always_on       BOOLEAN DEFAULT 0,
                    spawned_at      TEXT,
                    stopped_at      TEXT,
                    status          TEXT DEFAULT 'running',
                    generation      INTEGER DEFAULT 1,
                    task_id         TEXT,
                    conversation_id TEXT,
                    last_active     TEXT,
                    metadata        TEXT,
                    PRIMARY KEY (agent_id, tenant_id)
                )
            """)

            # Indexes for common query patterns
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_tenant   ON agent_hierarchy(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_parent   ON agent_hierarchy(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hierarchy_status   ON agent_hierarchy(status)")

            # Roadmap tables (Task 4)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roadmaps (
                    id              TEXT PRIMARY KEY,
                    tenant_id       TEXT NOT NULL DEFAULT 'operator',
                    project         TEXT NOT NULL,
                    pm_agent        TEXT NOT NULL,
                    title           TEXT,
                    vision          TEXT,
                    status          TEXT DEFAULT 'draft',
                    current_phase   INTEGER DEFAULT 0,
                    created_from    TEXT,
                    approved_at     TEXT,
                    created_at      TEXT,
                    updated_at      TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roadmap_phases (
                    id              TEXT PRIMARY KEY,
                    roadmap_id      TEXT NOT NULL,
                    phase_number    INTEGER NOT NULL,
                    title           TEXT,
                    description     TEXT,
                    status          TEXT DEFAULT 'pending',
                    milestone       TEXT,
                    started_at      TEXT,
                    completed_at    TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS roadmap_tasks (
                    id                  TEXT PRIMARY KEY,
                    phase_id            TEXT NOT NULL,
                    task_id             TEXT,
                    agent_id            TEXT,
                    dependency_tasks    TEXT,
                    status              TEXT DEFAULT 'pending'
                )
            """)

            # Indexes for roadmap queries
            conn.execute("CREATE INDEX IF NOT EXISTS idx_roadmaps_tenant    ON roadmaps(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_roadmaps_project   ON roadmaps(project)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rphases_roadmap    ON roadmap_phases(roadmap_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rtasks_phase       ON roadmap_tasks(phase_id)")

            conn.commit()
        finally:
            conn.close()

    def send(self, from_agent: str, to_agent: str, type: str,
             subject: str = None, body: str = None, priority: str = "medium",
             task_id: str = None, conversation_id: str = None, parent_id: str = None,
             source: str = "system", metadata: dict = None, msg_id: str = None,
             depends_on: list = None, gather_mode: str = "gather_all",
             tenant_id: str = "operator", closes_thread: bool = False,
             reason: str = None, contributes_to: str = None,
             allow_unaddressable: bool = False,
             allow_self_addressed: bool = False) -> str:
        """Send a message. Returns message ID.

        reason / contributes_to (§9.6-B.4, DEC-1787032722): the sender-side
        envelope for drive-class sends — machine-readable justification +
        initiative/node/mission ref, riding metadata (additive; router lint is
        flag-only and lives router-side)."""
        msg_id = msg_id or _gen_id()
        if not conversation_id:
            conversation_id = f"conv_{hashlib.md5(f'{from_agent}:{to_agent}:{task_id or subject or msg_id}'.encode()).hexdigest()[:12]}"

        # Merge closes_thread + envelope keys into metadata if requested
        if closes_thread or reason or contributes_to:
            metadata = dict(metadata) if metadata else {}
            if closes_thread:
                metadata["closes_thread"] = True
            if reason:
                metadata["reason"] = reason
            if contributes_to:
                metadata["contributes_to"] = contributes_to

        # leg-4: the SENDER side. gm wrote junk rows under initiative-architect's
        # identity by copying flags without reading them, and msg_store accepted an
        # impersonated, self-addressed, one-character row with zero validation.
        # SHADOW first; fails OPEN on an unidentifiable caller, because cron,
        # daemons and hooks legitimately send under other ids.
        try:
            _sv = sender_verdict(from_agent, to_agent, body)
        except Exception:      # noqa: BLE001
            _sv = None
        if _sv and _sv["self_addressed"] and allow_self_addressed:
            metadata = dict(metadata) if metadata else {}
            metadata["self_addressed_override"] = True
            _addressability_log(
                f"[self-addressed] DELIBERATE {msg_id} ({from_agent} -> itself) "
                f"— recorded, not silent")
            _sv = None
        if _sv and _sv["suspect"]:
            _why = (f"caller={_sv['caller']} claims from={from_agent}"
                    if _sv["impersonated"]
                    else f"self-addressed ({from_agent}) with a trivial body")
            # Impersonation is diagnosed FIRST when both hold: it names the
            # actual culprit (a caller writing under someone else's identity),
            # where self-addressed only describes the row's shape. gm's rows are
            # both, and "you are not who you claim" is the more useful error.
            if _impersonation_armed() and _sv["impersonated"]:
                raise ImpersonatedSender(
                    f"IMPERSONATED SENDER: {_why}. A row written under another "
                    f"agent's identity is either a bug or a spoof; both deserve "
                    f"refusal. Send as yourself, or pass the real sender.")
            if _impersonation_armed() and _sv["self_addressed"]:
                raise SelfAddressedRow(
                    f"SELF-ADDRESSED: {from_agent} -> itself is a contradiction, "
                    f"not a delivery. If you are deliberately planting a row to "
                    f"verify the delivery path, pass allow_self_addressed=True so "
                    f"the exception is recorded rather than silent.")
            _addressability_log(
                f"[impersonation-shadow] WOULD REFUSE {msg_id}: {_why} "
                f"(to={to_agent}, self_addressed={_sv['self_addressed']}, "
                f"trivial_body={_sv['trivial_body']})")

        # leg-4 act 3: classify the DESTINATION (never the sender) before writing.
        try:
            _verdict = _addressability_verdict(to_agent)
        except Exception:      # noqa: BLE001 — fail OPEN, never block a send
            _verdict = None
        if _verdict and _verdict.get("disposition") != "deliver":
            _kind = _verdict.get("kind")
            _disp = _verdict.get("disposition")
            if allow_unaddressable:
                metadata = dict(metadata) if metadata else {}
                metadata["addressability_override"] = _kind
                _addressability_log(
                    f"[addressability] OVERRIDE {from_agent} -> {to_agent} "
                    f"(kind={_kind}, msg={msg_id}) — deliberate, recorded")
            elif not _addressability_armed():
                _addressability_log(
                    f"[addressability-shadow] WOULD "
                    f"{'REFUSE' if _disp == 'refuse' else 'REDIRECT'} "
                    f"{from_agent} -> {to_agent} (kind={_kind}, "
                    f"why={_verdict.get('why')}, msg={msg_id})")
            elif _disp == "refuse":
                raise UnaddressableTarget(
                    f"{to_agent} is a {_kind} and cannot receive pane-injected "
                    f"mail ({_verdict.get('why')}). Re-address to a live agent, "
                    f"write to the ledger, or pass allow_unaddressable=True.")
            else:
                # daemon: its mail is a FEEDBACK SIGNAL a mechanism depends on
                # (§4.7). Never refused — redirected, and written meanwhile so the
                # signal is not destroyed before the daemon has a read channel.
                _addressability_log(
                    f"[addressability] REDIRECT {from_agent} -> {to_agent} "
                    f"(kind={_kind}, msg={msg_id}) — daemon feedback preserved")

        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO messages (id, conversation_id, task_id, parent_id, type, from_agent, to_agent,
                   subject, body, priority, source, status, metadata, created_at, depends_on, gather_mode, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [msg_id, conversation_id, task_id, parent_id, type, from_agent, to_agent,
                 subject, body, priority, source, "pending",
                 json.dumps(metadata) if metadata else None, _now(),
                 json.dumps(depends_on) if depends_on is not None else None, gather_mode, tenant_id]
            )
            # Upsert conversation
            conn.execute(
                """INSERT INTO conversations (id, subject, participants, task_id, created_at, updated_at, tenant_id)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET updated_at=?, participants=?""",
                [conversation_id, subject,
                 json.dumps(sorted(set([from_agent, to_agent]))), task_id, _now(), _now(), tenant_id,
                 _now(), json.dumps(sorted(set([from_agent, to_agent])))]
            )
            conn.commit()
            return msg_id
        finally:
            conn.close()

    def inbox(self, agent_id: str, status: str = "pending", limit: int = 50,
              type: str = None, since: str = None, tenant_id: str = "operator") -> list:
        """Get messages for an agent's inbox."""
        conn = self._conn()
        try:
            sql = "SELECT * FROM messages WHERE to_agent = ? AND status = ? AND tenant_id = ?"
            params = [agent_id, status, tenant_id]
            if type:
                sql += " AND type = ?"
                params.append(type)
            if since:
                sql += " AND created_at > ?"
                params.append(since)
            sql += f" ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, created_at ASC LIMIT {limit}"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def held_pending_for(self, session: str, tenant_id: str = "operator") -> list:
        """Pending §9.6 held rows (type='held_message') addressed to `session`,
        created_at ASC. The boundary-delivery consumer (s96) reads this on a
        turn_ended to bundle + deliver; the router backstop reads the same rows.
        Both gate on claim() (shared CAS) so a row can't double-deliver."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE to_agent = ? AND status = 'pending' "
                "AND type = 'held_message' AND tenant_id = ? ORDER BY created_at ASC",
                [session, tenant_id]).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def has_superseding_row(self, from_agent: str, to_agent: str, created_at) -> bool:
        """True iff a LATER row exists for the same (from_agent → to_agent) pair
        (§9.6 stale-delivery banner: a newer message from the same sender to the
        same recipient means this one may be superseded). One same-pair created_at
        comparison; any status counts (a delivered newer row still supersedes)."""
        if not (from_agent and to_agent and created_at):
            return False
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE from_agent = ? AND to_agent = ? "
                "AND created_at > ? LIMIT 1", [from_agent, to_agent, created_at]
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def stamp_latency(self, msg_id: str, created_at, fired_at):
        """Record a per-delivery latency stamp (created_at→fired_at) into the row
        metadata (§9.6 B6). Merges into existing metadata; best-effort — never
        raises on a bad row. Enables a per-row before/after latency scorecard."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT metadata FROM messages WHERE id = ?",
                               [msg_id]).fetchone()
            if not row:
                return
            try:
                md = json.loads(row["metadata"]) if row["metadata"] else {}
                if not isinstance(md, dict):
                    md = {}
            except (ValueError, TypeError):
                md = {}
            md["boundary_latency"] = {"created_at": created_at, "fired_at": fired_at}
            conn.execute("UPDATE messages SET metadata = ? WHERE id = ?",
                         [json.dumps(md), msg_id])
            conn.commit()
        finally:
            conn.close()

    def stamp_metadata(self, msg_id: str, **kv):
        """Merge arbitrary keys into a row's metadata JSON (DEC-1787031444
        digest stamping — e.g. digested_at — so the Stop-hook digest and the
        router gate can see each other's presentations). Best-effort like
        stamp_latency; never raises on a bad row."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT metadata FROM messages WHERE id = ?",
                               [msg_id]).fetchone()
            if not row:
                return
            try:
                md = json.loads(row["metadata"]) if row["metadata"] else {}
                if not isinstance(md, dict):
                    md = {}
            except (ValueError, TypeError):
                md = {}
            md.update(kv)
            conn.execute("UPDATE messages SET metadata = ? WHERE id = ?",
                         [json.dumps(md), msg_id])
            conn.commit()
        finally:
            conn.close()

    DRIVE_CLASS_TYPES = ("task_request", "directive", "request")
    DISPOSITIONS = ("acted", "queued", "declined")

    def dispose(self, msg_id: str, disposition: str, by: str,
                reason: str = None) -> bool:
        """Record the receiver's disposition on a row (§9.6-B.5,
        DEC-1787032722): acted / queued / declined(+reason). Returns False on
        an unknown id (the #13 ack lesson — never ok:true on a ghost row).
        Raises ValueError on an invalid disposition or a reason-less decline."""
        if disposition not in self.DISPOSITIONS:
            raise ValueError(f"disposition must be one of {self.DISPOSITIONS}, "
                             f"got {disposition!r}")
        if disposition == "declined" and not (reason and reason.strip()):
            raise ValueError("a declined disposition requires a reason")
        conn = self._conn()
        try:
            row = conn.execute("SELECT metadata FROM messages WHERE id = ?",
                               [msg_id]).fetchone()
            if not row:
                return False
        finally:
            conn.close()
        kv = {"disposition": disposition, "disposition_by": by,
              "disposed_at": _now()}
        if reason:
            kv["disposition_reason"] = reason
        self.stamp_metadata(msg_id, **kv)
        return True

    def disposition_stats(self, sender: str = None, tenant_id: str = "operator") -> dict:
        """Per-sender disposition stats over drive-class rows (§9.6-B.6): the
        gm/PM feedback loop that keeps interruptions measurably beneficial.
        Undisposed rows are counted — absence is itself the stat."""
        conn = self._conn()
        try:
            q = ("SELECT from_agent, metadata FROM messages "
                 "WHERE type IN (?,?,?) AND tenant_id = ?")
            params = list(self.DRIVE_CLASS_TYPES) + [tenant_id]
            if sender:
                q += " AND from_agent = ?"
                params.append(sender)
            rows = conn.execute(q, params).fetchall()
        finally:
            conn.close()
        out = {"sender": sender, "drive_class_total": len(rows),
               "dispositions": {}, "undisposed": 0}
        for r in rows:
            try:
                md = json.loads(r["metadata"]) if r["metadata"] else {}
            except (ValueError, TypeError):
                md = {}
            d = md.get("disposition") if isinstance(md, dict) else None
            if d:
                out["dispositions"][d] = out["dispositions"].get(d, 0) + 1
            else:
                out["undisposed"] += 1
        return out

    def dead_letter(self, msg_id: str, reason: str) -> bool:
        """TERMINAL disposition for an undeliverable row (leg-4 act 2, gm
        msg_c74d3ae3). An escalation that never terminates trains everyone to
        ignore escalations — gm's own HIGH row re-escalated every ~30min for
        34h because its target (a service pane) can never be idle. A
        dead-lettered row leaves 'pending' forever: no re-escalation, no
        re-delivery attempt, reason recorded on the row. Returns False on an
        unknown id OR one already terminal (idempotent — the #13 ack lesson)."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE messages SET status='dead_letter', error=? "
                "WHERE id=? AND status='pending'", [reason, msg_id])
            conn.commit()
            if cur.rowcount == 0:
                return False
        finally:
            conn.close()
        self.stamp_metadata(msg_id, dead_lettered_at=_now(),
                            dead_letter_reason=reason)
        return True

    def queued_open(self, sender: str = None, tenant_id: str = "operator") -> list:
        """Guard-(d) of §9.6 (s96 guards-a-e): every row whose CURRENT
        disposition is 'queued' — the live tracked set a PM verifies at node
        completion so ACK+QUEUE can never become a silent drop. A later
        re-dispose (acted/declined) overwrites the metadata disposition and
        removes the row from this set. Read-only."""
        conn = self._conn()
        try:
            q = ("SELECT id, from_agent, to_agent, subject, created_at, metadata "
                 "FROM messages WHERE metadata LIKE '%\"disposition\": \"queued\"%' "
                 "AND tenant_id = ?")
            params = [tenant_id]
            if sender:
                q += " AND from_agent = ?"
                params.append(sender)
            rows = conn.execute(q + " ORDER BY created_at ASC", params).fetchall()
        finally:
            conn.close()
        out = []
        for r in rows:
            try:
                md = json.loads(r["metadata"]) if r["metadata"] else {}
            except (ValueError, TypeError):
                continue
            if isinstance(md, dict) and md.get("disposition") == "queued":
                out.append({"id": r["id"], "from_agent": r["from_agent"],
                            "to_agent": r["to_agent"], "subject": r["subject"],
                            "created_at": r["created_at"],
                            "disposition": "queued",
                            "disposed_at": md.get("disposed_at"),
                            "disposition_by": md.get("disposition_by")})
        return out

    def recipient_replied_after(self, conversation_id: str, to_agent: str,
                                created_at) -> bool:
        """True iff `to_agent` SENT a row in this conversation AFTER created_at —
        the mechanical 'demonstrably acted past this thread' predicate for
        [SUPERSEDED-CANDIDATE] tagging (DEC-1787031444 D4). Zero semantics."""
        if not (conversation_id and to_agent and created_at):
            return False
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM messages WHERE conversation_id = ? "
                "AND from_agent = ? AND created_at > ? LIMIT 1",
                [conversation_id, to_agent, created_at]).fetchone()
            return row is not None
        finally:
            conn.close()

    def claim(self, msg_id: str) -> bool:
        """Atomically claim a message for processing. Returns True if claimed."""
        conn = self._conn()
        try:
            result = conn.execute(
                "UPDATE messages SET status = 'processing', attempted_at = ? WHERE id = ? AND status = 'pending'",
                [_now(), msg_id]
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            conn.close()

    def deliver(self, msg_id: str):
        """Mark message as delivered (injected into agent's tmux)."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE messages SET status = 'delivered', delivered_at = ? WHERE id = ?",
                [_now(), msg_id]
            )
            conn.commit()
        finally:
            conn.close()

    def acknowledge(self, msg_id: str) -> bool:
        """Mark message as acknowledged (agent confirmed receipt). Returns True
        iff a row was actually updated — acking an unknown id must NEVER be a
        silent success (replay-flood class: the agent believes it acked, the
        router re-injects forever; self-caught in the 2026-08-18 edge campaign,
        msg_22587546)."""
        conn = self._conn()
        try:
            result = conn.execute(
                "UPDATE messages SET status = 'acknowledged', acknowledged_at = ? WHERE id = ?",
                [_now(), msg_id]
            )
            conn.commit()
            return result.rowcount > 0
        finally:
            conn.close()

    def archive(self, msg_id: str):
        """Mark message as archived (fully processed)."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE messages SET status = 'archived', archived_at = ? WHERE id = ?",
                [_now(), msg_id]
            )
            conn.commit()
        finally:
            conn.close()

    def fail(self, msg_id: str, error: str = None):
        """Mark message as failed. Increments retry count. Resets to pending if retries remain."""
        conn = self._conn()
        try:
            msg = conn.execute("SELECT retry_count, max_retries FROM messages WHERE id = ?", [msg_id]).fetchone()
            if not msg:
                return
            new_count = msg["retry_count"] + 1
            new_status = "pending" if new_count < msg["max_retries"] else "failed"
            conn.execute(
                "UPDATE messages SET status = ?, retry_count = ?, error = ?, attempted_at = ? WHERE id = ?",
                [new_status, new_count, error, _now(), msg_id]
            )
            conn.commit()
        finally:
            conn.close()

    def query(self, to_agent: str = None, from_agent: str = None, type: str = None,
              status: str = None, task_id: str = None, conversation_id: str = None,
              limit: int = 100, since: str = None, tenant_id: str = None) -> list:
        """Flexible message query."""
        conn = self._conn()
        try:
            conds = ["1=1"]
            params = []
            if to_agent:
                conds.append("to_agent = ?"); params.append(to_agent)
            if from_agent:
                conds.append("from_agent = ?"); params.append(from_agent)
            if type:
                conds.append("type = ?"); params.append(type)
            if status:
                conds.append("status = ?"); params.append(status)
            if task_id:
                conds.append("task_id = ?"); params.append(task_id)
            if conversation_id:
                conds.append("conversation_id = ?"); params.append(conversation_id)
            if since:
                conds.append("created_at > ?"); params.append(since)
            if tenant_id is not None:
                conds.append("tenant_id = ?"); params.append(tenant_id)
            sql = f"SELECT * FROM messages WHERE {' AND '.join(conds)} ORDER BY created_at DESC LIMIT {limit}"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def thread(self, conversation_id: str, limit: Optional[int] = None) -> list:
        """Get all messages in a conversation thread, chronological."""
        conn = self._conn()
        try:
            if limit:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC LIMIT ?",
                    [conversation_id, limit]
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
                    [conversation_id]
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        """Get message statistics."""
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            by_status = {}
            for row in conn.execute("SELECT status, COUNT(*) as c FROM messages GROUP BY status").fetchall():
                by_status[row["status"]] = row["c"]
            by_type = {}
            for row in conn.execute("SELECT type, COUNT(*) as c FROM messages GROUP BY type ORDER BY c DESC LIMIT 20").fetchall():
                by_type[row["type"]] = row["c"]
            pending_by_agent = {}
            for row in conn.execute("SELECT to_agent, COUNT(*) as c FROM messages WHERE status='pending' GROUP BY to_agent ORDER BY c DESC").fetchall():
                pending_by_agent[row["to_agent"]] = row["c"]
            failed = conn.execute("SELECT COUNT(*) FROM messages WHERE status='failed'").fetchone()[0]
            return {
                "total": total,
                "by_status": by_status,
                "by_type": by_type,
                "pending_by_agent": pending_by_agent,
                "failed": failed,
            }
        finally:
            conn.close()

    def get(self, msg_id: str):
        """Get a single message by ID."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", [msg_id]).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def bulk_send(self, messages: list) -> list:
        """Send multiple messages in a single transaction."""
        conn = self._conn()
        ids = []
        try:
            for m in messages:
                msg_id = m.get("id") or _gen_id()
                fa, ta = m["from_agent"], m["to_agent"]
                conv_id = m.get("conversation_id") or f"conv_{hashlib.md5(f'{fa}:{ta}:{msg_id}'.encode()).hexdigest()[:12]}"
                conn.execute(
                    """INSERT INTO messages (id, conversation_id, task_id, parent_id, type, from_agent, to_agent,
                       subject, body, priority, source, status, metadata, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [msg_id, conv_id, m.get("task_id"), m.get("parent_id"), m["type"],
                     m["from_agent"], m["to_agent"], m.get("subject"), m.get("body"),
                     m.get("priority", "medium"), m.get("source", "system"), "pending",
                     json.dumps(m.get("metadata")) if m.get("metadata") else None, _now()]
                )
                ids.append(msg_id)
            conn.commit()
            return ids
        finally:
            conn.close()


    def reply(self, msg_id: str, body: str = None, close: bool = False) -> str:
        """Reply to a message. Swaps from/to, increments iteration, handles conversation mode."""
        orig = self.get(msg_id)
        if not orig:
            raise ValueError(f"Message {msg_id} not found")

        reply_id = _gen_id()
        conv_id = orig["conversation_id"]
        tenant_id = orig.get("tenant_id") or "operator"

        conn = self._conn()
        try:
            # Insert reply message (inherit tenant_id from parent)
            reply_metadata = json.dumps({"closes_thread": True}) if close else None
            conn.execute(
                """INSERT INTO messages (id, conversation_id, task_id, parent_id, type, from_agent, to_agent,
                   subject, body, priority, source, status, created_at, gather_mode, tenant_id, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [reply_id, conv_id, orig.get("task_id"), msg_id, "reply",
                 orig["to_agent"], orig["from_agent"],
                 orig.get("subject"), body,
                 orig.get("priority", "medium"), "system", "pending", _now(), "gather_all", tenant_id,
                 reply_metadata]
            )

            # Increment iteration_count
            conn.execute(
                "UPDATE conversations SET iteration_count = iteration_count + 1, updated_at = ? WHERE id = ?",
                [_now(), conv_id]
            )

            # Fetch updated conversation state
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", [conv_id]).fetchone()
            if conv:
                conv = dict(conv)
                mode = conv.get("mode") or "fire_and_forget"
                max_iter = conv.get("max_iterations") or 1
                iter_count = conv.get("iteration_count") or 1
                completion_condition = conv.get("completion_condition")

                should_close = False
                if mode == "fire_and_forget":
                    should_close = True
                elif mode == "supervised_loop":
                    if iter_count >= max_iter:
                        should_close = True
                    elif completion_condition and body and completion_condition.lower() in body.lower():
                        should_close = True
                elif mode == "ping_pong":
                    if close:
                        should_close = True

                if should_close:
                    conn.execute(
                        "UPDATE conversations SET status = 'closed', updated_at = ? WHERE id = ?",
                        [_now(), conv_id]
                    )

            conn.commit()
            return reply_id
        finally:
            conn.close()

    def conversation_status(self, conversation_id: str, tenant_id: str = None) -> dict:
        """Get conversation status including per-message breakdown."""
        conn = self._conn()
        try:
            conv = conn.execute("SELECT * FROM conversations WHERE id = ?", [conversation_id]).fetchone()
            if not conv:
                return {"error": "conversation not found"}
            conv = dict(conv)

            msg_sql = """SELECT id, from_agent, to_agent, type, status, subject,
                          delivered_at, acknowledged_at, created_at
                   FROM messages WHERE conversation_id = ?"""
            msg_params = [conversation_id]
            if tenant_id is not None:
                msg_sql += " AND tenant_id = ?"
                msg_params.append(tenant_id)
            msg_sql += " ORDER BY created_at ASC"
            msgs = conn.execute(msg_sql, msg_params).fetchall()
            msg_list = [dict(m) for m in msgs]

            return {
                "mode": conv.get("mode") or "fire_and_forget",
                "max_iterations": conv.get("max_iterations") or 1,
                "iteration_count": conv.get("iteration_count") or 0,
                "completion_condition": conv.get("completion_condition"),
                "status": conv.get("status") or "open",
                "total_messages": len(msg_list),
                "messages": msg_list,
            }
        finally:
            conn.close()

    def set_conversation_mode(self, conversation_id: str, mode: str,
                              max_iterations: int = None,
                              completion_condition: str = None):
        """Update conversation mode and optional settings."""
        conn = self._conn()
        try:
            updates = ["mode = ?"]
            params = [mode]
            if max_iterations is not None:
                updates.append("max_iterations = ?")
                params.append(max_iterations)
            if completion_condition is not None:
                updates.append("completion_condition = ?")
                params.append(completion_condition)
            updates.append("updated_at = ?")
            params.append(_now())
            params.append(conversation_id)
            conn.execute(
                f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?",
                params
            )
            conn.commit()
        finally:
            conn.close()

    def pending_with_deps_resolved(self, agent_id: str = None, tenant_id: str = None) -> list:
        """Get pending messages whose dependencies (depends_on) are all resolved."""
        conn = self._conn()
        try:
            sql = "SELECT * FROM messages WHERE status = 'pending'"
            params = []
            if agent_id:
                sql += " AND to_agent = ?"
                params.append(agent_id)
            if tenant_id is not None:
                sql += " AND tenant_id = ?"
                params.append(tenant_id)
            sql += " ORDER BY CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END, created_at ASC"
            rows = conn.execute(sql, params).fetchall()
            all_msgs = [dict(r) for r in rows]

            resolved_statuses = {"archived", "delivered", "acknowledged"}
            result = []
            for msg in all_msgs:
                deps_raw = msg.get("depends_on")
                if not deps_raw:
                    result.append(msg)
                    continue
                try:
                    dep_ids = json.loads(deps_raw)
                except (json.JSONDecodeError, TypeError):
                    result.append(msg)
                    continue
                if not dep_ids:
                    result.append(msg)
                    continue
                # Check all deps are resolved
                placeholders = ",".join("?" * len(dep_ids))
                dep_rows = conn.execute(
                    f"SELECT id, status FROM messages WHERE id IN ({placeholders})",
                    dep_ids
                ).fetchall()
                dep_statuses = {r["id"]: r["status"] for r in dep_rows}
                all_resolved = all(dep_statuses.get(d) in resolved_statuses for d in dep_ids)
                if all_resolved:
                    result.append(msg)
            return result
        finally:
            conn.close()


# ── Task-Message Integration ──────────────────────────────────

    def create_task_with_message(self, title: str, description: str,
                                  assigned_to: str, from_agent: str,
                                  project: str = None, priority: str = "medium",
                                  tenant_id: str = "operator",
                                  roadmap_phase_id: str = None) -> tuple:
        """Create a task AND send a message to the assigned agent.
        Returns (task_id, msg_id)."""
        task_id = _gen_id("task")
        now = _now()

        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO tasks
                       (id, title, description, status, assigned_to, priority,
                        project_id, tenant_id, roadmap_phase_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [task_id, title, description, "queued", assigned_to, priority,
                 project, tenant_id, roadmap_phase_id, now, now]
            )
            conn.commit()
        finally:
            conn.close()

        # Send message via self.send (handles its own connection)
        msg_id = self.send(
            from_agent=from_agent,
            to_agent=assigned_to,
            type="task",
            subject=title,
            body=description,
            priority=priority,
            task_id=task_id,
            tenant_id=tenant_id,
        )

        # Fetch the conversation_id that send() generated
        msg = self.get(msg_id)
        conv_id = msg["conversation_id"] if msg else None

        # Link message + conversation back onto the task
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE tasks SET message_id = ?, conversation_id = ?, updated_at = ? WHERE id = ?",
                [msg_id, conv_id, _now(), task_id]
            )
            conn.commit()
        finally:
            conn.close()

        return (task_id, msg_id)

    def sync_task_from_message(self, msg_id: str) -> Optional[str]:
        """Update task status based on linked message status.
        Returns the new task status, or None if no linked task found."""
        msg = self.get(msg_id)
        if not msg:
            return None

        # Find the task linked to this message
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id, status FROM tasks WHERE message_id = ?",
                [msg_id]
            ).fetchone()
            if not row:
                return None

            task_id = row["id"]

            # Map message status → task status
            status_map = {
                "pending":      "queued",
                "processing":   "queued",
                "delivered":    "assigned",
                "acknowledged": "in_progress",
                "archived":     "done",
                "failed":       "queued",
            }
            # "reply received" is detected by checking for a reply message
            # (i.e., a message with parent_id == msg_id)
            reply_row = conn.execute(
                "SELECT id FROM messages WHERE parent_id = ? LIMIT 1",
                [msg_id]
            ).fetchone()
            if reply_row:
                new_status = "review"
            else:
                new_status = status_map.get(msg["status"], "queued")

            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                [new_status, _now(), task_id]
            )
            conn.commit()
            return new_status
        finally:
            conn.close()


# ── Roadmap CRUD ───────────────────────────────────────────────

    def roadmap_create(self, project: str, pm_agent: str, title: str = None,
                       vision: str = None, tenant_id: str = "operator") -> str:
        """Create a new roadmap. Returns roadmap id."""
        roadmap_id = _gen_id("roadmap")
        now = _now()
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO roadmaps
                       (id, tenant_id, project, pm_agent, title, vision,
                        status, current_phase, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [roadmap_id, tenant_id, project, pm_agent, title, vision,
                 "draft", 0, now, now]
            )
            conn.commit()
        finally:
            conn.close()
        return roadmap_id

    def roadmap_add_phase(self, roadmap_id: str, title: str = None,
                          description: str = None, milestone: str = None) -> str:
        """Append a phase to a roadmap. Phase number is auto-incremented. Returns phase_id."""
        phase_id = _gen_id("phase")
        conn = self._conn()
        try:
            # Determine next phase_number
            row = conn.execute(
                "SELECT COALESCE(MAX(phase_number), 0) FROM roadmap_phases WHERE roadmap_id = ?",
                [roadmap_id]
            ).fetchone()
            next_num = (row[0] or 0) + 1
            conn.execute(
                """INSERT INTO roadmap_phases
                       (id, roadmap_id, phase_number, title, description, status, milestone)
                   VALUES (?,?,?,?,?,?,?)""",
                [phase_id, roadmap_id, next_num, title, description, "pending", milestone]
            )
            conn.execute(
                "UPDATE roadmaps SET updated_at = ? WHERE id = ?",
                [_now(), roadmap_id]
            )
            conn.commit()
        finally:
            conn.close()
        return phase_id

    def roadmap_add_task(self, phase_id: str, task_id: str = None,
                         agent_id: str = None, dependencies: list = None) -> str:
        """Add a task reference to a roadmap phase. Returns roadmap_task id."""
        rt_id = _gen_id("rt")
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO roadmap_tasks
                       (id, phase_id, task_id, agent_id, dependency_tasks, status)
                   VALUES (?,?,?,?,?,?)""",
                [rt_id, phase_id, task_id, agent_id,
                 json.dumps(dependencies) if dependencies else None, "pending"]
            )
            conn.commit()
        finally:
            conn.close()
        return rt_id

    def roadmap_get(self, roadmap_id: str) -> Optional[dict]:
        """Get a roadmap with phases and tasks nested. Returns None if not found."""
        conn = self._conn()
        try:
            rm = conn.execute("SELECT * FROM roadmaps WHERE id = ?", [roadmap_id]).fetchone()
            if not rm:
                return None
            result = dict(rm)

            phases = conn.execute(
                "SELECT * FROM roadmap_phases WHERE roadmap_id = ? ORDER BY phase_number ASC",
                [roadmap_id]
            ).fetchall()

            result["phases"] = []
            for phase in phases:
                p = dict(phase)
                tasks = conn.execute(
                    "SELECT * FROM roadmap_tasks WHERE phase_id = ? ORDER BY rowid ASC",
                    [phase["id"]]
                ).fetchall()
                p["tasks"] = [dict(t) for t in tasks]
                result["phases"].append(p)

            return result
        finally:
            conn.close()

    def roadmap_advance_phase(self, roadmap_id: str) -> Optional[dict]:
        """Mark the current phase complete and start the next one.
        Returns the new active phase dict, or None if already on last phase."""
        conn = self._conn()
        try:
            rm = conn.execute("SELECT * FROM roadmaps WHERE id = ?", [roadmap_id]).fetchone()
            if not rm:
                return None
            rm = dict(rm)
            current = rm["current_phase"]

            # Mark current phase done
            conn.execute(
                """UPDATE roadmap_phases SET status = 'completed', completed_at = ?
                   WHERE roadmap_id = ? AND phase_number = ?""",
                [_now(), roadmap_id, current]
            )

            # Find next phase
            next_phase = conn.execute(
                """SELECT * FROM roadmap_phases WHERE roadmap_id = ? AND phase_number > ?
                   ORDER BY phase_number ASC LIMIT 1""",
                [roadmap_id, current]
            ).fetchone()

            if not next_phase:
                # No more phases — mark roadmap completed
                conn.execute(
                    "UPDATE roadmaps SET status = 'completed', updated_at = ? WHERE id = ?",
                    [_now(), roadmap_id]
                )
                conn.commit()
                return None

            next_phase = dict(next_phase)
            next_num = next_phase["phase_number"]

            # Activate next phase
            conn.execute(
                """UPDATE roadmap_phases SET status = 'in_progress', started_at = ?
                   WHERE id = ?""",
                [_now(), next_phase["id"]]
            )
            conn.execute(
                """UPDATE roadmaps SET current_phase = ?, status = 'active', updated_at = ?
                   WHERE id = ?""",
                [next_num, _now(), roadmap_id]
            )
            conn.commit()

            # Return updated phase
            row = conn.execute(
                "SELECT * FROM roadmap_phases WHERE id = ?", [next_phase["id"]]
            ).fetchone()
            return dict(row) if row else next_phase
        finally:
            conn.close()

    def roadmap_status(self, roadmap_id: str) -> dict:
        """Return a progress summary for the roadmap."""
        conn = self._conn()
        try:
            rm = conn.execute("SELECT * FROM roadmaps WHERE id = ?", [roadmap_id]).fetchone()
            if not rm:
                return {"error": "roadmap not found"}
            rm = dict(rm)

            phases = conn.execute(
                "SELECT status, COUNT(*) as c FROM roadmap_phases WHERE roadmap_id = ? GROUP BY status",
                [roadmap_id]
            ).fetchall()
            phase_counts = {row["status"]: row["c"] for row in phases}
            total_phases = sum(phase_counts.values())

            tasks = conn.execute(
                """SELECT rt.status, COUNT(*) as c FROM roadmap_tasks rt
                   JOIN roadmap_phases rp ON rt.phase_id = rp.id
                   WHERE rp.roadmap_id = ? GROUP BY rt.status""",
                [roadmap_id]
            ).fetchall()
            task_counts = {row["status"]: row["c"] for row in tasks}
            total_tasks = sum(task_counts.values())

            completed_phases = phase_counts.get("completed", 0)
            pct = round(completed_phases / total_phases * 100) if total_phases else 0

            return {
                "roadmap_id": roadmap_id,
                "title": rm.get("title"),
                "project": rm["project"],
                "status": rm["status"],
                "current_phase": rm["current_phase"],
                "total_phases": total_phases,
                "completed_phases": completed_phases,
                "pct_complete": pct,
                "phase_counts": phase_counts,
                "total_tasks": total_tasks,
                "task_counts": task_counts,
            }
        finally:
            conn.close()


# ── Hierarchy CRUD ────────────────────────────────────────────

    def hierarchy_upsert(self, agent_id: str, tenant_id: str = "operator",
                         parent_id: str = None, project: str = None,
                         role: str = None, always_on: bool = False,
                         status: str = "running", generation: int = 1,
                         task_id: str = None, conversation_id: str = None,
                         metadata: dict = None) -> None:
        """Insert or update an agent in the hierarchy table."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO agent_hierarchy
                       (agent_id, tenant_id, parent_id, project, role, always_on,
                        spawned_at, status, generation, task_id, conversation_id,
                        last_active, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(agent_id, tenant_id) DO UPDATE SET
                       parent_id       = excluded.parent_id,
                       project         = excluded.project,
                       role            = excluded.role,
                       always_on       = excluded.always_on,
                       status          = excluded.status,
                       generation      = excluded.generation,
                       task_id         = excluded.task_id,
                       conversation_id = excluded.conversation_id,
                       last_active     = excluded.last_active,
                       metadata        = excluded.metadata""",
                [agent_id, tenant_id, parent_id, project, role, int(always_on),
                 _now(), status, generation, task_id, conversation_id,
                 _now(), json.dumps(metadata) if metadata else None]
            )
            conn.commit()
        finally:
            conn.close()

    def hierarchy_get(self, agent_id: str, tenant_id: str = "operator") -> Optional[dict]:
        """Get a single agent from the hierarchy by agent_id + tenant_id."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM agent_hierarchy WHERE agent_id = ? AND tenant_id = ?",
                [agent_id, tenant_id]
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def hierarchy_list(self, tenant_id: str = "operator", status: str = None,
                       role: str = None, project: str = None) -> list:
        """List agents in the hierarchy, optionally filtered."""
        conn = self._conn()
        try:
            conds = ["tenant_id = ?"]
            params = [tenant_id]
            if status:
                conds.append("status = ?"); params.append(status)
            if role:
                conds.append("role = ?"); params.append(role)
            if project:
                conds.append("project = ?"); params.append(project)
            sql = f"SELECT * FROM agent_hierarchy WHERE {' AND '.join(conds)} ORDER BY spawned_at ASC"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def hierarchy_always_on(self, tenant_id: str = None) -> list:
        """Return all always_on agents, optionally scoped to a tenant."""
        conn = self._conn()
        try:
            if tenant_id is not None:
                rows = conn.execute(
                    "SELECT * FROM agent_hierarchy WHERE always_on = 1 AND tenant_id = ?",
                    [tenant_id]
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_hierarchy WHERE always_on = 1"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def hierarchy_update_status(self, agent_id: str, tenant_id: str, status: str) -> None:
        """Update agent status. Sets stopped_at when status='stopped'."""
        conn = self._conn()
        try:
            if status == "stopped":
                conn.execute(
                    """UPDATE agent_hierarchy SET status = ?, stopped_at = ?, last_active = ?
                       WHERE agent_id = ? AND tenant_id = ?""",
                    [status, _now(), _now(), agent_id, tenant_id]
                )
            else:
                conn.execute(
                    """UPDATE agent_hierarchy SET status = ?, last_active = ?
                       WHERE agent_id = ? AND tenant_id = ?""",
                    [status, _now(), agent_id, tenant_id]
                )
            conn.commit()
        finally:
            conn.close()

    def hierarchy_children(self, parent_id: str, tenant_id: str = "operator") -> list:
        """Return direct children of a parent agent within a tenant."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM agent_hierarchy WHERE parent_id = ? AND tenant_id = ? ORDER BY spawned_at ASC",
                [parent_id, tenant_id]
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ── CLI Interface ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OrchestraOS Message Store")
    sub = parser.add_subparsers(dest="command")

    # send
    p = sub.add_parser("send")
    p.add_argument("--from", dest="from_agent", required=True)
    p.add_argument("--to", dest="to_agent", required=True)
    p.add_argument("--type", default="task_request")
    p.add_argument("--subject", default="")
    p.add_argument("--body", default="")
    # FILE-SOURCED BODIES (gm msg_96d31877). Shell metacharacters mangling
    # inline bodies hit FOUR agents in one night: a backtick silently deleted
    # a word inside a DELIVERED body; another deleted exactly the technical
    # words, so sentences arrived reading as if a field name were missing
    # rather than named; a stray fragment in a command chain FORGED a row
    # under another agent's identity. The signature defect is that the send
    # REPORTS SUCCESS while delivering something other than what was written
    # — {"sent": true} and a mangled body are indistinguishable at send time.
    #
    # Every agent's workaround was the same six-line python wrapper: the
    # fleet standardised on a workaround instead of fixing the interface,
    # and every handoff carries "use file-sourced bodies" as GUIDANCE. That
    # is a LAW where a MECHANISM belongs — documenting a hazard is strictly
    # worse than removing the surface.
    p.add_argument("--body-file", default=None,
                   help="read the body from PATH verbatim (bytes -> UTF-8, no "
                        "shell, no interpolation). Mutually exclusive with "
                        "--body.")
    p.add_argument("--subject-file", default=None,
                   help="read the subject from PATH verbatim. Mutually "
                        "exclusive with --subject.")
    p.add_argument("--priority", default="medium")
    p.add_argument("--task-id", default=None)
    p.add_argument("--source", default="cli")
    p.add_argument("--closes-thread", action="store_true", default=False)
    p.add_argument("--reason", default=None,
                   help="§9.6-B.4 envelope: machine-readable justification for drive-class sends")
    p.add_argument("--contributes-to", dest="contributes_to", default=None,
                   help="§9.6-B.4 envelope: initiative/node/mission ref")

    # dispose (§9.6-B.5)
    p = sub.add_parser("dispose")
    p.add_argument("--id", "--message-id", dest="message_id", required=True)
    p.add_argument("--disposition", required=True, choices=("acted", "queued", "declined"))
    p.add_argument("--by", required=True)
    p.add_argument("--reason", default=None)

    # disposition-stats (§9.6-B.6)
    p = sub.add_parser("disposition-stats")
    p.add_argument("--sender", default=None)

    # queued-open (guard-d): live tracked ACK+QUEUE set
    p = sub.add_parser("queued-open")
    p.add_argument("--sender", default=None)

    # inbox
    p = sub.add_parser("inbox")
    p.add_argument("--agent", required=True)
    p.add_argument("--status", default="pending")
    p.add_argument("--all", dest="all_flag", action="store_true", default=False)
    p.add_argument("--limit", type=int, default=20)

    # reply
    p = sub.add_parser("reply")
    p.add_argument("--id", "--message-id", dest="message_id", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--from", dest="from_agent", default=None)
    p.add_argument("--close", action="store_true", default=False)

    # ack
    p = sub.add_parser("ack")
    p.add_argument("--id", "--message-id", dest="message_id", required=True)

    # thread
    p = sub.add_parser("thread")
    p.add_argument("--conversation-id", required=True)
    p.add_argument("--limit", type=int, default=50)

    # conversations
    p = sub.add_parser("conversations")
    p.add_argument("--agent", required=True)
    p.add_argument("--limit", type=int, default=20)

    # stats
    sub.add_parser("stats")

    # get
    p = sub.add_parser("get")
    p.add_argument("--id", "--message-id", dest="id", required=True)

    args = parser.parse_args()
    store = MessageStore()

    if args.command == "send":
        def _from_file(path, inline, flag):
            """Verbatim read. REFUSES on a missing/unreadable file and sends
            nothing — a silently empty message because a path was wrong is
            the same failure wearing different clothes."""
            if inline:
                print(json.dumps({
                    "sent": False,
                    "error": f"--{flag} and --{flag}-file are mutually "
                             f"exclusive; supplying both is an error, not a "
                             f"silent precedence rule"}))
                sys.exit(2)
            try:
                return Path(path).read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                print(json.dumps({
                    "sent": False,
                    "error": f"--{flag}-file {path!r} unreadable: {e}. "
                             f"NOTHING WAS SENT."}))
                sys.exit(2)

        body = args.body
        subject = args.subject
        if args.body_file is not None:
            body = _from_file(args.body_file, args.body, "body")
        if args.subject_file is not None:
            subject = _from_file(args.subject_file, args.subject, "subject")

        msg_id = store.send(
            from_agent=args.from_agent, to_agent=args.to_agent,
            type=args.type, subject=subject, body=body,
            priority=args.priority, task_id=args.task_id, source=args.source,
            closes_thread=args.closes_thread,
            reason=args.reason, contributes_to=args.contributes_to
        )
        print(json.dumps({"sent": True, "id": msg_id}))

    elif args.command == "dispose":
        try:
            ok = store.dispose(args.message_id, args.disposition,
                               by=args.by, reason=args.reason)
        except ValueError as e:
            print(json.dumps({"disposed": False, "error": str(e)}))
            sys.exit(1)
        out = {"disposed": bool(ok), "id": args.message_id,
               "disposition": args.disposition}
        if not ok:
            out["error"] = "no such message"
        print(json.dumps(out))

    elif args.command == "disposition-stats":
        print(json.dumps(store.disposition_stats(sender=args.sender), indent=1))

    elif args.command == "queued-open":
        print(json.dumps(store.queued_open(sender=args.sender), indent=1))

    elif args.command == "reply":
        result = store.reply(args.message_id, args.body, close=args.close)
        print(json.dumps({"replied": True, "id": result}, default=str))

    elif args.command == "ack":
        ok = store.acknowledge(args.message_id)
        out = {"acknowledged": bool(ok), "id": args.message_id}
        if not ok:
            out["error"] = "no such message"
        print(json.dumps(out))

    elif args.command == "thread":
        messages = store.thread(args.conversation_id, limit=args.limit)
        print(json.dumps({"messages": messages, "count": len(messages)}, indent=2, default=str))

    elif args.command == "conversations":
        conn = store._conn()
        try:
            rows = conn.execute(
                """SELECT c.id, c.subject, c.status, c.mode, c.updated_at,
                   (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count
                   FROM conversations c
                   JOIN messages m ON m.conversation_id = c.id
                   WHERE m.from_agent = ? OR m.to_agent = ?
                   GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ?""",
                [args.agent, args.agent, args.limit]
            ).fetchall()
            convos = [dict(r) for r in rows]
            print(json.dumps({"conversations": convos, "count": len(convos)}, indent=2, default=str))
        finally:
            conn.close()

    elif args.command == "inbox":
        status = "all" if args.all_flag else args.status
        messages = store.inbox(args.agent, status=status, limit=args.limit)
        print(json.dumps({"messages": messages, "count": len(messages)}, indent=2, default=str))

    elif args.command == "stats":
        print(json.dumps(store.stats(), indent=2))

    elif args.command == "get":
        msg = store.get(args.id)
        print(json.dumps(msg, indent=2, default=str) if msg else '{"error": "not found"}')

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
