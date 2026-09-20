# Handoff: build -> review
- **Lineage:** build (Gen 1)
- **Timestamp:** 2026-09-20T20:10:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (task target repo: /Users/flybyflow/duelo-de-dibujo)
- **Target repo branch:** `build/arabic-letter-tracing-vertical`, pushed to `origin` (cut from up-to-date `main` @ `1784d29`, NOT the stale local `gm/v2-tally-replay-pwa-kv` — that branch's local ref was pre-merge; `origin/main` already has its content squash-merged as `1784d29`, verified byte-identical on every file this round touches before branching).
- **Last commit SHA (duelo-de-dibujo):** `86fca3e`
- **Plan file:** `/Users/flybyflow/duelo-de-dibujo/DOCS/designs/skill-duel-engine-v3.md` (`## Eng Review Lock` section is the binding spec), committed this round at `a0bf30b`.

## 1. Current Goal & Phase State
- **Goal:** ship Approach A (config-driven CHALLENGES engine + Arabic letter-tracing vertical) per the locked eng review spec, verify it, hand off to Review.
- **Phase:** Build complete. Every requirement in the Eng Review Lock section is DONE (see mapping below). Handing off to Review.
- **Current Step:** None — Build's work is done.

## 2. Requirement -> Commit Mapping
All in commit `86fca3e` (design doc itself is `a0bf30b`):
- CHALLENGES schema (`public/challenges.json`, single source of truth, `type: draw|trace`, per-entry `rubric`) — DONE.
- Client fetches `/challenges.json`, replaces hardcoded `ANIMALS`; two-tab picker groups by `type`; fetch failure shows inline error + retry (not a blank picker) — DONE.
- Server (`api/judge.js`) loads the same file via `readFileSync(new URL("../public/challenges.json", import.meta.url))`, builds `id -> rubric` Map — DONE.
- Security boundary: client sends only `challengeId`, never rubric text; server resolves server-side; unknown id -> 400; sits after existing origin check + rate limiter, doesn't weaken either — DONE, verified (see §4).
- Load-failure fallback: try/catch at module init, hardcoded 4-animal fallback, bad deploy degrades to "new vertical down" not "all judging down" — DONE.
- `JUDGE_PROMPT` reworded verb-neutral (judge.js:37,43-ish: "a friendly duel", "reproduced the reference") — DONE.
- Vibe reframed independently for `alif` (confidence/flow of the stroke), fairness mechanic ("usually give it to the other player") preserved — DONE.
- UI copy fix: `T.tagline`, `T.learnTitle`, `T.learnSub`, `T.done`, `T.capSub` reworded verb-neutral in ar/fr/en (all three, not just English) — DONE.
- Content blocker resolved: letter = alif (ا); reference glyph generated (`public/img/alif.png`, Pillow + macOS SFArabic font, large render + numbered stroke-order arrow); stroke-order video sourced (YouTube `fKwOMa3r1_c`, "Learn With Zakaria - English", oEmbed-verified public/embeddable) — DONE.
- `qa-judge.mjs` updated: existing frog/cat case now sends `challengeId`; new alif/cat case exercises the trace vertical; new unknown-`challengeId` case proves the 400 guard — DONE, all 3 written; **only the unknown-id case actually ran green in this environment** (see Open Loops).
- `qa-ratelimit.mjs`: unchanged, ran, still PASS (rate limit fires before `challengeId` is even read) — VERIFIED.
- Vercel-runtime bundling smoke test (the item this design doc flagged as impossible to catch via plain-Node harnesses) — DONE, verified via a real preview deploy, not `vercel dev` (see §4).
- Rename `.key` -> `.id` complete-the-rename audit (index.html:334's error string, etc.) — DONE, grepped clean.

## 3. Open Loops & Active Callbacks
- [ ] **qa-judge.mjs's two real-Claude-call cases (frog regression + alif trace) could not be run to green in this environment.** `ANTHROPIC_API_KEY` is a Vercel "sensitive" env var scoped to Production only; it is NOT retrievable via `vercel env pull` or `env ls` from this machine (redacted/empty). This is a pre-existing operational gap (same would've blocked a fresh clone before this round), not something this change introduced. I verified the actual code path a different way — see §4 — but Review/Test should run these two cases for real with the operator's own key or from a context that has it, before Ship.
- [ ] **Real-kid playtest of the reframed `rubric.vibe` wording for letter-tracing** (design doc's Open Question #4) — explicitly a Test-stage item, not code-testable.
- [ ] **RTL layout + Arabic TTS pronunciation check** for the new vocabulary (ألف / Alif), in all three languages — explicitly a Test-stage item.
- [ ] **Video content accuracy** — I verified `fKwOMa3r1_c` is public and oEmbed-embeddable and matches "Arabic alphabet, letter Alif, for kids" by title/channel, but I have not watched it (no browser session in this run). Test stage should actually play it before Ship.
- [ ] `T.nextPick` ("Winner picks the next **animal**!") is now slightly wrong for a letter-tracing win — the locked spec's UI-copy-fix list named exactly 5 keys (tagline/learnTitle/learnSub/done/capSub) and I kept to that list rather than expanding scope; flagging as a real but out-of-spec residual, not silently fixed.
- [ ] Ship stage: branch + PR only, per the operator's hard-stop — no land-and-deploy, no merge. A **preview** deployment already exists from my own verification pass: `https://duelo-de-dibujo-5l6fnvbq2-mos-projects-a67736c7.vercel.app` (not promoted to production).

## 4. Decisions Made & Rationale
1. **Branched off `origin/main`, not local `main`.** Local `main` ref was stale (missing the v2 tally/replay/PWA squash-merge, `1784d29`). Fetched first, diffed `fa6d417` (tip of the old `gm/v2-tally-replay-pwa-kv`) against `origin/main` on every file this round touches — byte-identical — then branched from the updated `main`. Branching from the stale local `main` would have silently dropped all v2 functionality.
2. **Verified Vercel-runtime file bundling via a real preview deploy, not `vercel dev`.** `vercel dev` refuses to start on this machine (`DEV_RECURSIVE_INVOCATION` — the project's own `package.json` `dev` script is literally `vercel dev`, which the installed CLI version now flags). The design doc explicitly allows "vercel dev (or deployed)" as alternatives, so I ran `vercel deploy` (default = preview, not production) instead. Confirmed `challengeId=alif` returns `{"error":"judge unavailable","detail":"missing ANTHROPIC_API_KEY"}` rather than `{"error":"unknown challengeId: alif"}` — since the hardcoded fallback map has no `alif` entry, this proves `readFileSync` of `challenges.json` succeeded under Vercel's real bundler/tracer, without needing a working Anthropic key. Also confirmed: `/challenges.json` serves all 5 ids, `origin` check still rejects a forged origin, missing-`challengeId` still 400s.
3. **Content picks (not architecture, but real choices, flagging per Build's own contract):** letter = alif (ا) per the design doc's own suggestion (simplest, single stroke). Reference glyph generated locally with Pillow (no commissioned art, matches spec) rather than sourced. Video picked from a real web search + oEmbed check for "public and embeddable", not personally watched — see Open Loops.

## 5. Declared First Effect (for Review)
Review reads `DOCS/designs/skill-duel-engine-v3.md`'s `## Eng Review Lock` section, then diffs `86fca3e` against it requirement-by-requirement (the mapping in §2 above is a starting index, not a replacement for Review's own pass), and separately validates the two open verification loops in §3 before clearing for Ship.

## 6. Next 3 Immediate Actions
1. Review seat: review `86fca3e` (+ `a0bf30b` for context) on `build/arabic-letter-tracing-vertical` against the Eng Review Lock spec.
2. Review/Test: run `qa-judge.mjs`'s frog and alif cases for real (needs a working `ANTHROPIC_API_KEY` — Production-scoped, ask the operator or run from a context that has it).
3. Test stage: real-kid playtest (Vibe wording) + RTL/TTS check + actually watch the sourced video, per §3.

## 7. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** Why does `readFileSync` inside `api/judge.js` use `new URL("../public/challenges.json", import.meta.url)` instead of a `process.cwd()`-relative path (jsonl regarding the Eng Review Lock section of the design doc)?
2. **Q2:** What HTTP status and error string does an unknown `challengeId` produce, and at what point in the handler (relative to the origin check and rate limiter) does that check run (jsonl regarding `api/judge.js`'s handler)?
3. **Q3:** Why was a Vercel **preview deploy** used instead of `vercel dev` to verify the bundling behavior, and what specific error made `vercel dev` unusable on this machine (jsonl regarding this handoff's §4, decision 2)?
4. **Q4:** Why couldn't the real-Claude-call cases in `qa-judge.mjs` be run to completion in this environment, and what's needed to run them (jsonl regarding this handoff's §3, first open loop)?
5. **Q5:** Which specific `T{}` key was identified as still drawing/animal-specific but deliberately left unfixed because it fell outside the locked spec's named list (jsonl regarding this handoff's §3)?
