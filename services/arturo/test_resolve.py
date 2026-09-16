# test_resolve.py — Speech→entity resolver (spec §3.1, Phase 1). Pure core.
import time
from services.arturo import resolve as R


def _al(*pairs):
    return [{"alias": a, "confidence": c} for a, c in pairs]


def _ent(kind, name, label, aliases, **kw):
    e = {"kind": kind, "id": f"{kind}:{name}", "label": label, "aliases": aliases,
         "pending": kw.pop("pending", False)}
    e.update(kw)
    return e


# ---------------------------------------------------------------------------
# resolve() core: match / ambiguous / none decision gating
# ---------------------------------------------------------------------------

def test_strong_match_with_margin():
    now = 1000.0
    cands = [
        _ent("approval", "apr_1", "Deploy adaptiv payments to prod?",
             _al(("adaptiv", "inferred"), ("payments", "inferred")),
             from_agent="pm-adaptiv-payments", feature="payments-deploy",
             recency_ts=now - 30, pending=True),
        _ent("agent", "gm", "gm", _al(("general manager", "curated")),
             recency_ts=now - 600),
    ]
    res = R.resolve("what's that adaptiv approval about", cands, now)
    assert res["status"] == "match"
    assert res["entity"]["id"] == "approval:apr_1"
    # namespaced id survives untouched (registry-swappable seam)
    assert res["entity"]["id"].startswith("approval:")
    # scores always present for observe-only logging
    assert res["scores"] and res["scores"][0]["id"] == "approval:apr_1"


def test_ambiguous_near_tie_asks_either_or():
    now = 1000.0
    cands = [
        _ent("agent", "acme-web", "acme-web",
             _al(("acme web", "curated"), ("web", "inferred")), recency_ts=now - 60),
        _ent("agent", "insurance-data-agent", "insurance-data-agent",
             _al(("acme data", "curated"), ("data", "inferred")), recency_ts=now - 60),
    ]
    res = R.resolve("the acme agent", cands, now)
    assert res["status"] == "ambiguous"
    ids = {a["id"] for a in res["alternatives"]}
    assert "agent:acme-web" in ids and "agent:insurance-data-agent" in ids
    assert " or " in res["said"]


def test_none_gives_sharp_best_guess_not_vague():
    now = 1000.0
    cands = [
        _ent("agent", "gm", "gm", _al(("general manager", "curated")), recency_ts=now - 5000),
    ]
    # nothing meaningfully overlaps → none, but name the best guess (§0a-B)
    res = R.resolve("the quarterly budget spreadsheet", cands, now)
    assert res["status"] == "none"
    assert res["entity"] is None
    # sharp one-liner naming a concrete guess, never a content-free re-prompt
    assert "?" in res["said"]
    assert res["said"].strip().lower() != "clarify what you mean"


def test_empty_or_stopword_ref_never_matches_on_recency_alone():
    now = 1000.0
    # a fresh PENDING approval would score high on recency+pending — but with no keyword evidence
    # (empty / all-stopword ref) it must NOT auto-focus.
    cands = [
        _ent("approval", "apr_1", "Deploy something",
             _al(("deploy", "inferred")), from_agent="gm",
             recency_ts=now - 5, pending=True),
    ]
    for ref in ("", "   ", "the one about that"):
        res = R.resolve(ref, cands, now)
        assert res["status"] == "none", ref
        assert res["entity"] is None, ref


def test_keyword_floor_blocks_recency_only_match():
    now = 1000.0
    # spoken names NOTHING in the candidate; only recency+pending push score over T_STRONG.
    cands = [
        _ent("approval", "apr_1", "Ship the widget",
             _al(("widget", "inferred")), from_agent="gm",
             recency_ts=now - 1, pending=True),
    ]
    res = R.resolve("the acme explorer", cands, now)
    assert res["status"] != "match"        # no keyword overlap → never an auto-match


def test_empty_candidates_is_none():
    res = R.resolve("the adaptiv approval", [], 1000.0)
    assert res["status"] == "none"
    assert res["entity"] is None
    assert res["alternatives"] == []


def test_pending_bonus_breaks_toward_pending_approval():
    now = 1000.0
    # same textual overlap, one is a pending approval, one a stale agent
    cands = [
        _ent("approval", "apr_9", "adaptiv thing",
             _al(("adaptiv", "inferred")), from_agent="pm-adaptiv-payments",
             recency_ts=now - 30, pending=True),
        _ent("agent", "pm-adaptiv-payments", "pm-adaptiv-payments",
             _al(("adaptiv", "curated")), recency_ts=now - 30, pending=False),
    ]
    res = R.resolve("the adaptiv one", cands, now)
    assert res["entity"]["kind"] == "approval"


def test_normalization_matches_transcription_variants():
    now = 1000.0
    cands = [
        _ent("agent", "pm-adaptiv-payments", "pm-adaptiv-payments",
             _al(("adaptiv payments", "curated")), recency_ts=now - 30),
    ]
    for spoken in ("adaptive", "adaptiv-payments", "Adaptiv Payments!"):
        res = R.resolve(spoken, cands, now)
        assert res["entity"] is not None, spoken
        assert res["entity"]["id"] == "agent:pm-adaptiv-payments", spoken


# ---------------------------------------------------------------------------
# §0a ACCEPTANCE FIXTURE — the exact live gap this feature eliminates.
# "go through the options you suggested for the recommendations" must resolve
# to the GM agent's latest OUTPUT (a `content` entity), in ONE turn, with an
# audible pick — never "I don't see a decision menu."
# ---------------------------------------------------------------------------

def test_acceptance_recommendations_resolve_to_gm_content():
    now = 2_000_000.0
    cands = [
        # the GM's freshly-captured output, surfaced as a content entity
        _ent("content", "gm:abc123", "GM's 5 recommendations for acme",
             _al(("recommendations", "inferred"), ("options", "inferred"),
                 ("strategy", "inferred")),
             from_agent="gm", recency_ts=now - 10),
        # noise: the GM agent itself + an unrelated approval
        _ent("agent", "gm", "gm", _al(("general manager", "curated")), recency_ts=now - 300),
        _ent("approval", "apr_x", "Deploy hamilton site?",
             _al(("hamilton", "inferred"), ("deploy", "inferred")),
             from_agent="hamilton-dev", recency_ts=now - 900, pending=True),
    ]
    res = R.resolve("go through the options you suggested for the recommendations", cands, now)
    assert res["status"] == "match", res
    assert res["entity"]["kind"] == "content"
    assert res["entity"]["from_agent"] == "gm"


def test_content_reference_what_you_just_said():
    now = 2_000_000.0
    cands = [
        _ent("content", "gm:def456", "GM's strategy recommendations",
             _al(("recommendations", "inferred"), ("strategy", "inferred")),
             from_agent="gm", recency_ts=now - 5),
        _ent("agent", "gm", "gm", _al(("general manager", "curated")), recency_ts=now - 300),
    ]
    res = R.resolve("walk me through the recommendations", cands, now)
    assert res["entity"]["kind"] == "content"


# ---------------------------------------------------------------------------
# recency: a just-active candidate ranks above a stale identical one
# ---------------------------------------------------------------------------

def test_recency_breaks_ties():
    now = 1000.0
    cands = [
        _ent("agent", "old", "acme", _al(("acme", "curated")), recency_ts=now - 86_400),
        _ent("agent", "new", "acme", _al(("acme", "curated")), recency_ts=now - 10),
    ]
    res = R.resolve("acme", cands, now)
    # both textual-equal; the fresh one wins the ranking
    assert res["scores"][0]["id"] == "agent:new"
