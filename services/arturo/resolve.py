# resolve.py — Speech→entity resolver for Arturo (spec 2026-08-12-arturo-speech-driven-focus §3.1).
#
# PURE core: `resolve(spoken_ref, candidates, now) -> Resolution`. No network, no db — candidates are
# injected (the impure gatherers live in resolve_providers.py). This is the SPOKEN form of the
# orchestra registry's `resolve()` verb; the interface is kept stable so the durable-catalog provider
# can swap to the registry later with ZERO migration (namespaced type-prefixed ids +
# aliases-with-confidence are the registry-canonical shapes — see spec §3.1 field map / fork 3).
#
# Decision is CONFIDENCE-GATED (positive-signal precision, memory feedback_safety_classifier_positive
# _signal): auto-`match` ONLY on a strong top score WITH margin; a near-tie → `ambiguous` (ask a
# one-line either/or); a miss → `none` but with a SHARP best-guess one-liner naming the top candidate
# (never a vague "clarify what you mean" — §0a-B: the live fixture bounced the operator twice with vague
# re-asks). Thresholds/weights are module constants, OBSERVE-ONLY first (spec §6) — the caller logs
# scores + would-pick before the auto-focus path is armed.
#
# Entity   = {kind: "approval"|"agent"|"feature"|"project"|"content",
#             id: "<kind>:<name>",                     # namespaced (registry-canonical)
#             label, aliases: [{alias, confidence: "exact"|"curated"|"inferred"}],
#             from_agent?, feature?, recency_ts?, pending: bool}
# Resolution = {status: "match"|"ambiguous"|"none", entity: Entity|None,
#               alternatives: [Entity], said: str, scores: [ScoreRow]}

from services.arturo.call_journal import _norm_fuzzy

# --- tunable constants (OBSERVE-ONLY first; tune empirically before arming auto-act) ---
W_KW = 1.0          # keyword-overlap weight (the primary signal)
W_REC = 0.35        # recency weight (a just-fired approval / just-active agent ranks up)
W_PEND = 0.25       # pending weight (an approval the operator asks about is likely a pending one)

T_STRONG = 0.45     # top score must clear this to auto-match
T_WEAK = 0.20       # below this the top is too weak to even offer as an either/or
T_MARGIN = 0.12     # (top - second) must clear this to auto-match; else it's a near-tie
T_KW_FLOOR = 0.15   # a match needs REAL keyword evidence — recency+pending alone can't auto-focus
                    # the freshest pending thing regardless of what the operator actually said

RECENCY_HALFLIFE_S = 1800.0   # recency_bonus halves every 30 min; no hard cutoff

# confidence → keyword weight (an exact/curated alias hit counts for more than an inferred one)
_CONF_W = {"exact": 1.0, "curated": 0.85, "inferred": 0.7}
_LABEL_W = 0.8        # tokens that come from the entity label
_FIELD_W = 0.75       # tokens from from_agent / feature scalars

# scoring-only stopwords: dropped from BOTH sides before overlap so articles/preps ("the options
# for the recommendations") don't dilute the signal. NOT mutating call_journal._FILLER (that's the
# ASR-dedup filler set) — this is an additive scoring concern.
_STOP = {"the", "a", "an", "for", "of", "to", "on", "in", "at", "and", "or", "that",
         "this", "these", "those", "my", "your", "about", "with", "one", "ones", "it",
         "go", "through", "walk", "me", "what", "whats", "s",
         # entity-KIND words: conveyed by Entity.kind, not discriminators between candidates
         "agent", "agents", "approval", "approvals", "feature", "features",
         "project", "projects"}

_FUZZY_MIN = 4        # both tokens must be >= this length for a prefix (non-exact) match to count
_FUZZY_DISCOUNT = 0.9  # a prefix match ("adaptive"~"adaptiv") counts slightly less than an exact one


def _tokset(text):
    """Normalized content-token SET (reuses call_journal._norm_fuzzy, then drops scoring stopwords)."""
    return {t for t in _norm_fuzzy(text or "") if t and t not in _STOP}


def _candidate_token_weights(ent):
    """Map token -> best keyword weight it can earn, across the candidate's
    {label, aliases (by confidence), from_agent, feature} bag."""
    weights = {}

    def _add(text, w):
        for tok in _tokset(text):
            if weights.get(tok, 0.0) < w:
                weights[tok] = w

    _add(ent.get("label"), _LABEL_W)
    for a in ent.get("aliases") or []:
        _add(a.get("alias"), _CONF_W.get(a.get("confidence"), _CONF_W["inferred"]))
    if ent.get("from_agent"):
        _add(ent["from_agent"], _FIELD_W)
    if ent.get("feature"):
        _add(ent["feature"], _FIELD_W)
    return weights


