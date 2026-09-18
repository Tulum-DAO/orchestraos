"""RED-first matrix — A2 handoff-authoring-completeness (DEC-1788207386 CONSENSUS).

Amended spec §7 rulings (LOCKED):
  Axis-1 = (a)+(c): build_author_trigger INSTRUCTS a json block AND recognition
    accepts EITHER markdown `##` sections OR a json block; the markdown parser
    LIFTS canary_questions + first_effect from `##` sections.
  Axis-2 = (a) MERGE per-field: json wins per-field for SCALARS; for LIST fields
    json-list-WINS-IF-PRESENT-else-markdown (union REJECTED — grader-integrity
    hazard); markdown FILLS any field the json omits (kills the silent-drop).
  Axis-3 = (b) capture-at-author-of-PRIOR-rev (+ (a) manual seed fallback): a
    seat that authored BEFORE its first soft beat gets a baseline = the PRIOR
    committed rev, so its current (just-authored) handoff is FRESH.

TWO LOCKED CAVEATS (both models, non-negotiable, in the matrix):
  C1 file_hash CONTENT-IDENTITY: the freshness compare uses file_hash (sha256
     bytes) NOT commit-identity — a no-op/whitespace recommit (new commit_sha,
     SAME file_hash) MUST stay STALE.
  C2 LIST-FIELD DETERMINISM: markdown-5 + json-3-same-ids -> EXACTLY 3;
     markdown-5 + json-omits -> EXACTLY 5. NEVER a union (never 8).

Negative controls N1-N4 + the silent-drop control all STAND. NO richness bypass,
NO answer keys, fail-closed freshness.

RED honesty: freshness-compare tests call handoff_ready as today; the file_hash
tests RED because the current compare is on the full commit-bearing rev
(author_gate.py:116) — a real behavior gap, not a manufactured failure. The
markdown-lift + merge tests RED because _extract_markdown_sections omits
canary_questions/first_effect and _parse_handoff_text WINS-OUTRIGHT (no merge).
"""
import importlib.util
import json
import os
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))


