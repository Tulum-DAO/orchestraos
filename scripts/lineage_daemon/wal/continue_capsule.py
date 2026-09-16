"""continue_capsule — BG Layer-3 working-state continue-capsule (gm-authorized, congruence
DEC-1788658573; AGY counter-proposal + firewall-follow-up folded in).

WHY: the re-run gap — a freshly-booted green boots ALIVE (pieces #1/#2) but does NOT resume blue's
CURRENT task; it latches an OLD decision-dense *salient* thread inferred from the hydrate digest's
raw TIMELINE. This module STATES the current working-state as a structured capsule
``{objective, current_step, next_action, artifact_refs}`` so the green resumes blue's CURRENT
objective instead of inferring one.

DETERMINISTIC — NO LLM anywhere (the digest's contagion firewall: the court glitch spreads by
IMITATION of ingested model-voice, so no model call and no model-voice content is resolved here).

OBJECTIVE SOURCE (grounding finding, live WAL): ``file_mod``/``git`` events are captured scoped to
blue's CWD repo ONLY, so they are BLIND to a cross-repo objective (blue cwd=second-brain, actual
work=agent-orchestra) — the exact reason the green latched the local orb thread. So the objective is
anchored on the most-recent NON-NOISE ``prompt`` DIRECTIVE (world-INPUT, not model-voice), resolved
THROUGH ``court_scrub``. ``file_mod``/``git`` populate ``artifact_refs`` (repo-root disambiguated),
never the objective anchor. Priority: an explicit blue-authored ``checkpoint`` > directive-thread >
degraded (explicit UNKNOWN sentinel, never a synthesized objective).

FIREWALL MITIGATIONS for resolving prompt content (AGY, mandatory):
  1. court_scrub every resolved body (contaminated -> hard-exclude, bytes-only).
  2. quote sanitization -> strip markdown blockquotes / quoted-assistant lines (a correction prompt
     can embed prior model-voice = the primary contagion path).
  3. origin allowlist -> drop reflection/relay noise (stop-hook, queue-digest, lineage-ping).
  4. bounded window -> at most ``prompt_n`` (<=3) most-recent surviving directives.
``response``/``thinking``/``tool_call``-args content stays BANNED (never resolved here).
"""
import json
import os

UNKNOWN_OBJECTIVE = "UNKNOWN (derived from recent file mutations)"


def resolve_prompt_body(body_ref):
    """Production resolve_body seam for PROMPT events: read the world-INPUT directive text at a
    transcript ``<path>:<offset>`` body_ref. Returns the user text, or None (FAIL-SAFE: any bad
    ref / missing file / parse error / non-transcript ref -> None, so the capsule degrades cleanly
    rather than raising into the hydrate beat). NEVER resolves a ``git:`` artifact ref. Only PROMPT
    (world-input) text is read here; response/thinking are never passed to this seam."""
    if not body_ref or not isinstance(body_ref, str) or body_ref.startswith("git:"):
        return None
    try:
        path, off = body_ref.rsplit(":", 1)
        offset = int(off)
    except (ValueError, TypeError):
        return None
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            line = fh.readline()
        obj = json.loads(line)
    except (OSError, ValueError):
        return None
    msg = obj.get("message") if isinstance(obj, dict) else None
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        text = " ".join(p for p in parts if p).strip()
        return text or None
    return None

# origin allowlist: a prompt carrying any of these is reflection/relay noise, not a directive.
_NOISE_MARKERS = (
    "stop hook feedback", "[queue-digest]", "[msg from", "[lineage soft-handoff]",
    "<system-reminder>", "[reminder]", "tool_result",
)
# passive/read-only tool verbs — excluded from current_step unless they follow a failure/file_mod.
_PASSIVE_VERBS = ("grep", "glob", "read ", "read(", "ls ", "list_dir", "find ", "cat ", "search")


def _noop_scrub(text):
    return text, False


