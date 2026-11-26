# Handoff: postcall-dev -> postcall-dev (next generation)
- **Lineage:** postcall-dev (Gen 1 -> Gen 2)
- **Timestamp:** 2026-11-26T00:55Z (rotation, bg-beat msg_821e9ad6)
- **Working Directory:** <worktree> (dev branch build/post-call-commitments, local only); PR worktree <worktree>/../pr (branch build/post-call-commitments-pr, pushed)
- **Last Commit SHA:** dev a4b36e3 + this handoff commit; PR branch bac89be (one signed squash on origin/main 257c15a) + this handoff

## 1. Current Goal & Phase State
- **Goal:** Post-call commitments: one review card per real call (Pocket recordings + named-speaker segment files), listing Shaw's commitments and the participants' promises (chase items); on approval, human tasks plus dispatch of approved agent work, then one results line to gm.
- **Plan Reference:** `docs/DESIGN_post-call-commitments.md` (v4.3; congruence DEC-1795627933880541 reached; gm rulings in §9).
- **Phase:** Phase 2 (build) of 3. **DUE 12:00 local on 11-26** (gm msg_62f55733, Shaw order "accelerate"). Mail gm once per item, when it is live by effect, with evidence lines.
- **Current Step:** Code review round 2 (fenced Claude reviewer; codex is out of credits per gm) returned **REJECT** with 2 new blockers (docs/design/postcall-phase2-review-round2.md). Fix them, then review round 3, then open the PR.

## 2. Open Loops & Active Callbacks
- [ ] **R2-B1:** a re-ingest that fails once loses its new items. Each failure path stores the new content_hash first, so the next `ledger.card_revision` doesn't bump; `plan_card` then refuses the old r0 card id and the call ends up failed_final. Fix: store the new hash only once a card or no_card is committed (or bump the revision on hash mismatch, independent of the stored hash). Red test first.
- [ ] **R2-B2:** re-ingest duplicates commitments. The occurrence key includes the LLM's deliverable wording, so a re-extraction that words it differently gets a new key, and it lands on the new card with no ⚠. Fix per design §5 steps 1-3: pass the call's existing occurrences to the extractor (`extractor.run(existing=...)` already supports `same_as_occ`), relocate old quotes in the new transcript, and accept `same_as_occ` only with full field equality and one-to-one; anything overlapping an old quote span that fails those checks → uncertain (⚠, excluded from accept-all). Red tests first.
- [ ] Review round 3: fenced read-only `claude -p --strict-mcp-config --allowedTools "Read Grep Glob"`, brief = round-2 findings + diff.
- [ ] The PR. Copy scripts/postcall + the postcall docs into the PR worktree (`git checkout build/post-call-commitments -- scripts/postcall docs/...`), make one more `git commit -s` there (the configured noreply identity; never the personal email), push, and open the PR against main. CI must be green by name; scripts-top collects scripts/postcall/tests. **Never edit .github/workflows**: the token lacks the `workflow` scope. The dev branch can't be pushed for that reason (commit 8a08aa1 touched ci.yml), which is why the PR branch exists.
- [ ] Then mail gm "phase 2 done", with evidence: CI run URL, suite count, and a read-only check showing 0 postcall-dev rows in the live tasks.db.
- [ ] Rollout (later, gm-gated): cron line; MODE=live; service identity; the validation card is drafted to gm once the shadow ledger has 3+ real calls; the calendar ICS URL that gm is asking Shaw for.
- [ ] Known gap: transcripts over 60k chars are truncated (flagged `_truncated`), not chunked as design §2 says.
- [ ] The local scripts-top run had pre-existing failures that don't involve postcall (test_tier_rule, test_gate_hook latency under load, pane_cli, seat_supervisor, notify_policy). Check them against main's CI on the PR.

## 3. Decisions Made & Rationale
1. **Decision:** The suite lives in scripts/postcall/tests, a package. **Rationale:** without the workflow scope, CI can't gain a new job, and scripts-top already collects scripts/.
2. **Decision:** The fence is a per-test autouse fixture, not a module-level env change. **Rationale:** the suite runs inside the repo-wide scripts job and must not leak env into other tests.
3. **Decision:** No literal city clocks, ICS stamps, home paths or overlay paths in source; ICS fixtures are built at test time from a template. **Rationale:** the repo's privacy scanners (scan_operator_identifiers, test_no_personal_path) gate CI.
4. **Decision:** The PR is one squashed, signed commit on current main. **Rationale:** DCO needs a sign-off on every commit, and no commit in the PR may touch workflows.

## 4. Declared First Effect
`cd <worktree> && nice -n 19 taskset -c 6,7 python3 -m pytest scripts/postcall/tests -q -p no:cacheprovider` → 248 passed (one-off "HERMETICITY LEAK" errors come from other seats editing the live registry; rerun)

## 5. Next 3 Immediate Actions
1. Red test + fix for R2-B1 (hash stored only after the outcome is committed; the revision bumps on mismatch).
2. Red tests + fix for R2-B2 (existing occurrences to the extractor; relocation; strict `same_as_occ`; anything else is uncertain).
3. Review round 3, then the PR branch commit, push, PR and CI, then the done-mail to gm.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** Which call sources did gm's scope correction keep, and which did it drop? (msg_ecea3d6c_28210188)
2. **Q2:** Why can't the dev branch be pushed, and where did the tests move to get CI coverage? (commit ea130d7)
3. **Q3:** Which two blockers did review round 1 find, and in which commit were they fixed? (commit f1a4a39)
4. **Q4:** What new phase-2 due date did gm set, and which reviewer replaces codex? (msg_62f55733_49526005)
5. **Q5:** Which re-ingest failure modes did review round 2 reject? (commit a4b36e3)
