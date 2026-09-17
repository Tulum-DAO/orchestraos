#!/usr/bin/env python3
"""sid_invariants — the four session-pointer invariants, mechanized.

Commissioned after
an operator-ordered fleet audit found TWO live crossed rows.
Why this is P1 plumbing rather than hygiene: a fleet-wide respawn reads these
rows, so a crossed row swaps two agents' identities silently, mid-flight. The
known gotcha was "a writer NULLS a sid"; this class is strictly worse — a
writer put ANOTHER AGENT'S sid in a row.

    INV1  no two rows share a session_id
    INV2  sessions.session_id == registry.session_id
    INV3  every recorded sid resolves to a transcript, searched across ALL
          project dirs, and the row records which dir
    INV4  the transcript's DECLARED identity matches the row key
    INV5  competing sids are resolved by SUBSTANCE, never by recency
    INV6  a transcript with no declaration is UNVERIFIABLE, never PASSING
    INV7  every resolver agrees on every live agent, and tmux pointers exist

INV7 (ia, adopted by gm gen-14 over its own narrower INV2 ask): the fleet has
TWO resolution paths reading DIFFERENT stores. `resolve_delivery_target`
answers about the store its caller feeds it; `resolve_live_head` defaults to
live tmux + agent-sessions.json. gm consolidated its panes, updated the
registry only, verified with the first resolver and got ('gm', direct-live) —
while the second returned ('gm-gen14', direct-live). Both correct, different
stores, and the OPERATOR-FACING path (approval/questionnaire delivery) was routing
to the wrong pane throughout the period gm believed it verified.

That is the orthogonality axis in a new position: not content-vs-authorship
but ONE QUESTION, TWO ORACLES, AND NO ASSERTION THAT THEY AGREE.

INV5 (ia, derived from the repair pass): client-page-access had a 3-line/27KB/
3-SECOND aborted compaction stub that was NEWER than its 710-line/2.3MB/6-day
head. An implementer "modernizing" rows to the newest sid orphans six days of
work and calls it a repair. Rank by line count and span; never by mtime.

INV6 (ia, and it is a defect this module shipped with): a no-declaration
transcript was reported as info and counted as passing. A checker that
silently passes what it cannot see is ornamental. UNVERIFIABLE is now its own
reported state, never folded into clean.

INV4's two whitelisted exceptions, both proven necessary by ia's audit:
  * lineage rename — a canonical name legitimately holds the current
    generation's session (what `gm` does). Decided from DECLARED registry
    fields (lineage/lineage_root/succeeded_by/handoff_from/parent), never
    from name-prefix guessing.
  * compaction summary — a summary MENTIONS other agents. A matcher that
    cannot tell a DECLARATION from a MENTION false-positives on exactly
    this; ia's audit hit it.

Declaration-vs-mention is decided STRUCTURALLY, never by line distance
(no_proximity_lint binding): a declaration is (a) in an early init entry,
(b) not inside a compaction-summary entry, and (c) names a KNOWN agent id.

IMPLEMENTER TRAP, recorded where gm ruled it belongs — in the DOING, not the
specifying: the invariant is correct as written; the repair pass that searches
ONE project dir manufactures FALSE DEATHS and destroys valid pointers (it
nulled client-page-access, whose session lives under its own repo's project
dir). Hence `find_transcript` globs every project dir, and the suite pins that
with a cross-project-dir fixture.

REPORTS. It does not repair, and it does not refuse — wiring a refusal into
the respawn path is an arming decision (gm + the operator), not a build detail.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ORCH = Path(os.environ.get("SID_ORCH") or os.environ.get("ORCHESTRA_DIR")
            or os.path.expanduser("~/orchestra"))
PROJECTS_ROOT = Path(os.environ.get(
    "SID_PROJECTS", os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")))
CODEX_SESSIONS_ROOT = Path(os.environ.get(
    "CODEX_SESSIONS_ROOT", os.path.expanduser("~/.codex/sessions")))

# "You are <agent-id>" / "You are **<agent-id>**". The captured token is only
# trusted when it is a KNOWN agent id — that is what turns a prose mention
# ("You are the reviewer") into a non-match instead of a false identity.
# Minimum length is 2, NOT 3: the original {2,40} quantifier meant a captured
# token needed >=3 chars, so `gm` — the fleet's most important identity — was
# structurally invisible to this predicate on every plane that consumes it
# (INV4, the grade plane's H2, session-index's matcher). Found by building a
# negative fixture that used gm as the stranger, not by review. Loosening is
# safe because a token only counts when it is a KNOWN agent id, so "You are
# an agent" still matches nothing.
# CASE: the charset was [a-z], and 31 of 431 real agent ids start with an
# UPPERCASE letter (Some-App-Opus, Full-Audit, Some-one-pager, ...).
# They were as invisible as `gm` was, for the same reason — a constraint
# that reads like tightening is also a blind spot. Found by gm's ordered
# sweep of every length/shape/charset constraint in the identity plane,
# run against the REAL id set rather than reasoned about.
_DECL_RE = re.compile(
    r"(?:You are|I am) (?:\*\*)?([A-Za-z][A-Za-z0-9_\-]{0,40})(?:\*\*)?\b")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def session_uuid_from_stem(stem: str) -> str:
    """Extract standard UUID from a filename stem (e.g. rollout-YYYY-MM-DDTHH-MM-SS-<uuid> -> <uuid>),
    or return stem as-is if no UUID is present."""
    if not stem:
        return ""
    m = _UUID_RE.search(stem)
    return m.group(0) if m else stem

# Structural markers of a continuation/summary entry — a summary MENTIONS
# agents and must never be read as a declaration.
_SUMMARY_MARKERS = (
    "This session is being continued from a previous conversation",
    "Context: This summary will be shown in a list",
    "analysis:",
)
INIT_SCAN_ENTRIES = 40      # declarations live in the init injection


def load_stores(orch: Path = None):
    orch = orch or ORCH
    sessions = json.loads((orch / "state" / "agent-sessions.json").read_text())
    registry = json.loads((orch / "registry.json").read_text())
    return sessions, (registry.get("agents") or {})


# ---------------------------------------------------------------- transcripts
def find_transcript(
    sid: str,
    projects_root: Path = None,
    gemini_brain_root: Path = None,
    codex_sessions_root: Path = None,
) -> str | None:
    """Search EVERY project dir (and Gemini brain logs, and Codex sessions). Transcripts are cwd-scoped,
    so a single-dir existence check produces false deaths (gm's repair pass, 2026-08-19)."""
    if not sid:
        return None
    root = Path(projects_root or os.environ.get("SID_PROJECTS", PROJECTS_ROOT))
    hits = sorted(glob.glob(str(root / "*" / f"{sid}.jsonl")))
    if hits:
        return hits[0]
    g_root = Path(gemini_brain_root or os.environ.get("GEMINI_BRAIN_ROOT", os.path.expanduser("~/.gemini/antigravity-cli/brain")))
    gemini_hit = g_root / sid / ".system_generated" / "logs" / "transcript.jsonl"
    if gemini_hit.is_file():
        return str(gemini_hit)
    alt_hit = g_root / sid / "transcript.jsonl"
    if alt_hit.is_file():
        return str(alt_hit)
    c_root = Path(codex_sessions_root or os.environ.get("CODEX_SESSIONS_ROOT", CODEX_SESSIONS_ROOT))
    if c_root.is_dir():
        if sid.startswith("rollout-"):
            c_hits = sorted(glob.glob(str(c_root / "**" / f"{sid}.jsonl"), recursive=True))
            if c_hits:
                return c_hits[0]
        uuid_part = session_uuid_from_stem(sid)
        c_hits = sorted(glob.glob(str(c_root / "**" / f"rollout-*{uuid_part}*.jsonl"), recursive=True))
        if c_hits:
            return c_hits[0]
        c_hits_any = sorted(glob.glob(str(c_root / "**" / f"*{sid}*.jsonl"), recursive=True))
        if c_hits_any:
            return c_hits_any[0]
    return None


def declared_identity(transcript: str, known_ids: set) -> str | None:
    """The identity the transcript DECLARES about itself, or None.

    Structural, not proximity-based: only early entries (the init injection),
    never a compaction-summary entry, and the token must be a known agent id.
    """
    try:
        fh = open(transcript, errors="replace")
    except OSError:
        return None
    with fh:
        for i, line in enumerate(fh):
            if i >= INIT_SCAN_ENTRIES:
                return None
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("isCompactSummary") or d.get("type") == "summary":
                continue

            # Codex session_meta (base_instructions / developer_instructions)
            if d.get("type") == "session_meta":
                payload = d.get("payload") or {}
                instructions = payload.get("base_instructions") or payload.get("developer_instructions") or ""
                if isinstance(instructions, str) and instructions:
                    for m in _DECL_RE.finditer(instructions):
                        if m.group(1) in known_ids:
                            return m.group(1)
                continue

            # Codex response_item (type == "message")
            if d.get("type") == "response_item":
                payload = d.get("payload") or {}
                if payload.get("type") == "message":
                    content = payload.get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("text"))
                    if text:
                        if any(m in text for m in _SUMMARY_MARKERS):
                            continue
                        for m in _DECL_RE.finditer(text):
                            if m.group(1) in known_ids:
                                return m.group(1)
                continue

            content = (d.get("message") or {}).get("content") if "message" in d else d.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(c.get("text", "") for c in content
                                if isinstance(c, dict))
            else:
                continue
            if any(m in text for m in _SUMMARY_MARKERS):
                continue          # a summary mentions; it does not declare
            for m in _DECL_RE.finditer(text):
                if m.group(1) in known_ids:
                    return m.group(1)
    return None


