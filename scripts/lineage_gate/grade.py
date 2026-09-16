"""lineage_gate.grade — run the gates, freeze the record, re-run it forever.

FROZEN-WORLD (DEC-1787085269, CONSENSUS_REACHED gm+agy, proposer recused —
classified DETERMINISM REPAIR by both voters independently): a grade binds to
the AUTHOR-TIME world. `grade` builds a LiveWorld that reads each fact once
and records every answer; the record's `environment` block is that recording.
`regrade` replays through a FrozenWorld that holds no live handle at all —
same artifacts + same environment = same verdict, forever, for anyone.

The jsonl is pinned as a byte-offset PREFIX (append-only file, alive during
its own grading window): appends are invisible, in-place rewrites and
truncations trip ARTIFACT-DRIFT (explicit len >= byte_len check, agy bind).

Records that predate this rubric version regrade as
RECORD-PREDATES-FROZEN-WORLD — a distinct, scriptable status (gm bind #3),
never a silent grade against the live world.

Hard-gates-only (the operator Q4): passed == every gate true. No number exists.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .artifact import (Handoff, UnparseableArtifact, canonical_json,
                       parse_canary, parse_handoff, sha256_bytes)
from . import rubric
from .world import FrozenWorld, LiveWorld, verify_jsonl_prefix

RECORD_VERSION = "v3.1-frozenworld"


def _grader_commit(repo_dir: str) -> str:
    r = subprocess.run(["git", "-C", repo_dir, "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown"


def grade(*, handoff_path: str, canary_path: str, jsonl_path: str,
          agent_id: str, successor_key: str, repo_dir: str, tasks_db: str,
          registry: dict, sessions: dict, resolver, leak_lint_result: dict,
          session_start_epoch: int, now_epoch: int, world=None) -> dict:
    """Every input is explicit and injectable — same artifacts, same grade,
    forever, for anyone. `now_epoch` is the ONLY time in the system.
    `world` is injectable for replay (FrozenWorld) and for tests; when None,
    a LiveWorld snapshots the author-time world."""
    gates = []
    try:
        handoff = parse_handoff(handoff_path)
        canary_rows = parse_canary(canary_path)
    except UnparseableArtifact as e:
        gates.append({"id": "H4", "passed": False, "evidence": [str(e)],
                      "why": "unparseable artifact"})
        return _record(gates, handoff_path, canary_path, None, repo_dir,
                       now_epoch, leak_lint_result)

    if world is None:
        world = LiveWorld(repo_dir=repo_dir, tasks_db=tasks_db,
                          registry=registry, sessions=sessions,
                          jsonl_path=jsonl_path, resolver=resolver,
                          graded_agent=agent_id)

    # resolver sensitivity: prove it resolves a KNOWN region before any gate
    # trusts it (calibration rule, spec Amendment 4) — a resolver returning ""
    # for everything blames the author for the instrument's failure. Early
    # entries may legitimately be textless meta rows, so the known-positive is
    # "ANY of the first 20 turns yields text", not turn-1 specifically (the
    # turn-1 form false-negatived on a real transcript during this build).
    resolver_sensitive = any(
        world.jsonl_region(f"jsonl:turn-{n}") for n in range(1, 21))

    gates.append(rubric.gate_h4_parses(handoff))
    gates.append(rubric.gate_h1_no_answer_key(handoff, canary_rows,
                                              leak_lint_result))
    h2 = rubric.gate_h2_pointers_resolve(canary_rows, world,
                                         session_start_epoch)
    if not resolver_sensitive:
        h2 = {**h2, "passed": False,
              "evidence": h2["evidence"] + ["RESOLVER-INSENSITIVE: known-region "
                                            "probe returned empty"],
              "why": h2["why"] + " (instrument failure, not author failure — "
                                 "fix the resolver, do not fail the author "
                                 "silently)"}
    gates.append(h2)
    gates.append(rubric.gate_h3_spawnable(world, successor_key))
    gates.append(rubric.gate_h5_debt_accounted(handoff, agent_id, world))
    gates.append(rubric.gate_struct(handoff, canary_rows, repo_dir,
                                    session_start_epoch, now_epoch))
    gates.append(rubric.gate_provenance(handoff, repo_dir,
                                        session_start_epoch, now_epoch))
    gates.append(rubric.gate_open_loops(handoff, world))
    gates.append(rubric.gate_effect(handoff, world))
    environment = world.recorded if isinstance(world, LiveWorld) else None
    return _record(gates, handoff_path, canary_path, environment, repo_dir,
                   now_epoch, leak_lint_result)


def _record(gates, handoff_path, canary_path, environment, repo_dir,
            now_epoch, leak_lint_result) -> dict:
    def _sha(p):
        try:
            return sha256_bytes(Path(p).read_bytes())
        except OSError:
            return None
    rec = {
        "rubric_version": RECORD_VERSION,
        "grader_commit": _grader_commit(repo_dir),
        "graded_at": now_epoch,
        "passed": all(g["passed"] for g in gates) and bool(gates),
        "gates": sorted(gates, key=lambda g: g["id"]),
        # jsonl is pinned by environment.jsonl_freeze (prefix), not a
        # whole-file hash — the predecessor's transcript grows during its own
        # grading window, so a whole-file hash drifts within minutes and a
        # drift gate that always fires is a dead gate (DEC-1787085269).
        "artifacts": {"handoff": _sha(handoff_path),
                      "canary": _sha(canary_path)},
        "calibrations": {"leak_lint": leak_lint_result},
        "inputs": {"now": now_epoch},
    }
    if environment is not None:
        rec["environment"] = environment
    return rec


def write_record(rec: dict, successor_key: str, out_dir: str) -> str:
    out = Path(out_dir) / f"{successor_key}.grade.json"
    out.write_text(canonical_json(rec))
    return str(out)


def regrade(record_path: str, **grade_kwargs) -> dict:
    """Replay from the frozen record. No live handle reaches any gate: the
    FrozenWorld answers from the environment block, and jsonl pointer
    resolution runs only over hash-VERIFIED prefix bytes."""
    old = json.loads(Path(record_path).read_text())
    if "environment" not in old:
        # gm bind #3 (DEC-1787085269): distinct scriptable status — the two
        # legacy records must never become a manual-interpretation step.
        return {"status": "RECORD-PREDATES-FROZEN-WORLD",
                "rubric_version": old.get("rubric_version"),
                "record": record_path}
    for name in ("handoff", "canary"):
        p = grade_kwargs[f"{name}_path"]
        try:
            cur = sha256_bytes(Path(p).read_bytes())
        except OSError:
            cur = None
        if cur != old["artifacts"].get(name):
            return {"status": "ARTIFACT-DRIFT", "artifact": name,
                    "recorded": old["artifacts"].get(name), "current": cur}
    ok, detail, verified_prefix = verify_jsonl_prefix(
        grade_kwargs["jsonl_path"], old["environment"]["jsonl_freeze"])
    if not ok:
        return {"status": "ARTIFACT-DRIFT", "artifact": "jsonl",
                "recorded": old["environment"]["jsonl_freeze"]["prefix_sha256"],
                "current": detail}
    frozen = FrozenWorld(old["environment"],
                         verified_prefix_path=verified_prefix,
                         resolver=grade_kwargs["resolver"])
    # the leak-lint CALIBRATION is frozen too (gm msg_8f57f42d bind 2b,
    # Amendment 4 reaching the record): a regrade must know the detector was
    # proven sensitive AT GRADE TIME, never re-derive it live — a lint that
    # later went insensitive would otherwise silently rewrite H1's history
    new = grade(**{**grade_kwargs, "world": frozen,
                   "leak_lint_result": old["calibrations"]["leak_lint"],
                   "now_epoch": old["inputs"]["now"]})
    reproduced = (
        [(g["id"], g["passed"]) for g in new["gates"]]
        == [(g["id"], g["passed"]) for g in old["gates"]]
        and new["passed"] == old["passed"])
    return {"status": "REPRODUCED" if reproduced else "MISMATCH",
            "recorded_passed": old["passed"], "recomputed_passed": new["passed"]}
