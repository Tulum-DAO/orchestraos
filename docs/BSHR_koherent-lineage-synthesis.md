# BSHR Synthesis: What Wants to Emerge From the Space Between — Koherent V1/V2/V3, Toddito, and the 1987 System

- **Requested by:** operator, via gm → plan (msg_37d40a10_3330180, relayed to bshr as
  msg_b517acb5_8484246, 2026-09-29)
- **Authored by:** bshr, running the BSHR loop (brainstorm/search/hypothesize/refine).
  Two threads were already researched this session by other seats and are reused here,
  not re-derived: Koherent V1 + Toddito/Pulse (`docs/PLAN_toddito-engineering.md`) and
  the 1987 system (`docs/PLAN_toddito-1987-venture.md`). Two threads were new ground,
  researched via forked subagents for this document specifically: Koherent V3
  (`relationalOS`) and locating Koherent V2 (`koherent-organizations`, not previously
  found).
- **North star, the operator's own words, held throughout:** *"How might we foster
  connection and unlock the potential in human collaboration using technology?"*
- **What this is:** a synthesis — what pattern is visible only when all five artifacts
  are held in view at once, and what that pattern implies wants to get built next. **What
  this is not:** an engineering plan (that's `docs/PLAN_toddito-engineering.md`) or a
  venture/pitch document (that's `docs/PLAN_toddito-1987-venture.md`). Where this
  synthesis implies build work, it points at those documents rather than re-scoping.

---

## 0. The five threads, condensed — what's real vs. aspirational in each

| Thread | What's actually real & working | What's aspirational / stubbed / dead |
|---|---|---|
| **1987 THOR** (`mic-program-toddito`) | 8 lettered variants, 3 content-families, per-variant weight files, templated report generation — a real, shipped, paper-based product, 38 yrs old. A **separate module** (Client Relationship / `CPCSP.EXE`) diagnosing consultant↔client trust — distinct product surface from org diagnostics. | Industry-to-letter mapping unconfirmed (needs Todd). Pre-LLM: fixed template text, no narrative synthesis. |
| **V1** (`koherentai-main`) | Big Five scoring (`lib/chat/actions.tsx:740,756`), @-mention multi-person profile-context injection (`:291,306` + `:269`) — real, working code. | App shell is an unmodified Vercel chatbot template. No real product shape around the scoring/context code. No What/So-What/Now-What. |
| **V2** (`koherent-organizations`, GitHub, 2025-03-28→04-24) | The actual **recovered prompt text** for What/So-What/Now-What (`src/prompts/base/insight_system_prompt.md`, `unified_system_meta_prompt.md`) and Self/Other/Whole framing (`roast_prompt.md`, `serenade_prompt.md`) — full instruction-level craft, not just field names. Daily voice check-in UI (`CheckInModal.tsx`). | The actual insight-generation backend (`koherentACE.ts`, `utilityHelpers.ts`) is explicitly a **stub** ("replace with actual implementation when available") — the real pipeline, if it ever ran, lived outside this repo (Supabase edge function, never checked in). "Nugget" field: **zero hits**, code-search-confirmed, across the whole repo. |
| **V3** (`relationalos`, still branded "Koherent" internally) | Full voice pipeline (ElevenLabs, real and wired), Circles→Pulses→Sessions relational data model (`self/partner/family/work/friends/custom`), TIPI-driven prompt personalization (`pulseConfigurationService.ts:125-159`). A "Shared Mind" collective-intelligence concept was **built** (recovered from git history, `eac9b37`). | The "Shared Mind" route/service was **deleted** in a 6-day gutting sprint (Sept 16–21, 2025) and its live replacement, the Insights Feed, is **hardcoded mock data** (`// Mock limit for demo`). Legacy `hivemind_reflection` team-theme aggregation exists in schema but is explicitly disabled in code (`"REMOVED FROM THIS FLOW"`). What/So-What/Now-What survives only as **unused DB schema**. No test files exist despite a full Jest config. "Nugget": zero hits, repo-wide grep. |
| **Toddito/Pulse** (current, live, `getyourpulse.io`) | The **only** end-to-end production pipeline in the entire lineage: ElevenLabs → Claude scoring → Supabase → rendered report, ~135 PRs, real accounts/auth. Org-level Focus-of-Concern report (`{key, priority, why, practice}`, 838 lines) — real synthesis, not mocked. Group/multi-respondent aggregation exists and works. | No individual reflective-coaching layer (What/So-What/Now-What). No relational/dyadic "space between two named people" feature. No recurring cadence (single-session). F8 (THOR weight divergence) open. 6 S1 security findings open, incl. the OD6 biometric-voice-ToS gate, unresolved 3+ months. |

---

## 1. The pattern visible only across all five: an altitude ladder nobody has built end to end

Every one of the five artifacts sits at a different altitude of "who is being understood":

**Individual** (one person's traits/state) → **Relational** ("the space between" two named
people) → **Team/Group** (aggregated signal across several people who share a context) →
**Collective/Org** (the shape of a whole organization or living culture).

Laid against the five threads:

- **Individual:** Big Five (V1, live), TIPI (V3, live but no intake UI), MBTI/DISC (V3,
  dead schema only), What/So-What/Now-What (V2, real recovered prompt craft, stubbed
  backend; V3, dead schema only).
- **Relational:** V1's @-mention context-pulling (real code, never became a real
  multi-party feature). V2's Self/Other/Whole prompt instruction (real prompt craft,
  literally tells the model *not* to synthesize Other/Whole at the Self stage — implying
  a relational synthesis step was planned downstream and never built, per the missing
  `practice_output_experiment`/`practice_output_control` templates and the stubbed ACE
  service). The 1987 Client Relationship Module (a real, shipped, *separate* 1987 product
  diagnosing consultant↔client trust — proof this altitude was worth its own product
  surface even pre-digital).
