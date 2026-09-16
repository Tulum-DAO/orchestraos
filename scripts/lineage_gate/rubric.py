"""lineage_gate.rubric — HARD GATES ONLY (the operator's Q4 ruling, 2026-08-18 19:38Z).

"A-" means EVERY unfakeable gate passed. No score exists to arbitrage — three
independent exploits scored 100/100 through the resolves-vs-carries-work seam,
so the point table died; its checks live on as this gate list's evidence detail.

Every gate returns {"id", "passed", "evidence": [...], "why"} and consults only:
the committed artifacts' raw bytes, git object existence, the two debt stores,
the predecessor's own jsonl, and an INJECTED `now`. No model calls, no network,
no wall clock, no randomness (Amendment 1 determinism set).

Context rule: structure only — a closing brace, an item boundary, a section
title. Never line distance (no_proximity_lint binding, spec 6.2).
"""
from __future__ import annotations

import re
import subprocess

from .artifact import Handoff

_TRIVIAL_DIFF_RE = re.compile(r"^[\s\r\n]*$")

# D3 verbatim-runtime-value probe classes (calibrated against agy's
# noise-mining fixture 5/5 + the real g11 set 0/5, 2026-08-18). Deterministic;
# widened only via a new fixture, never speculatively.
_TRIVIA_PROBE_RE = re.compile(
    r"(?i)\b(?:stdout|stderr|exit\s+code|inode|byte\s+count|millisecond"
    r"|line\s+\d+\b|log\s+output|printed\s+(?:on|in|by)|hex\s+(?:value|number"
    r"|inode|dump))\b")


def _gate(gid, passed, evidence, why):
    return {"id": gid, "passed": bool(passed), "evidence": sorted(evidence),
            "why": why}


# ---------------------------------------------------------------- H1: no key
def gate_h1_no_answer_key(handoff: Handoff, canary_rows: list,
                          leak_lint_result: dict):
    """No answer key anywhere. Two independent predicates: the calibrated leak
    lint's verdict (its calibration is recorded in the grade artifact — a hard
    gate whose detector went insensitive is worse than no gate, spec A4), plus
    a structural check that every canary row is exactly {q, source_pointer}."""
    bad_rows = [i for i, q in enumerate(canary_rows)
                if sorted(q.keys()) != ["q", "source_pointer"]]
    lint_clean = leak_lint_result.get("leaking", 1) == 0
    calibrated = leak_lint_result.get("calibration_passed") is True
    return _gate(
        "H1", lint_clean and calibrated and not bad_rows,
        [f"leak_lint={leak_lint_result}", f"non_pointer_rows={bad_rows}"],
        "a leaked gate certifies an absorption never proven; a zero from an "
        "uncalibrated detector is not a result")


