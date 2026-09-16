"""Live committed handoff provider for the lineage daemon.

Discovers and parses committed handoff artifacts across workspace docs and state directories,
extracting machine-readable JSON blocks and mtime to transition SOFT_AUTHORING -> SOFT_READY
and feed S3 confirmation.
"""
from __future__ import annotations

import json
import os
import re
import hashlib
import subprocess
from pathlib import Path
from typing import Tuple, Optional, Dict, Any

ORCHESTRA_DIR = os.environ.get(
    "ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))


def _extract_machine_block(md_text: str) -> Optional[Dict[str, Any]]:
    """Extract the last ```json fenced block in a markdown text."""
    blocks = re.findall(r"```json\s*(.*?)```", md_text, re.DOTALL)
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(md_text: str) -> Dict[str, str]:
    """Return {normalized-heading -> body} for each `## heading` section. The
    field name is the heading's FIRST token lowercased (so `## decisions (guards
    + lane boundaries)` -> `decisions`, matching what agents actually author)."""
    out: Dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(md_text))
    for i, mobj in enumerate(matches):
        raw = mobj.group(1).strip()
        name = re.split(r"[\s(]", raw, 1)[0].strip().lower()
        start = mobj.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out[name] = md_text[start:end].strip()
    return out


def _bullets(body: str) -> list:
    return [re.sub(r"^[-*]\s+", "", ln).strip()
            for ln in body.splitlines() if ln.strip().startswith(("-", "*"))]


def _numbered(body: str) -> list:
    return [re.sub(r"^\s*\d+[.)]\s+", "", ln).strip()
            for ln in body.splitlines() if re.match(r"^\s*\d+[.)]\s+", ln)]


def _kv_line(ln: str):
    """Split a `key: value` line on the FIRST colon ONLY (A2 CRITICAL: a
    source_pointer value carries its own colons, e.g. 'jsonl region ~05:25Z' —
    splitting on every ':' would mangle it). Returns (key_lower, value) or None."""
    s = re.sub(r"^[-*]\s+", "", ln).strip()
    if ":" not in s:
        return None
    k, v = s.split(":", 1)               # FIRST colon only
    return k.strip().lower(), v.strip()


def _parse_colon_items(body: str) -> list:
    """Parse a COLON-MULTILINE list section (the format the fleet authors) into a
    list of dicts. Each item OPENS on a `- id:` sub-bullet and its following
    `key: value` continuation lines (question:, source_pointer:) belong to it.
    Split every line on the FIRST colon only (values keep their colons)."""
    items: list = []
    cur = None
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        is_bullet = stripped.startswith(("-", "*"))
        kv = _kv_line(raw)
        if is_bullet and kv and kv[0] == "id":
            cur = {"id": kv[1]}
            items.append(cur)
        elif cur is not None and kv:
            cur[kv[0]] = kv[1]
    return items


def _parse_colon_kv(body: str) -> Dict[str, Any]:
    """Parse a COLON-MULTILINE key/value section (e.g. `## first_effect` ->
    {kind, target, check}). First-colon-only split; bullets or plain lines."""
    out: Dict[str, Any] = {}
    for raw in body.splitlines():
        kv = _kv_line(raw)
        if kv:
            out[kv[0]] = kv[1]
    return out