def _is_noise_prompt(text):
    low = text.lower()
    return any(m in low for m in _NOISE_MARKERS)


def _sanitize_directive(text):
    """Strip markdown blockquotes / quoted-assistant lines (embedded model-voice) before the
    directive text is placed in the capsule."""
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue  # blockquoted (quoted assistant / court snippet)
        kept.append(line)
    return "\n".join(kept).strip()


def _parse_body_ref(body_ref):
    """Resolve a file_mod/git body_ref to (repo_name, path). Forms seen in the WAL:
    ``git:<root>#<path>`` and ``git:<root>@<sha>``. Repo is the OWN root of the ref (cross-repo
    disambiguation), never blue's base cwd. A transcript ``path:offset`` ref is not an artifact."""
    if not body_ref or not body_ref.startswith("git:"):
        return (None, None)
    rest = body_ref[4:]
    if "#" in rest:
        root, path = rest.split("#", 1)
        return (os.path.basename(root.rstrip("/")), path or None)
    if "@" in rest:
        root = rest.split("@", 1)[0]
        return (os.path.basename(root.rstrip("/")), None)
    return (os.path.basename(rest.rstrip("/")), None)


def _is_passive(row):
    if row["kind"] != "tool_call":
        return False
    low = (row["summary"] or "").lower()
    return any(v in low for v in _PASSIVE_VERBS)


def _is_active(row):
    """An event that ADVANCES the objective: a file/git mutation, or a non-read-only tool_call."""
    if row["kind"] in ("file_mod", "git"):
        return True
    if row["kind"] == "tool_call":
        return not _is_passive(row)
    return False


def _resolve_directives(events, resolve_body, scrub, prompt_n):
    """The recent directive thread: the last ``prompt_n`` prompts that resolve, survive scrub, and
    are not relay noise — sanitized. Returned most-recent-first."""
    survivors = []  # (seq, text) in seq order
    for r in events:
        if r["kind"] != "prompt" or not r["body_ref"]:
            continue
        raw = resolve_body(r["body_ref"]) if resolve_body else None
        if not raw:
            continue
        clean, contaminated = scrub(raw)
        if contaminated or not clean:
            continue
        if _is_noise_prompt(clean):
            continue
        text = _sanitize_directive(clean)
        if not text:
            continue
        survivors.append((r["seq"], text))
    survivors = survivors[-prompt_n:]  # bounded window (most recent)
    return [{"seq": s, "text": t} for s, t in reversed(survivors)]  # most-recent-first


def _artifact_refs(events, limit=20):
    """Recent file_mods as {repo, path, body_ref}, repo-root disambiguated per ref, deduped."""
    refs, seen = [], set()
    for r in events:
        if r["kind"] != "file_mod":
            continue
        repo, path = _parse_body_ref(r["body_ref"])
        if not path:
            continue
        key = (repo, path)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"repo": repo, "path": path, "body_ref": r["body_ref"]})
    return refs[-limit:]


def _current_step(events):
    """The most-recent ACTIVE event, rendered structurally (never a read-only probe tail)."""
    for r in reversed(events):
        if _is_active(r):
            return f"{r['kind']} {r['summary'] or ''}".strip()
    return None


def _next_action(objective, current_step, artifact_refs):
    if objective == UNKNOWN_OBJECTIVE:
        return ("Inspect the recent file mutations below and re-derive the objective from blue's "
                "live state before acting; do NOT infer one from the decision timeline.")
    tail = f" — last active: {current_step}" if current_step else ""
    if artifact_refs:
        a = artifact_refs[-1]
        tail += f" (touch: {a['repo'] or '?'}:{a['path']})"
    return f"Resume blue's CURRENT objective: {objective}{tail}"


