"""Tool-call ledger: idempotency on tool_call_id and receipts (RFC section 5.3). Ported from the reviewed POC
(poc/gptlive_native/tool_ledger.py); the scratch fence is replaced by an EXPLICIT directory (no default path, so a
test can never reach the production store by accident; the service passes state/arturo/native).

One SQLite file in that directory. A tool call is executed inside ONE write transaction that also records it,
so a retried or duplicated call_id can never run its side effect twice, even across a crash or a reconnect.

Receipt states move forward only, and only by the boundary: requested -> accepted -> running -> completed, or to
failed / cancelled. A model's claim ("it's done") is stored as a claim and never changes a receipt.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

STATES = ("requested", "accepted", "running", "completed", "failed", "cancelled")
FORWARD = {
    "requested": {"accepted", "failed"},
    "accepted": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "completed": set(), "failed": set(), "cancelled": set(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id   TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    principal      TEXT NOT NULL,
    name           TEXT NOT NULL,
    args_sha       TEXT NOT NULL,
    args_json      TEXT NOT NULL,
    result_json    TEXT NOT NULL,
    task_id        TEXT,
    created        REAL NOT NULL,
    duplicate_hits INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tool_calls_conv ON tool_calls (conversation_id, created);
CREATE TABLE IF NOT EXISTS call_outcomes (
    tool_call_id    TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    name            TEXT NOT NULL,
    args_sha        TEXT NOT NULL,
    status          TEXT NOT NULL,      -- 'superseded' (a newer request replaced it) | 'reused' (same request, same turn)
    ref             TEXT NOT NULL,      -- the delegation that superseded it, or the call whose result it reused
    result_json     TEXT NOT NULL,      -- what we submitted for it (never an effect)
    created         REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    task_id   TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    state     TEXT NOT NULL,
    evidence  TEXT NOT NULL DEFAULT '',
    updated   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS receipt_history (
    task_id TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL, by TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_claims (
    task_id TEXT, claimed TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id   TEXT PRIMARY KEY,
    tenant    TEXT NOT NULL,
    owner     TEXT NOT NULL,
    title     TEXT NOT NULL,
    read_only INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    task_id   TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    text      TEXT NOT NULL,
    state     TEXT NOT NULL,
    channel   TEXT NOT NULL DEFAULT '',
    client_event_id TEXT NOT NULL DEFAULT '',
    updated   REAL NOT NULL
);
"""


class ReceiptError(RuntimeError):
    pass