# ------------------------------------------------- H2: pointers resolve (A7+)
def gate_h2_pointers_resolve(canary_rows: list, world,
                             session_start_epoch: int):
    """Every pointer resolves to ≥200 chars of the agent's OWN jsonl, in the
    WORKING ERA (after session start + 10 minutes — Amendment 1: the system
    prompt cannot be mined for deep facts), no two pointers share a region
    (A7 distinctness), and the region contains assistant reasoning, not only
    tool output (D3). Resolver sensitivity is proven by the caller against a
    known region before this gate's result counts (calibration rule)."""
    # FIFTH PLANE FIRST (gm gen-14 msg_65242938): before resolving a single
    # pointer, establish that this transcript BELONGS to the graded agent.
    # The grade plane previously took ownership from the sid on the row and
    # never asked whose declaration it was — so a crossed sessions row would
    # score a successor's answers against a stranger's era and return a
    # well-formed verdict either way. Refuse; never silently score.
    ident = world.jsonl_identity() if hasattr(world, "jsonl_identity") else {}
    if ident and ident.get("ok") is False:
        return _gate("H2", False,
                     [f"expected={ident.get('expected')}",
                      f"declared={ident.get('declared')}",
                      f"reason={ident.get('reason')}"],
                     "a grade resolved against the wrong lineage's transcript "
                     "is a verdict about an era the agent never lived — it "
                     "passes a stranger or fails an honest heir, and both "
                     "look well-formed")

    evid, seen, ok = [], set(), True
    if len(canary_rows) < 5:
        return _gate("H2", False, [f"only {len(canary_rows)} canaries"],
                     "five pointed questions minimum")
    for i, q in enumerate(canary_rows):
        region = world.jsonl_region(q.get("source_pointer", ""))
        key = region[:400]
        dup = key in seen
        seen.add(key)
        era_ok = _pointer_after(q.get("source_pointer", ""),
                                session_start_epoch + 600)
        substantial = len(region) >= 200
        # D3 (defeated by agy's noise-mining fixture, msg_02fe43a9): a canary
        # must probe COMPREHENSION, not transcription. A question DEMANDING a
        # verbatim runtime-execution value (stdout minutiae the predecessor
        # never *knew*) is noise-mining regardless of what its pointer
        # resolves to. Calibrated on the fixture set: fires 5/5 on agy's
        # noise questions, 0/5 on the real g11 set. Pattern set is versioned
        # and adversarially iterable — a rephrasing that evades it is a new
        # fixture, not a reason to widen blindly.
        trivia = bool(_TRIVIA_PROBE_RE.search(q.get("q", "")))
        row_ok = substantial and not dup and era_ok and not trivia
        ok = ok and row_ok
        evid.append(f"q{i+1}: chars={len(region)} distinct={not dup} "
                    f"working_era={era_ok} trivia_probe={trivia}")
    return _gate("H2", ok, evid,
                 "an unpointable fact is not gradeable and was never a fair "
                 "question; five questions aimed at one paragraph is the "
                 "cheapest gaming move; a verbatim-stdout question tests "
                 "nothing a successor needs to know (D3)")


def _pointer_after(pointer: str, min_epoch: int) -> bool:
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", pointer or "")
    if not m:
        return True   # non-range pointers (msg_/turn-) are era-checked by content
    from datetime import datetime, timezone
    t = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)
    return int(t.timestamp()) >= min_epoch


# ------------------------------------------------------- H3: spawnable record
def gate_h3_spawnable(world, successor_key: str):
    snap = world.h3_snapshot(successor_key)
    checks = {
        "registry_row": snap["registry_row"],
        "model_1m": snap["model"].endswith("[1m]"),
        "cwd_exists": snap["cwd_exists"],
        "resume_command": snap["resume_command"],
    }
    return _gate("H3", all(checks.values()),
                 [f"{k}={v}" for k, v in sorted(checks.items())],
                 "a promotion to an unspawnable identity orphans the lineage")


# --------------------------------------------------------- H4: parses (bytes)
def gate_h4_parses(handoff: Handoff):
    # reaching here with a Handoff means raw bytes parsed; record the hash the
    # judgment was made over (Amendment 7.3: raw committed bytes, no transform).
    return _gate("H4", True, [f"sha256={handoff.raw_sha256}"],
                 "graded-anyway is how gates rot; the hash pins WHICH bytes")


# ------------------------------------------- H5: debt accounted, BOTH stores
def gate_h5_debt_accounted(handoff: Handoff, agent_id: str, world):
    """Every pending msg_store row addressed to this agent AND every active
    tasks row it owns must appear in the artifact (Amendment 2 Patch 3: a
    clean inbox is not a clean slate). Debt ids come through the world seam
    so a debt paid AFTER promotion cannot rewrite a historical grade
    (DEC-1787085269)."""
    text = "\n".join(handoff.sections.values())
    debts = world.h5_debts(agent_id)
    missing, evid = [], []
    for mid in debts["pending_msg_ids"]:
        if mid not in text:
            missing.append(mid)
    evid.append(f"pending_msgs={len(debts['pending_msg_ids'])}")
    if debts["active_task_ids"] is None:
        evid.append("active_tasks=schema-absent")
    else:
        for tid in debts["active_task_ids"]:
            if f"#{tid}" not in text and str(tid) not in text:
                missing.append(f"task#{tid}")
        evid.append(f"active_tasks={len(debts['active_task_ids'])}")
    return _gate("H5", not missing, evid + [f"missing={missing}"],
                 "a dropped debt is a hard fail, not a lost point — the "
                 "bidirectional check the resolvability rubric was missing")


