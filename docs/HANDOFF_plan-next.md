# Handoff: plan -> build
- **Lineage:** plan (Gen 1)
- **Timestamp:** 2026-09-20T19:25:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (task target repo: /Users/flybyflow/duelo-de-dibujo)
- **Last Commit SHA (orchestraos):** 9d16e94
- **Target repo HEAD (duelo-de-dibujo):** fa6d417, branch `gm/v2-tally-replay-pwa-kv` (stale — Build cuts a fresh branch off `main` per think's original instruction)

## 1. Current Goal & Phase State
- **Goal:** gm task "10x duelo-de-dibujo — scope-expansion round." CEO review + eng review both complete; scope and technical spec are locked. Handing off to Build.
- **Plan Reference:** design doc at `/Users/flybyflow/duelo-de-dibujo/DOCS/designs/skill-duel-engine-v3.md` — now contains the original think-stage design PLUS an "## Eng Review Lock" section (appended this session) with the final locked spec, folded outside-voice fixes, and required verification steps. **Currently uncommitted** — Build commits it on whatever fresh branch it cuts.
- **Phase:** Plan stage complete. Handing off to Build.
- **Current Step:** None (Plan's work is done)

## 2. Open Loops & Active Callbacks
- [ ] Build must source real content for the new vertical before it can ship: pick a specific Arabic letter (suggested: ا/alif — single stroke, simplest), source or record a stroke-order video, generate a reference glyph image. No engineering step produces this — it's a real blocker, not solved by this review.
- [ ] Build must update `qa-judge.mjs` to send `challengeId:"frog"` in its request body — under the new required-lookup design, the harness as it exists today would `400` (an outside-voice-caught contradiction between the locked spec and the existing QA harness).
- [ ] Build must run one real `vercel dev` (or deployed) smoke test of `/api/judge` with a `challengeId` before calling this shipped — `readFileSync("...challenges.json...")` inside a Vercel serverless function (zero-config project, no `vercel.json`) is a real deploy-time bundling risk that the plain-Node `qa-*.mjs` harnesses cannot catch (they call the handler directly, bypassing Vercel's build).
- [ ] Test stage: real-kid playtest of the reframed `rubric.vibe` wording for letter-tracing (Open Question #4) — does it land, or degenerate into "whoever's handwriting looks nicer"? Not code-testable.
- [ ] Test stage: RTL layout + Arabic TTS pronunciation of any new vocabulary, in all three languages.
- [ ] Ship stage: branch + PR only, per the operator's explicit hard-stop — no land-and-deploy, no merge, no production touch. That stays gated behind gm's own operator approval card.

## 3. Decisions Made & Rationale
1. **Decision (CEO review, logged `3ce5f817`):** Approach A only this round (config-driven CHALLENGES engine + Arabic letter-tracing vertical). Approach B (Duelo Arena — multi-device rooms + persistent leaderboard) deferred to a future round. — **Rationale:** confirmed directly with the operator via AskUserQuestion; the design doc's own cross-model cold-read argued chasing rooms/scale means competing with funded teams (Doodle Duel/Artbitrator/Sketchmate already ship that) instead of deepening Duelo's actual moat.
2. **Decision (eng review, logged `b7f9870a`):** CHALLENGES schema is a single shared `public/challenges.json` (not two hand-synced lists) — client fetches it for the picker, server `readFileSync`s it for the `challengeId -> rubric` lookup. Vibe crown gets per-challenge reframing via `rubric.vibe`. Server wraps the file load in try/catch with a 4-entry hardcoded fallback so a malformed JSON degrades to "new vertical down," not "all judging down." — **Rationale:** confirmed with the operator over 3 AskUserQuestion decisions; DRY (one source of truth, not two files to hand-sync) and blast-radius containment (a bad deploy shouldn't take down the 4 working animals).
3. **Decision (eng review, cross-model tension, operator-confirmed):** kept the shared-schema approach after an outside-voice pass (fresh Claude subagent — Codex CLI errored, model unsupported on this account) argued for a narrower hardcoded-array-only cut. — **Rationale:** the design doc's whole thesis this round is proving the engine generalizes past drawing; a second ad-hoc hardcoded array doesn't prove that. The outside voice's other 9 findings (see full review in the design doc's "Eng Review Lock" section) applied identically to both approaches and are folded into the spec as requirements, not re-litigated as a schema question.
4. **Decision (eng review, direct — no real alternative, not asked):** `JUDGE_PROMPT`'s framing text (not just the two rubric bullets) needed a wording fix — "Both players **drew** the reference from memory" is false for a traced letter. Reworded to "reproduced," same pattern applied to 5 UI strings in `index.html` across all 3 languages (outside voice caught this was broader than just the `s-learn` screen — Arabic and French `learnSub` are drawing-specific too, not just English).

## 4. Declared First Effect
Build reads `/Users/flybyflow/duelo-de-dibujo/DOCS/designs/skill-duel-engine-v3.md` in full — specifically the "## Eng Review Lock" section at the end — then cuts a fresh branch off `main` (not the stale `gm/v2-tally-replay-pwa-kv`) and commits the design doc's repo copy as its first commit.

## 5. Next 3 Immediate Actions
1. Build seat: cut fresh branch off `main`, commit `DOCS/designs/skill-duel-engine-v3.md` (currently uncommitted).
2. Build seat: source the letter-tracing content (letter pick, stroke-order video, reference glyph) — real blocker, do first, not last.
3. Build seat: implement per the "Eng Review Lock" section's exact spec (schema shape, security boundary, load-failure fallback, prompt/UI wording fixes, `qa-judge.mjs` update, Vercel smoke test) — then hand off to Test per its own contract.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** What did the operator choose when asked whether to pull Approach B into this round vs. prove Approach A first (jsonl regarding the first AskUserQuestion this session, "Pull Approach B forward now")?
2. **Q2:** What specific HIGH-severity contradiction did the outside-voice review find between the locked `challengeId`-required design and `qa-judge.mjs`'s existing request body (jsonl regarding the Agent tool result, "Outside-voice eng review of Duelo plan")?
3. **Q3:** Why was `new URL("../public/challenges.json", import.meta.url)` chosen over a `process.cwd()`-relative path for the server-side file read (jsonl regarding the Eng Review Lock section written to the design doc)?
4. **Q4:** What does the eng review's chosen fallback behavior do when `public/challenges.json` is malformed or missing at server cold start, and why was "let it crash" rejected (jsonl regarding the second AskUserQuestion in the eng review)?
5. **Q5:** Which specific `T{}` string keys in `index.html` were identified as drawing-specific in all three languages, not just English (jsonl regarding the "Additional UI copy fix" section of the Eng Review Lock)?