def _extract_markdown_sections(md_text: str) -> Optional[Dict[str, Any]]:
    """Parse a genuine agent-authored `##`-section handoff into a schema dict.

    axis-1 (accept-either) + axis-3 (POSITIVE MARKER): returns None unless the
    fixed-schema anchor `## current_goal` is present — a daemon auto-snapshot or
    arbitrary markdown has no such section, so it is POSITIVELY rejected (not
    incidentally by key-absence). NO richness fabrication: only fields actually
    authored are populated, so a hollow handoff still FAILS the downstream
    validate(require_richness) gate (the critical-safety-caveat: markdown feeds
    the SAME gate, no bypass). Fields markdown cannot carry structurally
    (canary_questions, first_effect) are left ABSENT on purpose."""
    secs = _split_sections(md_text)
    if not secs.get("current_goal"):
        return None
    data: Dict[str, Any] = {"current_goal": secs["current_goal"].strip()}

    if secs.get("phase_state"):
        ps: Dict[str, Any] = {}
        for ln in _bullets(secs["phase_state"]):
            low = ln.lower()
            if low.startswith("next_gate:"):
                ps["next_gate"] = ln.split(":", 1)[1].strip()
            elif low.startswith("plan_ref:"):
                ps["plan_ref"] = ln.split(":", 1)[1].strip()
            elif low.startswith("current_step:"):
                ps["current_step"] = ln.split(":", 1)[1].strip()
            else:
                mph = re.search(r"phase\s+(\d+)\s+of\s+(\d+)", low)
                if mph:
                    ps["phase_n"], ps["phase_m"] = int(mph.group(1)), int(mph.group(2))
        if ps:
            data["phase_state"] = ps

    acts = _numbered(secs.get("next_3_actions", "")) or _bullets(secs.get("next_3_actions", ""))
    if acts:
        data["next_3_actions"] = acts

    decs = []
    for b in _bullets(secs.get("decisions", "")):
        if " — " in b:
            t, r = b.split(" — ", 1)
            decs.append({"text": t.strip(), "rationale": r.strip()})
        elif " - " in b:
            t, r = b.split(" - ", 1)
            decs.append({"text": t.strip(), "rationale": r.strip()})
        else:
            decs.append({"text": b, "rationale": ""})
    if decs:
        data["decisions"] = decs

    for fld in ("open_loops", "hazards", "file_roots_touched"):
        vals = _bullets(secs.get(fld, ""))
        if vals:
            data[fld] = vals
    if secs.get("working_state"):
        data["working_state"] = secs["working_state"].strip()

    # A2 Axis-1(b) (DEC-1788207386): LIFT the exam data agents author as
    # COLON-MULTILINE sections so a markdown-only handoff can pass require_richness.
    # canary_questions: `- id:/question:/source_pointer:` sub-bullets (first-colon
    # split preserves colon-bearing source_pointers). NEVER scraped from
    # next_3_actions prose (N4 anti-scrape: only a real `## canary_questions`/
    # `## first_effect` section is lifted).
    if secs.get("canary_questions"):
        cq = _parse_colon_items(secs["canary_questions"])
        if cq:
            data["canary_questions"] = cq
    if secs.get("first_effect"):
        fe = _parse_colon_kv(secs["first_effect"])
        if fe:
            data["first_effect"] = fe
    return data


