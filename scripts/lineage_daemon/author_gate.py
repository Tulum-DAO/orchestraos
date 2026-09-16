"""S1 — author-trigger + the hard_rotate readiness gate (WS3 v2, DEC-1786724046).

The daemon must not spawn a successor pointing at a handoff that doesn't exist,
is hollow, or is stale (H2). Two pieces, both pure over injected seams:

 1. build_author_trigger(agent_id, handoff_path) -> the durable instruction the
    daemon injects at decide()==soft_handoff (~85% ctx), telling the predecessor
    to AUTHOR its fixed-schema handoff — including the v2 fields (canary_questions
    with DEEP non-leading answers, hazards, first_effect) and its GUARDS/lane
    boundaries as decisions[{text,rationale}] — and COMMIT it, BEFORE hard_rotate.

 2. handoff_ready(handoff_dict, mtime, now, session_turns) -> {ready, reasons}.
    The hard_rotate gate: no longer exists+parses. Requires the committed handoff
    to (a) parse as the fixed schema and (b) pass validate(require_richness=True)
    and (c) be FRESH (not stale vs last-activity). A hollow/stale handoff FAILS →
    the daemon stays SOFT and re-triggers authoring rather than spawning against a
    phantom/hollow/stale handoff.

Reading the committed handoff off disk + committing are IO the daemon does; this
module stays pure so it is hermetically testable.
"""

from scripts.lineage_daemon.handoff_schema import Handoff, is_stale


def build_author_trigger(agent_id, handoff_path):
    """The soft_handoff authoring instruction (durable msg_store body). The
    daemon injects this to the predecessor at ~85% ctx; the predecessor writes +
    commits the fixed-schema handoff before hard_rotate proceeds."""
    return (
        f"[LINEAGE SOFT-HANDOFF] You are at ~soft-handoff context. Author your "
        f"successor handoff NOW (while you still hold the deep context) and COMMIT "
        f"it to {handoff_path}. Use the fixed schema (handoff_schema.Handoff): "
        f"current_goal; phase_state{{plan_ref, phase, next_gate}}; next_3_actions "
        f"(action[0] MUST declare a first_effect {{kind,target,check}} — a "
        f"checkable first result); decisions[{{text,rationale}}] carrying your "
        f"GUARDS + lane boundaries (single-trunk, never-kill-services, "
        f"verify-before-completion, your focus boundary); open_loops (reconciled "
        f"against your outstanding msg_store requests); file_roots_touched; "
        f"hazards (top-3 live hazards); canary_questions (3-5 "
        f"{{id,question,source_pointer}} whose facts live DEEP in your session and "
        f"are NOT inferable from the question phrasing — the successor must answer "
        f"them IN ITS OWN WORDS after reading your jsonl, to prove absorption). "
        f"NEVER write an answer, expected answer, or answer key ANYWHERE — not in "
        f"the handoff, not in the canary, not in any file. source_pointer names "
        f"WHERE the fact lives in YOUR jsonl (message id / turn index / timestamp "
        f"range); the grader verifies the successor's own words against that "
        f"transcript at grade time. A question you cannot point at is not "
        f"gradeable and was never a fair question — drop it. A POINTER CAN LEAK "
        f"AS BADLY AS AN ANSWER: if the question asks for an identifier (a row "
        f"id, an agent id, a commit hash), do NOT point with that identifier — "
        f"use 'jsonl:turn-<n>' or an ISO range, which locates the fact without "
        f"naming it. "
        f"CANONICAL RICH FORM (A2, DEC-1788207386): ALSO emit a fenced ```json "
        f"machine-block carrying the full schema (all fields above, INCLUDING "
        f"canary_questions[{{id,question,source_pointer}}] and first_effect) — the "
        f"json block is the lossless canonical form the daemon reads; the prose "
        f"`## sections` stay acceptable and are merged per-field when both are "
        f"present. "
        f"Do NOT rotate until the committed handoff exists + is rich + fresh."
    )


_NO_BASELINE = object()   # sentinel: caller supplied no baseline (legacy call)


def handoff_rev(handoff_dict) -> "str | None":
    """The freshness IDENTITY of a handoff: git-commit provenance
    `commit_sha:file_hash` (both injected by handoff_provider._get_git_provenance
    over the CANONICAL non-clobbered docs path). None when neither is present —
    an unprovenanced dict has no rev and can never be baseline-fresh."""
    if not isinstance(handoff_dict, dict):
        return None
    sha = str(handoff_dict.get("handoff_commit_sha") or "").strip()
    fhash = str(handoff_dict.get("file_hash") or "").strip()
    if not sha and not fhash:
        return None
    return f"{sha}:{fhash}"