- **Team/Group:** V3's `hivemind_reflection`/`team_pulse` theme-aggregation pipeline
  (real code, explicitly disabled: `webhookProcessingService.ts:396-398`). Toddito's
  multi-respondent aggregation (real, live, but statistical/org-level, not relational).
- **Collective/Org:** Toddito's Focus-of-Concern report (real, live, shipped). The 1987
  system's 8 org-diagnostic variants (real, shipped, 1987). V3's "Shared Mind" — the
  most explicit articulation anyone has written of this altitude, and the closest
  wording anyone has come to the operator's own north star (§2 below) — built, then
  deleted.

**No version has ever had more than two adjacent altitudes live at once.** V1 had
individual + a relational mechanism, no team/org. V2 had individual + a relational
*framing*, no working backend at any altitude. V3 had individual (partial) + relational
(circle typing) + a team/collective concept, but gutted the team/collective piece and
mocked its replacement. Toddito has group + org, live and real, but nothing below it —
no individual reflective layer, no relational "space between two people" layer. **The
ladder has been climbed once per rung, by different versions, never as one structure.**

## 2. The near-miss: V3 already wrote the north star, then deleted it

This is the single most concrete finding in this synthesis. Recovered from V3's git
history (commit `eac9b37`, deleted by the Sept 16–21 cleanup, not present in the current
working tree), the "Shared Mind" endpoint's own doc comment and system-prompt framing
read:

> "You are a collective intelligence agent — an AI embodiment of the circle's living
> culture, also known as the Shared Mind. You are designed to help members better
> understand their relationship to the circle, discover alignment, resolve tensions, and
> co-shape a flourishing future for both themselves and the collective. You do this by
> integrating and reflecting on dynamic, multi-source data."

