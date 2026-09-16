"""RED-first matrix — SPEC (A) handoff schema-recognition + freshness authority
(gm final ruling DEC-1788165818 CONSENSUS_REACHED; proposal
.workspace/proposals/handoff-schema-recognition-freshness-authority-spec.md).

Two load-bearing seams under test here (pure/hermetic):
  RECOGNITION (axis-1/3/4): handoff_provider.read_committed_handoff must parse a
    genuine agent-authored MARKDOWN (`##`-section) handoff (accept-either with the
    legacy ```json block), positively reject a daemon auto-snapshot, and HARD-
    EXCLUDE state/agent-handoffs/<id>.md as a freshness source.
  FRESHNESS (axis-2 + caveats): author_gate.handoff_ready must gate freshness by a
    per-seat BASELINE-RELATIVE rev (commit_sha:file_hash differs-from/newer-than a
    baseline), REPLACING is_stale(mtime) — NOT beside it. Fail-CLOSED on an
    absent/null baseline. A stale prior-rotation handoff (rev == baseline) stays
    SOFT.

The beat.py capture-at-soft-open + reset-at-promote integration is a separate
RED file (baseline threading). Here we pin the pure contracts.

RED honesty (fleet note): the freshness tests call handoff_ready with a NEW
`baseline_rev` kwarg the pre-fix signature lacks — those RED as TypeError (proves
the param is new), which is a legitimate failing direction, not manufactured.
"""
import importlib.util
import json
import os
import subprocess
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- recognition
def _fresh_provider(orch):
    os.environ["ORCHESTRA_DIR"] = str(orch)
    spec = importlib.util.spec_from_file_location(
        "hp_recog_uut", os.path.join(_HERE, "handoff_provider.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()


# A genuine agent-authored MARKDOWN handoff (no ```json block) — the shape the
# fleet actually emits (build_author_trigger instructs prose fields). Rich enough
# that recognition -> a dict; richness/freshness are separate gates.
_MD_HANDOFF = """# HANDOFF — worker-z -> successor

## current_goal
Land the widget pipeline slice-2 over the real backend.

## phase_state
- plan_ref: docs/PLAN_widget.md
- phase 2 of 4
- next_gate: gm merge-gate

## next_3_actions
1. wire the seam
2. add the RED test
3. push

## decisions
- single-trunk discipline — every land through the merge-gate
- never kill services — projections are idempotent

## hazards
- shared registry write contention

## open_loops
- awaiting gm merge-gate
"""

_SNAPSHOT_JSON = {  # daemon auto-snapshot shape — must be positively rejected
    "agent_id": "worker-z", "saved_at": "2026-08-31T00:00:00Z",
    "summary": "auto snapshot", "recent_messages": [], "raw_output_tail": "..."}


def _repo(tmp_path):
    orch = tmp_path / "orch"
    orch.mkdir()
    _git(orch, "init", "-q", "-b", "main")
    (orch / "SEED").write_text("x")
    _git(orch, "add", "SEED")
    _git(orch, "commit", "-q", "-m", "seed")
    return orch


def _commit(orch, rel, text):
    p = orch / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(orch, "add", rel)
    _git(orch, "commit", "-q", "-m", f"add {rel}")


def test_recognition_markdown_section_handoff_is_found(tmp_path):
    """axis-1 accept-either: a markdown `##`-section handoff (no json block) at the
    canonical docs path is RECOGNIZED (returns a dict), not treated as missing."""
    orch = _repo(tmp_path)
    _commit(orch, "docs/HANDOFF_worker-z-next.md", _MD_HANDOFF)
    m = _fresh_provider(orch)
    d, mt = m.read_committed_handoff("worker-z", orchestra_dir=str(orch))
    assert d is not None, "markdown-section handoff must be recognized (accept-either)"
    assert d.get("current_goal"), "recognition must extract current_goal from `## current_goal`"


def test_recognition_positive_marker_rejects_daemon_snapshot(tmp_path):
    """axis-3 positive marker: a daemon auto-snapshot (.json, no schema fields) is
    positively rejected, not incidentally-excluded."""
    orch = _repo(tmp_path)
    _commit(orch, "docs/HANDOFF_worker-z-next.md", json.dumps(_SNAPSHOT_JSON))
    m = _fresh_provider(orch)
    d, mt = m.read_committed_handoff("worker-z", orchestra_dir=str(orch))
    assert d is None, "a daemon-snapshot shape must NOT be recognized as a handoff"


def test_recognition_positive_marker_rejects_snapshot_with_lookalike_key(tmp_path):
    """axis-3 (gen28 TIGHTEN #2): the positive marker must be LOAD-BEARING, not
    incidental. A snapshot that ALSO carries a schema-lookalike key (current_goal)
    AND a snapshot marker (saved_at / raw_output_tail) must STILL be rejected —
    proving the rejection is by the positive snapshot-signature, not by
    current_goal being absent."""
    orch = _repo(tmp_path)
    decoy = {"current_goal": "I look like a handoff", "saved_at": "2026-08-31",
             "raw_output_tail": "...", "recent_messages": []}
    # a fenced json block so _extract_machine_block parses it into the decoy dict
    _commit(orch, "docs/HANDOFF_worker-z-next.md",
            "# H\n```json\n" + json.dumps(decoy) + "\n```\n")
    m = _fresh_provider(orch)
    d, mt = m.read_committed_handoff("worker-z", orchestra_dir=str(orch))
    assert d is None, ("a snapshot carrying a lookalike current_goal + snapshot "
                       "markers must STILL be rejected (positive marker, not "
                       "incidental key-absence)")


def test_recognition_does_not_scrape_first_effect_from_prose(tmp_path):
    """CONFIRM #1 (gen28): acme embeds first_effect INSIDE a next_3_actions
    bullet as prose. The parser must NOT lift it to a top-level first_effect — a
    false-richness-pass off an embedded string is a false-confirm vector. The
    parsed dict must have NO top-level first_effect, so richness correctly fails."""
    orch = _repo(tmp_path)
    md = ("## current_goal\nship it\n\n"
          "## next_3_actions\n"
          "1. do the thing — first_effect: {kind: message, target: gm} then wait\n"
          "2. verify\n")
    _commit(orch, "docs/HANDOFF_worker-z-next.md", md)
    m = _fresh_provider(orch)
    d, mt = m.read_committed_handoff("worker-z", orchestra_dir=str(orch))
    assert d is not None, "the handoff is still recognized (current_goal present)"
    assert not d.get("first_effect"), (
        "first_effect embedded in next_3_actions prose must NOT become a top-level "
        "field — that would false-satisfy the richness gate")


def test_recognition_excludes_state_agent_handoffs_as_freshness_source(tmp_path):
    """axis-4 HARD-EXCLUDE: a valid handoff present ONLY at the daemon-clobbered
    state/agent-handoffs/<id>.md must NOT be used as a freshness source -> None."""
    orch = _repo(tmp_path)
    _commit(orch, "state/agent-handoffs/worker-z.md", _MD_HANDOFF)
    m = _fresh_provider(orch)
    d, mt = m.read_committed_handoff("worker-z", orchestra_dir=str(orch))
    assert d is None, ("state/agent-handoffs/<id>.md is daemon-clobbered and must be "
                       "excluded as a freshness source (axis-4)")


# ------------------------------------------------------------------ freshness
def _author_gate():
    spec = importlib.util.spec_from_file_location(
        "ag_fresh_uut", os.path.join(_HERE, "author_gate.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rich_dict(*, sha="shaNEW", fhash="hashNEW"):
    """A structurally-rich handoff dict (passes require_richness) carrying git
    provenance so a rev = commit_sha:file_hash can be derived."""
    return {
        "current_goal": "land slice-2",
        "phase_state": {"phase_n": 2, "phase_m": 4, "next_gate": "gm merge-gate",
                        "plan_ref": "docs/PLAN.md"},
        "next_3_actions": ["a", "b", "c"],
        "decisions": [{"text": "single-trunk", "rationale": "critical path"},
                      {"text": "no kill", "rationale": "idempotent"}],
        "open_loops": ["awaiting gm"],
        "hazards": ["registry contention"],
        "canary_questions": [
            {"id": "q1", "question": "why X", "source_pointer": "jsonl:turn-3"},
            {"id": "q2", "question": "why Y", "source_pointer": "jsonl:turn-9"},
            {"id": "q3", "question": "why Z", "source_pointer": "jsonl:turn-12"}],
        "first_effect": {"kind": "command", "target": "pytest", "check": "green"},
        "handoff_commit_sha": sha, "file_hash": fhash,
    }


_OLD_MTIME = time.time() - 6 * 3600   # 6h old -> is_stale(1h window) would be True
_FRESH_MTIME = time.time()


def test_freshness_rev_differs_from_baseline_is_ready(tmp_path):
    """axis-2: rev (sha:hash) DIFFERS from the per-seat baseline -> ready."""
    ag = _author_gate()
    d = _rich_dict(sha="shaNEW", fhash="hashNEW")
    r = ag.handoff_ready(d, _FRESH_MTIME, time.time(), baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is True, f"fresh (rev != baseline) must be ready; got {r}"


def test_freshness_stale_prior_rotation_rev_equals_baseline_stays_soft(tmp_path):
    """N3: a committed-but-OLD handoff from a prior rotation (rev == baseline) must
    NOT be ready (else HARD-rotate on stale context)."""
    ag = _author_gate()
    d = _rich_dict(sha="shaOLD", fhash="hashOLD")
    r = ag.handoff_ready(d, _FRESH_MTIME, time.time(), baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is False, "rev == baseline is STALE -> must stay SOFT"


def test_freshness_absent_baseline_fails_closed(tmp_path):
    """N4 / caveat C2: absent/null baseline -> fail-CLOSED -> SOFT, never ready."""
    ag = _author_gate()
    d = _rich_dict()
    r = ag.handoff_ready(d, _FRESH_MTIME, time.time(), baseline_rev=None)
    assert r["ready"] is False, "absent baseline must fail-closed (SOFT), never ready"


def test_freshness_replaces_mtime_old_mtime_still_ready_when_rev_new(tmp_path):
    """REPLACE (not beside): an OLD mtime (is_stale would be True) must NOT block a
    rev-fresh handoff — mtime no longer gates freshness."""
    ag = _author_gate()
    d = _rich_dict(sha="shaNEW", fhash="hashNEW")
    r = ag.handoff_ready(d, _OLD_MTIME, time.time(), baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is True, ("mtime must NOT gate: an old-mtime but rev-fresh "
                                "handoff is ready (is_stale REPLACED)")


def test_freshness_replaces_mtime_fresh_mtime_not_ready_when_rev_stale(tmp_path):
    """REPLACE inverse: a FRESH mtime must NOT rescue a rev-stale handoff (mtime is
    untrustworthy — daemon touches it)."""
    ag = _author_gate()
    d = _rich_dict(sha="shaOLD", fhash="hashOLD")
    r = ag.handoff_ready(d, _FRESH_MTIME, time.time(), baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is False, "fresh mtime must NOT rescue rev==baseline (stay SOFT)"


def test_freshness_hollow_markdown_stays_soft_no_bypass(tmp_path):
    """N2 / critical safety caveat: a recognized-but-HOLLOW handoff (missing
    canary_questions + first_effect) must stay SOFT even when rev is fresh — the
    richness gate is NOT bypassed by recognition."""
    ag = _author_gate()
    hollow = {"current_goal": "x", "next_3_actions": ["a"],
              "handoff_commit_sha": "shaNEW", "file_hash": "hashNEW"}
    r = ag.handoff_ready(hollow, _FRESH_MTIME, time.time(), baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is False, "hollow handoff must stay SOFT (no richness bypass)"
