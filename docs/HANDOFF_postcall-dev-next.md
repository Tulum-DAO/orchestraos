# Handoff: postcall-dev -> postcall-dev (next generation)
- **Lineage:** postcall-dev (Gen 1 -> Gen 2)
- **Timestamp:** 2026-11-25T18:20Z (checkpoint after increment 5)
- **Working Directory:** <worktree> (branch build/post-call-commitments, pushed)
- **Last Commit SHA:** f072262 (local; origin is at ecb5ccc because pushes with ci.yml are rejected)

## 1. Current Goal & Phase State
- **Goal:** One review card per real call (Pocket recordings + named-speaker segment files), listing Shaw's commitments and the participants' promises (chase items). On approval: human tasks + dispatch of approved agent work + one results line to gm.
- **Plan Reference:** `docs/DESIGN_post-call-commitments.md` (v4.3; congruence DEC-1795627933880541 reached; gm rulings in §9).
- **Phase:** Phase 2 of 3 (build, test/shadow only). Phase 3 = chase-task render spec with avatar-dev (spec only, do not mail avatar-dev).
- **Current Step:** increments 1-12 are committed (fence through dedupe/results); 205 tests. gm's new due date for phase 2 is 12:00 local on 11-26 (msg_62f55733). A subagent is wiring participants + calendar into the pipeline, live duenote/patch, and sweep results_due. After that: move tests/postcall to scripts/postcall/tests (the token lacks the `workflow` scope, so ci.yml can't be pushed and the scripts-top CI job collects scripts/); revert the ci.yml edit (8a08aa1); build a signed PR branch from main with cherry-pick -s, dropping the hand-written Signed-off-by email line in db7b14d; fenced Claude review (codex is out of credits per gm); CI green; mail gm.

## 2. Open Loops & Active Callbacks
- [ ] gm is asking Shaw for a calendar ICS URL at ~/.secrets/shaw-calendar.ics.url. Read it by reference only. If it's absent, deadlines stay unknown.
- [ ] orchestraos-builder: guarded `update_fields` (LOW, queued by gm). The reschedule patch stays behind POSTCALL_RESCHEDULE_PATCH=off until it lands.
- [ ] Rollout (later): gm OKs the cron line; the service identity replaces `--from postcall-dev`; the validation card is drafted to gm once the shadow ledger has 3+ real calls.
- [x] Codex is back (gm msg_4a2b9618): reviews go to `codex exec` again. A fenced Claude reviewer is used only while codex is actually out, and the report then says so. Any limit hit gets reported with its window (5-hour/weekly/monthly) and the reset time in Tulum; a 5-hour stop means wait.

## 3. Decisions Made & Rationale
1. **Decision:** Reuse the commitments miner's pure functions with a cmt-v2 prompt rather than the dream facts pipeline. **Rationale:** facts drop the recording id and carry no due date, counterpart or citation, and they run nightly.
2. **Decision:** A sweeper plus a sqlite ledger with deterministic effect intents, reconciled before any re-send. **Rationale:** op_key dedupe only covers pending rows, and the 900 s guard counts from creation.
3. **Decision:** Shadow is the default mode; the shadow and live ledgers are separate; there's no replay at activation. **Rationale:** filing a questionnaire pushes immediately, so a scratch DB alone isn't a fence.
4. **Decision:** DOCS/SHARED_DECISIONS.json is kept untracked. **Rationale:** its proposal_sha256 trips the pre-push detect-secrets scan.

## 4. Declared First Effect
`cd <worktree> && nice -n 19 taskset -c 6,7 python3 -m pytest scripts/postcall/tests -q -p no:cacheprovider` → 82 passed

## 5. Next 3 Immediate Actions
1. Done in increment 6: `ledger.plan_card` / `apply_answers` / `_decide` / `_dispatch_authorized`. Not yet wired: reading answers from tasks.db (`questionnaire_answers`, read-only), plus `qack` once they're applied.
2. Done in increment 7: `effects.mode()` (MODE must read exactly `live`; anything else is shadow; POSTCALL_MODE=test for tests), `Shadow` (jsonl), and `Live` card send through the approval CLI plus a read-only find by op_key, proven against the scratch DB with notified_at null. Still to do: Live.send for `task` (approval.py human-task --feature commitment --op-key cmt) and `dispatch` (msg_store send --body-file), each RED first.
3. Still to build: the sweeper (flock inherited by children, Pocket dir scan, live_start_ts); cross-call dedupe (§5); §2a participants (calendar overlap, speaker mapping); the calendar ICS reader; the cmt-v2 extractor prompt (headless claude -p, niced). Each one RED first, then a sabotage run.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** Which call sources did gm's scope correction keep, and which did it drop? (msg_ecea3d6c_28210188)
2. **Q2:** Who reviewed round 6 of the design, and what did the reviewer's brief contain? (msg_92221c61_28950966)
3. **Q3:** Which sabotage initially failed to turn any test red in increment 4, and which test was added for it? (commit c75bc97)
4. **Q4:** What happens to a Pocket call whose Shaw-label evidence sits in another speaker's turn? (commit 1331bb9)
5. **Q5:** What must exist before the reschedule patch path can be switched on? (msg_92221c61_28950966)
