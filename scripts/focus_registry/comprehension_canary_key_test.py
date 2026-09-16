"""RED tests — canary-key normalization (DEC-1787808620 fix #2, the LINCHPIN).

The live completion S3 confirm HELD because the successor's readback answers are
keyed `qN` (s3_live_seams._split_readback_sections strips any `c` prefix), while the
daemon-held ground_truth canary ids are `cqN` (the predecessor's authored
canary_questions[].id). `grade_canary` did `given.get("cq1")` against a dict keyed
`q1` -> "" -> every question scored missed, DESPITE a human-GOOD readback (FINDING
reason #2 / gm seq-62 class).

The fix normalizes `c?q<N>` answer keys to the ground_truth canary id form so a GOOD
readback scores comprehended. HOLE 3 (the invariant that must survive): the fix must
NOT weaken strictness — a DEGENERATE readback (empty / hallucinated / <2 grounded
anchors) must STILL FAIL, on both the short-answer and the strict citation-grounding
paths.
"""
from scripts.focus_registry.comprehension import grade_canary


# ---------------------------------------------------------------------------
# Short-answer path (non-pointer): cqN ids vs qN answer keys
# ---------------------------------------------------------------------------

_SHORT_GT = [
    {"id": "cq1", "question": "which commit is merge-9 holding?", "answer": "77eeb56"},
    {"id": "cq2", "question": "what model does app-dev run?", "answer": "fable-5"},
]


def test_good_readback_qN_keys_vs_cqN_ids_now_passes():
    # answers keyed q1/q2 (what _split_readback_sections emits) vs ground_truth cq1/cq2
    given = {"q1": "it is holding the land of 77eeb56", "q2": "app-dev runs fable-5"}
    r = grade_canary(given, _SHORT_GT)
    assert r["passed"] is True, r
    assert r["missed"] == []


def test_gN_ids_vs_qN_answer_keys_now_pass():
    """REAL flagship datum (ios-watch-dev-g5): the predecessor numbered its canaries
    g1..g8 while _split_readback_sections emits q1..q8 -> pre-fix intersect was EMPTY
    (all missed). _canon_qid now normalizes any 1-2 alpha-prefix + digits -> q<N>, so
    g<N> aligns with q<N>."""
    gt = [{"id": "g1", "question": "which commit?", "answer": "77eeb56"},
          {"id": "g2", "question": "what model?", "answer": "fable-5"}]
    given = {"q1": "it holds 77eeb56", "q2": "app-dev runs fable-5"}
    r = grade_canary(given, gt)
    assert r["passed"] is True, r
    assert r["missed"] == []


def test_gN_degenerate_still_fails():
    gt = [{"id": "g1", "question": "which commit?", "answer": "77eeb56"},
          {"id": "g2", "question": "what model?", "answer": "fable-5"}]
    given = {"q1": "some commit", "q2": "a model"}          # no exact tokens
    r = grade_canary(given, gt)
    assert r["passed"] is False
    assert "g1" in r["missed"] and "g2" in r["missed"]


def test_non_numeric_ids_untouched():
    from scripts.focus_registry.comprehension import _canon_qid
    assert _canon_qid("g1") == "q1"
    assert _canon_qid("cq3") == "q3"
    assert _canon_qid("q5") == "q5"
    assert _canon_qid("q_plant") == "q_plant"              # non-numeric id NOT normalized


def test_degenerate_short_answer_still_fails_after_normalization():
    # keys align now, but the exact ground-truth token is absent -> STILL missed
    given = {"q1": "some commit was involved", "q2": "a model runs somewhere"}
    r = grade_canary(given, _SHORT_GT)
    assert r["passed"] is False
    assert "cq1" in r["missed"] and "cq2" in r["missed"]


# ---------------------------------------------------------------------------
# Strict citation-grounding path (from_pointer, mode=strict) — Hole 3
# ---------------------------------------------------------------------------

# A hermetic grounding corpus. Each question's two strong (digit-bearing) anchors
# appear EXACTLY ONCE, so every answer grounds >=2 distinct strong anchors and the
# union carries an id-class token (a1b2c3d9 / 1787728346).
_TRANSCRIPT = (
    "The rollback landed at commit a1b2c3d9 after the port 8891 dashboard.\n"
    "The msg row 44721x confirmed and the build 7731 shipped cleanly.\n"
    "Lane 5052 carried it and DEC-1787728346 gated the final approval.\n"
)

# from_pointer with EMPTY resolved region => the grounding path (not legacy overlap),
# and no region-corroboration constraint (vacuous for an unresolved pointer).
_GROUND_GT = [
    {"id": "cq1", "question": "what commit did the rollback land at, and on which port?",
     "answer": "", "from_pointer": True},
    {"id": "cq2", "question": "which msg row confirmed it and what build shipped?",
     "answer": "", "from_pointer": True},
    {"id": "cq3", "question": "which lane carried it and what decision gated approval?",
     "answer": "", "from_pointer": True},
]

_GOOD_GROUNDED = {
    "q1": "the rollback commit was a1b2c3d9 on port 8891",
    "q2": "msg row 44721x confirmed and build 7731 shipped",
    "q3": "lane 5052 carried it and DEC-1787728346 gated approval",
}


def test_good_grounded_qN_keys_vs_cqN_ids_now_passes_strict():
    r = grade_canary(_GOOD_GROUNDED, _GROUND_GT,
                     transcript_text=_TRANSCRIPT, mode="strict")
    assert r["passed"] is True, r
    assert r["missed"] == []
    assert r["aggregate"]["idclass"] is True


def test_degenerate_empty_grounded_still_fails_strict():
    r = grade_canary({"q1": "", "q2": "", "q3": ""}, _GROUND_GT,
                     transcript_text=_TRANSCRIPT, mode="strict")
    assert r["passed"] is False


def test_hallucinated_grounded_still_fails_strict():
    # anchors that appear NOWHERE in the transcript -> HALLUCINATED / MISS
    halluc = {
        "q1": "commit deadbee9 on port 9999",
        "q2": "msg row 88888z and build 6666",
        "q3": "lane 4040 and DEC-9999123456 gated it",
    }
    r = grade_canary(halluc, _GROUND_GT,
                     transcript_text=_TRANSCRIPT, mode="strict")
    assert r["passed"] is False


def test_under_two_anchors_grounded_still_fails_strict():
    # each answer grounds only ONE strong anchor (<2) -> not a PASS verdict
    thin = {
        "q1": "the commit was a1b2c3d9",
        "q2": "the row was 44721x",
        "q3": "lane 5052 carried it",
    }
    r = grade_canary(thin, _GROUND_GT,
                     transcript_text=_TRANSCRIPT, mode="strict")
    assert r["passed"] is False
