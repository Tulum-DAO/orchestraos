# Handoff: build -> gm (direct-task mode: build ran review+qa+ship itself)
- **Lineage:** build (Gen 1)
- **Timestamp:** 2026-09-21T17:20:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (task target repo: /Users/flybyflow/duelo-de-dibujo)
- **Target repo branch:** `gm/solo-practice-mode`, pushed to `origin`, cut from up-to-date `main` (v0.2.1.0, the 28-letter round, already merged/deployed).
- **Last commit SHA (duelo-de-dibujo):** `aacd8bb` (version bump to v0.3.0.0 is the final commit; the actual feature/fix commits are `1557e60`..`abefa4c`, see PR for the full list).
- **PR:** https://github.com/flybyflow/duelo-de-dibujo/pull/5 — OPEN, not merged, no production touch (hard-stop respected).

## 1. Current Goal & Phase State
- **Goal:** gm task "Gamify duelo-de-dibujo Duolingo-style — 10x round 3," via plan's direct handoff (msg_a55609ff_85798482). Design doc + Eng Review Lock spec locked by plan; build implemented, reviewed, QA'd, and shipped it directly (not handed to separate Review/Test/Ship seats — this task's instructions were "Run /review, then /qa, then /ship").
- **Phase:** Complete. PR open, waiting on gm/operator approval per the standing hard-stop.

## 2. What shipped
Solo-practice mode for the 28 trace verticals only (draw/animal challenges untouched):
- `api/judge.js`: explicit `mode` field, a genuine 2-image solo judge pipeline (judgePrompt/callClaude/validVerdict variants), server-side trace-only allowlist, `validPriorScore()` for "most improved" grounding data.
- `public/index.html`: capture-screen mode toggle, `duelo_progress` primitive, a new 32-challenge mastery-grid screen, a minimal celebration-polish slice.
- `public/progress-logic.mjs` (new): the mastery-grid `best`/`isSoloMastered` logic, extracted into a real ES module so it has node-level regression coverage.
- `qa-progress.mjs` (new), `qa-judge.mjs`/`qa-challenges.mjs` (extended).
- Version bumped v0.2.1.0 → v0.3.0.0 (MINOR, operator-confirmed).

## 3. Open Loops & Active Callbacks
- [ ] **Operator decision needed, not resolved in this PR:** on this shared no-accounts device, the "you improved!" solo-mode compliment compares against the device's last same-mode attempt regardless of which child is currently playing — it could congratulate the wrong kid on an "improvement" that was actually a sibling's prior score. Asymmetric (only ever positive-direction, never guilt-inducing), so it does NOT cross the hard ethical line as locked, but it's a real personalization-accuracy question an adversarial review pass raised and I did not decide unilaterally. Needs an explicit operator call: leave as-is, or scope the compliment to same-session-only (never persisted/cross-session), or something else.
- [ ] **Known, recurring project pitfall (not new, still applies):** `ANTHROPIC_API_KEY` is a Vercel "sensitive" env var, Production-scope only, unreadable via `vercel env pull`/`env ls` from this machine. `qa-judge.mjs`'s 5 real-Claude-call cases (2 duel regression, 3 new solo-mode cases) cannot be run to a passing conclusion locally — verified this is the same pre-existing gap (verbatim Anthropic 401s), not a code defect, by testing every non-key-dependent code path directly. Same limitation, same operator-accepted precedent, across all 3 rounds shipped so far.
- [ ] Test stage (if a separate Test seat ever picks this up before merge): a real-kid playtest specifically for tone (mastery grid / "most improved" narration / the flat-or-lower suppression case) — not code-testable, flagged by the design doc's own Open Question #4. Also: RTL + Arabic TTS for the new solo-mode UI copy — spot-checked live in ar/fr/en during build, but not a full dedicated pass.
- [ ] Ship stage already done by build directly per this task's instructions (branch + PR only, no land-and-deploy) — if gm's normal pipeline expects a separate Ship seat to also touch this, note that build already completed that role for this round.
- [ ] Still not this round, still flagged and not addressed: PDR item #5 (cross-instance KV rate limiting) — unshipped since the original PDR, `api/judge.js`'s rate limiter is still in-memory/single-instance with its own `ponytail:` comment naming the upgrade path.

## 4. Decisions Made & Rationale
1. **Version bump MINOR (v0.3.0.0), operator-confirmed via AskUserQuestion** — this round adds a genuine new feature (mode + screen + API contract shape), not just content like the 28-letters round (which was rightly a PATCH).
2. **Design-doc mechanism adaptation, confirmed by an independent plan-completion audit as legitimate, not a gap:** the locked spec's literal wording for "most improved" regression-handling ("the grounding data omits the comparison entirely on a flat/lower result") describes a temporally impossible mechanism — the new score doesn't exist until the same Claude call that would need to omit-or-include based on it. Implemented instead as a prompt instruction ("mention only if strictly higher, otherwise say nothing") — same locked outcome (never surface a decline), different mechanism, matching how this codebase already enforces its Vibe-fairness rule (by instruction, not app-code post-processing).
3. **Approach C (celebration polish) shipped partially, not fully** — a CSS pop animation only, no sound asset, no distinct persona voice. Explicitly permitted by the design doc itself ("lowest risk, easiest to cut if time runs short"); noted as a real cut, not silently dropped.
4. **TODOS.md not re-asked this round** — operator declined creating one in the immediately-prior round for the same reasoning (small solo project); treated that decision as still valid rather than re-prompting the identical question.

## 5. Declared First Effect (for whoever reads this next)
If gm/operator approves the PR: merge is still gated behind the operator's own approval card per the standing hard-stop — build does not merge or deploy. If gm wants further changes: read PR #5's body in full first (it has the complete review/QA trail), especially the "one operator-facing question raised, not resolved" item — that needs an explicit answer before it's truly done, not just before merge.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** What specific bug did the adversarial review pass find that the 6-specialist review gauntlet did NOT catch, and why did it slip past the specialists (jsonl regarding the adversarial-review agent's findings on commit `33f7b4c`)?
2. **Q2:** Why was `public/progress-logic.mjs` created as a separate file instead of just fixing the bug inline in `public/index.html`'s existing script (jsonl regarding the ship coverage-audit agent's recommendation and the `8cbaa8e` commit)?
3. **Q3:** What does `bumpProgress()`'s loud-failure guard actually check, and what specific future code change would it protect against (jsonl regarding the final focused-review finding and commit `abefa4c`)?
4. **Q4:** Why does the "most improved" narration's regression-handling in `api/judge.js` NOT literally match the design doc's wording about "omitting the grounding data" on a flat/lower result (jsonl regarding the plan-completion audit's finding on this item)?
5. **Q5:** What is the one operator-facing question this PR raises that was deliberately left unresolved rather than decided unilaterally (jsonl regarding the adversarial review's finding #3 and this handoff's Open Loops section)?
