# Phase-2 code review, round 3: Claude reviewer (codex out of workspace credits), 2026-11-26T01:11:47Z
Brief: round-2 findings + diff a4b36e3..cb53ab6 (scripts/postcall). Fenced: claude -p --strict-mcp-config --allowedTools "Read Grep Glob".

**B1 and B2 are both closed for the paths the spec covers, and I found no new class-3 bug.** I only read code: I did not run the suite, because the brief allowed no commands that write.

## Are B1 and B2 closed?

**B1 is closed.**
- `card_revision` now compares against `card_hash`, which only `plan_cards` writes, in the same transaction as the pages (`ledger.py:85-101, 133-146`). The failure paths still store the new content hash, but nothing compares against it any more. So a retry after any failure bumps to r1 and never reuses the r0 card id.
- The paths I checked hold: crash between `card_revision` and `plan_cards`; crash after `plan_cards` but before `set_call_status`; first ingest with no `calls` row yet (the `'carding'` insert); and content going back to an earlier hash.
- Putting all pages in one transaction also removes the half-planned-card refusal. The sqlite connection's context manager rolls back on an exception.

**B2 is closed per §5 steps 1-3.**
- The extractor now gets this call's existing occurrences.
- Old quotes are relocated by text, not by raw offsets.
- A `same_as_occ` claim is accepted only when owner, action_type, operation and deliverable are equal, the quote overlaps or is equal, and the claim is one-to-one.
- A failed claim, or a same-owner overlap with no claim, gets `uncertain_match`. That shows the ⚠ and a Same-as option, keeps the item out of accept-all, and skips cross-call auto-linking (`dedupe.py:50-53`).
- Adding the known items' own keys to `claims` correctly marks a second claimant as uncertain when one item is an exact restatement.

## Blockers (class 3)

None.

## Shoulds

1. **Wrong Same-as target** (`pipeline.py:193-194`). With no claim, the code picks the first same-owner overlapping occurrence by rowid. Take one sentence holding two deliverables: the deck (restated exactly, so already known) and the contract (reworded). The reworded contract item gets "Same as 'send the pricing deck'", and the real contract commitment is not offered. Shaw then taking it duplicates the contract. He sees the ⚠, so this isn't class 3, but it's close. Fix: prefer the occurrence whose fields match, skip occurrences already restated by a known key, or offer every overlapping one.
2. **Due-date change dropped on an accepted claim** (`pipeline.py:190-192`). `ledger.link` records the link but no due change. `dedupe.link` does record one for an auto-link, as §5 "Due-date changes" asks. A re-transcription that fixes "Friday" to "Monday" is silently lost.
3. **Attempts never reset across content hashes** (`ledger.py` `set_call_status`, existing behaviour). A call with two old failures goes straight to `failed_final` on its first re-ingest failure. Its new items then wait for another transcript change. This is the leftover form of B1's symptom.
4. **Rollout: old ledger rows.** Adding owner to `_source_key` changes every key, and rows written before this change have `quote` NULL. On such a ledger, any re-ingest of an earlier call re-cards every item with no ⚠: the keys no longer match `known()` and there's no span to overlap. The `COALESCE(card_hash, content_hash)` fallback also brings B1 back for those rows. I found no postcall `ledger.db` in the state directory, so this isn't live yet. Make "fresh ledger, or migrate keys and quotes" a recorded rollout prerequisite.
5. **`same_as_occ` type isn't checked** (`pipeline.py:182, 188`). If the model returns a list or dict, the set intersection or `dict.get` raises `TypeError`. That gives failed_retry three times, then failed_final. Accept only a `str`.
6. **Prompt wording** (`extractor.py:56`). The header says "earlier extractions of related calls", but these are from this same call.
7. **Gap in the spec itself, not the code.** If the old quote can't be relocated (re-transcription changed its words) and the model makes no claim, step 4 treats the item as new, with no ⚠. Worth deciding on purpose.

## Test gaps (a sabotage that would stay green)

- **T1:** Remove the overlap-or-equal-quote condition from acceptance. No test has a claim with equal fields on a quote that neither overlaps nor matches.
- **T2:** Drop `action_type` from `_same_fields`. The parametrize only covers operation, deliverable and owner.
- **T3:** "First claimant wins". In the double-claim test, one claimant is already a known key. There's no test where two new items claim one occurrence and both must be ⚠ as §5 requires.
- **T4:** Drop `x["owner"] == it.owner` from the overlap search. No test covers an overlap with a different owner. This is low risk on diarized calls, but real on non-diarized ones.

VERDICT: APPROVE