def render_capsule_banner(capsule):
    """Render the capsule as the TOP block of the hydrate body — STATED, not inferred. Directives
    ride inert ``[DIRECTIVE seq=N]`` containers (never open conversational turns)."""
    lines = ["⟢⟢ RESUME THIS — your CURRENT objective (do NOT infer one from the timeline below) ⟢⟢",
             f"OBJECTIVE: {capsule['objective']}"]
    if capsule.get("degraded"):
        lines.append("  (DEGRADED: no directive found — re-derive from the mutations below.)")
    if capsule.get("current_step"):
        lines.append(f"CURRENT STEP: {capsule['current_step']}")
    lines.append(f"NEXT ACTION: {capsule['next_action']}")
    refs = capsule.get("artifact_refs") or []
    if refs:
        lines.append("ARTIFACTS (blue's working set):")
        for a in refs[-8:]:
            lines.append(f"  - {a.get('repo') or '?'}:{a['path']}")
    directives = capsule.get("directives") or []
    if directives:
        lines.append("DIRECTIVE THREAD (most-recent first, scrub-gated world-input):")
        for d in directives:
            lines.append(f"  [DIRECTIVE seq={d['seq']}] {d['text']}")
    lines.append(f"(capsule source: {capsule.get('source')})")
    return "\n".join(lines)


def build_checkpoint_path(orchestra_dir, lineage_root):
    """The convention path for a lineage's blue-authored BUILD-CHECKPOINT (M1a)."""
    import os
    return os.path.join(orchestra_dir, "state", "agent-handoffs",
                        f"{lineage_root}.build-checkpoint.json")


def load_build_checkpoint(orchestra_dir, lineage_root):
    """Load the blue-authored BUILD-CHECKPOINT (M1a) for a lineage, or None if absent/
    unreadable/malformed (FAIL-SAFE — a missing/broken checkpoint must never abort the
    hydrate; build_continue_capsule then falls to the directive-thread). Only a dict
    carrying a truthy ``objective`` is honored (mirrors build_continue_capsule's guard)."""
    import json
    import os
    path = build_checkpoint_path(orchestra_dir, lineage_root)
    try:
        with open(path) as fh:
            cp = json.load(fh)
    except (OSError, ValueError):
        return None
    if not (isinstance(cp, dict) and cp.get("objective")):
        return None
    # M1a cond 3 (consumer guard): a checkpoint stamped for a DIFFERENT lineage is
    # stale/wrong — never ship it into this lineage's capsule.
    lr = cp.get("lineage_root")
    if lr is not None and lr != lineage_root:
        return None
    return cp


def build_continue_capsule(store, lineage_root, *, resolve_body=None, scrub=None,
                           prompt_n=3, tail_n=20, checkpoint=None):
    """Build the deterministic working-state continue-capsule for a green. Priority:
    checkpoint (blue-authored) > directive-thread (resolved recent prompts) > degraded (UNKNOWN)."""
    scrub = scrub or _noop_scrub
    events = list(store.events(lineage_root))
    artifact_refs = _artifact_refs(events, limit=tail_n)
    current_step = _current_step(events)

    if checkpoint and checkpoint.get("objective"):
        objective = checkpoint["objective"]
        next_action = checkpoint.get("next_action") or _next_action(objective, current_step,
                                                                    artifact_refs)
        return {"objective": objective, "current_step": current_step, "next_action": next_action,
                "artifact_refs": artifact_refs, "directives": [], "source": "checkpoint",
                "degraded": False}

    directives = _resolve_directives(events, resolve_body, scrub, prompt_n)
    if not directives:
        return {"objective": UNKNOWN_OBJECTIVE, "current_step": current_step,
                "next_action": _next_action(UNKNOWN_OBJECTIVE, current_step, artifact_refs),
                "artifact_refs": artifact_refs, "directives": [],
                "source": "degraded-file-mutations", "degraded": True}

    objective = directives[0]["text"]  # the operative (most-recent) directive
    return {"objective": objective, "current_step": current_step,
            "next_action": _next_action(objective, current_step, artifact_refs),
            "artifact_refs": artifact_refs, "directives": directives,
            "source": "directive-thread", "degraded": False}