# --------------------------------------------------------------- INV5 substance
STUB_MAX_LINES = 10          # ia's threshold, from the shipped fixture
STUB_MAX_SPAN_S = 60


def transcript_substance(path: str) -> dict:
    """Measure a transcript by SUBSTANCE — lines and span. NEVER mtime: the
    shipped fixture is a 3-line/3-second aborted compaction stub that was
    NEWER than the 710-line/6-day head it would have replaced."""
    lines = 0
    first_ts = last_ts = None
    try:
        fh = open(path, errors="replace")
    except OSError:
        return {"lines": 0, "span_s": 0, "is_stub": True, "bytes": 0}
    with fh:
        for line in fh:
            lines += 1
            try:
                d = json.loads(line)
                ts = d.get("timestamp") or d.get("created_at")
                if not ts and isinstance(d.get("payload"), dict):
                    ts = d["payload"].get("timestamp")
            except json.JSONDecodeError:
                continue
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
    span = 0
    if first_ts and last_ts:
        from datetime import datetime
        try:
            a = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
            b = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            span = int((b - a).total_seconds())
        except ValueError:
            span = 0
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return {"lines": lines, "span_s": span, "bytes": size,
            "is_stub": lines < STUB_MAX_LINES or span < STUB_MAX_SPAN_S}


