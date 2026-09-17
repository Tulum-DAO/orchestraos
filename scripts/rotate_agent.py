#!/usr/bin/env python3
# scripts/rotate_agent.py
"""On-demand mechanical agent rotation orchestrator.

Implements the normative rotation transaction:
  T1: Trigger & Inspect Predecessor
  T2: Author Handoff & Canaries (docs/HANDOFF_<seat>-next.md, state/agent-handoffs/<succ>.canary.json)
  T3: Spawn Successor in isolated tmux session with temp alias (<seat>-g<N+1>)
  T4: Absorb Context & Author Readback (state/agent-handoffs/<succ>.readback.md)
  T5: Machine Grade Readback (state/agent-handoffs/<succ>.comprehension.json)
  T6: Safe Pane Swap (rename predecessor to <seat>-gen<N> keeping alive; rename successor to <seat>)
  T7: Atomic Promotion via promote_successor.py (registry.json, agent-sessions.json, state/agents/)
  T8: Task Continuation (FIRST_EFFECT / next_3_actions injection)

Usage:
  python3 scripts/rotate_agent.py <seat_name> [--runtime RUNTIME] [--model MODEL] [--task TASK] [--force] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Setup paths
ORCHESTRA_DIR = Path(os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/orchestra")))
# CODE lives in the checkout (this file's parent's parent); ORCHESTRA_DIR is the DATA dir.
CODE_ROOT = Path(os.environ.get("ORCHESTRA_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(CODE_ROOT))
sys.path.insert(0, str(CODE_ROOT / "scripts"))

from registry_lock import registry_lock
import promote_successor as promoter
from runtime_signatures import resolve_runtime
from lineage_daemon.auto_grade import auto_grade_successor


def _orchestra_path_layout(base):
    """SINGLE SOURCE for every ORCHESTRA_DIR-derived path (name -> Path). Used by
    both the import-time binding AND set_orchestra_dir so the two can never drift —
    the exact drift that let the stale rotate_agent tests write to the LIVE tree/DB."""
    base = Path(base)
    return {
        "ORCHESTRA_DIR": base,
        "REGISTRY_PATH": base / "registry.json",
        "SESSIONS_PATH": base / "state" / "agent-sessions.json",
        "HANDOFFS_DIR": base / "state" / "agent-handoffs",
        "DOCS_DIR": base / "docs",
        "LOGS_DIR": base / "logs",
        "LOG_FILE": base / "logs" / "rotate-agent.log",
    }


def set_orchestra_dir(base):
    """Injectable hermeticity seam: repoint ORCHESTRA_DIR + ALL derived path globals
    at `base` so rotation runs fully isolated from the live tree/DB (a test points it
    at a FLAGLESS tmp sandbox so _cutover_active is False + no live-DB registration).
    Returns the PRIOR ORCHESTRA_DIR so a caller can restore it. This is the durable
    root fix for the non-hermetic tests — isolate the whole dir, not a narrow stub."""
    prior = ORCHESTRA_DIR
    g = globals()
    for name, val in _orchestra_path_layout(base).items():
        g[name] = val
    return prior


for _name, _val in _orchestra_path_layout(ORCHESTRA_DIR).items():
    globals()[_name] = _val

# --- identity-store cutover seam (INERT until the operator-armed) --------------------
# Cheap flag-file check FIRST so that while the cutover is unarmed (the default +
# current state) rotate_agent imports nothing new and Step-3 registration / the
# hold-cleanup prune behave byte-identically (direct-JSON). Only when armed do we
# lazily import the store: the successor alias becomes a PROVISIONAL generation row
# (project_faithful synthesizes the <root>-g<N> alias) and the hold-prune deletes
# that provisional row. The promote swap itself is Step-7 via promoter (p3a).
def _cutover_active() -> bool:
    # Resolve the flag path at CALL time from the (test-injectable) ORCHESTRA_DIR
    # module global so the check is hermetic against the live armed flag. Cheap
    # flag-FILE check; imports nothing new on the flag-off path.
    return (ORCHESTRA_DIR / "state" / "identity-store-cutover.flag").exists() \
        or os.environ.get("IDENTITY_STORE_CUTOVER") == "1"


def resolve_pred_generation_or_refuse(seat_name, pred):
    """DEC-1788483120 (RED 5/8): predecessor generation, DB-first, refusal
    ABSOLUTE on null-in-both — the fabricated default-1 is dead. Seeding a
    genuinely generation-less seat is a SEPARATE deliberate act via
    identity_store adopt_identity/register (full 5-field validation), then
    rotate. No flag path exists by consensus (r-a-b override of agy)."""
    from scripts.gen_resolve import resolve_predecessor_generation
    gen = resolve_predecessor_generation(
        seat_name, pred.get("generation") if isinstance(pred, dict) else None,
        orchestra_dir=str(ORCHESTRA_DIR))
    if not isinstance(gen, int):
        raise RuntimeError(
            f"rotation REFUSED for {seat_name!r}: no authoritative generation "
            f"in orchestra-registry.db OR the flat registry. A rotation must "
            f"not invent lineage numbers. Seed the seat via the identity "
            f"store (adopt_identity/register) first, then re-run.")
    return gen


def _db_register_provisional(root: str, generation: int, model: str) -> bool:
    """Register the successor as a provisional (non-canonical) generation in the
    store. Lazy import (only when armed) keeps rotate_agent INERT while unarmed.
    Returns True iff the DB handled the registration.

    F3 (gm msg_2e109091): after the DB write, FORCE a synchronous
    project_faithful (identity_writer.project_now) BEFORE returning — the very
    next step invokes spawn-agent.sh, a LEGACY registry.json reader, and inside
    the projector daemon's debounce window it would read a STALE registry
    (missing the <root>-g<N> alias), auto-register, and hit the U16 refusal.
    FAIL-CLOSED: a project_now failure propagates and ABORTS the rotation
    before spawn — never spawn into an unprojected state."""
    if not _cutover_active():
        return False
    from scripts.identity_store import identity_writer
    handled = identity_writer.register_provisional(
        str(ORCHESTRA_DIR), root, generation, model=model)
    if handled:
        identity_writer.project_now(str(ORCHESTRA_DIR))  # raises => abort pre-spawn
    return handled


def _db_prune_provisional(succ_alias: str) -> bool:
    """Prune the provisional successor generation (hold/abort cleanup) from the
    store. Derives (root, generation) from the ``<root>-g<N>`` alias. Returns True
    iff the DB handled the prune (caller then skips the legacy JSON prune)."""
    if not _cutover_active():
        return False
    root, sep, gen = succ_alias.rpartition("-g")
    if not sep or not gen.isdigit():
        return False
    from scripts.identity_store import identity_writer
    return identity_writer.prune_provisional(str(ORCHESTRA_DIR), root, int(gen))


def log(msg: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    line = f"{now} [rotate-agent] {msg}"
    print(line)
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True,
            env: dict | None = None) -> subprocess.CompletedProcess:
    cwd_str = str(cwd or ORCHESTRA_DIR)
    # FIX #2: env is forwarded so the spawn step can pass AGENT_MODEL through to
    # spawn-agent.sh (default None => inherit the parent env, exact prior behavior).
    p = subprocess.run(cmd, cwd=cwd_str, capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"Command failed ({p.returncode}): {' '.join(cmd)}\n"
            f"STDERR: {p.stderr.strip()}\nSTDOUT: {p.stdout.strip()}"
        )
    return p


def _spawn_successor(succ_alias: str, model: str, runtime: str) -> None:
    """Spawn the successor via spawn-agent.sh, propagating the resolved launch model as
    AGENT_MODEL (fix #2, fable-DOA class) AND the lineage runtime as AGENT_RUNTIME. Under
    the identity-store cutover spawn-agent.sh routes a not-yet-registered successor through
    spawn_adopt.py, which REFUSES without a runtime (gm msg_0a37acf3) — so a gemini/codex
    lineage previously mis-registered as claude (or was refused). Forward the runtime the
    lineage declares (resolved at the call site) so the successor is adopted on its OWN runtime."""
    spawn_cmd = [str(CODE_ROOT / "spawn-agent.sh"), succ_alias]
    run_cmd(spawn_cmd, env={**os.environ, "AGENT_MODEL": model, "AGENT_RUNTIME": runtime})


def get_agent_registry(agent_id: str) -> dict:
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(f"Registry not found at {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    agents = data.get("agents", {})
    if agent_id not in agents:
        raise KeyError(f"Agent '{agent_id}' not found in registry.json")
    return agents[agent_id]


def get_agent_session(agent_id: str) -> dict:
    if not SESSIONS_PATH.exists():
        return {}
    with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get(agent_id, {})


def _claude_projects_dir() -> Path:
    """Claude transcripts live under <config dir>/projects; honor CLAUDE_CONFIG_DIR."""
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(cfg) / "projects" if cfg else Path(os.path.expanduser("~/.claude/projects"))


def _sid_from_pane_events(session_name: str) -> str | None:
    try:
        r = subprocess.run(["tmux", "list-panes", "-t", session_name, "-F", "#{pane_id}"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        panes_dir = ORCHESTRA_DIR / "state" / "agent-events" / "panes"
        for pane in r.stdout.split():
            p = panes_dir / f"{pane.lstrip('%')}.json"
            if p.exists():
                sid = (json.loads(p.read_text(encoding="utf-8")) or {}).get("session_id")
                if sid:
                    return str(sid)
    except Exception:  # noqa: BLE001
        return None
    return None


def extract_active_sid(session_name: str, runtime: str) -> str | None:
    """Attempt to extract active session UUID for the given agent using declared identity."""
    # 0. From the pane-event files the shipped hooks write (<data>/state/agent-events/panes/
    # <pane>.json carries session_id) — the push truth, runtime-agnostic once hooks exist.
    sid = _sid_from_pane_events(session_name)
    if sid:
        return sid
    # 1. From agent-sessions.json
    sess_meta = get_agent_session(session_name)
    if sess_meta.get("session_id"):
        return sess_meta["session_id"]
    
    # 2. From runtime session logs
    if runtime == "gemini":
        brain_dir = Path(os.path.expanduser("~/.gemini/antigravity-cli/brain"))
        if brain_dir.exists():
            for p in sorted(brain_dir.glob("*/.system_generated/logs/transcript.jsonl"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        line = f.readline()
                        if not line:
                            continue
                        data = json.loads(line)
                        content = data.get("content", "")
                        if session_name in content:
                            return p.parent.parent.parent.name
                except Exception:
                    pass
    elif runtime == "codex":
        codex_dir = Path(os.path.expanduser("~/.codex/sessions"))
        if codex_dir.exists():
            for p in sorted(codex_dir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read(2048)
                        if session_name in content:
                            return p.stem
                except Exception:
                    pass
    elif runtime == "claude":
        claude_dir = _claude_projects_dir()
        if claude_dir.exists():
            for p in sorted(claude_dir.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        # FIX (bug 2a): scan the transcript HEAD, not just line 1. A
                        # Claude transcript's first line is a `last-prompt` meta object
                        # ({type,leafUuid,sessionId}); the seat identity ("You are
                        # <alias>") is ~line 9, so a line-1-only check MISSED the real
                        # successor transcript -> the promote fell back to a stale sid.
                        head = "".join(next(f, "") for _ in range(_SID_HEAD_LINES))
                        if session_name in head:
                            return p.stem
                except Exception:
                    pass

    return None


def ensure_handoff_artifact(seat_name: str, pred_info: dict, custom_task: str | None = None, resolved_gen: int | None = None) -> Path:
    """Ensure a valid structured handoff artifact exists for the seat."""
    HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
    handoff_path = HANDOFFS_DIR / f"{seat_name}.md"
    doc_handoff = DOCS_DIR / f"HANDOFF_{seat_name}-next.md"

    if doc_handoff.exists():
        content = doc_handoff.read_text(encoding="utf-8")
        handoff_path.write_text(content, encoding="utf-8")
        return handoff_path

    if handoff_path.exists() and handoff_path.stat().st_size > 50:
        return handoff_path

    # Synthesize standard handoff
    goal = custom_task or f"Maintain and execute active responsibilities for {seat_name} seat."
    first_effect = {
        "kind": "command",
        "target": f"python3 msg_store.py inbox --agent {seat_name}",
        "check": "exit 0"
    }

    body = f"""# Handoff: {seat_name} (Automated On-Demand Rotation)

## Current Goal
{goal}

## Phase State
- Seat: {seat_name}
- Predecessor Generation: {resolved_gen if isinstance(resolved_gen, int) else (pred_info.get('generation') if isinstance(pred_info.get('generation'), int) else 'unknown')}
- Runtime: {pred_info.get('runtime', 'claude')}
- Status: Ready for seamless rotation

## Working State
Autonomous mechanical rotation requested on-demand. All working trees, state files, and active session context are preserved.

## Decisions
- Rotate on-demand preserving canonical identity, active tmux buffers, and continuous execution.

## Open Loops
- Ingest handoff artifact and author readback.
- Verify comprehension against canaries.
- Reclaim canonical seat and execute pending tasks.

## File Roots Touched
- {pred_info.get('cwd', str(ORCHESTRA_DIR))}

## Next 3 Actions
1. FIRST_EFFECT {json.dumps(first_effect)}
2. Ingest pending mailbox messages from msg_store.
3. Continue seat objectives.
"""
    handoff_path.write_text(body, encoding="utf-8")
    log(f"Synthesized handoff artifact at {handoff_path}")
    return handoff_path


_CANARY_LINE_KEYS = ("id", "question", "expected_answer", "source_pointer")


def _parse_canary_line_form(text: str) -> list[dict]:
    """gm's baton form: one '- {id: q1, question: "...", expected_answer: "..."}' per line.
    Values are double-quoted and may contain commas, colons, single quotes and code; each
    value is bounded by a lookahead to the NEXT key or the closing brace, so inner
    punctuation never splits a field."""
    import re as _re
    keys = "|".join(_CANARY_LINE_KEYS)
    field = _re.compile(
        rf'\b({keys})\s*:\s*(?:"(.*?)"|([A-Za-z0-9_-]+))\s*(?=,\s*(?:{keys})\s*:|\}}\s*$)',
        _re.S)
    out = []
    for ln in text.splitlines():
        st = ln.strip()
        if not (st.startswith("- {") and st.endswith("}")):
            continue
        rec = {}
        for m in field.finditer(st):
            rec[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
        if rec.get("id") and rec.get("question"):
            out.append(rec)
    return out


def _parse_canary_json_form(text: str) -> list[dict]:
    """rab's baton form: a "canary_questions": [ ... ] array inside a JSON block (fenced or
    not). Decode the array in place with raw_decode so the rest of the block (which may not
    be valid JSON as a whole) cannot break it."""
    import re as _re
    out = []
    for m in _re.finditer(r'"canary_questions"\s*:\s*', text):
        start = m.end()
        try:
            arr, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(arr, list):
            for q in arr:
                if isinstance(q, dict) and q.get("id") and q.get("question"):
                    out.append(q)
            if out:
                break
    return out


def parse_baton_canary_questions(text: str) -> list[dict]:
    """Canary questions carried by a baton, in EITHER live shape (gm msg_86bcc168 item 3):
    gm's line form or a JSON block. [] when the baton carries none. Pure."""
    if not text:
        return []
    qs = _parse_canary_json_form(text)
    if not qs:
        qs = _parse_canary_line_form(text)
    return qs


