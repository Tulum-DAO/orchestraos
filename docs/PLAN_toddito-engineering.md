# Engineering Plan: Improving Toddito/Pulse Using Koherent's Patterns

- **Requested by:** operator, via gm (msg_67391fac_865948 + addendum
  msg_37d40a10_3330180, 2026-09-29)
- **Authored by:** plan (non-interactive), research dispatched to two
  forked subagents (Pulse/Koherent code-mining; 1987 DOS-binary variant
  analysis — the latter shared with the separate standalone business plan,
  `docs/PLAN_toddito-1987-venture.md`, per gm's explicit instruction to
  keep the two documents independent)
- **Scope:** a real, scoped engineering plan for what to build first in
  Pulse (repo: `/Users/flybyflow/conductor/repos/pulse`, live at
  `getyourpulse.io`), grounded in Koherent's patterns, Todd's actual asks,
  and Pulse's own current state — verified directly against the repo, not
  assumed from the relay.

## 0. Correction to the premise, checked before scoping anything

**Pulse is not a lightly-touched hackathon MVP.** `docs/STATUS.md`
(snapshot 2026-07-22, confirmed current via `git log` — ~135 merged PRs)
shows a mature, actively-developed product: a full voice-led diagnostic
pipeline (ElevenLabs → Claude scoring → Supabase → report), an invite-only
alpha with real accounts/auth/referrals (Track B, shipped), a full ops
console (ADR 003, shipped), voice-infra extraction already shipped, and —
most relevant to this plan — **a 1987-methodology fidelity pass already
done**: `docs/methodology/mis-glossary-distilled.md` and
`docs/methodology/pulse-vs-1987-fidelity-memo.md` (8 named findings,
F1–F8, most already shipped). Any plan that treats this as green-field
"mining Koherent to build the diagnostic engine" would be redundant with
real, already-shipped work. This plan is scoped against what's actually
still open, not what's already done.

## 1. Koherent V1 (`koherentai-main`) — what's real, corrected from the
operator's framing

The operator said "there's a lot of things there that are usable" — checked
directly, this is **overstated for the bulk of the repo, accurate for a
few specific functions.** The app shell is the **unmodified Vercel
"Next.js AI Chatbot" template** (its own README still reads "Next.js AI
Chatbot"; OpenAI + Vercel KV; "We Love Koherent ❤️" is the only edit to the
README itself). `instructions.json` (32KB, the file most worth reading per
the operator) is a single OpenAI Assistants-API config — `name: "Math
Tutor - gpt3"` (never renamed from a template default) — whose 32KB
`instructions` string is **pure vision/prompt text**: Koherent's pitch plus
a catalog of ~50 named psychology/facilitation frameworks (Big Five, MBTI,
DISC, Belbin, Hogan, FIRO-B, Johari Window, World Café, Open Space, Theory
U, NVC, Sociocracy, Collective Presencing). No schema, no scoring logic —
reference material, not code.

**Real, reusable, working code does exist, in `lib/chat/actions.tsx`
(777 lines):**
- `calculateBigFiveScores` / `calculateTraitScore` (`:740, :756`) — averages
  1–20 sub-trait answers into a 0–100 standardized score. Small, portable,
  generic scoring pattern.
- `formatProfileForPrompt` (`:702`) — builds a profile string (DISC/MBTI/
  Big Five type) injected into a system prompt.
- `getMentionedUsersData` / `formatAllUserData` (`:291, :306`) +
  `extractEmails` (`:269`) — **the actual "profile people, tag them,
  converse about the space between them" mechanism**: detects @-mentioned
  emails in a message, pulls each person's stored profile, and formats
  multi-person context into the prompt. This is the one piece with a
  concrete, scoped reuse case for Pulse (§5c below).
- `lib/chat/ai-instructions.ts` — a slightly newer copy of the same prompt,
  keyed `en:` — implies a localization pattern already existed in Koherent,
  relevant context for Todd's multi-language (French) ask, though not
  itself reusable code.

## 2. "What / So What / Now What" — not found in V1; a genuine, separate
design opportunity, not core scope here

