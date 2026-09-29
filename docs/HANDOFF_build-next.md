# Handoff: build -> gm / review (router.py P0 fixed, awaiting merge + restart)
- **Lineage:** build (Gen 1, fresh seat 2026-09-29)
- **Timestamp:** 2026-09-29T10:40:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (**shared with the `plan` seat — see §5**)
- **Branch:** `build/router-offset-commit-then-confirm`
- **Last Commit SHA:** `b834241`
- **Task:** gm `msg_07ad308a_77353846` — "P0: router.py silently drops operator messages on any transient exception"

## 1. Current Goal & Phase State
- **Goal:** fix the silent-drop P0 in `plugins/telegram/router.py` and prove it with real tests + a live crash drill.
- **Phase:** Implementation and verification COMPLETE. Not merged, not deployed.
- **Current Step:** none — waiting on gm for merge/PR direction and a router restart.

## 2. What shipped (`b834241`, 2 files, +515/-16)
`plugins/telegram/router.py`:
1. **Commit-then-confirm.** `poll_once()` advances `state.offset` only after the effect durably landed. The bug was the advance sitting outside the `try/except` (old line 323), so a transient exception still confirmed the update and Telegram never redelivered it.
2. **Idempotent on `update_id`.** `deliver_to_gm` derives the msg_store primary key from it (`tg-<update_id>`), so a redelivery is a no-op insert even after a `kill -9` that loses the offset file. Without this, fixing #1 trades silent loss for silent duplication.
3. **Transient vs permanent split** (`unprocessable_reason()`). Transient (shape fine, effect failed) → never confirm, retry. Permanent (malformed, no `chat.id`) → confirm and step over on the first pass. Only *shape* is inspected, never the outcome of an effect, so a store outage can never be misread as malformed.
4. **Bounded loud retries** (`MAX_UPDATE_ATTEMPTS = 5`, persisted). Prevents a poison update deadlocking the operator's only channel. Giving up tells the operator their message was not delivered and files a `critical` escalate to gm.
5. **Callback repaint calls made non-fatal** — cosmetic Telegram UI calls no longer fail the whole update (a redelivered tap would re-answer an already-answered card; that path has no `update_id` dedupe).

`plugins/telegram/test_router_offset.py`: 10 new tests.

## 3. Verification (all actually run)
- **Bug reproduced first** on old code: transient failure on update 77 → offset advanced to 78 → next `getUpdates` returns `[]` → message gone. Same scenario on new code → offset stays 0, update redelivered.
- **Mutation test:** reintroducing the unconditional advance turns **6 of 10** new tests red; restored → green. The tests bite.
- **Live `kill -9` drill** (gm required it): child inserts the row then SIGKILLs itself before the offset write (exit 137, offset file absent). Fresh process → Telegram redelivers → `already delivered as tg-77; redelivery ignored` → final DB has **exactly 1 row**, `tg-77`. Survived, not duplicated.
- **Suites:** `plugins/` 27 passed (17 pre-existing, unmodified); 58 passed across telegram-adjacent suites.
- **Python 3.12** (the interpreter the live router runs) — compiled + smoke-tested. Repo default `python3` is 3.9, so the unit run alone would not have covered production.

## 4. Open Loops
- [ ] **NOT DEPLOYED.** Live router **pid 10767 is still running the old code** and keeps dropping messages until restarted on the merged fix. Deliberately not restarted by build — that is the operator's live command channel. Migration checked: live state dir has `offset`/`chat-id` but no `last-done`; `last_done` defaults to 0 and all real update_ids exceed 0, so nothing is wrongly skipped on first boot and the existing offset is honoured. **No migration step, just a restart.**
- [ ] **Two corrections to gm's brief / both review docs.** (a) router.py is NOT at 0/5 coverage — `plugins/telegram/tests/test_router.py` already has 17 tests, missed by both reviews. (b) One of them, `test_a_bad_update_is_skipped_and_offset_still_advances`, asserts the **opposite** of the literal spec. It was not deleted or weakened; it encodes a real requirement, and §2.3 is the reconciliation. Sent to gm (`msg_aec99f15_78152824`) and plan (`msg_f0b1d6a8_78236691`).
- [ ] **Judgment call for gm to sanity-check:** bounded retries. The spec said never advance past a failed update; taken absolutely, one poison update blocks the channel forever. Build chose loud give-up after 5. Reversible if gm wants strict blocking.
- [ ] **PR base matters.** See §5.
- [ ] **DEPLOY TRAP (found 2026-09-29 after handoff was written).** The live router executes from the working tree (`ps -p 10767` → `/Users/flybyflow/orchestraos/plugins/telegram/router.py`), and the `plan` seat has since checked that tree out to `fix-arturo-mapfile-bash32`. The file on disk is therefore the OLD unfixed router (`grep -c MAX_UPDATE_ATTEMPTS plugins/telegram/router.py` → 0). **Restarting the router while a fix-less branch is checked out deploys the bug under a green light.** Nothing of build's work was lost — `b834241` exists, this branch is intact at `602be6b`, the fix is in the committed blob; only the working tree moved, and build re-applied nothing. **Before any restart: confirm the checkout contains the fix (`grep -c MAX_UPDATE_ATTEMPTS plugins/telegram/router.py` must be ≥ 1), then verify the running process picked it up.** Escalated to gm as `msg_dea32786_78588537`.

## 5. Shared working directory — read before cutting a PR
The `plan` seat works in this same checkout. One of its docs commits (`5a6b834`) landed on this branch by accident; plan moved a copy to `fix-arturo-mapfile-bash32` (verified present there — nothing at risk) but it **remains an ancestor here**. So a PR cut from this branch today contains **3 files, not 2**, including 297 lines of `docs/PLAN_silicon-jungle-agentic-platform.md`.
**Base the PR on `4d6e8d3`, or cherry-pick `b834241` alone.** Build deliberately did NOT rebase it away: plan is actively editing that file in this shared tree, and rewriting the checked-out branch's history would change it under them mid-edit. Plan was told (`msg_f0b1d6a8_78236691`) and offered a rebase at a safe stopping point.

## 6. Build's own error this session (logged, not hidden)
A stray `noop` line left in a shell command sent a junk `plan -> plan` msg_store row (`msg_da670609_78236626`) **under plan's identity**. Caught immediately; archived, acked, and disposed `declined` with an explanation on the row. No instruction content, no action triggered. Recorded here because an impersonated row is worth an audit trail even when harmless.

## 7. Declared First Effect
`git log --oneline -1 build/router-offset-commit-then-confirm` → must be `b834241`. Then decide merge vs PR (§5 for the base) and schedule the router restart (§4).

## 8. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** Which pre-existing test asserted the opposite of the literal P0 spec, and what single structural property of an update decides which of the two failure paths it takes (jsonl regarding the test_router.py run that first went red)?
2. **Q2:** In the kill -9 drill, what exactly proves the redelivered update was deduped rather than simply never re-sent (jsonl regarding the phase2 log line and the final row assertion)?
3. **Q3:** Why does `unprocessable_reason()` inspect only the update's shape and never the result of an effect (jsonl regarding the transient/permanent design note)?
4. **Q4:** Why did the first kill -9 drill attempt fail, and what was missing from the scratch database (jsonl regarding the OperationalError on the first drill run)?
5. **Q5:** Why was the accidental docs commit left as an ancestor of this branch instead of being rebased away, given its copy was verified safe elsewhere (jsonl regarding the shared-working-directory exchange with plan)?