def _tok_match_weight(spoken_tok, cand_weights):
    """Best keyword weight this spoken token earns against the candidate bag. Exact hit = full
    weight; a prefix hit ("adaptive"~"adaptiv", "adaptiv-payments"~"adaptiv") = discounted — this is
    what lets ASR/transcription variants normalize-match (spec §3.1). Guarded by _FUZZY_MIN so short
    tokens can't spuriously prefix-match."""
    best = 0.0
    for c, w in cand_weights.items():
        if spoken_tok == c:
            if w > best:
                best = w
        elif (len(spoken_tok) >= _FUZZY_MIN and len(c) >= _FUZZY_MIN
              and (c.startswith(spoken_tok) or spoken_tok.startswith(c))):
            fw = w * _FUZZY_DISCOUNT
            if fw > best:
                best = fw
    return best


def _recency_bonus(recency_ts, now):
    if not recency_ts:
        return 0.0
    age = max(0.0, now - float(recency_ts))
    return 0.5 ** (age / RECENCY_HALFLIFE_S)      # 1.0 fresh → 0.5 at one half-life → →0


def _score_one(spoken_toks, ent, now):
    """score = W_KW*keyword + W_REC*recency + W_PEND*pending. keyword = confidence-weighted
    overlap of the spoken tokens explained by this candidate, over the spoken token count."""
    cw = _candidate_token_weights(ent)
    if spoken_toks:
        matched = sum(_tok_match_weight(t, cw) for t in spoken_toks)
        kw = matched / len(spoken_toks)
    else:
        kw = 0.0
    rec = _recency_bonus(ent.get("recency_ts"), now)
    pend = 1.0 if ent.get("pending") else 0.0
    score = W_KW * kw + W_REC * rec + W_PEND * pend
    return {"id": ent.get("id"), "score": round(score, 4),
            "kw": round(kw, 4), "rec": round(rec, 4), "pend": pend}


def _phrase(ent):
    """A short spoken-ready description of a candidate for confirm/either-or/best-guess lines."""
    label = (ent.get("label") or ent.get("id") or "that").strip()
    kind = ent.get("kind")
    frm = ent.get("from_agent")
    if kind == "approval":
        return f"the {label} approval" + (f" from {frm}" if frm else "")
    if kind == "content":
        return f"{frm}'s {label}" if frm else f"the {label}"
    if kind == "agent":
        return f"the {label} agent"
    if kind == "project":
        return f"the {label} project"
    if kind == "feature":
        return f"the {label} feature"
    return label


def resolve(spoken_ref, candidates, now):
    """Resolve a spoken reference to ONE canonical entity across the injected candidate union.
    Pure + stable across the Phase-1→2 registry swap. Returns a Resolution dict (see module doc)."""
    candidates = candidates or []
    spoken_toks = _tokset(spoken_ref)

    # No content tokens (empty / all-stopword ref) → nothing to key on. Never let recency/pending
    # signal alone conjure a "match" off zero keyword evidence.
    if not spoken_toks:
        return {"status": "none", "entity": None, "alternatives": [],
                "said": "I'm not sure which one you mean — can you say more?", "scores": []}

    scored = sorted(
        ((_score_one(spoken_toks, e, now), e) for e in candidates),
        key=lambda pair: pair[0]["score"], reverse=True)
    score_rows = [s for s, _ in scored]

    if not scored:
        return {"status": "none", "entity": None, "alternatives": [],
                "said": "I'm not sure which one you mean — can you say more?", "scores": []}

    top_s, top_e = scored[0]
    second_score = scored[1][0]["score"] if len(scored) > 1 else 0.0
    margin = top_s["score"] - second_score

    # STRONG + clear margin + real keyword evidence → auto-match (spoken so a wrong pick is
    # audible + correctable). The keyword floor stops a fresh/pending candidate from auto-focusing
    # on recency+pending signal alone when the operator's words didn't actually name it.
    if top_s["score"] >= T_STRONG and margin >= T_MARGIN and top_s["kw"] >= T_KW_FLOOR:
        return {"status": "match", "entity": top_e, "alternatives": [],
                "said": f"Focusing on {_phrase(top_e)}.", "scores": score_rows}

    # near-tie (top is meaningful but no clear winner) → ask a one-line either/or over the top-2/3.
    if top_s["score"] >= T_WEAK and margin < T_MARGIN:
        alts = [e for s, e in scored[:3] if (top_s["score"] - s["score"]) < T_MARGIN][:3]
        if len(alts) < 2:
            alts = [e for _, e in scored[:2]]
        joined = " or ".join(_phrase(a) for a in alts)
        return {"status": "ambiguous", "entity": None, "alternatives": alts,
                "said": f"Do you mean {joined}?", "scores": score_rows}

    # miss → none, but name the BEST guess as a sharp yes/no (never a content-free re-prompt).
    return {"status": "none", "entity": None, "alternatives": [],
            "said": f"Do you mean {_phrase(top_e)}?", "scores": score_rows}