def _content_id(rev) -> "str | None":
    """A2 C1 (DEC-1788207386): the CONTENT-IDENTITY of a rev = its file_hash part
    (sha256 of the handoff bytes), NOT the commit_sha. A no-op/whitespace recommit
    yields a NEW commit_sha but the SAME file_hash, so a file_hash compare keeps it
    STALE. A rev is `commit_sha:file_hash` -> take the part AFTER the FIRST colon
    (a file_hash is hex, no colon). Returns None for a non-rev (the \\x00 no-handoff
    SENTINEL and None must NOT be split — the caller handles those before calling)."""
    if not isinstance(rev, str) or ":" not in rev:
        return None
    return rev.split(":", 1)[1]


def handoff_ready(handoff_dict, mtime, now, session_turns=0,
                  baseline_rev=_NO_BASELINE):
    """The hard_rotate readiness gate. Returns {ready: bool, reasons: [...]}.

    ready iff: parses as Handoff AND validate(require_richness=True) is clean AND
    FRESH. A missing handoff is signalled by passing handoff_dict=None.

    FRESHNESS (DEC-1788165818, axis-2): the mtime staleness gate is REPLACED
    (not sat beside) by a per-seat BASELINE-RELATIVE rev check. The lineage
    daemon touches handoff mtimes on every snapshot, so mtime is untrustworthy;
    the handoff's git-provenance rev (`commit_sha:file_hash`) is the identity
    mtime cannot corrupt. FRESH iff the handoff's rev DIFFERS FROM the per-seat
    `baseline_rev` captured at soft-window-open / last rotation. FAIL-CLOSED: an
    absent/None baseline, a rev-less handoff, or rev == baseline (a stale handoff
    from a prior rotation) all stay SOFT — never a false SOFT_READY that would
    HARD-rotate on stale context.

    Back-compat: a caller that passes no `baseline_rev` gets the legacy
    structural+richness verdict WITHOUT a freshness gate (the freshness check is
    enforced only once the daemon supplies the per-seat baseline).
    """
    reasons = []
    if handoff_dict is None:
        return {"ready": False, "reasons": ["handoff missing (not committed yet)"]}

    try:
        h = Handoff.from_dict(handoff_dict)
    except Exception as e:  # noqa: BLE001 -- malformed -> not ready
        return {"ready": False, "reasons": [f"handoff does not parse: {e}"]}

    errs = h.validate(session_turns=session_turns, require_richness=True)
    reasons.extend(errs)

    if baseline_rev is not _NO_BASELINE:
        rev = handoff_rev(handoff_dict)
        # A2 C1: compare CONTENT-IDENTITY (file_hash), not the full commit-bearing
        # rev, so a no-op/whitespace recommit (new commit_sha, SAME file_hash)
        # stays STALE. The \x00 no-handoff SENTINEL has no file_hash -> a real
        # authored handoff differs from it -> FRESH (eligible-on-first-author). A
        # null/absent baseline is fail-CLOSED. Never blind-split the sentinel/null.
        cur_cid = _content_id(rev)
        base_cid = _content_id(baseline_rev)  # None for sentinel/null (no colon)
        if not baseline_rev:
            reasons.append("freshness: no per-seat baseline recorded — fail-closed "
                           "(re-author); a null baseline is never SOFT_READY")
        elif rev is None:
            reasons.append("freshness: handoff has no git-provenance rev "
                           "(commit_sha/file_hash) — cannot prove fresh authoring")
        elif base_cid is not None and cur_cid == base_cid:
            reasons.append("freshness: handoff content-id (file_hash) == per-seat "
                           "baseline — stale (unchanged content since last rotation/"
                           "soft-window open), re-author")

    return {"ready": not reasons, "reasons": reasons}


def handoff_complete(*, canary_present, readback_present, comprehension_passed):
    """F17 — the three-artifact completion predicate (pure).

    A soft-handoff is COMPLETE once the successor has fully oriented: its canary
    was authored, its readback recorded, AND its comprehension grade PASSED. Only
    then does the soft-handoff beat stop re-pinging the predecessor's author-
    trigger (b53311d69 dedup class). A graded-but-FAILED comprehension is NOT
    complete — the successor must keep re-studying, so the ping stays live.
    """
    return bool(canary_present and readback_present and comprehension_passed)