_POINTER_LOCATOR = __import__("re").compile(
    r"(?:jsonl:)?\s*(msg_[0-9a-z_]+|turn-\d+|\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?\.\.\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?)",
    __import__("re").IGNORECASE)


def normalize_baton_canary(qs: list[dict]) -> list[dict]:
    """Seed-time discipline for baton-sourced canaries (gpt-6-astra-agent g3->g4, 2026-09-16):
    (a) ids become q1..qN in order (orig_id kept) — the readback prompt, the pre-gate and the
    grader all key sections by q<N>, so a baton's c1..c5 wedged a COMPLETE readback as
    incomplete; (b) a source_pointer is reduced to its resolvable locator (author_canary's
    _POINTER_FORM mirror: msg_<id> | turn-<n> | <ISO>..<ISO>); prose with no locator is
    DROPPED and recorded as pointer_defect=unresolvable-form (orig_source_pointer kept) so a
    prose pointer can never reach the grader as if it were resolvable. Pure."""
    out = []
    for i, q in enumerate(qs, 1):
        q = dict(q)
        qid = f"q{i}"
        if q.get("id") != qid:
            q["orig_id"] = q.get("id")
            q["id"] = qid
        ptr = q.get("source_pointer")
        if ptr is not None:
            m = _POINTER_LOCATOR.search(str(ptr))
            loc = f"jsonl:{m.group(1)}" if m else None
            if loc != ptr:
                q["orig_source_pointer"] = ptr
            if loc:
                q["source_pointer"] = loc
            else:
                q["source_pointer"] = None
                q["pointer_defect"] = "unresolvable-form"
        out.append(q)
    return out


