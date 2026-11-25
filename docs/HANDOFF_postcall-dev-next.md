# Handoff: postcall-dev -> postcall-dev (next generation)
- **Lineage:** postcall-dev (Gen 1 -> Gen 2)
- **Timestamp:** 2026-11-25T18:00Z (checkpoint, written early)
- **Working Directory:** /home/shaw/wt/postcall-dev/program (branch build/post-call-commitments, pushed)
- **Last Commit SHA:** 35a5fea

## 1. Current Goal & Phase State
- **Goal:** One review card per real call (Pocket recordings + named-speaker segment files), listing Shaw's commitments and the participants' promises (chase items). On approval: human tasks + dispatch of approved agent work + one results line to gm.
- **Plan Reference:** `docs/DESIGN_post-call-commitments.md` (v4.3; congruence DEC-1795627933880541 reached; gm rulings in §9).
- **Phase:** Phase 2 of 3 (build, test/shadow only). Phase 3 = chase-task render spec with avatar-dev (spec only, do not mail avatar-dev).
- **Current Step:** increments 1-3 are done (fence, empty call, speaker binding, effect recovery). Next is increment 4, deadlines (§3).

## 2. Open Loops & Active Callbacks
- [ ] gm is asking Shaw for a calendar ICS URL at ~/.secrets/shaw-calendar.ics.url. Read it by reference only. If it's absent, deadlines stay unknown.
- [ ] orchestraos-builder: guarded `update_fields` (LOW, queued by gm). The reschedule patch stays behind POSTCALL_RESCHEDULE_PATCH=off until it lands.
- [ ] Rollout (later): gm OKs the cron line; the service identity replaces `--from postcall-dev`; the validation card is drafted to gm once the shadow ledger has 3+ real calls.
- [ ] Codex is out of credits; the second Claude reviewer stands in (gm ruling).

## 3. Decisions Made & Rationale
1. **Decision:** Reuse the commitments miner's pure functions with a cmt-v2 prompt rather than the dream facts pipeline. **Rationale:** facts drop the recording id and carry no due date, counterpart or citation, and they run nightly.
2. **Decision:** A sweeper plus a sqlite ledger with deterministic effect intents, reconciled before any re-send. **Rationale:** op_key dedupe only covers pending rows, and the 900 s guard counts from creation.
3. **Decision:** Shadow is the default mode; the shadow and live ledgers are separate; there's no replay at activation. **Rationale:** filing a questionnaire pushes immediately, so a scratch DB alone isn't a fence.
4. **Decision:** DOCS/SHARED_DECISIONS.json is kept untracked. **Rationale:** its proposal_sha256 trips the pre-push detect-secrets scan.

## 4. Declared First Effect
`cd /home/shaw/wt/postcall-dev/program && nice -n 19 taskset -c 6,7 python3 -m pytest tests/postcall -q -p no:cacheprovider` → 25 passed

## 5. Next 3 Immediate Actions
1. RED: tests/postcall/test_deadlines.py for §3. Cases: explicit date > relative > calendar > unknown; tz_at_call from shaw-location `since`; "Friday at 2pm" → 14:00; ambiguous → null; DST.
2. RED: tests/postcall/test_card_shape.py for §4. Cases: a Decide-by first line; (a) Yours / (b) Theirs; Q1 rules; ⚠ items excluded from accept-all; frozen options; paging at 19.
3. RED: answer application for §5/§6. Cases: first applied wins, `task:<cmt>`/`dispatch:<cmt>`, the dispatch authorization check.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** Which call sources did gm's scope correction keep, and which did it drop? (msg_ecea3d6c_28210188)
2. **Q2:** Who reviewed round 6 of the design, and what did the reviewer's brief contain? (msg_92221c61_28950966)
3. **Q3:** Which sabotage initially failed to turn any test red in increment 3, and which test was added for it? (commit 35a5fea)
4. **Q4:** What happens to a Pocket call whose Shaw-label evidence sits in another speaker's turn? (commit 1331bb9)
5. **Q5:** What must exist before the reschedule patch path can be switched on? (msg_92221c61_28950966)