Compare this, unprompted, to the operator's own stated north star for this exact BSHR
task: *"foster connection and unlock the potential in human collaboration."* This is not
a coincidence worth glossing over — **the operator (or a collaborator building under the
operator's direction) already wrote a working first draft of the north star as literal
system-prompt text, shipped a route for it, and then deleted it six days into the same
sprint that wrote it.** The backing service (`generateSharedMind()`) never did real
cross-participant synthesis — it fetched rows and bundled them, no LLM call, no
weighting — so what got deleted was aspirational scaffolding, not a working feature. But
the *articulation* is the most precise statement of intent found anywhere in the five
threads, more precise than anything in this synthesis's own north-star framing, and it
already existed before this task began.

**Open question this document cannot answer and flags rather than guesses at:** why was
it deleted? The git history shows *what* happened (built, then removed in a cleanup
sprint alongside a lot of other stubbing) but not *why* — technical difficulty, running
out of the 6-day window, a deliberate pivot away from the idea, or simple cleanup churn
that didn't distinguish "mock scaffolding to prune" from "the actual thesis" are all
consistent with the evidence. Worth asking the operator directly; this is not
reconstructable from the repo alone.

## 3. The repeating shape of failure: the synthesis step is what never survives contact — except twice

A second pattern, once you line up *which* layer of each pipeline is real vs. fake:

| Version | Data capture layer | Synthesis/insight layer |
|---|---|---|
| V1 | Real (chat, profile formatting) | Real but shallow — string formatting into a prompt, not a diagnostic |
| V2 | Real (voice check-in UI, prompt craft) | **Stub** — `koherentACE.ts` literally comments "replace with actual implementation when available" |
| V3 | Real (ElevenLabs voice pipeline, fully wired) | **Mocked** — hardcoded demo strings, `// Mock limit for demo` |
| Toddito | Real (voice pipeline, fully wired) | **Real and shipped** — Claude scoring, Focus-of-Concern report |
| 1987 THOR | Real (paper questionnaires) | **Real and shipped** — templated report generation, per-variant weights |

Across five attempts spanning 38 years, **capturing the raw signal (a conversation, a
questionnaire, a voice call) has never been the hard part** — every version solved it.
**Turning that signal into a trustworthy synthesis is the part that has failed to ship
three times out of five**, and the two times it *did* ship, it shipped at only one
altitude each (1987: org/team, template-based; Toddito: org/group, LLM-based). This
reframes the question "what's missing" in a specific, non-obvious way: **it is not that
the lineage needs a better or newer synthesis engine. Toddito already has the one
synthesis engine in this entire lineage that is real, live, and trusted in production.**
What's missing is that engine being pointed at the individual and relational altitudes
V2 and V1 already did the prompt-craft and mechanism work for, instead of those altitudes
being re-attempted as new, separate, from-scratch prototypes that predictably stub out
the same synthesis step a fourth and fifth time.

## 4. What wants to emerge

Holding all five in view, the pattern is not "build a new product" — it's **"the missing
middle altitudes of the ladder already have their prompt craft and mechanism written,
they've just never been wired to the one synthesis engine that actually works in
production."** Concretely, what the space between these five artifacts is pointing at:

1. **An individual reflective layer on top of Toddito's existing org diagnostic**,
   using V2's *actual recovered prompt language* (not a re-invention) for
   What/So-What/Now-What — the engineering plan already flags this as a real, separate-
   design-review-worthy gap (its §2); this synthesis adds that the raw material to build
   it from is not hypothetical, it is sitting in `koherent-organizations`, tested enough
   to have shipped a daily check-in UI once already.
2. **A relational "space between two named people" layer**, structurally validated three
   independent times (V1's @-mention mechanism, V2's Self/Other/Whole prompt
   instruction, the 1987 Client Relationship Module as a standalone product) but never
   built as a real feature any of those three times. This is the one altitude with the
   least existing code to reuse and the most existing *evidence that it's wanted* —
   worth naming as its own explicit gap, distinct from #1, and distinct from Toddito's
   existing multi-respondent aggregation (which is statistical/org-level, not "what's
   true about the relationship between person A and person B specifically").
3. **A team/collective synthesis that says what V3's "Shared Mind" text already said**,
   built this time on Toddito's real synthesis engine instead of a from-scratch mock —
   this is the direct, buildable version of the near-miss in §2, and the most literal
   answer to the operator's own north-star phrasing of anything in this document.
4. **Recurring cadence** — every version from V2 onward reached for this (V2's daily
   check-in, V3's very entity being named "Pulse" for a recurring interview inside a
   circle) and Todd's own stated gap list for Toddito names it directly ("recurring
   cadence... not built" per the venture plan §2 item 4). Worth noting, flagged as an
   observation and not asserted as intentional: **the current product's own name,
   "Pulse," is the same word V3's data model uses for its recurring-interview entity** —
   whether that's the operator consciously carrying the word forward or convergent
   naming, this document doesn't know and doesn't guess; worth asking directly.

**The synthesis, in one sentence:** every prior version proved one rung of a ladder from
individual → relational → team → collective understanding, and independently proved that
capturing signal is solved while synthesizing it reliably is not — except in the one
place (Toddito) that already has a working synthesis engine and stops at the org rung;
what wants to emerge is that same engine climbing the two rungs below it (individual,
relational) using prompt craft and mechanisms the lineage already wrote and lost, rather
than each future attempt re-proving that data capture works and re-stubbing the part
that doesn't.

## 5. What this does not decide, and where it connects to already-open decisions

This is a synthesis, not a scoped plan — it does not sequence, estimate, or commit to
building any of §4's four threads. It connects directly to decisions already on record
elsewhere, without re-deciding them:

- **§4.1 (individual layer) is exactly `docs/PLAN_toddito-engineering.md` §2's flagged
  gap** ("What/So-What/Now-What... real opportunity, deserves its own design review, not
  slotted into build-first scope"). This synthesis's contribution is the recovered V2
  source material that design review would work from — not a decision to build it now.
- **§4.2 (relational layer) has a real, unconfirmed connection to OD4** (Todd's
  Consulting Communicator idea) and to the 1987 Client Relationship Module — the
  engineering plan already flagged the Client-Relationship-Module↔Consulting-
  Communicator hypothesis as "worth flagging, not asserting"; this synthesis adds V1's
  @-mention mechanism and V2's Self/Other/Whole framing as two more independent data
  points for the same underlying pattern (a relational, not organizational, diagnostic
  surface), without resolving whether they're the same feature.
- **§4.3 (collective layer) does not require new invention** — the exact language
  already exists (§2's recovered "Shared Mind" text) and could be reviewed as-is for
  reuse or rewrite, a much smaller lift than writing new north-star copy from scratch.
- **All four threads in §4 are explicitly gated by the same OD6 concern already on
  record** (`docs/PLAN_toddito-engineering.md` §6, `docs/PLAN_toddito-1987-venture.md`
  §6): every one of these layers adds *more* personal/relational data to a pipeline that
  already has an unresolved biometric-voice-data privacy gate. This synthesis does not
  recommend building any of §4 before OD6 closes — the more altitudes get added, the more
  this gate matters, not less.

## 6. Open threads — flagged, not chased (out of this task's scope)

- **A sixth artifact, not one of the five asked for:** V3's `dev.log` records a prior
  local working directory `/Users/wael/Koherent/koherent-mvp-main` — a collaborator
  ("wael") and a project literally named "koherent-mvp," chronologically prior to
  `relationalos`. This may be V2, may be a distinct intermediate build, or may be V3's
  own earlier working name before the "relationalos"/"Spectacle" rewrite — not
  determined, not investigated further here since it's outside the five named threads.
  Worth a one-line question to the operator if the lineage's exact version count
  matters later.
- **"Nugget" (the sixth field of the reported six-field pattern) was not found in
  either V2 or V3's codebase** — zero hits, code-search-confirmed in V2, full-repo grep
  confirmed in V3. Combined with the operator's own account that the Notion pages
  preserve only the field structure with blank prompts, this raises a real possibility
  worth surfacing plainly: **"Nugget" may never have existed as implemented prompt
  text, only as a planned field name that was never filled in, in Notion or in code.**
  Not asserted as fact — the two non-`main` branches of `koherent-organizations`
  (`remove-oauth`, `sloppy-testing-implementation-attempt`) and any Supabase edge
  functions outside the checked-in code were not fully inspected.
- **Why V3's "Shared Mind" was deleted (§2)** — genuinely unknown from the repo alone,
  flagged above, worth asking directly rather than guessing at motive.

---

## GSTACK REVIEW REPORT

**Mode: SELECTIVE EXPANSION**, self-decided (non-interactive T2 seat), per
`prompts/plan.md`'s narrowest-scope-when-non-interactive convention — this document's
whole purpose is synthesis/ideation across research already gathered, so naming a
pattern is the point, but every claim beyond what was directly verified in the five
repos/docs is marked as inference or flagged as an open question (§6), not asserted as
settled, and no build sequencing or commitment is made (§5).

**Step 0 — Premise Challenge:** real. Without this document, the five research passes
done today (V1+Toddito in the engineering plan, 1987 in the venture plan, V2 and V3 new
in this pass) remain four/five disconnected fact-finding exercises — nobody had yet lined
them up against each other or against the operator's own stated north star. The specific
finding in §2 (V3 already wrote the north star and deleted it) could only be found by
holding all five in view at once; it does not exist in either companion document.

**Existing leverage:** substantial, reused directly — both companion documents'
findings (F8, the security backlog, the Application Suite gap, the What/So-What/Now-What
gap already flagged in the engineering plan) are cross-referenced, not re-derived. New
leverage from this pass: V2's actual recovered prompt text (previously believed lost)
and V3's deleted "Shared Mind" text (recovered from git history, not previously known to
exist by anyone consulting only the current working tree).

**Adapted "eng review" (for a synthesis document — what needs verification before this
promotes from hypothesis to plan):** every claim in §4 is currently *architecturally
plausible*, not implementation-verified — nobody has checked whether V2's prompt
templates actually produce good output when run, whether V1's @-mention mechanism
generalizes past two hardcoded template variables it was built for, or whether V3's
"Shared Mind" prompt text would need a real rewrite vs. light editing to run against
Claude instead of whatever model it originally targeted. None of that verification
happened in this pass — it would be the first concrete step of any design review that
picks up §4.

**Adapted "test review":** N/A — no code changes in this document. The self-verifying
mechanism for whether this synthesis holds up is qualitative: does a design review that
reads §4 alongside the recovered V1/V2/V3 source material find the reuse claims accurate
when it actually opens those files. That check is future work, not something this
document can do for itself.

**VERDICT: CLEARED as a synthesis document.** Not cleared to be read as a commitment to
build §4's four threads, a timeline, or an estimate — those require the design review
this document recommends (§5), and are explicitly gated behind OD6 regardless of
sequencing (§5's last point).

**UNRESOLVED DECISIONS:**
- Does the operator want to pursue any of §4's four threads at all, and if so, in what
  order — this document deliberately does not rank or sequence them, unlike the
  engineering plan's ranked §8.
- Why was V3's "Shared Mind" deleted (§2) — worth asking directly; the answer might
  change how much of that recovered text is safe to reuse vs. how much was abandoned for
  a real, substantive reason not visible in the code.
- Does the "Nugget" field (§6) need to be reconstructed from scratch, or was it truly
  never more than a planned-but-unfilled Notion field — worth a direct question rather
  than continuing to search code that may not contain the answer.
- Should the relational-layer hypothesis (§4.2 / OD4's Client-Relationship-Module
  connection) be raised with Todd in the same conversation as OD4, given this synthesis
  adds two more independent data points (V1, V2) to a pattern the engineering plan
  already flagged from one (1987 only)?
