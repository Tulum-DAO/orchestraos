"""S1 author-trigger + hard_rotate readiness gate (WS3 v2, DEC-1786724046)."""

from scripts.lineage_daemon.author_gate import (
    build_author_trigger, handoff_ready, handoff_complete)
from scripts.lineage_daemon.handoff_schema import Handoff


def _rich_dict(next_gate="gate-A"):
    h = Handoff(
        current_goal="continue WS3",
        working_state="mid",
        open_loops=["wire it"],
        decisions=[{"text": "effects substrate", "rationale": "no lag"}],
        next_3_actions=["do x"],
        canary_questions=[
            {"id": "q1", "question": "why?", "source_pointer": "jsonl:msg_a1"},
            {"id": "q2", "question": "what?", "source_pointer": "jsonl:msg_a2"},
            {"id": "q3", "question": "how?", "source_pointer": "jsonl:msg_a3"},
        ],
        hazards=["h1"],
        first_effect={"kind": "file", "target": "x.md", "check": None},
    )
    h.phase_state.next_gate = next_gate
    return h.to_dict()


# --- author trigger ---

def test_build_author_trigger_mentions_v2_fields_and_path():
    body = build_author_trigger("ob", "docs/HANDOFF_ob-next.md")
    assert "docs/HANDOFF_ob-next.md" in body
    for token in ("canary_questions", "hazards", "first_effect", "GUARDS",
                  "DEEP", "single-trunk"):
        assert token in body, f"author trigger must mention {token}"


# --- readiness gate ---

def test_ready_on_rich_fresh_handoff():
    r = handoff_ready(_rich_dict(), mtime=100.0, now=200.0)  # 100s < 3600 window
    assert r["ready"] is True
    assert r["reasons"] == []


def test_not_ready_when_missing():
    r = handoff_ready(None, mtime=None, now=200.0)
    assert r["ready"] is False
    assert any("missing" in x for x in r["reasons"])


def test_not_ready_on_hollow_handoff():
    hollow = Handoff(current_goal="g", next_3_actions=["a"]).to_dict()
    r = handoff_ready(hollow, mtime=100.0, now=200.0)
    assert r["ready"] is False
    # richness reasons surface (canary/hazards/first_effect/next_gate)
    assert any("canary" in x for x in r["reasons"])
    assert any("hazards" in x for x in r["reasons"])
    assert any("first_effect" in x for x in r["reasons"])


def test_not_ready_when_stale():
    # DEC-1788165818 (axis-2): mtime staleness is REPLACED by baseline-relative
    # rev. A handoff whose git-provenance rev == the per-seat baseline is STALE
    # (unchanged since the last rotation / soft-window open) -> not ready, even
    # with a fresh mtime (mtime no longer gates).
    d = _rich_dict()
    d["handoff_commit_sha"], d["file_hash"] = "shaX", "hashX"
    r = handoff_ready(d, mtime=9e9, now=9e9, baseline_rev="shaX:hashX")
    assert r["ready"] is False
    assert any("freshness" in x for x in r["reasons"])


def test_ready_when_rev_differs_from_baseline():
    # a freshly-authored handoff (rev != per-seat baseline) is ready even with an
    # OLD mtime — mtime is REPLACED, the rev-vs-baseline identity gates.
    d = _rich_dict()
    d["handoff_commit_sha"], d["file_hash"] = "shaNEW", "hashNEW"
    r = handoff_ready(d, mtime=0.0, now=9e9, baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is True


def test_absent_baseline_fails_closed():
    # caveat C2 / N4: a null baseline -> fail-closed -> not ready (never SOFT_READY).
    d = _rich_dict()
    d["handoff_commit_sha"], d["file_hash"] = "shaNEW", "hashNEW"
    r = handoff_ready(d, mtime=100.0, now=200.0, baseline_rev=None)
    assert r["ready"] is False
    assert any("freshness" in x for x in r["reasons"])


def test_ready_ignores_staleness_when_mtime_none():
    # legacy call (no baseline supplied) -> structural+richness only, no freshness
    # gate; mtime never gates.
    r = handoff_ready(_rich_dict(), mtime=None, now=999999.0)
    assert r["ready"] is True


def test_richness_scales_with_session_turns():
    # 1 decision-with-rationale; >150 turns requires 3 -> not ready.
    r = handoff_ready(_rich_dict(), mtime=100.0, now=200.0, session_turns=200)
    assert r["ready"] is False
    assert any("decisions with rationale" in x for x in r["reasons"])


# --- F17: three-artifact completion predicate (no re-ping when done) ----------

def test_complete_when_all_three_artifacts_present_and_passed():
    # successor canary authored + readback recorded + comprehension pass:true
    assert handoff_complete(canary_present=True, readback_present=True,
                            comprehension_passed=True) is True


def test_not_complete_when_readback_missing():
    assert handoff_complete(canary_present=True, readback_present=False,
                            comprehension_passed=False) is False


def test_not_complete_when_canary_missing():
    assert handoff_complete(canary_present=False, readback_present=True,
                            comprehension_passed=True) is False


def test_not_complete_when_graded_but_failed():
    # readback+canary exist but comprehension FAILED -> still re-study, NOT done
    # (the round-1-fail case: successor must keep working, keep the ping alive).
    assert handoff_complete(canary_present=True, readback_present=True,
                            comprehension_passed=False) is False
