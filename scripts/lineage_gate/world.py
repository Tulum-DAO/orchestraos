"""lineage_gate.world — the ONLY seam between gates and the live world.

DEC-1787085269 (CONSENSUS_REACHED, gm+agy, proposer recused): a grade binds to
the author-time world. `LiveWorld` performs each read ONCE and records every
answer; the record's `environment` block is exactly `LiveWorld.recorded`.
`FrozenWorld` answers ONLY from that block — it accepts no db handle, no
registry, no fs root, structurally (the no-override-branch spirit: a future
gate cannot reach the live world by accident because there is nothing to
reach). A missing key raises FrozenWorldMiss, never a silent live read.

jsonl handling (agy binds, vote feedback in the ledger): the predecessor's
jsonl is append-only and ALIVE during its own grading window, so the freeze
pins a byte-offset PREFIX — {byte_len, prefix_sha256}. Appends after the
grade are structurally invisible (all resolution happens against a
materialized copy of bytes [0, byte_len)); an in-place rewrite inside the
prefix trips ARTIFACT-DRIFT; a file SHORTER than byte_len is drift too
(explicit len >= byte_len check).

Git is NOT behind this seam: full-40-char-sha object reads are
content-addressed and immutable (Amendment 1.4 #3), classified SAFE in the
approved audit. Widening the seam past the approved proposal would be a new
rubric change.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from .artifact import sha256_bytes


def _sid_invariants():
    """The declaration predicate has ONE home (scripts/sid_invariants.py); a
    second copy is how the class regrows."""
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        return importlib.import_module("sid_invariants")
    except ImportError:
        return None


class FrozenWorldMiss(Exception):
    """Replay asked for a fact the environment block never recorded —
    the record is incomplete for this rubric version; refuse, never
    silently consult the live world."""


def hostile_ref(ref: str) -> bool:
    """Absolute or traversal ref. Guarded AT THE WALK (gm msg_292739f3: 'the
    guard belongs where the walk happens'): Path(repo_dir)/ref silently
    DISCARDS repo_dir when ref is absolute — agy's cited hazard — so format
    checks at the gate are necessary but not sufficient."""
    return ref.startswith("/") or ".." in ref.split("/")


class LiveWorld:
    """Author-time world. Reads once, records everything it answered."""

    def __init__(self, *, repo_dir: str, tasks_db: str, registry: dict,
                 sessions: dict, jsonl_path: str, resolver,
                 graded_agent: str = None):
        self._repo_dir = Path(repo_dir)
        self._tasks_db = tasks_db
        self._registry = registry
        self._sessions = sessions
        self._resolver = resolver
        # snapshot the jsonl ONCE — the file may grow mid-grade (it did:
        # g10 answered mail during its own grading window)
        raw = Path(jsonl_path).read_bytes()
        self._prefix_file = tempfile.NamedTemporaryFile(
            prefix="lineage-gate-jsonl-", suffix=".jsonl", delete=False)
        self._prefix_file.write(raw)
        self._prefix_file.close()
        self.recorded: dict = {
            "jsonl_freeze": {"byte_len": len(raw),
                             "prefix_sha256": sha256_bytes(raw)},
            "g_effect": {}, "h3": {}, "h5": {}, "g_loops": {},
            # FIFTH PLANE (ob, ruled by gm gen-14 msg_65242938): the grade
            # plane took the transcript's OWNERSHIP from a FIELD (the sid on
            # the row) and never asked whose declaration it was. A crossed
            # sessions row would grade a successor against the WRONG
            # predecessor's transcript — scoring answers about an era it never
            # lived, passing a stranger or failing an honest heir, both
            # well-formed. The declaration is frozen HERE so a regrade
            # replays it rather than re-deriving it.
            "jsonl_identity": self._identity(self._prefix_file.name,
                                             graded_agent, registry, sessions),
        }

    @staticmethod
    def _identity(path: str, graded_agent: str, registry: dict,
                  sessions: dict = None) -> dict:
        """Whose transcript IS this? Declaration, never the sid field."""
        if not graded_agent:
            return {"expected": None, "declared": None, "ok": None,
                    "reason": "no graded agent supplied"}
        SI = _sid_invariants()
        if SI is None:
            return {"expected": graded_agent, "declared": None, "ok": None,
                    "reason": "checker unavailable"}
        agents = (registry or {}).get("agents") or registry or {}
        # BOTH stores: a generational alias pruned from one still names a
        # real identity in the other, and treating a live predecessor as
        # "undeclared" would refuse an honest grade (bricking a rotation is
        # not an acceptable price for this check).
        known = set(agents) | set(sessions or {}) | {graded_agent}
        declared = SI.declared_identity(path, known)
        if declared is None:
            return {"expected": graded_agent, "declared": None, "ok": False,
                    "reason": "transcript declares no identity — UNVERIFIABLE, "
                              "and unverifiable is never a pass (INV6)"}
        ok = (declared == graded_agent
              or SI.same_lineage(declared, graded_agent, agents))
        return {"expected": graded_agent, "declared": declared, "ok": ok,
                "reason": ("declared identity is outside the graded agent's "
                           "lineage — this is the crossed-transcript shape"
                           if not ok else "declared identity matches")}

    # -- G-EFFECT ----------------------------------------------------------
    def effect_pre_state(self, kind: str, target: str, needle) -> bool:
        if hostile_ref(target):
            # the gate rejects hostile targets before probing; reaching the
            # walk with one means a caller bypassed the gate — be loud
            raise ValueError(f"hostile first_effect target reached the walk: "
                             f"{target!r}")
        p = self._repo_dir / target
        if kind == "file_exists":
            pre = p.exists()
        else:  # file_contains
            try:
                pre = (needle or "") in p.read_text(errors="replace")
            except OSError:
                pre = False
        self.recorded["g_effect"] = {"kind": kind, "target": target,
                                     "needle": needle, "pre_state": pre}
        return pre

    # -- H3 ----------------------------------------------------------------
    def h3_snapshot(self, successor_key: str) -> dict:
        row = (self._registry.get("agents") or {}).get(successor_key) or {}
        sess = self._sessions.get(successor_key) or {}
        snap = {
            "registry_row": bool(row),
            "model": str(row.get("model") or ""),
            "cwd_exists": bool(row.get("cwd"))
                          and Path(str(row.get("cwd"))).is_dir(),
            "resume_command": bool(sess.get("resume_command") or ""),
        }
        self.recorded["h3"] = snap
        return snap

    # -- H5 ----------------------------------------------------------------
    def h5_debts(self, agent_id: str) -> dict:
        con = sqlite3.connect(self._tasks_db)
        try:
            msgs = [r[0] for r in con.execute(
                "select id from messages where to_agent=? and "
                "status='pending' order by id", [agent_id])]
            try:
                tasks = [str(r[0]) for r in con.execute(
                    "select id from tasks where status in "
                    "('pending','in_progress') order by id")]
            except sqlite3.OperationalError:
                tasks = None   # schema-absent, distinct from empty
        finally:
            con.close()
        debts = {"pending_msg_ids": msgs, "active_task_ids": tasks}
        self.recorded["h5"] = debts
        return debts

    # -- G-LOOPS -----------------------------------------------------------
    def loops_resolve(self, ref: str) -> bool:
        if hostile_ref(ref):
            # never walk it — fail-closed is the correct direction for a
            # ground: an unresolvable ref fails the gate, the fs stays untouched
            self.recorded["g_loops"][ref] = False
            return False
        if ref.startswith("msg_"):
            con = sqlite3.connect(self._tasks_db)
            try:
                ok = con.execute("select 1 from messages where id=?",
                                 [ref]).fetchone() is not None
            finally:
                con.close()
        elif ref.startswith("DEC-"):
            led = self._repo_dir / "DOCS" / "SHARED_DECISIONS.json"
            ok = ref in led.read_text() if led.exists() else False
        else:
            target = self._repo_dir / ref
            ok = target.exists() or bool(
                sorted(target.parent.glob(target.name + ".*"))
                if target.parent.is_dir() else [])
        self.recorded["g_loops"][ref] = ok
        return ok

    # -- H2 ----------------------------------------------------------------
    def jsonl_region(self, pointer: str) -> str:
        # resolution runs against the frozen prefix copy, never the live file
        return self._resolver(self._prefix_file.name, pointer) or ""

    def jsonl_identity(self) -> dict:
        return dict(self.recorded["jsonl_identity"])


class FrozenWorld:
    """Replay world. Holds the environment block and (for jsonl pointer
    resolution only) a path to hash-VERIFIED prefix bytes. No db, no
    registry, no repo root — by construction."""

    def __init__(self, environment: dict, *, verified_prefix_path: str,
                 resolver):
        self._env = environment
        self._prefix_path = verified_prefix_path
        self._resolver = resolver

    def _get(self, *keys):
        cur = self._env
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                raise FrozenWorldMiss("environment block missing: "
                                      + "/".join(keys))
            cur = cur[k]
        return cur

    def effect_pre_state(self, kind: str, target: str, needle) -> bool:
        rec = self._get("g_effect")
        if not rec or (rec.get("kind"), rec.get("target")) != (kind, target):
            raise FrozenWorldMiss(
                f"g_effect recorded for {rec.get('kind')}:{rec.get('target')!r}"
                f" but replay asked {kind}:{target!r}")
        return bool(rec["pre_state"])

    def h3_snapshot(self, successor_key: str) -> dict:
        return dict(self._get("h3"))

    def h5_debts(self, agent_id: str) -> dict:
        return dict(self._get("h5"))

    def loops_resolve(self, ref: str) -> bool:
        loops = self._get("g_loops")
        if ref not in loops:
            raise FrozenWorldMiss(f"g_loops has no recorded answer for {ref!r}")
        return bool(loops[ref])

    def jsonl_region(self, pointer: str) -> str:
        return self._resolver(self._prefix_path, pointer) or ""

    def jsonl_identity(self) -> dict:
        return dict(self._get("jsonl_identity"))


def verify_jsonl_prefix(jsonl_path: str, freeze: dict) -> tuple:
    """Explicit len >= byte_len check + prefix hash (agy bind). Returns
    (ok, current_prefix_sha_or_reason, verified_prefix_path_or_None)."""
    byte_len = int(freeze["byte_len"])
    try:
        raw = Path(jsonl_path).read_bytes()
    except OSError as e:
        return False, f"unreadable: {e}", None
    if len(raw) < byte_len:
        return False, f"file shorter than frozen prefix ({len(raw)} < {byte_len})", None
    prefix = raw[:byte_len]
    cur = sha256_bytes(prefix)
    if cur != freeze["prefix_sha256"]:
        return False, cur, None
    tmp = tempfile.NamedTemporaryFile(
        prefix="lineage-gate-verified-", suffix=".jsonl", delete=False)
    tmp.write(prefix)
    tmp.close()
    return True, cur, tmp.name
