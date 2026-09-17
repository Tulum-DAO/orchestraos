"""Comprehension gate — the real 'absorbed the context' check (RED-TEAM Finding 0 + H4).

Comprehension is verifiable ONLY by generation, never by observing that reads
occurred (gen-7's script-read proves watching-reads is unverifiable). The successor
GENERATES a read-back in its own words + answers predecessor-authored canary
questions; we grade MECHANICALLY against DAEMON-HELD ground truth — no LLM judge, so
the non-LLM daemon can run it.

Anti-Goodhart invariants (ratified constraints):
- Ground truth (canary answers + deep open_loops/hazards) is DAEMON-HELD; the
  successor-visible init carries QUESTIONS only, never answers.
- The read-back must cover DEEP items (buried open_loops/hazards), so a header-only
  400-line shallow read fails — it never saw them.
- Evidence is a CLAIM checked against ground truth; arrival alone proves nothing.
"""
import re
from typing import List

_STOP = {
    "the", "a", "an", "of", "to", "and", "or", "is", "it", "its", "for", "on", "in",
    "at", "by", "be", "as", "with", "that", "this", "must", "owed", "some", "there",
    "are", "holding", "land", "fix", "still", "not", "run", "sitting", "owe", "dont",
    "do", "stay", "clean", "them", "item", "items", "which", "what", "why",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _tokens(s: str) -> List[str]:
    return [t for t in _norm(s).split() if t]


def _distinctive(s: str) -> List[str]:
    """The salient tokens of a ground-truth item — ids/shas/names/long words that a
    genuine read-back would echo but a shallow read never saw. Stopwords + short
    generic tokens are dropped."""
    out = []
    for t in _tokens(s):
        if t in _STOP:
            continue
        if any(c.isdigit() for c in t) or len(t) >= 5:
            out.append(t)
    return out


def _covered(item: str, candidate_text: str) -> bool:
    """An item is covered iff ANY of its distinctive tokens appears in the candidate."""
    keys = _distinctive(item)
    if not keys:
        return True  # nothing distinctive to require
    cand = _norm(candidate_text)
    return any(re.search(r"\b" + re.escape(k) + r"\b", cand) for k in keys)


# A resolved transcript REGION carries many distinctive tokens; a short factual
# answer (a sha, a model id, a name) carries few. The two need different rules —
# demanding every distinctive word of a whole region appear in an own-words answer
# would fail every honest successor, and a gate that fails good candidates is
# worse than no gate.
REGION_TOKEN_THRESHOLD = 6     # more distinctive tokens than this = a region
REGION_MIN_OVERLAP = 0.25      # share of the region's distinctive terms required
REGION_MIN_TERMS = 2           # ...and at least this many, so a 1-word hit fails

# --- citation-grounding calibration (DEC-1787728346, CONSENSUS_REACHED) ------
# Real rotation canaries point at LARGE transcript spans (21k-92k chars on the
# gen4 fixtures) and the author-remembered windows are unreliable (gen4 q1's
# cited fact lives ~5h outside its window; gm-gen28's pointers are prose). A
# fraction-overlap of such a region fails every honest focused answer (measured
# 0.031-0.104 vs the 0.25 floor on a genuine 5/5 readback). For those, grade by
# CITATION GROUNDING against the FULL predecessor transcript instead. A small,
# correctly-resolved region (the daemon recanary path's planted one-sentence
# regions) keeps the legacy overlap rule, which is fair at that scale.
REGION_OVERLAP_MAX_CHARS = 2000  # resolved region larger than this -> grounding

STRONG_MIN_LEN = 3           # digit-bearing anchor tokens shorter than this are noise
STRONG_FREQ_MAX = 50         # anchor must not saturate the transcript (freq 1..50)
RARE_ALPHA_MIN_LEN = 6       # alpha citation tokens: specific vocabulary only
RARE_ALPHA_FREQ_MAX = 25     # ...and rare in the transcript (self-calibrating IDF)
PASS_STRONG_MIN = 2          # per-question PASS: >=2 grounded strong anchors...
PASS_STRONG_ONE_ALPHA = 3    # ...or 1 grounded strong + >=3 rare-alpha
WEAK_RARE_ALPHA_MIN = 6      # WEAK rung (tolerated minority): >=6 rare-alpha
UNION_MIN = 4                # grounded strong anchors across all questions
DISTINCT_EVIDENCE_MIN = 3    # questions that each ground an anchor no other cites
WEAK_TOLERATED = {"supervised": 1, "strict": 0}

_QLABEL = re.compile(r"^c?q\d+$")
_QNUM = re.compile(r"^[a-z]{1,2}(\d+)$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
# id-class tokens (hex ids, apr_/msg_/qnr_/DEC digit tails after _norm splits
# the prefix off, long numerals): a pass may not be built from round numbers.
_IDCLASS = re.compile(r"^(?:[0-9a-f]{7,40}|(?:apr|msg|qnr|dec)\d*|\d{9,})$")


def _canon_qid(k) -> str:
    """Canonicalize a canary answer/ground-truth id to `q<N>` (DEC-1787808620 fix
    #2). The successor's readback answers are keyed `q<N>` (s3_live_seams strips any
    `c` prefix in _split_readback_sections), while the daemon-held ground_truth ids
    are the predecessor's authored `cq<N>`. Aligning `c?q<N>` on BOTH sides makes a
    human-GOOD readback's answers reach the grader instead of scoring empty. A
    non-numeric id (e.g. `q_plant`) is left as-is (lowercased) — no other id class is
    touched, so no existing caller changes."""
    m = _QNUM.match(str(k).strip().lower())
    return f"q{m.group(1)}" if m else str(k).strip().lower()


def _word_freq(text: str) -> dict:
    freq = {}
    for t in _norm(text).split():
        freq[t] = freq.get(t, 0) + 1
    return freq


def _classify_grounded(answer: str, question: str, freq: dict) -> dict:
    """Per-question citation-grounding verdict against the full-transcript word
    frequencies. Tokens appearing in the QUESTION never count (anti-echo)."""
    if not str(answer).strip():
        return {"verdict": "EMPTY", "grounded_strong": [], "rare_alpha": 0}
    total_tokens = sum(freq.values()) if freq else 0
    max_strong = max(STRONG_FREQ_MAX, int(total_tokens * 0.005))
    max_alpha = max(RARE_ALPHA_FREQ_MAX, int(total_tokens * 0.0025))
    qtok = set(_norm(question).split())
    toks = [t for t in set(_distinctive(answer))
            if t not in qtok and not _QLABEL.match(t)]
    strong = {t for t in toks
              if any(c.isdigit() for c in t) and len(t) >= STRONG_MIN_LEN
              and not _YEAR.match(t)}
    alpha = {t for t in toks if t.isalpha() and len(t) >= RARE_ALPHA_MIN_LEN}
    gs = {t for t in strong if 1 <= freq.get(t, 0) <= max_strong}
    ra = {t for t in alpha if 1 <= freq.get(t, 0) <= max_alpha}
    out = {"cited_strong": len(strong), "grounded_strong": sorted(gs),
           "rare_alpha": len(ra), "grounded_ra": sorted(ra)}
    if len(strong) >= 2 and not any(freq.get(t, 0) for t in strong):
        out["verdict"] = "HALLUCINATED"
    elif len(gs) >= PASS_STRONG_MIN or (
            len(gs) >= 1 and len(ra) >= PASS_STRONG_ONE_ALPHA):
        out["verdict"] = "PASS"
    elif len(ra) >= WEAK_RARE_ALPHA_MIN:
        out["verdict"] = "WEAK"
    else:
        out["verdict"] = "MISS"
    return out


def _grade_grounded(rows: list, given: dict, transcript_text: str,
                    mode: str, synthesized: bool = False) -> dict:
    """Aggregate citation-grounding over the large-region/unresolved-pointer
    questions. Conservative by construction: any EMPTY / HALLUCINATED / MISS
    fails; WEAK tolerated only within the mode's budget; the grounded-anchor
    UNION must be broad (>=UNION_MIN), contain an id-class token, and
    >=DISTINCT_EVIDENCE_MIN questions must each ground an anchor cited in NO
    other answer (kills identical-ids-sprinkled-everywhere). Strict mode
    additionally requires every question whose region resolved to be
    region-corroborated (>=1 of its grounded anchors inside its own region)."""
    freq = _word_freq(transcript_text)
    per_q, sets, missed = {}, [], []
    region_ok = True
    for q in rows:
        qid = q.get("id")
        r = _classify_grounded(given.get(_canon_qid(qid), ""), q.get("question", ""), freq)
        gset = set(r["grounded_strong"])
        anchors = gset | set(r.get("grounded_ra", []))
        region = str(q.get("answer", "") or "")
        if region:
            r["region_corroborated"] = any(
                re.search(r"\b" + re.escape(t) + r"\b", _norm(region))
                for t in anchors)
            if not r["region_corroborated"]:
                region_ok = False
        per_q[qid] = r
        sets.append((qid, gset))
    n = len(sets)
    union = set().union(*(s for _, s in sets)) if sets else set()
    exclusive = {qid: (s - set().union(*([x for j, (_, x) in enumerate(sets) if j != i]
                                         or [set()])))
                 for i, (qid, s) in enumerate(sets)}
    distinct_q = sum(1 for qid in exclusive if exclusive[qid])
    hard_bad = [qid for qid, _ in sets
                if per_q[qid]["verdict"] in ("EMPTY", "HALLUCINATED", "MISS")]
    weak = [qid for qid, _ in sets if per_q[qid]["verdict"] == "WEAK"]
    tolerated = WEAK_TOLERATED.get(mode, 0)
    aggregate = {
        "union": len(union),
        "union_min": min(UNION_MIN, 2 * n),
        "distinct_q": distinct_q,
        # synthesized baton (`orchestra rotate --synthesize`, gm ruling msg_2520355c):
        # identity-shaped questions about ONE fresh seat cannot each own an exclusive
        # anchor (the sid/seat name is legitimately in every answer), so the
        # distinct-evidence floor is 1 there; per-question grounding, union_min,
        # union-level idclass and strict region corroboration are unchanged. Real
        # batons never set it.
        "distinct_min": 1 if synthesized else min(DISTINCT_EVIDENCE_MIN, n),
        "synthesized": bool(synthesized),
        "idclass": any(_IDCLASS.match(t) for t in union),
        "weak": len(weak), "weak_tolerated": tolerated,
        "region_corroborated_all": region_ok,
    }
    ok = (n > 0 and not hard_bad and len(weak) <= tolerated
          and len(union) >= aggregate["union_min"]
          and distinct_q >= aggregate["distinct_min"]
          and aggregate["idclass"])
    if mode == "strict":
        ok = ok and region_ok
    missed = hard_bad + (weak if len(weak) > tolerated else [])
    if ok:
        missed = []
    elif not missed:
        # Aggregate-level failure. NAME THE BLAMEABLE QUESTIONS when there are
        # any: a strict region-corroboration failure has exact culprits (the
        # non-corroborated qids) — listing all qids sent a reviewer to the
        # wrong question on the gen29 datum (ob, 08-26). Only the truly
        # collective failures (union/distinct/idclass) still report all
        # grounded questions, so an aggregate veto is never silent.
        if mode == "strict" and not region_ok:
            missed = [qid for qid, _ in sets
                      if per_q[qid].get("region_corroborated") is False]
        elif distinct_q < aggregate["distinct_min"]:
            # Aggregate-only veto (every per-question verdict PASS): missed stays []
            # and the aggregate carries the reason + the exact culprits — the
            # questions whose grounded anchors are all cited by some other answer
            # (gate rerun on 5b4f522: three PASSing answers, the sid repeated in each,
            # were reported as missed q1..q3 with no reason; gm ruling msg_cd1985d8).
            culprits = [qid for qid, _ in sets if not exclusive[qid]]
            aggregate["culprits"] = culprits
            aggregate["reason"] = (
                f"distinct_q {distinct_q} < {aggregate['distinct_min']}: questions share "
                "anchors — the same id repeated across answers earns credit for only one "
                "of them; " + ", ".join(culprits) + " cite nothing exclusive to them")
            missed = []
        elif len(union) < aggregate["union_min"] or not aggregate["idclass"]:
            aggregate["reason"] = (f"union {len(union)} < {aggregate['union_min']}" if len(union) < aggregate["union_min"]
                                   else "no id-class anchor (sha / msg id / hex) grounded in any answer")
            missed = []
        if not missed and "reason" not in aggregate:
            missed = [qid for qid, _ in sets]
    return {"passed": ok, "missed": missed, "per_question": per_q,
            "aggregate": aggregate}


def grade_canary(given: dict, canary: list, transcript_text: str = None,
                 mode: str = "supervised", synthesized: bool = False) -> dict:
    """Grade own-words answers against ground truth.

    SHORT expected answer (<= REGION_TOKEN_THRESHOLD distinctive tokens, not
    pointer-derived): strict — every distinctive token must appear (a sha or a
    model id is exact or wrong).

    SMALL resolved region (pointer-derived or long non-pointer, resolved text
    <= REGION_OVERLAP_MAX_CHARS): legacy overlap — the successor must share a
    real fraction of the region's distinctive terms.

    LARGE or UNRESOLVED pointer-derived region: CITATION GROUNDING against the
    full predecessor transcript (`transcript_text`) — see _grade_grounded. The
    author-remembered region cannot be trusted as ground-truth scope
    (DEC-1787728346 root cause 3), so the region never flunks an answer by
    itself; it corroborates (and gates, in strict mode).

    A pointer-derived question that needs grounding but has NO transcript_text
    is UNGRADEABLE: pass=False with reason (fail-hold) — never the silent
    vacuous PASS the old empty-region skip produced."""
    # Fix #2 (DEC-1787808620): align `c?q<N>` answer keys with ground_truth ids so a
    # GOOD readback keyed `q<N>` is graded against `cq<N>` ground truth (and vice
    # versa) instead of scoring every question empty. Strictness is untouched — only
    # the lookup key is normalized.
    given = {_canon_qid(k): v for k, v in (given or {}).items()}
    missed = []
    grounded_rows = []
    for q in canary:
        qid = q.get("id")
        expected = q.get("answer", "")
        ans = given.get(_canon_qid(qid), "")
        from_pointer = bool(q.get("from_pointer"))
        exp_keys = _distinctive(expected) or _tokens(expected)
        if from_pointer and (not exp_keys
                             or len(str(expected)) > REGION_OVERLAP_MAX_CHARS):
            grounded_rows.append(q)
            continue
        cand = _norm(ans)
        if not str(ans).strip() or not exp_keys:
            if not str(ans).strip():
                missed.append(qid)
            continue
        if not from_pointer and len(exp_keys) <= REGION_TOKEN_THRESHOLD:
            ok = all(re.search(r"\b" + re.escape(k) + r"\b", cand) for k in exp_keys)
        else:
            hits = [k for k in set(exp_keys)
                    if re.search(r"\b" + re.escape(k) + r"\b", cand)]
            ok = (len(hits) >= REGION_MIN_TERMS
                  and len(hits) / len(set(exp_keys)) >= REGION_MIN_OVERLAP)
        if not ok:
            missed.append(qid)
    result = {"passed": not missed, "missed": missed}
    if grounded_rows:
        if not (transcript_text or "").strip():
            gm = [q.get("id") for q in grounded_rows]
            return {"passed": False, "missed": missed + gm,
                    "ungradeable": gm,
                    "reason": "UNGRADEABLE: pointer-derived ground truth needs "
                              "the predecessor transcript for citation "
                              "grounding and none was provided — fail-hold, "
                              "never a vacuous pass"}
        g = _grade_grounded(grounded_rows, given, transcript_text, mode, synthesized=synthesized)
        result = {"passed": result["passed"] and g["passed"],
                  "missed": missed + g["missed"],
                  "per_question": g["per_question"],
                  "aggregate": g["aggregate"],
                  "mode": mode}
    return result


def grade_readback(readback: dict, ground_truth: dict) -> dict:
    """Keyword-set coverage of the DEEP items (v2 §S3.4). Requires the goal + EVERY
    standing guard + EVERY open_loop + EVERY hazard to be cited (in the successor's
    own words). Header-only / missing-a-class fails.

    `guards` ground-truth = the handoff's decisions[{text, rationale}] — cite by the
    decision text (the lane boundary a shallow read never internalises)."""
    missed = []

    goal = ground_truth.get("goal", "")
    if goal and not _covered(goal, str(readback.get("goal", ""))):
        missed.append(f"goal:{goal}")

    # guards: ground-truth carries either plain strings or decisions[{text,...}].
    guard_text = " ".join(str(x) for x in (readback.get("guards", []) or []))
    for g in ground_truth.get("guards", []) or []:
        item = g.get("text") if isinstance(g, dict) else g
        if item and not _covered(item, guard_text):
            missed.append(f"guards:{item}")

    for field in ("open_loops", "hazards"):
        gt_items = ground_truth.get(field, []) or []
        cand_text = " ".join(str(x) for x in (readback.get(field, []) or []))
        for item in gt_items:
            if not _covered(item, cand_text):
                missed.append(f"{field}:{item}")

    return {"passed": not missed, "missed": missed}


def _transcript_text(path) -> str:
    """Message text of a Claude-runtime jsonl transcript (the grounding corpus).
    Mirrors rotation_gate_manual.resolve_pointer's text extraction — message
    content only, never raw jsonl bytes (raw lines carry a uuid per row, which
    would ground arbitrary hex). Returns "" on any failure (callers fail-hold)."""
    import json as _json
    try:
        lines = open(path, errors="replace").read().splitlines()
    except OSError:
        return ""
    out = []
    for ln in lines:
        try:
            m = (_json.loads(ln).get("message") or {})
        except (ValueError, AttributeError):
            continue
        c = m.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") in ("text", "thinking"):
                    out.append(b.get("text") or b.get("thinking") or "")
    return "\n".join(out)


def check_comprehension(evidence: dict, ground_truth: dict) -> dict:
    """Combined gate: comprehended iff canary AND read-back both pass. `evidence` is
    the successor's CLAIM ({readback, canary_answers}); `ground_truth` is daemon-held.

    Citation grounding (DEC-1787728346) reads the corpus from
    ground_truth["transcript_text"], or loads it from
    ground_truth["transcript_path"] (the recanary/daemon path already carries
    it). Mode: ground_truth["mode"] in {"supervised","strict"} — strict is the
    auto-retire arming precondition (zero WEAK + region corroboration)."""
    ttext = ground_truth.get("transcript_text") or ""
    if not ttext and ground_truth.get("transcript_path"):
        ttext = _transcript_text(ground_truth["transcript_path"])
    canary = grade_canary(evidence.get("canary_answers", {}) or {},
                          ground_truth.get("canary", []) or [],
                          transcript_text=ttext,
                          mode=str(ground_truth.get("mode") or "supervised"),
                          synthesized=bool(ground_truth.get("synthesized")))
    readback = grade_readback(evidence.get("readback", {}) or {}, ground_truth)
    return {
        "comprehended": canary["passed"] and readback["passed"],
        "canary": canary,
        "readback": readback,
    }
