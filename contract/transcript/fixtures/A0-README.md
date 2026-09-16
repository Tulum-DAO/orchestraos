# A0 telemetry fixtures — `<provider>/a0-*.json`

These are the **Build A0** distilled golden fixtures — the RED tests for Build A
(multiplexed WAL tailer / per-provider adapters) and Build B1 (real-time status
deriver + token extractor). They are a DIFFERENT category from the flat
transcript-envelope conformance fixtures (`claude-basic.*`, `codex-basic.*`,
`antigravity-basic.*`) which test the RENDERING contract. Do not feed A0 fixtures
through `normalizeTranscript`.

## Files (per provider: claude / codex / gemini)
- `a0-status-discriminator.json` — measured chrome signature table + the 2×2
  (bytes × `/proc` CPU) discriminator constants + golden {input → expected_status}
  cases. Distilled from real byte streams + `/proc` samples (raw captures disposed).
- `a0-court-red.json` — one contaminated-lineage-per-provider RED. The contaminated
  body is a **SYNTHETIC sentinel**, never real flagged bytes (keeps the graduated
  set at zero flagged-lineage bytes — the A0 court RED — while exercising the
  flag→block-mode boundary in each provider's serialization).

## Guard
`contract/transcript/tests/test_a0_fixture_cleanliness.py` is the standing court
guard: asserts every court-RED body is the synthetic sentinel, block-mode/no-leak
is expected, and no real contagion signature graduated. Run it before touching
these fixtures.

## Provenance
Author = `telemetry-a0-probe` (T3, author≠verifier). Full findings +
throwaway-harness disposal are in the A0 handoff to gm. Distilled here; raw
captures + harness disposed per the canary-r6 throwaway discipline.