Full-repo grep of `koherentai-main` (app/, components/, lib/,
`instructions.json`) for "so what" / "now what" / "nugget": **zero hits.**
Neither the prompt text nor any schema/field/component name preserves the
6-field structure (What / So What / Now What-Self / Now What-Other / Now
What-Whole / Nugget). Per the operator's own later addendum
(msg_37d40a10_3330180), this pattern belongs to **Koherent V2** (a
separate repo, not checked in this pass) — the daily "roast me / serenade
me" flashcard exercises, not V1's chat tool.

**Checked against Pulse's existing report structure for overlap — genuine
gap, not redundant.** Pulse already has `src/components/report/
focus-of-concern.ts` (838 lines): each row is `{key, priority, why,
practice}` — org-level diagnostic priority, rationale, and a concrete
example, transcribed from the actual 1987 MIS tables. This answers "what
needs attention in the organization and why," at the **organization**
level. What/So-What/Now-What (Self/Other/Whole/Nugget) is an **individual
reflective-coaching** structure — different altitude entirely. Adopting it
would be additive, not a rebuild of something that already exists under a
different name — but it is real new product scope (a personal-takeaway
layer on top of an org-diagnostic report), not a small addition, and
deserves its own design pass before being built. **Recommended: flag as a
real future feature, don't fold into this plan's "build first" scope.**

## 3. "Collective intelligence" — out of scope for this plan

Zero trace in Pulse (`src/`, `docs/` grepped) or in `koherentai-main`.
Confirmed V3-only, per the operator's addendum — the pattern lives in
`relationalOS` (not researched in this pass; that repo is explicitly
task 5's territory, the BSHR synthesis across all three Koherent versions
+ Toddito + the 1987 system, sequenced after this plan per gm's own
instruction).

## 4. Pulse's own 1987-fidelity work, already done — don't duplicate

`docs/methodology/pulse-vs-1987-fidelity-memo.md`'s 8 findings, checked
directly:

| # | Finding | Status |
|---|---------|--------|
| F1 | Nurture bar pole order inverted | 🔴 fixed, shipped |
| F2 | Culture count (11 Full vs 14 Lite) | ✅ shipped (14 everywhere) |
| F3 | SCORING.md doc drift | ✅ fixed (docs-only) |
| F4 | Strategy dims (4 linguistic vs 5 bipolar) | ✅ shipped |
| F5 | Traditional culture excluded | 🟢 by design, matches legacy |
| F6 | Report-substance gaps (Growth Engine/Crisis copy, discouraged cultures, Focus of Concern) | mostly shipped; two-tier risk + numeric-vs-qualitative display still open, untracked as a numbered decision |
| F7 | Probe audit (Lite has 0 leadership probes vs 5 scored dims) | Lite v2 (P03→P22) shipped; her manual ElevenLabs prompt update still pending |
| F8 | **THOR weight divergence** | **🔴 OPEN — see §5** |

## 5. The highest-value concrete item: F8, and its real connection to
today's 1987-binary research