def _baton_paths_for_seat(seat_name: str) -> list[Path]:
    """The two places a seat's baton is banked (docs/ first, then state/)."""
    return [DOCS_DIR / f"HANDOFF_{seat_name}-next.md", HANDOFFS_DIR / f"{seat_name}.md"]


def ensure_canary_artifact(successor_alias: str, seat_name: str) -> Path:
    """Ensure canary questions artifact exists for strict machine grading. A pre-placed
    canary (driver-authored, >20 bytes) always wins. Otherwise the questions come from the
    seat's BATON in either live shape (gm msg_86bcc168 item 3 — rab g17->g18 got the 2-q
    stub although its baton carried five in a JSON block); the generic stub is written ONLY
    when neither baton carries any."""
    HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
    canary_path = HANDOFFS_DIR / f"{successor_alias}.canary.json"
    if canary_path.exists() and canary_path.stat().st_size > 20:
        return canary_path

    for bp in _baton_paths_for_seat(seat_name):
        try:
            qs = parse_baton_canary_questions(bp.read_text(encoding="utf-8")) if bp.exists() else []
        except (OSError, UnicodeDecodeError):
            qs = []
        if qs:
            qs = normalize_baton_canary(qs)
            canary_path.write_text(json.dumps({
                "successor_id": successor_alias,
                "canonical_seat": seat_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "baton",
                "source_path": str(bp),
                "questions": qs,
            }, indent=2), encoding="utf-8")
            log(f"Authored canary artifact at {canary_path} from baton {bp} ({len(qs)} q)")
            return canary_path

    log(f"No canary_questions in any baton for {seat_name} "
        f"({', '.join(str(b) for b in _baton_paths_for_seat(seat_name))}); writing the 2-q stub")
    canary_data = {
        "successor_id": successor_alias,
        "canonical_seat": seat_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": "stub",
        "questions": [
            {
                "id": "q1",
                "question": f"What is the canonical seat identity being taken over?",
                "expected_answer": seat_name
            },
            {
                "id": "q2",
                "question": "What is the first effect to execute upon seat promotion?",
                "expected_answer": "python3 ~/orchestra/msg_store.py inbox"
            }
        ]
    }
    canary_path.write_text(json.dumps(canary_data, indent=2), encoding="utf-8")
    log(f"Authoring canary artifact at {canary_path}")
    return canary_path


def _load_canary_for(successor_alias: str) -> dict:
    """Load the successor's canary questions ({questions:[{id,question,...}]})."""
    p = HANDOFFS_DIR / f"{successor_alias}.canary.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- absent/unreadable canary -> no questions
        return {"questions": []}


def build_readback_prompt(canary: dict, *, seat_name: str,
                          successor_alias: str | None = None) -> str:
    """Pure builder: turn the canary questions into the instruction that drives
    the SUCCESSOR to author its own readback answering q1..qn.

    LIVE-RUN FIX (first mechanical rotation): the successor's cwd is its OWN repo,
    so relative paths + a literal `<your-alias>` placeholder made it write the
    readback into the wrong tree. The prompt now names the ABSOLUTE orchestra
    readback path with the REAL alias, and the ABSOLUTE handoff + canary READ
    paths, so a successor with any cwd writes/reads the files the grader uses."""
    qs = (canary or {}).get("questions", []) or []
    alias = successor_alias or "<your-alias>"
    rb_abs = str(HANDOFFS_DIR / f"{alias}.readback.md")
    canary_abs = str(HANDOFFS_DIR / f"{alias}.canary.json")
    handoff_abs = str(HANDOFFS_DIR / f"{alias}.md")
    lines = [
        f"You are the successor for canonical seat '{seat_name}'. Author your "
        f"comprehension readback to the ABSOLUTE path {rb_abs} (write it there "
        f"regardless of your current working directory — do NOT use a relative "
        f"path, which resolves into your own repo).",
        f"First READ your handoff + canary at {handoff_abs} and {canary_abs} "
        f"(absolute paths — they live in the orchestra state dir, not your cwd).",
        "",
        "REQUIREMENTS (a strict machine grader reads this — a generic/boilerplate "
        "answer will HOLD the rotation):",
        "- Write one section per question, each STARTING with the canary id on "
        "its own line: `q1`, `q2`, ... (the grader keys sections by that id; do "
        "NOT prefix with markdown `#`).",
        "- Answer in YOUR OWN WORDS, citing >=2 mission-specific anchors per "
        "question from the predecessor's ACTUAL state (commit SHAs, msg ids, task "
        "ids, file paths) — not restated instructions.",
        "- Format each answer across MULTIPLE LINES (one bullet per anchor/point) "
        "— NOT a single long paragraph. The whole readback must have at least 10 "
        "non-blank lines or a separate non-content gate rejects it as a stub.",
        "",
        "Questions:",
    ]
    for q in qs:
        qid = q.get("id", "q?")
        lines.append(f"- {qid}: {q.get('question', '')}")
    if not qs:
        lines.append("- (no canary questions found — author a substantive "
                     "own-words readback of the handoff)")
    return "\n".join(lines)