# ------------------------------- G-STRUCT: richness + session-relevance (A1)
def gate_struct(handoff: Handoff, canary_rows: list, repo_dir: str,
                session_start_epoch: int, now_epoch: int):
    checks = {
        "sections>=4": len([s for s in handoff.sections.values() if s.strip()]) >= 4,
        "hazards>=3": len(handoff.hazards) >= 3,
        "decisions>=3": len(handoff.decisions) >= 3,
        "canaries>=5": len(canary_rows) >= 5,
        # session-relevance (A1-upgrade): at least one cited commit must be
        # IN-ERA. bool(commits) was DEFEATED by agy's ghost-handoff fixture
        # (msg_02fe43a9): standing guards + one resolvable OUT-OF-ERA hash
        # satisfied it — existence is not session relevance.
        "session_specific": any(
            _commit_in_era(repo_dir, sha, session_start_epoch, now_epoch)
            for sha in handoff.commits),
    }
    return _gate("G-STRUCT", all(checks.values()),
                 [f"{k}={v}" for k, v in sorted(checks.items())],
                 "hollow structure is the Ghost Handoff's skeleton")


def _commit_in_era(repo_dir: str, sha: str, start_epoch: int,
                   now_epoch: int) -> bool:
    r = subprocess.run(["git", "-C", repo_dir, "cat-file", "-e",
                        f"{sha}^{{commit}}"], capture_output=True)
    if r.returncode != 0:
        return False
    ct = subprocess.run(["git", "-C", repo_dir, "show", "-s", "--format=%ct",
                         sha], capture_output=True, text=True).stdout.strip()
    return ct.isdigit() and start_epoch <= int(ct) <= now_epoch


# ---------------------- G-PROV: commits exist, era-bound, substantive (A2+D1)
def gate_provenance(handoff: Handoff, repo_dir: str, session_start_epoch: int,
                    now_epoch: int):
    """Cited commits must EXIST, fall inside the session's era (or be authored
    by prior generations of this lineage — a handoff legitimately cites its
    inheritance), and at least one in-era commit must be SUBSTANTIVE (D1:
    non-trivial diff). No commits at all fails: this artifact class documents
    building sessions."""
    if not handoff.commits:
        return _gate("G-PROV", False, ["no commits cited"],
                     "an era with no citable work must say so explicitly")
    exists, in_era, substantive = [], [], []
    for sha in handoff.commits:
        r = subprocess.run(["git", "-C", repo_dir, "cat-file", "-e",
                            f"{sha}^{{commit}}"], capture_output=True)
        if r.returncode != 0:
            continue
        exists.append(sha)
        ct = subprocess.run(["git", "-C", repo_dir, "show", "-s",
                             "--format=%ct", sha], capture_output=True,
                            text=True).stdout.strip()
        if ct.isdigit() and session_start_epoch <= int(ct) <= now_epoch:
            in_era.append(sha)
            diff = subprocess.run(
                ["git", "-C", repo_dir, "show", "--format=", "--unified=0", sha],
                capture_output=True, text=True).stdout
            body = "\n".join(l for l in diff.splitlines()
                             if l[:1] in "+-" and l[:3] not in ("+++", "---"))
            if not _TRIVIAL_DIFF_RE.match(body):
                substantive.append(sha)
    passed = bool(exists) and bool(in_era) and bool(substantive)
    return _gate("G-PROV", passed,
                 [f"cited={len(handoff.commits)}", f"exist={len(exists)}",
                  f"in_era={len(in_era)}", f"substantive={len(substantive)}"],
                 "resolvable is not enough — a whitespace commit is provenance "
                 "for nothing (micro-commit arbitrage, Amendment 2)")


