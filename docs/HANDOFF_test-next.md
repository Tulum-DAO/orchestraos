# Handoff: test -> ship

- **Lineage:** build -> review -> test -> ship (Gen 1)
- **Timestamp:** 2026-09-20T21:20:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (task target repo: /Users/flybyflow/duelo-de-dibujo)
- **Branch:** `build/arabic-letter-tracing-vertical` @ `86fca3e`

## Result

**PASS, with concerns.** QA report: `/Users/flybyflow/duelo-de-dibujo/.gstack/qa-reports/qa-report-skill-duel-engine-v3-2026-09-20.md`. Baseline: `.gstack/qa-reports/baseline.json` (first baseline for this repo — delta is flat/N/A, nothing to regress against).

## Pass/fail summary

- `qa-judge.mjs` case 3 (unknown `challengeId` -> 400): **PASS**, confirmed on rerun.
- `qa-judge.mjs` cases 1-2 (real Claude calls): **not greened**, but root-caused, not a code defect — `ANTHROPIC_API_KEY` is a Vercel **Sensitive** var (write-only, unrecoverable by any local `vercel env pull`) scoped to **Production only**, so this branch's Preview deployment genuinely has no key access. Verified the judge pipeline itself is correct by curling the real Production deployment (pre-existing code) with the frog payload: HTTP 200, correct winner. The alif-specific Claude judgment is the one leg still unverified — it needs this branch on Production (or a non-Sensitive key) to test for real.
- RTL: page-level `dir`/`lang` toggle predates this branch and already covers the new alif content; `challenges.json`'s `alif.nm.ar` is real, correctly-encoded Arabic text. Pixel-level rendering and Arabic TTS pronunciation are un-verifiable from this non-interactive seat — stay open, same as the design doc already scoped them to human playtest.

## Delta

No prior baseline existed for this repo (Review's checks were API curls, not a `/qa` browser pass) — this is the first one. Flat, no regression possible to measure against.

## Open items for Ship / a human (non-blocking)

1. Promote to Production (or otherwise get a real key in front of it) and re-run `qa-judge.mjs` cases 1-2 to close the alif-Claude-judgment gap.
2. Real-kid playtest of `rubric.vibe` wording for letter-tracing (design doc Open Question #4).
3. Human RTL visual check + Arabic TTS pronunciation listen, ar/fr/en.
4. Watch video `fKwOMa3r1_c` for content accuracy.

## Next action

Ship: branch + PR only per the operator's hard-stop (no land-and-deploy without a fresh approval gate) — per Review's handoff §6.