def _q_keyed_scaffold(successor_alias: str, seat_name: str, canary: dict) -> str:
    """A readback SCAFFOLD keyed by canary id (q1..qn) so it matches the grader's
    section convention. This is a placeholder the successor OVERWRITES with real
    answers — it deliberately carries no fabricated anchors, so on its own it
    HOLDs (the grader refuses an uncited answer). Written ONLY when no readback
    exists yet (never clobbers a successor-authored one)."""
    qs = (canary or {}).get("questions", []) or []
    # Section headers MUST start with the canary id (q1, q2, ...) to match the
    # grader's ^\*{0,2}(?:cq|q)(\d+) convention — NOT markdown `# q1`.
    out = [f"Successor Readback: {successor_alias} (seat {seat_name})", ""]
    if not qs:
        out += ["q1", "TODO: author your own-words readback of the handoff."]
    for q in qs:
        out += [f"{q.get('id', 'q1')}",
                f"TODO(successor): answer in your own words with >=2 real anchors "
                f"— {q.get('question', '')}", ""]
    return "\n".join(out)


def ensure_readback_and_grade(successor_alias: str, seat_name: str, handoff_path: Path) -> Path:
    """Ensure a readback artifact exists WITHOUT clobbering a successor-authored
    one. Idempotent-safe (gm msg_9f93660d): if a readback already exists it is
    LEFT UNTOUCHED (re-running the grade step must never destroy genuine work).
    Only when absent do we write a q-keyed scaffold (matching the grader's canary
    section convention) for the successor to fill in. Grading is NOT done here — a
    strict comprehension.json may ONLY originate from the real grader."""
    HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
    readback_path = HANDOFFS_DIR / f"{successor_alias}.readback.md"

    existing = ""
    if readback_path.exists():
        try:
            existing = readback_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    if existing.strip():
        log(f"Readback already present at {readback_path} — NOT overwriting "
            f"(preserving successor-authored work).")
        return readback_path

    canary = _load_canary_for(successor_alias)
    readback_path.write_text(_q_keyed_scaffold(successor_alias, seat_name, canary),
                             encoding="utf-8")
    log(f"Wrote q-keyed readback scaffold at {readback_path} (successor to author "
        f"real answers; boilerplate alone HOLDs at the grader).")
    return readback_path


def _rotation_artifact_paths(successor_alias: str) -> list[str]:
    """Repo-relative paths of THIS attempt's artifacts that exist on disk (the ones T4 and
    the audit trail need committed)."""
    out = []
    for suffix in ("readback.md", "canary.json", "comprehension.json"):
        rel = f"state/agent-handoffs/{successor_alias}.{suffix}"
        if (ORCHESTRA_DIR / rel).exists():
            out.append(rel)
    return out