def args_sha(args):
    return hashlib.sha256(json.dumps(args, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ToolLedger:
    def __init__(self, root, name="tool_ledger.sqlite3", clock=time.time):
        if root is None:
            raise TypeError("ToolLedger needs an explicit directory")
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / name
        self.clock = clock
        with self._connect() as c:
            c.executescript(_SCHEMA)

    def _connect(self):
        c = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # ---- the one write path for a tool call -------------------------------------------------------------
    def run_once(self, tool_call_id, conversation_id, principal, name, args, effect):
        """Execute `effect(conn)` at most once per tool_call_id. Returns (result, duplicate: bool).
        The effect runs inside the same IMMEDIATE transaction that records the call, so the call row and its side
        effect commit together or not at all. A repeated id with the SAME args returns the stored result; with
        different args it is refused without running anything."""
        sha = args_sha(args)
        c = self._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
            if row is not None:
                if row["args_sha"] != sha or row["name"] != name or row["principal"] != principal:
                    c.execute("ROLLBACK")
                    return {"ok": False, "error": "TOOL_CALL_ID_REUSED",
                            "detail": "this tool_call_id already ran with different arguments"}, True
                c.execute("UPDATE tool_calls SET duplicate_hits = duplicate_hits + 1 WHERE tool_call_id = ?",
                          (tool_call_id,))
                c.execute("COMMIT")
                return json.loads(row["result_json"]), True
            result, task_id = effect(c)
            c.execute("INSERT INTO tool_calls (tool_call_id, conversation_id, principal, name, args_sha, args_json,"
                      " result_json, task_id, created) VALUES (?,?,?,?,?,?,?,?,?)",
                      (tool_call_id, conversation_id, principal, name, sha, json.dumps(args, sort_keys=True),
                       json.dumps(result, sort_keys=True), task_id, self.clock()))
            c.execute("COMMIT")
            return result, False
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    # ---- the write path for a REAL tool (seconds to minutes: spawn_agent waits up to 120 s) ------------------
    PENDING = {"ok": False, "error": "IN_PROGRESS", "detail": "this tool call is still running"}

    def run_once_external(self, tool_call_id, conversation_id, principal, name, args, effect, precheck=None):
        """At most once per tool_call_id for an effect that must NOT run inside the write transaction (it would hold
        the database lock for its whole duration). Phase 1 claims the id in a short transaction; phase 2 runs
        effect() with no lock; phase 3 stores the result. A duplicate during phase 2 gets IN_PROGRESS; a crash between
        1 and 3 leaves the claim, so the effect is never re-run (at-most-once, like run_once). precheck(conn) runs INSIDE
        the claim transaction and may return a refusal (stored nowhere, nothing runs): caps counted there cannot be
        overshot by overlapping calls. Returns (result, duplicate: bool)."""
        sha = args_sha(args)
        c = self._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
            if row is not None:
                if (row["args_sha"] != sha or row["name"] != name or row["principal"] != principal
                        or row["conversation_id"] != conversation_id):
                    c.execute("ROLLBACK")
                    return {"ok": False, "error": "TOOL_CALL_ID_REUSED",
                            "detail": "this tool_call_id already ran with different arguments or in another call"}, True
                c.execute("UPDATE tool_calls SET duplicate_hits = duplicate_hits + 1 WHERE tool_call_id = ?",
                          (tool_call_id,))
                c.execute("COMMIT")
                return json.loads(row["result_json"]), True
            refusal = precheck(c) if precheck else None
            if refusal is not None:
                c.execute("ROLLBACK")
                return refusal, False
            c.execute("INSERT INTO tool_calls (tool_call_id, conversation_id, principal, name, args_sha, args_json,"
                      " result_json, task_id, created) VALUES (?,?,?,?,?,?,?,?,?)",
                      (tool_call_id, conversation_id, principal, name, sha, json.dumps(args, sort_keys=True),
                       json.dumps(self.PENDING, sort_keys=True), None, self.clock()))
            c.execute("COMMIT")
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()
        try:
            result, task_id = effect()
        except Exception as e:  # noqa: BLE001 — stored as the call's result, never re-run
            result, task_id = {"ok": False, "error": "TOOL_ERROR", "detail": type(e).__name__}, None
        with self._connect() as c:                      # the result first: a receipt problem never strands it
            c.execute("UPDATE tool_calls SET result_json = ?, task_id = ? WHERE tool_call_id = ?",
                      (json.dumps(result, sort_keys=True), task_id, tool_call_id))
        if task_id:
            c = self._connect()
            try:
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM receipts WHERE task_id = ?", (task_id,)).fetchone() is None:
                    self.open_receipt(c, task_id, principal, state="accepted", evidence=f"started by {name}")
                c.execute("COMMIT")
            except Exception:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise
            finally:
                c.close()
        return result, False

    # ---- receipts ----------------------------------------------------------------------------------------
    def open_receipt(self, conn, task_id, principal, state="accepted", evidence=""):
        now = self.clock()
        conn.execute("INSERT INTO receipts (task_id, principal, state, evidence, updated) VALUES (?,?,?,?,?)",
                     (task_id, principal, "requested", "", now))
        conn.execute("INSERT INTO receipt_history VALUES (?,?,?,?,?)", (task_id, None, "requested", "boundary", now))
        if state != "requested":
            self._move(conn, task_id, state, evidence, by="boundary")

    def advance(self, task_id, to_state, evidence, by="boundary"):
        """The ONLY way a receipt moves. `by` must be the boundary or the simulated fleet, never the model."""
        if by not in ("boundary", "fleet"):
            raise ReceiptError(f"receipts move only by the boundary or the fleet, not {by!r}")
        c = self._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            self._move(c, task_id, to_state, evidence, by)
            c.execute("COMMIT")
        except Exception:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    def _move(self, conn, task_id, to_state, evidence, by):
        row = conn.execute("SELECT state FROM receipts WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise ReceiptError(f"no receipt for {task_id}")
        if to_state not in FORWARD[row["state"]]:
            raise ReceiptError(f"illegal receipt move {row['state']} -> {to_state} for {task_id}")
        now = self.clock()
        conn.execute("UPDATE receipts SET state = ?, evidence = ?, updated = ? WHERE task_id = ?",
                     (to_state, evidence, now, task_id))
        conn.execute("INSERT INTO receipt_history VALUES (?,?,?,?,?)", (task_id, row["state"], to_state, by, now))

    def receipt(self, task_id):
        with self._connect() as c:
            r = c.execute("SELECT * FROM receipts WHERE task_id = ?", (task_id,)).fetchone()
            return dict(r) if r else None

    def history(self, task_id):
        with self._connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT from_state, to_state, by FROM receipt_history WHERE task_id = ? ORDER BY rowid", (task_id,))]

    def note_model_claim(self, task_id, claimed, text):
        """Record what the model SAID about a task. Never touches the receipt (invariant 3)."""
        with self._connect() as c:
            c.execute("INSERT INTO model_claims VALUES (?,?,?,?)", (task_id, claimed, text, self.clock()))
        return self.receipt(task_id)

    def record_not_executed(self, tool_call_id, conversation_id, name, args, status, ref, result):
        """A call we answered WITHOUT running it (settle/hold): executed = false. Writes no task, receipt or effect."""
        if status not in ("superseded", "reused"):
            raise ValueError(f"unknown outcome {status!r}")
        with self._connect() as c:
            c.execute("INSERT OR IGNORE INTO call_outcomes VALUES (?,?,?,?,?,?,?,?)",
                      (tool_call_id, conversation_id, name, args_sha(args), status, ref,
                       json.dumps(result, sort_keys=True), self.clock()))

    def not_executed(self):
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM call_outcomes ORDER BY created, rowid")]

    # ---- reads for evidence ----------------------------------------------------------------------------
    def calls_for(self, conversation_id, limit=12):
        """This call's latest tool calls, oldest first (the tool strip; indexed, never the whole table)."""
        with self._connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM tool_calls WHERE conversation_id = ? ORDER BY created DESC,"
                                               " rowid DESC LIMIT ?", (conversation_id, limit))]
        return rows[::-1]

    def calls(self):
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tool_calls ORDER BY created, rowid")]

    def tasks(self):
        with self._connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM tasks ORDER BY created, rowid")]

    def executed(self, name, conversation_id=None, principal=None, since=None, ok_only=True):
        """Tool calls that RAN (not duplicates, not refusals recorded elsewhere): the spawn caps count these."""
        q, a = "SELECT result_json FROM tool_calls WHERE name = ?", [name]
        if conversation_id is not None:
            q, a = q + " AND conversation_id = ?", a + [conversation_id]
        if principal is not None:
            q, a = q + " AND principal = ?", a + [principal]
        if since is not None:
            q, a = q + " AND created >= ?", a + [since]
        with self._connect() as c:
            rows = [json.loads(r[0]) for r in c.execute(q, a)]
        return sum(1 for r in rows if not ok_only or r.get("ok"))

    def duplicate_hits(self):
        with self._connect() as c:
            return int(c.execute("SELECT COALESCE(SUM(duplicate_hits), 0) FROM tool_calls").fetchone()[0])