**F8, stated precisely:** the ported THOR weights (originally pulled from
a third-party `Mana-matrix/THOR` GitHub repo) do **not** reproduce two real
legacy verdicts — case A: legacy scored MODULATION, the current engine
ranks STALL first; case B: legacy scored ACCELERATION→PROJECTILE, the
current engine ranks AUTO_PILOT first. Encoded as `it.fails` documented-
divergence tests in `src/lib/scoring/__fixtures__/thor-score.test.ts`.
`docs/STATUS.md`'s own words: **"THOR panel = directional until weights
re-derived."** This is a real correctness gap in a shipping product —
every report's THOR panel is currently honest about being directional
(per F8's own mitigation), but it is not yet accurate.

**The memo's own lead #2** (checked directly): the legacy program ships
**per-industry weight files** named `GP{letter}SCORE.ASF`, and the two
ground-truth test cases came from *different* industry variants — meaning
the current matrix is very likely one industry's weights applied
universally, not the legacy system's actual per-variant behavior.

**This is where the two forked research passes connect, not
coincidentally:** the dedicated 1987-binary analysis (run in parallel for
the standalone business plan, `docs/PLAN_toddito-1987-venture.md`) found
these exact files, confirmed present, in `/Users/flybyflow/conductor/repos/
mic-program-toddito`:
- 8 real lettered variants (A, B, C, D, M, R, S, T), each a `GP{letter}
  QUEST/RISK/SCORE/LIB.ASF` set — `GPxSCORE.ASF` is real, not
  hypothesized.
- Confirmed, evidence-based structural finding: the 8 variants group into
  **3 underlying content-families** by shared questionnaire/report text —
  {A, M}, {B, S}, {C, D, R, T} — meaning the RISK/SCORE weight matrices
  likely differ *within* a family too, or the families themselves may be
  the real "industry" boundary. Not yet determined which; see the venture
  plan for the full evidentiary trail.
- **Not yet determined:** which letter/family maps to which named
  industry. The one real industry-selection menu found in the binaries
  ("Manufacturing & Service" / "Retail Only" / "Retail & Restaurant Only")
  belongs to a *different* module (`CPCSP.EXE`, "THE CLIENT RELATIONSHIP
  MODULE") and is not confirmed to correspond 1:1 to the A–T letters.
  Todd is the real source for this mapping — recommended to ask him
  directly (per the research pass's own conclusion) rather than guess.

**Real, scoped engineering task (recommended #1 priority, contingent on
schedule — see §7 ranking):**
1. Reverse-engineer the `.ASF` binary format on the smallest file first
   (likely `GPxLIB.ASF` or `GPxRISK.ASF` — not yet inspected at the byte-
   layout level in either research pass; this is genuinely new work, not
   yet scoped to file-format spec depth).
2. Extract the weight matrix per lettered variant / content-family.
3. Re-run `thor-score.test.ts`'s two ground-truth cases against the
   variant-specific weights (not the universal matrix) and check whether
   divergence closes.
4. If it does: this becomes a real per-industry (or per-family) weight
   selection in the scoring pipeline, not a single hardcoded matrix — a
   genuinely new capability, not just a bugfix.
5. **Gate, not guessed around:** confirming which of Pulse's respondents/
   organizations should map to which legacy variant needs the same
   Todd-sourced industry mapping named above — this task can start on the
   binary-format reverse-engineering in parallel, but cannot fully close
   (verified fix, not just closer weights) without that mapping.

## 6. Security backlog — flagged prominently, not buried, because this is
where real risk lives

`docs/SECURITY.md` (snapshot 2026-07-15) lists **6 S1 findings, verified
still ALL open** (checked `git log` since 2026-07-15 for any SEC-0x /
prompt-injection / rate-limit / webhook-replay / fail-closed commit —
zero matches): transcript prompt-injection into the scoring model
(highest priority — `src/lib/scoring/prompt.ts` is on the repo's own STOP
list), an unauthenticated score endpoint (cost/DoS exposure), a public
config route leaking client/org PII and minting billable ElevenLabs signed
URLs, webhook replay (no timestamp-freshness check), silent unsigned-
webhook fallback, and no rate limiting anywhere in `src/`.

**Directly relevant to this plan's priority call:** the security doc's own
"Full-pass checklist" explicitly still lists **"Privacy/terms (Instinct AI
LLC)"** as owed before public launch. This confirms, independently, that
**OD6 from the 2026-06-17 CEO review — the hard gate, one-way-door
decision on biometric voice-data ToS/privacy — is still unresolved more
than three months later.** Pulse captures and behaviorally analyzes voice
(biometric data); Todd is in the data chain; the June review was explicit
that this must not be bypassed by momentum. This plan does not recommend
any feature work that assumes or moves toward wider distribution until
this is resolved — consistent with, not a new restriction beyond, what was
already decided in June.

## 7. Application Suite — Consulting Communicator, blocked not scoped

Todd's own most-differentiated idea ("I've never seen anything like that
ever" — the 16-question consultant self-diagnostic) remains blocked on
OD4: Mo needs the full application doc from Todd, not yet received per any
evidence checked in this pass. **One new, unconfirmed candidate
connection, worth flagging not asserting:** the 1987 binaries' separate
"Client Relationship Module" (`CPCSP.EXE` / `GPDDMS.AWS`, confirmed by its
own marketing text — "a diagnosis of the extent to which you have managed
to build trust and credibility with key decision makers in the client
organization") sounds structurally adjacent to the Consulting Communicator
concept. Not verified as the same thing — surfaced for Todd to confirm or
rule out when the OD4 conversation happens, not treated as settled.

## 8. Recommended scope — what to build first, ranked

1. **Resolve OD6 (privacy/terms for biometric data) + close the 6 S1
   security findings.** Not exciting, explicitly flagged as a hard gate
   3+ months ago, still open — the highest-consequence, lowest-visibility
   risk in this whole plan. Blocks nothing else on this list technically,
   but should not keep losing priority to feature work.
2. **F8 THOR weight reverse-engineering (§5).** Real, concrete, directly
   connects today's Koherent-mining research to the 1987-system research —
   the rare case where "mining the old system" produces a specific,
   testable engineering deliverable rather than just inspiration. Start
   the `.ASF` format reverse-engineering now (doesn't need Todd); the
   industry-mapping question (needs Todd) can run in parallel, not
   sequentially blocking.
3. **Small, scoped Koherent V1 reuse: the multi-person @-mention context
   pattern (§1).** Worth a real look for Pulse's existing group/consultant
   multi-respondent reports — check whether `getMentionedUsersData`'s
   pattern (detect a mention, pull stored context, format into a prompt)
   maps cleanly onto Pulse's `engagement`/`session` model. Small, bounded,
   not a rewrite.
4. **What/So-What/Now-What (§2) — recommend a dedicated design review
   before building, not slotted into this plan's build scope.** Real
   opportunity, different altitude from what exists, deserves its own
   `/plan-design-review` pass the way the community-brain rebuild got one,
   not squeezed in here as an afterthought.
5. **Consulting Communicator (§7) — blocked on Todd (OD4).** Do not scope
   blind; flag as the next thing to unblock once his call happens.

## GSTACK REVIEW REPORT

**Mode: HOLD SCOPE.** This is improvement work on an existing, mature,
actively-developed product with real open findings (F8) and real open
risk (the security backlog) already on record — the job here is
correctly prioritizing and connecting what's already known, not inventing
new ambition. Scope-expansion framing would be the wrong instinct against
a backlog this concrete.

**Step 0 — Premise Challenge:** real. The operator's actual ask ("start
improving Toddito now, not just keep researching") is legitimate — but
"improve" concretely means closing F8 and the security backlog before it
means porting new Koherent patterns, since those are the two items with
the clearest do-nothing cost (a THOR panel that's honest about being
"directional" is still wrong; unresolved S1 findings are a real, standing
exposure, not a hypothetical one).

**Existing leverage:** substantial — `thor-score.test.ts`'s ground-truth
harness already exists and is exactly the verification mechanism §5's
task needs; `getMentionedUsersData`'s pattern is small and portable, not
a new subsystem.

**ENG REVIEW — Scope Challenge:** the one real technical unknown in this
plan is the `.ASF` binary format itself — neither research pass inspected
it at the byte level, so §5's step 1 (format reverse-engineering) has a
real, not-yet-bounded effort estimate. Recommend build's first concrete
step be a short timeboxed spike (e.g. half a day) on one `.ASF` file to
establish whether the format is quickly tractable (a simple fixed-width
or delimited record format) or genuinely obscure (proprietary binary
encoding needing more serious reverse-engineering) — and report back
before committing to the rest of §5's sequence.

**Test Review:** §5's fix is self-verifying by construction —
`thor-score.test.ts`'s two `it.fails` cases are the pass/fail criteria
already in the repo; no new test framework needed, just re-running an
existing one against corrected weights.

**VERDICT: CLEARED for the ranked scope in §8**, items 1–3 buildable now
(1 and 3 need no new research; 2's format-reverse-engineering spike can
start immediately, its industry-mapping half needs Todd). Items 4–5
explicitly deferred pending, respectively, a dedicated design review and
Todd's OD4 response — not silently folded into "build first" scope.

**UNRESOLVED DECISIONS:**
- Is the operator/Mo comfortable with security-backlog closure (§6, item 1
  in §8) taking priority over new feature work, given it was already
  flagged as a hard gate 3+ months ago and remains open?
- `.ASF` binary format: tractable or not — needs the timeboxed spike
  before §5's full sequence can be estimated honestly.
- Which 1987 letter/family maps to which industry — needs Todd directly;
  do not guess in the meantime (per the research pass's own conclusion).
- Does build agree with deferring What/So-What/Now-What to its own design
  review rather than building it now, given the operator's "start
  improving now" framing might read as wanting it included?