def _fresh_provider(orch):
    os.environ["ORCHESTRA_DIR"] = str(orch)
    spec = importlib.util.spec_from_file_location(
        "hp_a2_uut", os.path.join(_HERE, "handoff_provider.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _author_gate():
    spec = importlib.util.spec_from_file_location(
        "ag_a2_uut", os.path.join(_HERE, "author_gate.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---- shared markdown fixtures -------------------------------------------------
# COLON-MULTILINE is the MANDATORY format the fleet actually authors (verified in
# docs/HANDOFF_acme-mcp-builder-next.md). CRITICAL: source_pointer VALUES
# contain colons (e.g. "jsonl region ~05:25-05:45Z") — the parser MUST split each
# sub-line on the FIRST colon only, never on every ':' and never pipe-split.
_MD_CANARY = """## current_goal
ship slice-2

## canary_questions
- id: Q1
  question: why did the seam change at the promote boundary
  source_pointer: jsonl region ~05:25-05:45Z 2026-08-31 — evidence pack msg_a7f8db92
- id: Q2
  question: what broke first when the gate refused
  source_pointer: jsonl:turn-40
- id: Q3
  question: which store held the stale value
  source_pointer: jsonl region ~06:00-06:10Z; corroborate at :88

## first_effect
- kind: command
- target: pytest scripts/x
- check: green
"""

_DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "docs")


def _discover_real_handoff_with_canaries(provider):
    """PROVE-BY-EFFECT re-point (gm test-hygiene ruling): DISCOVER a CURRENT real fleet
    handoff that carries the colon-multiline `## canary_questions` shape, instead of
    PINNING one file that rotates out. The old test pinned
    docs/HANDOFF_acme-mcp-builder-next.md, which rotated -> canary_questions=None ->
    the test flaked RED with no code fault. Discovery keeps the prove-by-effect intent
    (the parser lifts the fleet's REAL colon-multiline canaries) AND is durable against
    re-rotation (any qualifying current handoff works; skip only if the fleet has none).
    Deterministic (sorted). Returns (path, canary_questions) for the first
    docs/HANDOFF_*.md the parser lifts >=3 colon-carrying canaries from, else (None, None)."""
    import glob
    for path in sorted(glob.glob(os.path.join(_DOCS_DIR, "HANDOFF_*.md"))):
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            continue
        d = provider._extract_markdown_sections(text) or {}
        cq = d.get("canary_questions")
        if (isinstance(cq, list) and len(cq) >= 3
                and all(c.get("id") and c.get("question") and c.get("source_pointer")
                        for c in cq[:3])
                and ":" in (cq[0].get("source_pointer") or "")
                and len(cq[0].get("source_pointer") or "") > 20):
            return path, cq
    return None, None


# =============================================================== Axis-1(b) LIFT
def test_markdown_parser_lifts_canary_questions():
    """Axis-1(b): a `## canary_questions` markdown section (id | question |
    source_pointer per bullet) is parsed into a canary_questions LIST of
    {id, question, source_pointer} dicts."""
    m = _fresh_provider_tmp()
    d = m._extract_markdown_sections(_MD_CANARY)
    assert d is not None
    cq = d.get("canary_questions")
    assert isinstance(cq, list) and len(cq) == 3, f"expected 3 canary Qs; got {cq}"
    assert cq[0]["id"] == "Q1"
    assert "seam change" in cq[0]["question"]
    # CRITICAL: split on FIRST colon only — the source_pointer value has colons.
    assert cq[0]["source_pointer"] == (
        "jsonl region ~05:25-05:45Z 2026-08-31 — evidence pack msg_a7f8db92"), \
        f"source_pointer with colons must survive intact; got {cq[0]['source_pointer']!r}"
    assert cq[2]["source_pointer"] == "jsonl region ~06:00-06:10Z; corroborate at :88"


def test_prove_by_effect_lifts_canary_from_a_real_current_handoff():
    """PROVE-BY-EFFECT (gen28 mandatory): the parser lifts canary_questions from a
    CURRENT real fleet handoff (colon-multiline), NOT a synthetic fixture — the real
    subject the arm-gate needs. Re-pointed via discovery (gm test-hygiene ruling) so it
    is durable against handoff rotation: the pinned acme handoff rotated out. A real
    source_pointer carries colons/em-dashes/msg-ids and MUST survive the first-colon
    split intact. Skip (not fail) only if the whole fleet currently has no qualifying
    handoff — that is a coverage gap to re-point, never a silent green."""
    m = _fresh_provider_tmp()
    path, cq = _discover_real_handoff_with_canaries(m)
    if path is None:
        pytest.skip("no current docs/HANDOFF_*.md carries the colon-multiline "
                    "canary_questions shape (all rotated) — re-point when one lands")
    assert isinstance(cq, list) and len(cq) >= 3, \
        f"{os.path.basename(path)}: must lift >=3 real colon-multiline canary_questions; got {cq}"
    assert cq[0].get("id") and cq[0].get("question") and cq[0].get("source_pointer")
    # a real source_pointer carries colons — must not be truncated at the FIRST colon
    assert ":" in cq[0]["source_pointer"] and len(cq[0]["source_pointer"]) > 20, \
        f"{os.path.basename(path)}: source_pointer must survive the first-colon split intact"


def test_markdown_parser_lifts_first_effect():
    """Axis-1(b): a `## first_effect` markdown section is parsed into a
    first_effect dict {kind, target, check}."""
    m = _fresh_provider_tmp()
    d = m._extract_markdown_sections(_MD_CANARY)
    fe = d.get("first_effect")
    assert isinstance(fe, dict) and fe.get("kind") == "command"
    assert fe.get("target") == "pytest scripts/x" and fe.get("check") == "green"


def test_markdown_only_handoff_can_now_be_rich():
    """The whole point: a MARKDOWN-only handoff (no json block) with all sections
    now passes require_richness (given freshness) — was impossible before A2."""
    m = _fresh_provider_tmp()
    ag = _author_gate()
    full_md = _MD_CANARY + (
        "\n## phase_state\n- phase 2 of 4\n- next_gate: gm merge-gate\n"
        "\n## next_3_actions\n1. a\n2. b\n"
        "\n## decisions\n- single-trunk — critical path\n- no kill — idempotent\n"
        "\n## open_loops\n- awaiting gm\n"
        "\n## hazards\n- registry contention\n")
    d = m._extract_markdown_sections(full_md)
    d["handoff_commit_sha"], d["file_hash"] = "shaNEW", "hashNEW"
    r = ag.handoff_ready(d, 100.0, 200.0, baseline_rev="shaOLD:hashOLD")
    assert r["ready"] is True, f"markdown-only rich handoff must be ready; got {r['reasons']}"


# =========================================================== Axis-1(a) TRIGGER
def test_author_trigger_instructs_json_block():
    """Axis-1(a): build_author_trigger now instructs the agent to also emit a
    fenced json machine-block (the canonical rich form)."""
    ag = _author_gate()
    body = ag.build_author_trigger("seat", "docs/HANDOFF_seat-next.md")
    # must instruct emitting a FENCED json machine-block — not merely mention
    # 'jsonl' (the transcript) incidentally. Assert the fence or an explicit
    # "machine-block"/"fenced json" instruction.
    low = body.lower()
    assert ("```json" in body or "machine-block" in low or "fenced json" in low
            or "json machine block" in low), \
        "author-trigger must INSTRUCT a fenced json machine-block (Axis-1(a))"


# ========================================================= Axis-2(a) MERGE
def test_merge_json_missing_field_filled_from_markdown_no_silent_drop():
    """Silent-drop control (DOGFOODED): a rich markdown body + a json block that
    OMITS open_loops must still carry open_loops (filled from markdown) — NOT
    dropped by json-wins-outright."""
    m = _fresh_provider_tmp()
    md = ("## current_goal\ng\n\n## open_loops\n- deep loop from markdown\n\n"
          "```json\n" + json.dumps({"current_goal": "g",
                                    "next_3_actions": ["a"]}) + "\n```\n")
    d = m._parse_handoff_text(md)
    assert d.get("open_loops"), "open_loops must be filled from markdown (merge), not dropped"
    assert d["open_loops"] == ["deep loop from markdown"]


def test_merge_json_scalar_wins_over_markdown():
    """Per-field precedence: a scalar present in BOTH — json wins (deliberate
    structured override)."""
    m = _fresh_provider_tmp()
    md = ("## current_goal\nMARKDOWN goal\n\n"
          "```json\n" + json.dumps({"current_goal": "JSON goal",
                                    "next_3_actions": ["a"]}) + "\n```\n")
    d = m._parse_handoff_text(md)
    assert d["current_goal"] == "JSON goal", "json scalar must win over markdown"


# ---- C2 LIST-FIELD DETERMINISM (positive control) ----------------------------
def _md_canary_block(n):
    """n colon-multiline canary items (the mandatory fleet format)."""
    return "\n".join(
        f"- id: q{i}\n  question: question {i}\n  source_pointer: jsonl:turn-{i}"
        for i in range(1, n + 1))


def _md5_json3_same_ids():
    md_cq = _md_canary_block(5)
    json_cq = [{"id": f"q{i}", "question": f"J{i}", "source_pointer": f"jsonl:t{i}"}
               for i in range(1, 4)]
    md = (f"## current_goal\ng\n\n## canary_questions\n{md_cq}\n\n"
          "```json\n" + json.dumps({"current_goal": "g", "next_3_actions": ["a"],
                                    "canary_questions": json_cq}) + "\n```\n")
    return md


def test_list_determinism_json_present_wins_exactly_three():
    """C2: markdown-5 + json-3-same-ids -> EXACTLY the json 3 (json-list-wins),
    never a union of 8, never a blended 5."""
    m = _fresh_provider_tmp()
    d = m._parse_handoff_text(_md5_json3_same_ids())
    cq = d["canary_questions"]
    assert len(cq) == 3, f"json list present must win outright (3), never union; got {len(cq)}"
    assert all(q["question"].startswith("J") for q in cq), "the json list must be the one that won"


def test_list_determinism_json_omits_uses_markdown_five():
    """C2: markdown-5 + json-omits-canary -> EXACTLY the markdown 5 (fill-gap)."""
    m = _fresh_provider_tmp()
    md = (f"## current_goal\ng\n\n## canary_questions\n{_md_canary_block(5)}\n\n"
          "```json\n" + json.dumps({"current_goal": "g",
                                    "next_3_actions": ["a"]}) + "\n```\n")
    d = m._parse_handoff_text(md)
    assert len(d["canary_questions"]) == 5, "markdown fills the omitted list (5), never union"


def test_merge_json_empty_list_treated_as_omitted_markdown_fills():
    """FLAG-3 (gen28): key-PRESENT-but-EMPTY-list is treated as OMITTED -> markdown
    fills (an author who writes open_loops:[] + real markdown items must NOT
    silently lose them). Deterministic, not incidental."""
    m = _fresh_provider_tmp()
    md = ("## current_goal\ng\n\n## open_loops\n- real markdown loop\n\n"
          "```json\n" + json.dumps({"current_goal": "g", "next_3_actions": ["a"],
                                    "open_loops": []}) + "\n```\n")
    d = m._parse_handoff_text(md)
    assert d.get("open_loops") == ["real markdown loop"], \
        "an empty json list must be treated as omitted -> markdown fills (no silent loss)"


def test_merge_json_nonempty_list_wins_over_markdown():
    """FLAG-3 corollary: key-PRESENT-AND-NON-EMPTY -> json wins (deliberate
    authoritative override), markdown does NOT append."""
    m = _fresh_provider_tmp()
    md = ("## current_goal\ng\n\n## open_loops\n- markdown loop\n\n"
          "```json\n" + json.dumps({"current_goal": "g", "next_3_actions": ["a"],
                                    "open_loops": ["json loop"]}) + "\n```\n")
    d = m._parse_handoff_text(md)
    assert d["open_loops"] == ["json loop"], "non-empty json list wins outright"


# ===================================================== C1 file_hash CONTENT-ID
def test_freshness_noop_recommit_same_filehash_stays_stale():
    """C1 (LOCKED): a no-op/whitespace recommit -> NEW commit_sha but SAME
    file_hash. Freshness must compare file_hash (content-identity), so it stays
    STALE. Baseline shaOLD:HASH, current shaNEW:HASH (same content) -> NOT ready."""
    ag = _author_gate()
    d = _rich(sha="shaNEW", fhash="SAMEHASH")
    r = ag.handoff_ready(d, 100.0, 200.0, baseline_rev="shaOLD:SAMEHASH")
    assert r["ready"] is False, "same file_hash (no-op recommit) must stay stale"
    assert any("fresh" in x for x in r["reasons"])


def test_freshness_genuine_new_content_different_filehash_is_fresh():
    """C1 corollary: genuinely new content -> different file_hash -> FRESH
    (must-not-regress the real authoring path)."""
    ag = _author_gate()
    d = _rich(sha="shaNEW", fhash="NEWHASH")
    r = ag.handoff_ready(d, 100.0, 200.0, baseline_rev="shaOLD:OLDHASH")
    assert r["ready"] is True


def test_filehash_compare_sentinel_baseline_is_eligible():
    """FLAG-2 (gen28): the C1 no-handoff sentinel baseline (\\x00-prefixed, NO
    colon structure) must NOT be blind-split on ':'. A real authored rev against
    the sentinel baseline -> FRESH (eligible-on-first-author), never a garbage
    compare."""
    from scripts.lineage_daemon import beat as _beat
    ag = _author_gate()
    d = _rich(sha="shaNEW", fhash="NEWHASH")
    r = ag.handoff_ready(d, 100.0, 200.0, baseline_rev=_beat._BASELINE_NO_HANDOFF)
    assert r["ready"] is True, "authored rev vs the no-handoff sentinel is fresh (eligible)"


def test_filehash_compare_null_baseline_fails_closed():
    """FLAG-2 (gen28): an absent/None baseline stays fail-CLOSED (unchanged from
    A) — never mistaken for a rev to split."""
    ag = _author_gate()
    d = _rich(sha="shaNEW", fhash="NEWHASH")
    r = ag.handoff_ready(d, 100.0, 200.0, baseline_rev=None)
    assert r["ready"] is False
    assert any("fresh" in x for x in r["reasons"])


# ===================================================== Axis-3(b) prior-rev seed
def test_capture_soft_open_uses_prior_rev_so_proactive_author_is_fresh():
    """Axis-3(b): a seat that authored a rich handoff BEFORE its first soft beat
    must not deadlock (baseline==current). capture_soft_open_baseline uses the
    PRIOR committed rev (via a prior_rev_fn seam) so the current handoff is fresh.
    prior content-id differs from current -> the current handoff reads FRESH."""
    from scripts.lineage_daemon import beat as _beat
    hist = {}
    # current handoff file_hash = CURR; the prior committed version had PRIOR.
    _beat.capture_soft_open_baseline(
        hist, "seat",
        handoff_provider=lambda s, lr=None: (_rich(sha="shaC", fhash="CURR"), 100),
        prior_rev_fn=lambda s, lr=None: "shaP:PRIOR")
    ag = _author_gate()
    cur = _rich(sha="shaC", fhash="CURR")
    r = ag.handoff_ready(cur, 100.0, 200.0,
                         baseline_rev=hist["handoff_baseline"]["seat"])
    assert r["ready"] is True, "prior-rev baseline must make a proactive author fresh"


def test_capture_soft_open_prior_equals_current_noop_stays_stale():
    """Axis-3(b) + C1 guard: if the 'prior' content-id equals the current one (a
    no-op recommit was the only change), the seat stays STALE — a prior-rev seed
    must not unlock an unchanged handoff."""
    from scripts.lineage_daemon import beat as _beat
    hist = {}
    _beat.capture_soft_open_baseline(
        hist, "seat",
        handoff_provider=lambda s, lr=None: (_rich(sha="shaC", fhash="SAME"), 100),
        prior_rev_fn=lambda s, lr=None: "shaP:SAME")   # same content-id as current
    ag = _author_gate()
    cur = _rich(sha="shaC", fhash="SAME")
    r = ag.handoff_ready(cur, 100.0, 200.0,
                         baseline_rev=hist["handoff_baseline"]["seat"])
    assert r["ready"] is False, "prior content-id == current -> stale (no-op guard)"


def test_capture_soft_open_no_prior_uses_sentinel_eligible():
    """Axis-3(b): a first-ever handoff (no prior committed rev) -> the sentinel,
    which is ELIGIBLE (distinct from an absent/None baseline that fail-closes)."""
    from scripts.lineage_daemon import beat as _beat
    hist = {}
    _beat.capture_soft_open_baseline(
        hist, "seat",
        handoff_provider=lambda s, lr=None: (_rich(sha="shaC", fhash="CURR"), 100),
        prior_rev_fn=lambda s, lr=None: None)          # no prior committed version
    assert hist["handoff_baseline"]["seat"] == _beat._BASELINE_NO_HANDOFF


def test_wired_beat_prior_rev_fn_unlocks_proactive_author():
    """Axis-3(b) WIRED-PATH proof: rotation_beat threads prior_rev_fn into the C1
    fallback so a proactively-authored seat reaches SOFT_READY (baseline = the
    PRIOR rev, current handoff differs -> fresh), not the pre-A2 deadlock."""
    from scripts.lineage_daemon import beat as _beat
    rich = {"agent_id": "seat", "tier_class": "T2", "death": {},
            "ctx": {"status_bar_pct": 75}}
    reg = {"agents": {"seat": {"generation": 1, "system_prompt": "p",
                               "tier_class": "T2", "lineage_root": "seat"}}}
    current = _rich(sha="shaCURR", fhash="CURRHASH")
    hist = {}
    t = _beat.rotation_beat(
        rich, reg, canary="seat", now=1000, session_turns=50,
        author_inject=lambda a, txt: None, history=hist,
        handoff_provider=lambda s, lr=None: (current, 100),
        prior_rev_fn=lambda s, lr=None: "shaPRIOR:PRIORHASH")  # distinct prior
    assert t["status"] == _beat.SOFT_READY, (
        "wired prior_rev_fn must unlock a proactively-authored seat (fresh vs prior)")
    assert hist["handoff_baseline"]["seat"] == "shaPRIOR:PRIORHASH"


# ===================================================== prove-by-effect (gate #2)
def test_prove_pmacme_shaped_markdown_becomes_soft_ready_eligible():
    """gate clause #2 (the (B) subject): pm-acme's live handoff is markdown-only
    and INCOMPLETE (no canary/first_effect) so it can't fire. This proves A2's
    intent: once that seat COMPLETES its authoring in the real markdown shape
    (colon-multiline canary + first_effect + the other sections), a markdown-ONLY
    handoff (no json block) becomes require_richness-COMPLETE -> SOFT_READY-eligible
    (given freshness). WITHOUT any hollow handoff passing (see N1)."""
    m = _fresh_provider_tmp()
    ag = _author_gate()
    complete_md = (
        "## current_goal\nrun the quarterly refresh cutover\n\n"
        "## phase_state\n- phase 3 of 5\n- next_gate: the operator cutover card\n\n"
        "## next_3_actions\n1. wire the seam\n2. verify\n3. push\n\n"
        "## decisions\n- single-trunk — critical path\n- no kill — idempotent\n\n"
        "## open_loops\n- awaiting the operator cutover\n\n"
        "## hazards\n- client-facing refresh window\n\n"
        + _MD_CANARY.split("## canary_questions", 1)[1].join(
            ["## canary_questions", ""]))
    d = m._extract_markdown_sections(complete_md)
    d["handoff_commit_sha"], d["file_hash"] = "shaNEW", "NEWHASH"
    r = ag.handoff_ready(d, 100.0, 200.0, baseline_rev="shaOLD:OLDHASH")
    assert r["ready"] is True, (
        f"a COMPLETE markdown-only handoff must be SOFT_READY-eligible post-A2; "
        f"got {r['reasons']}")


# ===================================================== N1-N4 negative controls
def test_n1_hollow_markdown_stays_soft():
    """N1: a recognized but HOLLOW handoff (missing canary/first_effect) stays
    SOFT even when fresh — no richness bypass."""
    ag = _author_gate()
    hollow = {"current_goal": "x", "next_3_actions": ["a"],
              "handoff_commit_sha": "shaNEW", "file_hash": "NEWHASH"}
    r = ag.handoff_ready(hollow, 100.0, 200.0, baseline_rev="shaOLD:OLDHASH")
    assert r["ready"] is False
    assert any("canary" in x or "first_effect" in x for x in r["reasons"])


def test_n2_answer_key_in_handoff_is_rejected():
    """N2: a canary_question carrying an ANSWER KEY is rejected by the leak-lint
    (the no-answer-key invariant survives the new authoring shape)."""
    from scripts.lineage_daemon.handoff_schema import Handoff
    d = _rich(sha="s", fhash="h")
    d["canary_questions"] = [{"id": "q1", "question": "why", "source_pointer": "t1",
                              "answer": "LEAKED"}]
    errs = Handoff.from_dict(d).validate(require_richness=True)
    assert any("ANSWER KEY" in e or "answer key" in e.lower() for e in errs)


def test_n4_first_effect_in_next_actions_prose_does_not_satisfy():
    """N4 (the (A) anti-scrape guard, must survive Axis-1(b)): a first_effect
    embedded ONLY inside next_3_actions prose is NOT lifted to a top-level field,
    so richness still fails."""
    m = _fresh_provider_tmp()
    md = ("## current_goal\ng\n\n## next_3_actions\n"
          "1. do it — first_effect: {kind: message, target: gm}\n2. verify\n")
    d = m._extract_markdown_sections(md)
    assert not d.get("first_effect"), "prose-embedded first_effect must NOT become top-level"


# ---- helpers -----------------------------------------------------------------
def _rich(*, sha, fhash):
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


def _fresh_provider_tmp():
    # a provider module bound to a scratch ORCHESTRA_DIR (recognition helpers are
    # pure over their text arg; no git needed for the parser-level tests).
    import tempfile
    return _fresh_provider(tempfile.mkdtemp())