def _merge_handoff_fields(json_block: Dict[str, Any],
                          markdown: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """A2 Axis-2(a) MERGE per-field (DEC-1788207386): json wins per-field, markdown
    FILLS any field json omits — killing the silent-drop (a partial json block used
    to drop markdown decisions/open_loops/hazards). Rules:
      - json key ABSENT            -> markdown fills.
      - json scalar PRESENT        -> json wins.
      - json LIST present+NON-EMPTY-> json wins OUTRIGHT (never a union — a union
                                      could duplicate/conflict canary exam items).
      - json LIST present but EMPTY-> treated as OMITTED -> markdown fills (so an
                                      author who writes []+real markdown items does
                                      NOT silently lose them).
    """
    out = dict(json_block)
    if not markdown:
        return out
    for k, mv in markdown.items():
        jv = out.get(k, _ABSENT)
        if jv is _ABSENT:
            out[k] = mv                              # json omitted -> markdown fills
        elif isinstance(jv, list) and len(jv) == 0:
            out[k] = mv                              # empty json list -> markdown fills
        # else: json present (scalar or non-empty list) -> json wins (keep out[k])
    return out


_ABSENT = object()


def _parse_handoff_text(md_text: str) -> Optional[Dict[str, Any]]:
    """axis-1 accept-either: a fenced ```json block AND/OR `##`-section markdown.
    A2 Axis-2(a): when BOTH are present, MERGE per-field (json wins per-field,
    markdown fills the gaps) — no longer json-wins-OUTRIGHT (which silently dropped
    markdown fields). Markdown-only or json-only are handled by the merge degenerating
    to that single source."""
    block = _extract_machine_block(md_text)
    markdown = _extract_markdown_sections(md_text)
    if block is not None:
        return _merge_handoff_fields(block, markdown)
    return markdown


# The daemon agent-state snapshot's signature fields (state/agent-handoffs/<id>.json
# writer). A genuine authored handoff never carries these — so their presence is a
# POSITIVE discriminator that rejects a snapshot even if it also carries a schema-
# lookalike key like current_goal (axis-3, robust to key-collision, not incidental).
_SNAPSHOT_MARKERS = ("saved_at", "raw_output_tail", "recent_messages",
                     "status_at_save")


def _is_daemon_snapshot(data: Dict[str, Any]) -> bool:
    return isinstance(data, dict) and any(k in data for k in _SNAPSHOT_MARKERS)


def _get_git_provenance(file_path: str) -> Optional[Dict[str, Any]]:
    """Verify git commit details if available, capturing provenance and file hash.
    
    If the file is inside a git repository, it MUST be tracked and committed.
    If the file is not in a git repository at all (e.g. non-git test temp dir),
    it generates file hash and mtime without requiring git commit.
    """
    dirname = os.path.dirname(file_path)
    basename = os.path.basename(file_path)
    commit_sha = None
    timestamp = None

    # Check if directory is inside a git repository
    is_git_repo = False
    try:
        chk_repo = subprocess.run(
            ["git", "-C", dirname, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, check=False
        )
        is_git_repo = (chk_repo.returncode == 0 and chk_repo.stdout.strip() == "true")
    except Exception:
        is_git_repo = False

    if is_git_repo:
        try:
            # Must be tracked by git
            res_tracked = subprocess.run(
                ["git", "-C", dirname, "ls-files", "--error-unmatch", "--", basename],
                capture_output=True, text=True, check=False
            )
            if res_tracked.returncode != 0:
                return None

            # Must have at least one commit log
            res_log = subprocess.run(
                ["git", "-C", dirname, "log", "-1", "--pretty=format:%H %at", "--", basename],
                capture_output=True, text=True, check=False
            )
            if res_log.returncode != 0 or not res_log.stdout.strip():
                return None

            parts = res_log.stdout.strip().split()
            if len(parts) >= 2:
                commit_sha = parts[0]
                timestamp = float(parts[1])
            else:
                return None
        except Exception:
            return None

    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
    except Exception:
        file_hash = ""

    if timestamp is None:
        try:
            timestamp = os.path.getmtime(file_path)
        except Exception:
            timestamp = 0.0

    return {
        "handoff_commit_sha": commit_sha,
        "path": os.path.abspath(file_path),
        "file_hash": file_hash,
        "authoring_timestamp": timestamp,
    }


def read_committed_handoff(
    agent_id: str,
    lineage_root: Optional[str] = None,
    orchestra_dir: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """Locate and parse the committed handoff for agent_id / lineage_root.

    Returns (handoff_dict, mtime) or (None, None) on failure.
    """
    od = orchestra_dir or ORCHESTRA_DIR
    root = lineage_root or agent_id

    candidates = []
    if cwd and os.path.isdir(cwd):
        candidates.extend([
            os.path.join(cwd, "docs", f"HANDOFF_{root}-next.md"),
            os.path.join(cwd, "docs", f"HANDOFF_{agent_id}-next.md"),
            os.path.join(cwd, f"HANDOFF_{root}-next.md"),
            os.path.join(cwd, f"HANDOFF_{agent_id}-next.md"),
            os.path.join(cwd, "docs", f"HANDOFF_{root}.md"),
            os.path.join(cwd, "docs", f"HANDOFF_{agent_id}.md"),
        ])

    # axis-4 (DEC-1788165818): the canonical agent-authored artifact is
    # docs/HANDOFF_<root>-next.md. state/agent-handoffs/<id>.{md,json} is
    # HARD-EXCLUDED as a freshness SOURCE — it is daemon-clobbered (the lineage
    # snapshot re-writes it every beat, so its mtime/content churns and is NOT a
    # trustworthy authoring signal). Do not add those paths back here.
    candidates.extend([
        os.path.join(od, "docs", f"HANDOFF_{root}-next.md"),
        os.path.join(od, "docs", f"HANDOFF_{agent_id}-next.md"),
        os.path.join(od, "docs", f"HANDOFF_{root}.md"),
        os.path.join(od, "docs", f"HANDOFF_{agent_id}.md"),
    ])

    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            mtime = os.path.getmtime(p)
            data = None
            if p.endswith(".json"):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            elif p.endswith(".md"):
                with open(p, "r", encoding="utf-8") as f:
                    text = f.read()
                data = _parse_handoff_text(text)   # axis-1 accept-either

            if data is not None and isinstance(data, dict):
                # axis-3 POSITIVE MARKER (DEC-1788165818): reject a daemon
                # auto-snapshot even if it carries a schema-LOOKALIKE key (e.g.
                # current_goal). Rejecting by key-ABSENCE alone is incidental
                # (what axis-3 forbids); the snapshot signature (saved_at +
                # raw_output_tail/recent_messages/status_at_save) is the positive
                # discriminator no genuine handoff carries.
                if _is_daemon_snapshot(data):
                    continue
                # Require a recognizable handoff field (a genuine schema signal).
                recognized_keys = {"current_goal", "phase_state", "open_loops", "decisions", "next_3_actions", "hazards"}
                if not any(k in data for k in recognized_keys):
                    continue

                provenance = _get_git_provenance(p)
                if provenance is None:
                    continue

                # Inject provenance metadata without overwriting existing explicit fields
                for k, v in provenance.items():
                    if k not in data:
                        data[k] = v
                return data, mtime
        except Exception:
            continue

    return None, None


def build_live_handoff_provider(
    orchestra_dir: Optional[str] = None,
    sessions_meta: Optional[dict] = None,
    registry: Optional[dict] = None,
):
    """Build a callable (canary, lineage_root=None) -> (dict | None, float | None)."""
    od = orchestra_dir or ORCHESTRA_DIR

    def provider(canary: str, lineage_root: Optional[str] = None) -> Tuple[Optional[dict], Optional[float]]:
        # Resolve agent cwd if available
        cwd = None
        if registry and isinstance(registry, dict):
            agent_info = (registry.get("agents") or {}).get(canary)
            if isinstance(agent_info, dict) and agent_info.get("cwd"):
                cwd = agent_info["cwd"]

        if not cwd and sessions_meta and isinstance(sessions_meta, dict):
            sess_info = sessions_meta.get(canary)
            if isinstance(sess_info, dict) and sess_info.get("project_dir"):
                cwd = sess_info["project_dir"]

        return read_committed_handoff(canary, lineage_root=lineage_root, orchestra_dir=od, cwd=cwd)

    return provider


def prior_committed_rev(agent_id: str, lineage_root: Optional[str] = None,
                        orchestra_dir: Optional[str] = None) -> Optional[str]:
    """A2 Axis-3(b): the freshness rev (`commit_sha:file_hash`) of the PENULTIMATE
    committed version of the seat's CANONICAL handoff (docs/HANDOFF_<root>-next.md)
    — i.e. the git parent of the current handoff commit, over the SAME path the
    provider resolves (path-identity). Returns None when there is no prior version
    (a first-ever handoff -> the caller uses the eligible sentinel). Content-hash
    is sha256 of the PRIOR blob bytes so a byte-identical no-op recommit yields the
    same file_hash the current rev has (the no-op stays stale)."""
    od = orchestra_dir or ORCHESTRA_DIR
    root = lineage_root or agent_id
    rel = os.path.join("docs", f"HANDOFF_{root}-next.md")
    if not os.path.isfile(os.path.join(od, rel)):
        rel = os.path.join("docs", f"HANDOFF_{agent_id}-next.md")
    try:
        # the two most recent commit shas touching the canonical path
        log = subprocess.run(
            ["git", "-C", od, "log", "-2", "--pretty=format:%H", "--", rel],
            capture_output=True, text=True, check=False)
        shas = [s for s in log.stdout.split() if s]
        if len(shas) < 2:
            return None                       # no prior version (first-ever)
        prior_sha = shas[1]
        blob = subprocess.run(
            ["git", "-C", od, "show", f"{prior_sha}:{rel}"],
            capture_output=True, check=False)
        if blob.returncode != 0:
            return None
        prior_hash = hashlib.sha256(blob.stdout).hexdigest()
        return f"{prior_sha}:{prior_hash}"
    except Exception:
        return None


def build_prior_rev_fn(orchestra_dir: Optional[str] = None):
    """Callable (canary, lineage_root=None) -> prior committed rev | None."""
    od = orchestra_dir or ORCHESTRA_DIR

    def _prior(canary: str, lineage_root: Optional[str] = None) -> Optional[str]:
        return prior_committed_rev(canary, lineage_root=lineage_root, orchestra_dir=od)

    return _prior

