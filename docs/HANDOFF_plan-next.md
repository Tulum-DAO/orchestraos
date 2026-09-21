# Handoff: plan -> build
- **Lineage:** plan (Gen 1)
- **Timestamp:** 2026-09-21T11:10:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (task target repo: /Users/flybyflow/duelo-de-dibujo)
- **Last Commit SHA (orchestraos):** 0a14ec4
- **Target repo HEAD (duelo-de-dibujo):** clean `main`, v3 (config-driven CHALLENGES + 28 trace letters) already merged and deployed.

## 1. Current Goal & Phase State
- **Goal:** gm task "Gamify duelo-de-dibujo Duolingo-style — 10x round 3." CEO review + eng review both complete; scope, ethical line, and technical spec are locked. Handing off to Build.
- **Plan Reference:** design doc at `/Users/flybyflow/duelo-de-dibujo/DOCS/designs/gamification-round3.md` — now contains the original think-stage design PLUS an "## Eng Review Lock" section (appended this session) with the final locked spec, folded outside-voice fixes, and required verification steps. **Currently uncommitted** — Build commits it on the fresh branch it cuts off clean `main`.
- **Phase:** Plan stage complete. Handing off to Build.
- **Current Step:** None (Plan's work is done)

## 2. Open Loops & Active Callbacks
- [ ] Build implements in this order (per the Eng Review Lock + design doc's own Next Steps): (a) server-side solo path — `judge.js:224`'s validation gate, explicit `mode` field, `judgePrompt`/`callClaude`/`validVerdict` solo branches, server-side trace-only type allowlist; (b) capture-screen "Practice solo" entry point + `state.mode` UI fork; (c) client blast-radius fork — `judge()`/`recordDuel()`→`recordSolo()`, distinct solo-verdict renderer, no `shareCard()` for solo; (d) `duelo_progress` primitive with the corrected per-mode sub-record schema; (e) "most improved" narration with the locked never-surface-a-decline rule; (f) 28-challenge mastery grid as its own screen; (g) Approach C's celebration polish last (lowest risk, cuttable if time runs short).
- [ ] Verify the existing 2-player duel path is byte-for-byte unaffected at every step — this round's diff sits on top of a shipped, deployed v3, not a fresh feature.
- [ ] Test stage: real-kid playtest specifically probing tone (mastery grid / "most improved" / the flat-or-lower-score suppression case) — not just a functional pass, per Premise 5.
- [ ] Test stage: RTL + Arabic TTS for new solo-mode UI copy, all three languages.
- [ ] Known project pitfall (not new this round, still applies): `ANTHROPIC_API_KEY` is a write-only Vercel Sensitive var — verify the solo real-Claude-call path with a live curl against Preview/Production; a local QA harness run alone cannot prove it end to end.
- [ ] Ship stage: branch + PR only, per the operator's standing hard-stop — no land-and-deploy, no merge, no production touch. Gated behind gm's own operator approval card.
- [ ] Not this round, flagged again by think and not re-litigated: PDR item #5 (cross-instance KV rate limiting) — an unshipped prior commitment from the original PDR, still worth someone picking up eventually.

## 3. Decisions Made & Rationale
1. **Decision (CEO review, ethical line, logged `0a29f1b4`):** hold the ethical line — no loss-framed streaks, no guilt-based push notifications, no cloud data collection — even though it diverges from the most literal reading of the operator's "addictive like crack" brief. — **Rationale:** confirmed directly with the operator via AskUserQuestion, presented as a genuine values decision, not skimmed past. This round targets one specific real child the operator knows personally, which raises rather than lowers the ethical stakes; extends the app's existing "never mean, these are children" judge-prompt rule to the gamification layer.
2. **Decision (CEO review, scope, logged `0a29f1b4`):** hold Approach B (a forgiving weekly practice counter) this round; ship Approach A (solo-practice mode) + Approach C (celebration polish) only. — **Rationale:** operator confirmed; validates whether solo mode actually gets used before adding a second new mechanic on top of an unproven one — same sequencing round 2 used for its own bigger swing.
3. **Decision (eng review, logged `a0137616`):** solo-mode response schema is `{solo_score: 1-5, solo_reason, verdict_narration}` (no vibe fields), scoped to the 28 trace verticals only, not the 4 draw/animal verticals. — **Rationale:** operator confirmed; a stored numeric score preserves real "most improved" signal that a qualitative-band-only response would lose; trace-only matches the actual language-learning goal and avoids forcing the inherently-comparative draw rubric into an ill-fitting absolute score.
4. **Decision (eng review, direct fixes, logged `dca7643a`):** fixed 3 blocking gaps an outside-voice pass found by reading the live repo — `judge.js:224`'s validation gate still hard-required `drawingB` (every solo request would 400), the original `duelo_progress` schema couldn't hold per-mode prior records once duel/solo attempts interleave on one letter, and the solo-mode UI entry point was unspecified. — **Rationale:** all three were concrete implementation gaps with no real alternative, not judgment calls — fixed directly in the design doc's Eng Review Lock section rather than re-opened as operator decisions. Also added: explicit `mode` field in the payload (not inferred from `drawingB`'s absence), server-side trace-only type allowlist, and a rule that "most improved" narration only ever appears on genuine improvement, never surfaced as a decline — the one real collision point between the "honest comparison" promise and the locked no-guilt ethical line.

## 4. Declared First Effect
Build reads `/Users/flybyflow/duelo-de-dibujo/DOCS/designs/gamification-round3.md` in full — specifically the "## Eng Review Lock" section at the end — then cuts a fresh branch off clean `main` and commits the design doc's repo copy as its first commit.

## 5. Next 3 Immediate Actions
1. Build seat: cut fresh branch off `main`, commit `DOCS/designs/gamification-round3.md` (currently uncommitted).
2. Build seat: implement the server-side solo path first (§2a above) — it's the actual structural fix everything else depends on, and the validation-gate fix is a one-line change with an outsized blast radius if missed.
3. Build seat: implement per the Eng Review Lock's exact corrected schemas (`duelo_progress` per-mode sub-records, explicit `mode` field, server-side type allowlist) — then hand off to Test per its own contract.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** What did the operator choose when asked directly whether to hold the ethical line or override it toward the literal "addictive like crack" brief (jsonl regarding the first AskUserQuestion this session, "Ethical line")?
2. **Q2:** What specific line in `judge.js` did the outside-voice review identify as making every solo request 400 before reaching any solo-aware code (jsonl regarding the Agent tool result, "Outside-voice review of Duelo gamification plan," finding 1)?
3. **Q3:** Why can't the original `duelo_progress` schema (`{[challengeId]: {attempts, lastMode, lastResult, lastTs}}`) support "most improved" comparisons once a kid interleaves duel and solo attempts on the same letter (jsonl regarding outside-voice finding 2 and the corrected schema in the Eng Review Lock section)?
4. **Q4:** What does the locked spec do when a solo attempt's score is flat or lower than the stored prior same-mode record, and why (jsonl regarding outside-voice finding 9 and the "Regression handling" subsection)?
5. **Q5:** Which function does the original design doc's cons section incorrectly list as needing a solo variant, and why doesn't it actually need one (jsonl regarding the "Corrected from the original draft" subsection)?