def commit_rotation_artifacts(successor_alias: str, *, run_fn=None, sleep_fn=None,
                              lock_wait_s: float = 5.0) -> dict:
    """Commit the rotation artifacts and report the TRUTH by effect (gm msg_86bcc168 item 2).

    A data dir that is not a git repository (the normal public install: <data> is plain) skips
    the commit with ok=True and sha=None — the artifacts stay on disk as the audit trail.

    Returns {"ok", "sha", "reason", "retried"}. ok is True only when every artifact present
    on disk is tracked (git ls-files --error-unmatch rc 0) AND clean (empty porcelain) after
    the commit — the same predicate promoter's T4 applies ('EXISTS on disk but is
    UNCOMMITTED'). A commit refused by a shared-tree index.lock race waits lock_wait_s and
    retries ONCE. 'nothing to commit' is fine only if the artifacts are already tracked +
    clean. Never raises; never logs success it has not verified."""
    if not (ORCHESTRA_DIR / ".git").exists() and subprocess.run(
            ["git", "-C", str(ORCHESTRA_DIR), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True).returncode != 0:
        log(f"Rotation artifacts kept on disk under {ORCHESTRA_DIR / 'state' / 'agent-handoffs'} "
            f"(data dir is not a git repository; nothing to commit).")
        return {"ok": True, "sha": None, "reason": "data dir is not a git repository", "retried": False}
    run_fn = run_fn or run_cmd
    sleep_fn = sleep_fn or time.sleep
    targets = _rotation_artifact_paths(successor_alias)
    msg = f"chore(rotation): record handoff and readback artifacts for {successor_alias}"
    retried = False
    try:
        run_fn(["git", "add", "state/agent-handoffs/"], check=False)
        p = run_fn(["git", "commit", "-m", msg], check=False)
        blob = f"{p.stdout or ''}\n{p.stderr or ''}"
        if p.returncode != 0 and "index.lock" in blob:
            retried = True
            sleep_fn(lock_wait_s)
            run_fn(["git", "add", "state/agent-handoffs/"], check=False)
            p = run_fn(["git", "commit", "-m", msg], check=False)
            blob = f"{p.stdout or ''}\n{p.stderr or ''}"
        if p.returncode != 0 and "nothing to commit" not in blob:
            reason = f"git commit rc={p.returncode}: {blob.strip()[-300:]}"
            log(f"Rotation artifacts NOT committed for {successor_alias}: {reason}")
            return {"ok": False, "sha": None, "reason": reason, "retried": retried}
        # by effect: tracked AND clean, per artifact (T4's predicate)
        for rel in targets:
            ls = run_fn(["git", "ls-files", "--error-unmatch", rel], check=False)
            if ls.returncode != 0:
                reason = f"{rel}: EXISTS on disk but is UNCOMMITTED (not tracked after commit)"
                log(f"Rotation artifacts NOT committed for {successor_alias}: {reason}")
                return {"ok": False, "sha": None, "reason": reason, "retried": retried}
            st = run_fn(["git", "status", "--porcelain", "--", rel], check=False)
            if (st.stdout or "").strip():
                reason = f"{rel}: tracked but UNCOMMITTED changes remain ({(st.stdout or '').strip()[:80]})"
                log(f"Rotation artifacts NOT committed for {successor_alias}: {reason}")
                return {"ok": False, "sha": None, "reason": reason, "retried": retried}
        sha = (run_fn(["git", "rev-parse", "--short=10", "HEAD"], check=False).stdout or "").strip() or None
        log(f"Committed rotation artifacts for {successor_alias} ({sha}; {len(targets)} artifact(s) "
            f"verified tracked+clean{'; retried after index.lock' if retried else ''})")
        return {"ok": True, "sha": sha, "reason": "", "retried": retried}
    except Exception as e:  # noqa: BLE001 — truthful failure, never a crash mid-rotation
        reason = f"git commit step raised: {e}"
        log(f"Rotation artifacts NOT committed for {successor_alias}: {reason}")
        return {"ok": False, "sha": None, "reason": reason, "retried": retried}


def hold_artifacts_uncommitted(seat_name: str, successor_alias: str, reason: str | None) -> dict:
    """The HOLD result for an uncommitted-artifacts rotation (pure)."""
    return {"status": "hold_artifacts_uncommitted", "seat": seat_name,
            "successor": successor_alias, "reason": reason or "artifacts uncommitted",
            "promoted": False}


def archive_attempt_artifacts(successor_alias: str) -> None:
    """ARCHIVE-ON-HOLD (gm ruling msg_5c740bd8): when a rotation attempt ends in
    HOLD/FAIL, rename that attempt's artifacts (readback/canary/comprehension) to
    a timestamped `.held-<ts>` suffix — audit trail preserved, alias FREED so a
    same-alias retry gets a fresh q-keyed scaffold instead of re-HOLDing forever
    on a stale template. Generation-proof (no template-shape recognition). Best-
    effort: a missing artifact is a no-op."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for suffix in ("readback.md", "canary.json", "comprehension.json"):
        src = HANDOFFS_DIR / f"{successor_alias}.{suffix}"
        if src.exists():
            try:
                src.rename(HANDOFFS_DIR / f"{successor_alias}.{suffix}.held-{ts}")
                log(f"Archived {src.name} -> .held-{ts} (alias freed for retry)")
            except OSError as e:
                log(f"Warning: could not archive {src.name}: {e}")


def _prune_registered_successor(succ_alias: str) -> None:
    """Remove a provisioning successor row from the registry (hold/abort cleanup).
    Idempotent and best-effort: a missing row / unreadable registry is a no-op, so
    cleanup never itself wedges the rotation. Uses the shared registry lock."""
    try:
        # cutover: prune the provisional generation row (the alias disappears);
        # skip the direct-JSON prune. INERT: _db returns False while unarmed.
        if _db_prune_provisional(succ_alias):
            log(f"Pruned provisional successor generation for '{succ_alias}' from the store")
            return
        with registry_lock(timeout_s=15.0):
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                reg_data = json.load(f)
            if reg_data.get("agents", {}).pop(succ_alias, None) is not None:
                with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
                    json.dump(reg_data, f, indent=2)
                log(f"Pruned orphan successor row '{succ_alias}' from registry.json")
    except Exception as e:  # noqa: BLE001 -- cleanup is best-effort; never wedge
        log(f"Warning: could not prune successor row '{succ_alias}': {e}")


def should_promote_after_grade(grade_result: dict) -> bool:
    """The arm precondition: a promotion may proceed ONLY on a real-grader STRICT
    PASS with a written artifact. Mirrors auto_grade.may_graduate exactly
    (disposition==PASS AND strict is True AND artifact_written) so the gate
    self-enforces the strict invariant rather than trusting the caller. FAIL/
    REFUSED, a non-strict PASS, or a PASS with no written artifact all HOLD."""
    return (grade_result.get("disposition") == "PASS"
            and grade_result.get("strict") is True
            and bool(grade_result.get("artifact_written")))


def _tmux_send(session: str, key: str) -> None:
    subprocess.run(["tmux", "send-keys", "-t", session, key], capture_output=True)


def _tmux_tail(session: str) -> str:
    r = subprocess.run(["tmux", "capture-pane", "-p", "-t", session, "-S", "-15"],
                       capture_output=True, text=True)
    return r.stdout or ""


def _tmux_inject(session: str, text: str, *, send_fn=_tmux_send,
                 capture_fn=_tmux_tail, sleep_fn=time.sleep,
                 enter_attempts: int = 3) -> bool:
    """Inject a prompt into the pane and VERIFY it committed. CLI 2.1.260 ingests
    multi-word text as a bracketed paste; an Enter in the same send-keys call (or
    sent too soon) is absorbed into the paste and the prompt SITS in the composer
    uncommitted — this stranded gm-g42's step-9 promotion inject (the operator had to
    hand-Enter it, 09-04; same class as the watch_gateway digit-only bug). So:
    send the text, then SEPARATE Enters, and after each one check by effect that
    the composer no longer shows the text (prefix match — long pastes wrap/elide).
    Returns True iff the commit was verified; False = honest failure, never a
    silent strand. Real seam; tests pass fake send/capture/sleep fns."""
    send_fn(session, text)
    sleep_fn(0.5)
    probe = text[:60]
    for attempt in range(1, enter_attempts + 1):
        send_fn(session, "Enter")
        sleep_fn(0.8 * attempt)
        if probe not in capture_fn(session):
            return True
    return False


def drive_successor_readback(successor_alias: str, seat_name: str, *,
                             session: str, inject_fn=_tmux_inject, wait_fn=None,
                             scaffold_text: str = "", poll_attempts: int = 20) -> bool:
    """Drive the spawned successor to AUTHOR its own readback: inject the
    canary-derived prompt into its pane, then poll the readback file until it
    DIFFERS from the scaffold (the successor wrote real answers). Returns True iff
    a real (non-scaffold, non-empty) readback appeared. Injectable seams
    (inject_fn / wait_fn) keep it hermetic — no real tmux/sleep in tests.

    This closes the load-bearing gap: rotate_agent used to AUTHOR a template
    itself; now the SUCCESSOR authors the readback the grader reads."""
    canary = _load_canary_for(successor_alias)
    prompt = build_readback_prompt(canary, seat_name=seat_name,
                                   successor_alias=successor_alias)
    inject_fn(session, prompt)
    readback_path = HANDOFFS_DIR / f"{successor_alias}.readback.md"
    _wait = wait_fn if wait_fn is not None else (lambda: time.sleep(15))
    # Compare on normalized (stripped) content so a trivially-whitespace-mutated
    # scaffold is NOT mistaken for real authored answers (review hardening). This
    # flag is ADVISORY ONLY — the grader is the real gate — but keep its semantic
    # honest.
    baseline = (scaffold_text or "").strip()
    for _ in range(poll_attempts):
        try:
            current = readback_path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current.strip() and current.strip() != baseline:
            return True
        _wait()
    return False


_SID_HEAD_LINES = 40  # bug 2a: how many transcript head lines to scan for the seat id


def _readback_is_complete(successor_alias: str, canary: dict | None = None) -> bool:
    """BUG 1 (premature-grade TIMING): True iff the successor's readback is a
    COMPLETE authored artifact — not the scaffold, not mid-authoring. Guards the
    grade step so it never runs against an in-progress/empty/scaffold file (which
    strict-FAILs a genuine successor). Criteria: file exists + non-blank; carries NO
    scaffold TODO markers; has >=10 non-blank lines (the grader's own stub gate); and
    has a section header for the LAST canary q-id (gm: 'q5 section appearing')."""
    rb = HANDOFFS_DIR / f"{successor_alias}.readback.md"
    try:
        text = rb.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.strip():
        return False
    if "TODO(successor)" in text or "TODO: author" in text:
        return False
    non_blank = [ln for ln in text.splitlines() if ln.strip()]
    # Stub floor: 10 non-blank lines OR 600+ chars of content. A successor that answers each
    # question as one long paragraph line (7 lines, 1500 chars) is complete, not a stub —
    # the promoter's own T4 floor already lets a strict PASS outrank the line count, so the
    # pre-gate must not be the strictest link.
    if len(non_blank) < 10 and sum(len(ln.strip()) for ln in non_blank) < 600:
        return False
    canary = canary if canary is not None else _load_canary_for(successor_alias)
    qs = (canary or {}).get("questions", []) or []
    if qs:
        # Use the GRADER's OWN section parser as the single source of truth so this
        # pre-gate can NEVER be stricter than the grader it fronts. A stricter matcher
        # (e.g. one that only accepts bare `q5` and rejects the grader-valid `**Q5**`,
        # `## q5`, `Q5:`, uppercase — the forms executors.py:106 documents to
        # successors) would WEDGE a complete, passing readback on the 180s wait. The
        # grader requires exactly {q1..qn} sections (no dup); split_readback_sections
        # raises on any mismatch, which we treat as not-yet-complete.
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from rotation_gate_manual import split_readback_sections
            split_readback_sections(text, len(qs))
        except Exception:
            return False
    return True


def _wait_for_complete_readback(successor_alias: str, *, timeout_s: float = 90.0,
                                poll_s: float = 3.0, canary: dict | None = None) -> bool:
    """Poll until the readback is complete or timeout. Prevents the premature-grade
    race — the successor may still be authoring when the grade would otherwise run."""
    canary = canary if canary is not None else _load_canary_for(successor_alias)
    deadline = time.monotonic() + timeout_s
    while True:
        if _readback_is_complete(successor_alias, canary):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


def _successor_sid_verified(succ_sid: str | None, succ_alias: str) -> bool:
    """BUG 2b (fail-closed): a successor sid is promotable ONLY if it is a real sid
    whose transcript actually references THIS successor. Fail-closed: a missing sid,
    the 'unverifiable-sid' sentinel, no transcript, or a stale/dead transcript that
    does NOT name the successor (the 37eb175b corpse class) => False, so the caller
    HOLDs and never points canonical at a corpse via the None->stale-fallback path.
    Accepts either an explicit lineage declaration OR the seat name appearing in the
    transcript head (how a live successor reads before it declares)."""
    if not succ_sid or succ_sid == "unverifiable-sid":
        return False
    try:
        # Locate the transcript by sid via os.path.expanduser (honored at runtime AND
        # under test), with sid_invariants as a fallback resolver.
        path = None
        claude_dir = _claude_projects_dir()
        if claude_dir.exists():
            for p in claude_dir.glob(f"**/{succ_sid}.jsonl"):
                path = p
                break
        if path is None:
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import sid_invariants as SI
                sp = SI.find_transcript(succ_sid)
                if sp:
                    path = Path(sp)
            except Exception:
                path = None
        if path is None or not Path(path).exists():
            return False
        # explicit lineage declaration (best-effort) OR the seat name in the head
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import sid_invariants as SI2
            if SI2.declared_identity(Path(path), {succ_alias}) == succ_alias:
                return True
        except Exception:
            pass
        with open(path, "r", encoding="utf-8") as f:
            head = "".join(next(f, "") for _ in range(_SID_HEAD_LINES))
        return succ_alias in head
    except Exception:
        return False


def grade_successor_readback(successor_alias: str, seat_name: str, *,
                             predecessor_sid: str | None) -> dict:
    """Grade the successor's ACTUAL readback with the REAL grader (strict) and
    return its disposition. NEVER fabricates a pass: the strict comprehension.json
    is written by the grader itself, only on PASS/FAIL; a generic readback with no
    mission-specific anchors FAILs (HOLD_GRADE), and a missing locator REFUSEs.
    Only disposition=="PASS" may feed a promotion."""
    result = auto_grade_successor(successor_alias, predecessor_sid, strict=True)
    log(f"Grade for {successor_alias}: disposition={result.get('disposition')} "
        f"exit={result.get('exit_code')} artifact_written="
        f"{result.get('artifact_written')}")
    return result


def execute_rotation(
    seat_name: str,
    runtime_override: str | None = None,
    model_override: str | None = None,
    custom_task: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    skip_spawn: bool = False
) -> dict:
    """Execute complete on-demand mechanical rotation transaction."""
    log(f"Starting on-demand rotation for seat: {seat_name} (dry_run={dry_run}, force={force})")

    # Step 1: Inspect predecessor
    pred = get_agent_registry(seat_name)
    pred_gen = resolve_pred_generation_or_refuse(seat_name, pred)
    succ_gen = pred_gen + 1
    succ_alias = f"{seat_name}-g{succ_gen}"
    runtime = runtime_override or pred.get("runtime") or "claude"
    # FIX #2: the claude default was the stale, credit-context-wrong "claude-3-5-sonnet";
    # a bare rotate whose predecessor row has no model would propagate that. Default to the
    # current fleet model so an empty-pred rotate still launches a live [1m] model, never a
    # stale/DOA one (the override / predecessor model still win when present).
    default_model = "gemini-3.7-flash" if runtime == "gemini" else ("gpt-5.6-terra" if runtime == "codex" else "claude-opus-4-8[1m]")
    model = model_override or pred.get("model") or default_model
    cwd = pred.get("cwd", str(ORCHESTRA_DIR))
    system_prompt = pred.get("system_prompt", f"prompts/{seat_name}.md")

    log(f"Predecessor: {seat_name} (gen {pred_gen}, runtime={runtime}, model={model})")
    log(f"Successor alias: {succ_alias} (gen {succ_gen})")

    # Step 2: Handoff & Canaries
    handoff_path = ensure_handoff_artifact(seat_name, pred, custom_task, resolved_gen=pred_gen)
    ensure_canary_artifact(succ_alias, seat_name)

    if dry_run:
        log("DRY-RUN: Preconditions verified successfully. No processes spawned or files modified.")
        return {
            "status": "dry_run_success",
            "canonical_seat": seat_name,
            "predecessor_generation": pred_gen,
            "successor_alias": succ_alias,
            "successor_generation": succ_gen,
            "runtime": runtime,
            "model": model,
            "handoff_path": str(handoff_path)
        }

    # Step 3: Register Successor Alias
    # cutover: register the successor as a PROVISIONAL (non-canonical) generation
    # in the store; project_faithful synthesizes the <root>-g<N> alias (Part B) so
    # the direct-JSON write is skipped. INERT: _use_db is False while unarmed =>
    # the byte-identical direct-JSON registration below runs exactly as before.
    _use_db = _db_register_provisional(seat_name, succ_gen, model)
    with registry_lock(timeout_s=15.0):
        if not _use_db:
            with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
                reg_data = json.load(f)

            reg_data["agents"][succ_alias] = {
                "name": succ_alias,
                "tier": pred.get("tier", "T2"),
                "machine": pred.get("machine", "vps"),
                "runtime": runtime,
                "model": model,
                "cwd": cwd,
                "tmux_session": succ_alias,
                "generation": succ_gen,
                "lineage_root": seat_name,
                "always_on": pred.get("always_on", True),
                "system_prompt": system_prompt,
                "status": "provisioning"
            }
            with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump(reg_data, f, indent=2)
        log(f"Registered successor alias '{succ_alias}' in registry.json")

    # Step 4: Spawn Successor
    if not skip_spawn:
        log(f"Spawning successor in tmux session '{succ_alias}'...")
        _spawn_successor(succ_alias, model, runtime)
        time.sleep(4)

    # Step 5: Extract Successor Session ID
    succ_sid = extract_active_sid(succ_alias, runtime) or "unverifiable-sid"
    log(f"Resolved successor session ID: {succ_sid}")

    # Step 6: Successor Readback, then REAL machine grade (no fabrication).
    # (a) write a q-keyed scaffold ONLY if no readback exists (never clobber a
    # successor-authored one); (b) DRIVE the spawned successor to author its own
    # real answers to the canary before grading; (c) grade the ACTUAL readback.
    scaffold_path = ensure_readback_and_grade(succ_alias, seat_name, handoff_path)
    if not skip_spawn:
        try:
            scaffold_text = scaffold_path.read_text(encoding="utf-8")
        except OSError:
            scaffold_text = ""
        authored = drive_successor_readback(
            succ_alias, seat_name, session=succ_alias, scaffold_text=scaffold_text)
        if not authored:
            log(f"Successor {succ_alias} did not author a real readback (still the "
                f"scaffold) — the grader will HOLD; predecessor keeps the seat.")
    # BUG 1 (premature-grade TIMING): on the DRIVE path (a live successor is authoring),
    # WAIT for a COMPLETE readback before grading, so the strict grade never runs against
    # an in-progress / scaffold / empty file (which FAILs a genuine successor mid-author —
    # the OB g36 class). --skip-spawn has no live author (the readback is static on disk),
    # so it grades as-is. If the drive path never completes within the window, HOLD as
    # incomplete and do NOT grade — and do NOT archive (preserve the genuine in-progress
    # artifact so a slow/retrying successor can finish it).
    # --skip-spawn WAKE: the retry path assumed a readback already sat on disk and graded the
    # fresh SCAFFOLD because the successor pane had never been asked anything. If no COMPLETE
    # readback exists, a --skip-spawn run drives the successor exactly like the spawn path
    # (readback prompt injected into the already-open pane), waits the bounded window, and
    # HOLDs on timeout; a complete readback on disk is still graded as-is (no second inject).
    needs_wake = skip_spawn and not _readback_is_complete(succ_alias)
    if needs_wake:
        log(f"--skip-spawn: no complete readback for {succ_alias} on disk — waking the "
            f"successor pane '{succ_alias}' with the readback prompt and waiting for a "
            f"successor-authored readback before grading.")
        try:
            scaffold_text = scaffold_path.read_text(encoding="utf-8")
        except OSError:
            scaffold_text = ""
        drive_successor_readback(succ_alias, seat_name, session=succ_alias,
                                 scaffold_text=scaffold_text)
    if (not skip_spawn or needs_wake) and not _wait_for_complete_readback(succ_alias, timeout_s=180.0):
        log(f"HOLD_READBACK_INCOMPLETE: successor {succ_alias} readback not complete "
            f"(missing final q-section / still scaffold / <10 lines) within the wait "
            f"window; NOT grading. Predecessor {seat_name} retains the seat.")
        _prune_registered_successor(succ_alias)
        return {
            "status": "hold_readback_incomplete",
            "seat": seat_name,
            "successor": succ_alias,
            "promoted": False,
        }
    if needs_wake and succ_sid == "unverifiable-sid":
        succ_sid = extract_active_sid(succ_alias, runtime) or "unverifiable-sid"
        log(f"Re-resolved successor session ID after wake: {succ_sid}")
    pred_sid = pred.get("session_id") or extract_active_sid(seat_name, runtime)
    if pred_sid and not pred.get("session_id"):
        log(f"Predecessor session ID resolved by effect (registry row had none): {pred_sid}")
    grade_result = grade_successor_readback(
        succ_alias, seat_name, predecessor_sid=pred_sid)

    # Commit artifacts for T4 compliance — TRUTHFULLY (gm msg_86bcc168 item 2): the old
    # step logged 'Committed rotation artifacts' unconditionally (check=False) and on rab
    # g17->g18 the commit had not landed, so promoter T4 refused AFTER the log said success.
    artifact_commit = commit_rotation_artifacts(succ_alias)

    # ARM PRECONDITION: promote ONLY on a real-grader strict PASS. A generic
    # readback FAILs (HOLD_GRADE) and a missing locator REFUSEs — neither may
    # promote. This closes the auto-vouch fabricated-pass path.
    if not should_promote_after_grade(grade_result):
        log(f"HOLD_GRADE: successor {succ_alias} did not earn a strict PASS "
            f"(disposition={grade_result.get('disposition')}); NOT promoting. "
            f"Predecessor {seat_name} retains the canonical seat.")
        # Prune the provisioning successor row we registered before grading — an
        # orphan always_on row pages pulse forever and can become a recovery/spawn
        # target. (The successor is registered pre-grade; a hold must undo that.)
        _prune_registered_successor(succ_alias)
        # ARCHIVE-ON-HOLD: free the alias so a same-alias retry starts clean (a
        # stale readback would otherwise be preserved by the never-clobber guard
        # and re-HOLD forever). Audit trail kept as .held-<ts>.
        archive_attempt_artifacts(succ_alias)
        return {
            "status": "hold_grade",
            "seat": seat_name,
            "successor": succ_alias,
            "grade": grade_result,
            "promoted": False,
        }

    # HOLD BEFORE PROMOTE when the artifacts did not land in git (item 2): promoter T4 would
    # refuse anyway, but with a misleading trail; refuse HERE with the real git reason. The
    # canary+readback pair is NOT archived (a genuine strict-PASS readback is preserved so
    # the driver can commit it and retry with --skip-spawn, the recovery used live).
    if not artifact_commit.get("ok"):
        log(f"HOLD_ARTIFACTS_UNCOMMITTED: {artifact_commit.get('reason')}; NOT promoting. "
            f"Predecessor {seat_name} retains the canonical seat. Readback+canary kept on "
            f"disk for a commit + --skip-spawn retry.")
        _prune_registered_successor(succ_alias)
        return hold_artifacts_uncommitted(seat_name, succ_alias, artifact_commit.get("reason"))

    # Step 7: Atomic Promotion Primitive — the LAST gate, run BEFORE any
    # observable/hard-to-reverse projection. promoter.promote owns its own T4
    # readback gate that can REFUSE even after the grade PASSed (the live-proven
    # split: a Step-7 tmux rename before a Step-8 promote refusal left tmux
    # canonical=successor while the registry still held the predecessor, twice
    # repaired by hand). Ordering rule (gm msg_67c57526 #2): never rename before
    # this passes; a refusal must roll back the Step-3 provisioning row and leave
    # tmux UNCHANGED (renames are Step 8, below).
    # BUG 2b (fail-closed sid): NEVER promote on an unverifiable/stale successor sid.
    # If succ_sid is the 'unverifiable-sid' sentinel (extract_active_sid found no live
    # transcript), promoter.promote(session_id=None) falls back to the canonical seat's
    # STALE agent-sessions sid and accepts it via the operator-assertion hatch — pointing
    # canonical at a corpse (the 37eb175b class). Require a verified live successor sid
    # whose transcript references THIS successor; else HOLD and never call promote.
    if not _successor_sid_verified(succ_sid, succ_alias):
        log(f"HOLD_UNVERIFIABLE_SID: successor {succ_alias} sid {succ_sid!r} is not a "
            f"verified live successor transcript; NOT promoting (canonical stays, no "
            f"corpse). Predecessor {seat_name} retains the seat.")
        _prune_registered_successor(succ_alias)
        archive_attempt_artifacts(succ_alias)
        return {
            "status": "hold_unverifiable_sid",
            "seat": seat_name,
            "successor": succ_alias,
            "successor_sid": succ_sid,
            "promoted": False,
        }

    log(f"Executing atomic promotion via promote_successor...")
    try:
        report = promoter.promote(
            canonical_id=seat_name,
            successor_session=succ_alias,
            session_id=succ_sid if succ_sid != "unverifiable-sid" else None,
            model=model,
            generation=succ_gen,
            rotation_trigger_override="on-demand mechanical rotation requested by user",
            shadow_skip_reason="on-demand rotation",
            readback_waived_reason=None
        )
    except promoter.PromotionRefused as e:
        # Clean HOLD: the last gate refused. tmux was NEVER touched (Step 8 is
        # below), so there is no split. Roll back the pre-gate mutations (the
        # Step-3 provisioning row that pages pulse + this attempt's artifacts),
        # exactly as the hold_grade path does. Predecessor retains the seat.
        log(f"HOLD_PROMOTE: promote refused for {succ_alias}: {e}. Rolling back "
            f"the provisioning successor row + attempt artifacts; tmux left "
            f"UNCHANGED (no split). Predecessor {seat_name} retains the seat.")
        _prune_registered_successor(succ_alias)
        archive_attempt_artifacts(succ_alias)
        return {
            "status": "hold_promote",
            "seat": seat_name,
            "successor": succ_alias,
            "error": str(e),
            "promoted": False,
        }
    except Exception:
        # Any other failure of the last gate must ALSO roll back the pre-gate
        # mutation before surfacing — an orphan always_on row pages pulse and
        # can become a recovery/spawn target.
        log(f"Promote failed unexpectedly for {succ_alias}; rolling back the "
            f"provisioning successor row + attempt artifacts before surfacing.")
        _prune_registered_successor(succ_alias)
        archive_attempt_artifacts(succ_alias)
        raise

    # Step 7b: SYNCHRONOUS flat-store projection of the committed promote (F3's
    # twin, post-promote side). promote() writes the DB source records and the
    # comment says "the projector regenerates the JSON" — but no projector daemon
    # is guaranteed live, so every legacy reader (registry.json, agent-sessions,
    # state/agents/, the tmux context widget) kept serving the PREDECESSOR's
    # sid/generation until someone projected by hand (gm gen43, 09-05: widget
    # showed gen42's 94% on the promoted gen43 pane). Unlike F3 this must NOT
    # abort: the promotion is already durable in the DB — projection failure is
    # a loud warning, and the DB-first resolve chain remains authoritative.
    if _cutover_active():
        try:
            from scripts.identity_store import identity_writer
            identity_writer.project_now(str(ORCHESTRA_DIR))
            log("Projected promote to flat stores (project_now).")
        except Exception as e:
            log(f"WARNING: post-promote project_now FAILED ({e}) — flat stores "
                f"(registry.json/agent-sessions/state/agents) are STALE until the "
                f"projector runs; DB is authoritative and correct.")

    # Step 8: Safe Tmux Session Swap — an IDEMPOTENT PROJECTION of the now-
    # committed authority change (STOP KILLING - Rename & Keep Predecessor).
    # Runs ONLY after promote() succeeded, so a refusal can never split tmux
    # from the registry.
    pred_old_tmux = f"{seat_name}-gen{pred_gen}"

    # Check if predecessor session already renamed
    pred_old_exists = subprocess.run(["tmux", "has-session", "-t", pred_old_tmux], capture_output=True).returncode == 0
    if not pred_old_exists:
        pred_tmux_exists = subprocess.run(["tmux", "has-session", "-t", seat_name], capture_output=True).returncode == 0
        if pred_tmux_exists:
            log(f"Renaming active predecessor tmux session '{seat_name}' -> '{pred_old_tmux}' (retained as fallback)...")
            subprocess.run(["tmux", "rename-session", "-t", seat_name, pred_old_tmux], capture_output=True)

    # Rename successor session to canonical
    succ_tmux_exists = subprocess.run(["tmux", "has-session", "-t", succ_alias], capture_output=True).returncode == 0
    if succ_tmux_exists:
        log(f"Renaming successor tmux session '{succ_alias}' -> '{seat_name}' (canonical)...")
        subprocess.run(["tmux", "rename-session", "-t", succ_alias, seat_name], capture_output=True)

    # Step 9: Inject Task Continuation / FIRST_EFFECT
    task_injection = (
        f"You are the newly promoted CANONICAL agent '{seat_name}' (Generation {succ_gen}). "
        f"Your handoff has been absorbed and graded PASS. "
        f"Proceed immediately with your first effect and active seat responsibilities."
    )
    if not _tmux_inject(seat_name, task_injection):
        log(f"WARNING: promotion inject to '{seat_name}' NOT verified committed — "
            f"the prompt may be sitting in the composer; press Enter in the pane.")

    log(f"SUCCESS: On-demand rotation for '{seat_name}' completed cleanly (Gen {succ_gen})!")
    return {
        "status": "success",
        "canonical_seat": seat_name,
        "generation": succ_gen,
        "predecessor_archived_tmux": pred_old_tmux,
        "session_id": succ_sid,
        "report": report
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="On-demand mechanical agent rotation orchestrator")
    parser.add_argument("seat_name", help="The canonical seat identity to rotate (e.g. gemini-gm, pm, dev)")
    parser.add_argument("--runtime", default=None, help="Override agent runtime (claude, gemini, codex)")
    parser.add_argument("--model", default=None, help="Override agent model")
    parser.add_argument("--task", default=None, help="Initial task or goal for the successor")
    parser.add_argument("--dry-run", action="store_true", help="Simulate rotation without spawning or modifying state")
    parser.add_argument("--force", action="store_true", help="Force rotation regardless of context thresholds")
    parser.add_argument("--skip-spawn", action="store_true", help="Skip spawning new tmux session if already started")

    args = parser.parse_args()
    try:
        res = execute_rotation(
            seat_name=args.seat_name,
            runtime_override=args.runtime,
            model_override=args.model,
            custom_task=args.task,
            dry_run=args.dry_run,
            force=args.force,
            skip_spawn=args.skip_spawn
        )
        print(json.dumps(res, indent=2, default=str))
        return 0
    except Exception as e:
        log(f"ERROR: Rotation failed for {args.seat_name}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