def competing_transcripts(agent: str, project_dir: str, known_ids: set,
                          exclude_sid: str = None) -> list:
    """Other transcripts in the SAME project dir that DECLARE this agent."""
    out = []
    if not project_dir or not os.path.isdir(project_dir):
        return out
    for path in sorted(glob.glob(os.path.join(project_dir, "*.jsonl"))):
        sid = Path(path).stem
        if exclude_sid and sid == exclude_sid:
            continue
        if declared_identity(path, known_ids) == agent:
            out.append({"session_id": sid, **transcript_substance(path)})
    return out


# ------------------------------------------------------------------- lineage
def same_lineage(a: str, b: str, registry: dict) -> bool:
    """DECLARED lineage relation only — registry fields, never name prefixes.
    A prefix rule would silently bless any two agents sharing a word.

    NOT `parent` , live specimen 64c85df6): parent
    records WHO SPAWNED a row — authorship of the spawn, not identity. 24 live
    rows carry parent=gm, so counting it made every gm-declaring transcript
    "same lineage" with every agent gm ever spawned; the matcher seated a
    gen-5 gm init on four unrelated rows, and the promote verb's successor
    check consumed the same false True. Succession fields (succeeded_by /
    handoff_from / lineage / lineage_root) ARE identity; spawn is not."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        row = registry.get(x) or {}
        for field in ("succeeded_by", "handoff_from", "lineage",
                      "lineage_root"):
            if str(row.get(field) or "") == y:
                return True
    ra, rb = registry.get(a) or {}, registry.get(b) or {}
    for field in ("lineage", "lineage_root"):
        va, vb = ra.get(field), rb.get(field)
        if va and vb and va == vb:
            return True
    return False


# ------------------------------------------------------------- INV7 resolvers
def _load_resolvers():
    """Both oracles, or None. Loaded by path because message-router.py is not
    a legal module name."""
    import importlib.util
    out = {}
    try:
        sys.path.insert(0, str(ORCH / "scripts"))
        import lineage_resolve as LR
        out["live_head"] = LR.resolve_live_head
    except Exception:
        out["live_head"] = None
    try:
        spec = importlib.util.spec_from_file_location(
            "message_router", str(ORCH / "scripts" / "message-router.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        out["delivery"] = m.resolve_delivery_target
        out["live_tmux"] = m.live_sessions if hasattr(m, "live_sessions") else None
    except Exception:
        out["delivery"] = None
        out["live_tmux"] = None
    return out


def tmux_session_exists(name: str) -> bool:
    if not name:
        return False
    r = subprocess.run(["tmux", "has-session", "-t", str(name)],
                       capture_output=True, timeout=10)
    return r.returncode == 0


def check_resolver_agreement(sessions: dict, registry: dict,
                             agents_to_check: list = None) -> list:
    """INV7: resolution must not depend on WHICH STORE a component reads,
    and a tmux pointer must name a session that exists.

    CORRECTION FOUND BY CALIBRATING: `resolve_live_head` DELEGATES to
    `resolve_delivery_target`. They are ONE resolver with two callers, so
    comparing them on identical input is vacuous — it can never fire. gm's
    observed ('gm') vs ('gm-gen14') was the SAME logic fed DIFFERENT STORES:
    gm passed a registry-derived meta it had just repaired, ia used the
    production default (agent-sessions.json). So the assertion that carries
    meaning is sessions-derived resolution == registry-derived resolution.
    One question, two STORES, and now an assertion that they agree.
    """
    R = _load_resolvers()
    if not R.get("live_head") or not R.get("delivery"):
        return [{"invariant": "INV7", "agent": None,
                 "reason": "resolvers unavailable — cannot assert agreement"}]
    findings = []
    live = set()
    try:
        live = set(R["live_tmux"]()) if R.get("live_tmux") else set()
    except Exception:
        live = set()
    meta_registry = {k: dict(v) for k, v in registry.items()
                     if isinstance(v, dict)}
    # TARGET SELECTION, corrected: "registry status == online" could not see
    # `agy` — a LIVE agent whose registry status is 'n/a' and whose registry
    # row carries no tmux_session at all. A check that picks its own
    # population from one field inherits that field's blind spots, which is
    # this night's recurring shape one level up from the check itself.
    # Live-ness is decided by a LIVE PANE named in EITHER store.
    if agents_to_check is not None:
        targets = agents_to_check
    else:
        targets = set()
        for store in (registry, sessions):
            for k, row in store.items():
                if not isinstance(row, dict):
                    continue
                if str(row.get("status") or "") == "online":
                    targets.add(k)
                name = row.get("tmux_session")
                if name and name in live:
                    targets.add(k)
        targets = sorted(targets)
    for agent in targets:
        heads = {}
        for label, meta in (("sessions", sessions), ("registry", meta_registry)):
            try:
                heads[label] = R["delivery"](agent, live, meta)[0]
            except Exception as e:
                findings.append({"invariant": "INV7", "agent": agent,
                                 "reason": f"resolution from {label} raised: {e}"})
                heads[label] = "<error>"
        hs, hr = heads.get("sessions"), heads.get("registry")
        if hs != hr:
            # TWO SEVERITIES, distinguished so this cannot cry wolf: a check
            # that reports one class loudly gets tuned away and takes the
            # other class with it.
            both_named = bool(hs) and bool(hr)
            findings.append({
                "invariant": "INV7", "agent": agent,
                "from_sessions": hs, "from_registry": hr,
                "severity": "CROSSING" if both_named else "ROUTING-GAP",
                "reason": ("the stores name DIFFERENT live heads — whichever "
                           "store a component reads decides which pane it "
                           "acts on, and the operator-facing delivery path is one "
                           "of those components"
                           if both_named else
                           "one store can resolve this agent and the other "
                           "cannot — a component reading the empty side "
                           "cannot route to a live agent at all")})
        # a pointer that names a non-existent session is a DEAD POINTER
        for store, row in (("sessions", sessions.get(agent) or {}),
                           ("registry", registry.get(agent) or {})):
            name = row.get("tmux_session")
            if name and not tmux_session_exists(name):
                findings.append({
                    "invariant": "INV7", "agent": agent, "store": store,
                    "tmux_session": name,
                    "reason": "tmux_session names a session that does not "
                              "exist — a daemon reading this will act on a "
                              "pane that is not there, or recreate it"})
    return findings


# ---------------------------------------------------------------- invariants
def check(sessions: dict, registry: dict, projects_root: Path = None,
          check_resolvers: bool = True) -> dict:
    known_ids = set(sessions) | set(registry)
    findings = {"INV1": [], "INV2": [], "INV3": [], "INV4": [], "INV5": [],
                "INV7": []}
    # INV6: UNVERIFIABLE is its own state. It is NOT info, and it is NOT a
    # pass — a checker that silently passes what it cannot see is ornamental.
    unverifiable = []
    info = {"lineage_exempt": []}

    # INV1 — no two rows share a session_id
    by_sid: dict = {}
    for key in sorted(sessions):
        sid = (sessions[key] or {}).get("session_id")
        if sid:
            by_sid.setdefault(sid, []).append(key)
    for sid, keys in sorted(by_sid.items()):
        if len(keys) > 1:
            exempt = all(same_lineage(keys[0], k, registry) for k in keys[1:])
            row = {"session_id": sid, "rows": keys}
            if exempt:
                info["lineage_exempt"].append({"invariant": "INV1", **row})
            else:
                findings["INV1"].append(row)

    # INV2 — the two stores must agree on WHO an agent is AND WHERE it lives.
    # tmux_session was added after a P0 : the stores agreed on
    # session_id while disagreeing on tmux_session, so this check reported
    # CLEAN at the exact moment the divergence existed — and agent-recovery,
    # which reads the sessions store's tmux field, spawned a SECOND claude on
    # a live session id. Same identity, different address: a two-store
    # divergence a sid-only comparison structurally cannot see.
    for key in sorted(sessions):
        srow, rrow = (sessions[key] or {}), (registry.get(key) or {})
        s_sid, r_sid = srow.get("session_id"), rrow.get("session_id")
        if s_sid and r_sid and s_sid != r_sid:
            findings["INV2"].append({"agent": key, "field": "session_id",
                                     "sessions": s_sid, "registry": r_sid})
        s_tmux, r_tmux = srow.get("tmux_session"), rrow.get("tmux_session")
        if s_tmux and r_tmux and s_tmux != r_tmux:
            findings["INV2"].append({
                "agent": key, "field": "tmux_session",
                "sessions": s_tmux, "registry": r_tmux,
                "why": "a daemon reading the stale half will act on the wrong "
                       "pane — this is the duplicate-writer path"})

    # INV3 — sid resolves to a transcript in SOME project dir; row records it
    for key in sorted(sessions):
        row = sessions[key] or {}
        sid = row.get("session_id")
        if not sid:
            continue
        path = find_transcript(sid, projects_root)
        if not path:
            findings["INV3"].append({"agent": key, "session_id": sid,
                                     "reason": "no transcript in any project dir"})
            continue
        recorded = row.get("project_dir")
        actual = str(Path(path).parent)
        if not recorded:
            findings["INV3"].append({"agent": key, "session_id": sid,
                                     "reason": "project_dir not recorded",
                                     "actual": actual})
        elif str(recorded).rstrip("/") != actual.rstrip("/"):
            findings["INV3"].append({"agent": key, "session_id": sid,
                                     "reason": "project_dir mismatch",
                                     "recorded": recorded, "actual": actual})

    # INV4 — declared identity matches the row key
    for key in sorted(sessions):
        sid = (sessions[key] or {}).get("session_id")
        if not sid:
            continue
        path = find_transcript(sid, projects_root)
        if not path:
            continue                      # already an INV3 finding
        # INV5 — does this row point at a STUB while a substantial transcript
        # declaring the same agent exists? Substance decides, never recency.
        subs = transcript_substance(path)
        if subs["is_stub"]:
            rivals = [c for c in competing_transcripts(
                key, str(Path(path).parent), known_ids, exclude_sid=sid)
                if not c["is_stub"]]
            if rivals:
                findings["INV5"].append({
                    "agent": key, "row_points_at": sid, "row_substance": subs,
                    "substantial_candidates": rivals,
                    "reason": "row points at a stub while a substantial "
                              "transcript for this agent exists — resolving "
                              "by recency here orphans the real session"})

        decl = declared_identity(path, known_ids)
        if decl is None:
            unverifiable.append({"agent": key, "session_id": sid,
                                 "transcript": path,
                                 "reason": "no identity declaration found — "
                                           "UNVERIFIABLE, not passing"})
        elif decl != key:
            if same_lineage(decl, key, registry):
                info["lineage_exempt"].append({"invariant": "INV4",
                                               "agent": key, "declared": decl})
            else:
                findings["INV4"].append({"agent": key, "declared": decl,
                                         "session_id": sid,
                                         "transcript": path})

    if check_resolvers:
        findings["INV7"] = check_resolver_agreement(sessions, registry)

    total = sum(len(v) for v in findings.values())
    return {"clean": total == 0, "violations": total,
            "findings": findings, "info": info,
            "unverifiable": unverifiable,
            "unverifiable_count": len(unverifiable),
            "counts": {k: len(v) for k, v in findings.items()},
            "rows_checked": len([k for k in sessions
                                 if (sessions[k] or {}).get("session_id")])}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sid-invariants")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 on any violation (for a gate/cron); this tool "
                         "NEVER repairs and never blocks a respawn on its own "
                         "— wiring it as a refusal is the operator's arming call")
    ap.add_argument("--strict", action="store_true",
                    help="with --check, UNVERIFIABLE rows also fail (INV6: "
                         "unverifiable is never a pass). The count is printed "
                         "unconditionally either way — it can never hide.")
    ap.add_argument("--orch", default=None)
    args = ap.parse_args(argv)

    sessions, registry = load_stores(Path(args.orch) if args.orch else None)
    result = check(sessions, registry)
    if args.json:
        print(json.dumps(result, indent=1, sort_keys=True))
    else:
        print(f"rows with a sid: {result['rows_checked']} | "
              f"violations: {result['violations']} {result['counts']} | "
              f"UNVERIFIABLE: {result['unverifiable_count']}")
        for inv, rows in sorted(result["findings"].items()):
            for r in rows:
                print(f"  {inv}: {json.dumps(r, sort_keys=True)}")
        print(f"  (info) lineage-exempt: {len(result['info']['lineage_exempt'])}")
    failed = (not result["clean"]) or (args.strict and result["unverifiable"])
    return 1 if (args.check and failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