# -------------------------- G-LOOPS: refs resolve, no bare dirs (A3+Patch 4)
def gate_open_loops(handoff: Handoff, world):
    if not handoff.open_loop_refs:
        return _gate("G-LOOPS", False, ["no resolvable refs found"],
                     "artifact PATHS not nicknames — 'per the runbook' rots "
                     "into limbo")
    bad, evid = [], []
    for ref in handoff.open_loop_refs:
        if ref.rstrip("/") in (".", "/", "docs", "state", "scripts",
                               "prompts", "logs"):
            bad.append(ref)          # bare-directory grounding (Patch 4)
            continue
        if ref.startswith("/") or ".." in ref.split("/"):
            bad.append(ref)          # absolute/traversal ref (agy bind,
            continue                 # DEC-1787085269 vote feedback)
        if ref.startswith("qnr_") or ref.lower().startswith("task"):
            ok = True                # store-verified by H5's pass over the db
        else:
            # exact file / artifact STEM / msg row / DEC entry — all through
            # the world seam so tomorrow's blessed deletion cannot fail
            # yesterday's grade (DEC-1787085269)
            ok = world.loops_resolve(ref)
        if not ok:
            bad.append(ref)
    evid.append(f"refs={len(handoff.open_loop_refs)} unresolved={bad}")
    return _gate("G-LOOPS", not bad, evid,
                 "a ground must be a specific versioned file or database "
                 "entity, never a directory")


# ------------------- G-EFFECT: first_effect is a TRANSITION (A1.1 + D2) -----
_EFFECT_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?first_effect:\s*(file_exists|file_contains):\s*(\S+)"
    r"(?:\s*::\s*(.+?))?\s*$")


def gate_effect(handoff: Handoff, world):
    """The declared first_effect must be a schema-enum assertion whose target
    is a SPECIFIC path and whose pre-state is FALSE at grade time. Built
    because agy's phantom-invariant fixture defeated the shipped grader
    (msg_02fe43a9): I had specced A1.1/D2 and implemented NO first_effect
    gate at all — `file_exists: docs/README.md` sailed through. A pre-existing
    truth is not an effect; Gate B observes the transition post-spawn."""
    m = None
    for sec in sorted(handoff.sections):
        m = _EFFECT_RE.search(handoff.sections[sec])
        if m:
            break
    if not m:
        return _gate("G-EFFECT", False, ["no first_effect declaration"],
                     "an artifact with no checkable first effect asserts "
                     "nothing a successor can be held to")
    kind, target, needle = m.group(1).lower(), m.group(2), m.group(3)
    if target.rstrip("/") in (".", "/", "docs", "state", "scripts", "prompts",
                              "logs"):
        return _gate("G-EFFECT", False, [f"bare-directory target {target!r}"],
                     "a ground must be specific (Patch 4)")
    if target.startswith("/") or ".." in target.split("/"):
        return _gate("G-EFFECT", False,
                     [f"absolute/traversal target {target!r}"],
                     "the walk must never leave the repo (agy bind, "
                     "DEC-1787085269); the world seam is also guarded")
    if kind == "file_contains" and not needle:
        return _gate("G-EFFECT", False,
                     ["file_contains missing ':: needle'"],
                     "an assertion without a needle asserts nothing")
    # pre_state through the world seam: recorded at author time, replayed
    # from the record forever — a COMPLETED transition can no longer flip a
    # historical verdict (determinism leak #7, DEC-1787085269)
    pre_state = world.effect_pre_state(kind, target, needle)
    return _gate(
        "G-EFFECT", not pre_state,
        [f"kind={kind}", f"target={target}", f"pre_state={pre_state}"],
        "D2: the assertion must be FALSE before and true after — a truth that "
        "predates the successor proves zero work (phantom invariant)")


ALL_GATES = ("H1", "H2", "H3", "H4", "H5", "G-STRUCT", "G-PROV", "G-LOOPS",
             "G-EFFECT")
