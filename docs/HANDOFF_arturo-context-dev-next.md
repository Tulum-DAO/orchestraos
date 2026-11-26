# Handoff: arturo-context-dev (gen row 1997) -> next
- **Timestamp:** 2026-11-25T22:05Z (17:05 Tulum)
- **Working Directory:** /home/shaw/wt/arturo-context-dev/program
- **Merged:** #627 -> main aa84d24 (squash). D1+D4+meta+D2(flag off) = PR #635 (branch feat/agent-call-context-signed, worktree /home/shaw/wt/arturo-context-dev/agents, head 59baa8a; #634 closed, unsigned), review asked of orchestraos-builder (codex-gm); shapes mailed to builder + avatar-dev.

## HAZARD (read first)
- **LIVE native voice runs from /home/shaw/wt/arturo-context-dev/native-live** (detached worktree at aa84d24), pane arturo-native:0.0, pid 3555327 at cutover. DO NOT remove, move or check out anything in that worktree while the pane runs from it (gm condition, card apr_0886cf3e_42878839). The repoint to ~/orchestraos rides orchestraos-builder's main-forward card later.
- Rollback command (old tree), on the card: `ARTURO_NATIVE_GPTLIVE=1 bash /home/shaw/scripts/agent-orchestra/services/arturo/native/run.sh 2>&1 | tee -a /home/shaw/scripts/agent-orchestra/logs/arturo-native.log` via `tmux respawn-pane -k -t arturo-native:0.0`, quiet check first.
- ~/orchestraos (live tree) is 41 commits behind main with 432 dirty entries; never pull it yourself.

## 1. Current Goal & Phase State (rewritten at rotation, 2026-11-25 23:55Z)
- LIVE: native :5076, pane arturo-native:0.0, pid 4106439, from the pinned worktree /home/shaw/wt/arturo-context-dev/native-live (aa84d24), env ARTURO_NATIVE_CONTEXT_TOKENS=2500. Opens with gm chat + Arturo calls (about 24 turns). The dashboard already sends start.thread (avatar-dev, bundle index-DXsXqetJ.js); the live build ignores it until #644.
- Shaw's due dates (gm msg_c6dacdfa): 25-turn own-thread opening LIVE by 23:00 Tulum tonight (already at risk); latency plan with numbers 08:00 Tulum 11-26; #635 (takes-calls D1+D4; it replaced #634) card 10:00 11-26; unified-log writer verdict with the latency plan.
- Codex is out of credits: every review is a fenced read-only Claude reviewer; cards say so.

## 2. Open Loops (in order)
- [ ] #638 (feat/native-opening-turns, worktree /home/shaw/wt/arturo-context-dev/turns, head 700a51d): builder's reviewer BLOCKED it (9 findings, PR comment 7177841744). Still open after 700a51d: keep only the LAST 25 turns after a floor re-read (not the whole 4,000-token block); test the 25 boundary exactly; report fields budget / reread / source (route vs twin) / timeout-skip; the "ET" label in tests should be "Local" (#637 convention). Card apr_fe545d12 has a HOLD note. Fold red-first, push, mail builder the head, note the card.
- [ ] #645 (feat/turn-is-exchange, worktree /home/shaw/wt/arturo-context-dev/merge, head cc83b28): builder's reviewer BLOCKED it (6 findings, PR comment 7177842452). The clip cuts the END of a merged turn (keep the tail, or clip per phrase); the same-call check is untested in both twins + fixture; the "phrase(s) not shown" label; name matching can fold operator and agent together (match on role); a time-gap limit within a turn; a test comment with a pronoun + internal id. Card apr_f7e96424 has a HOLD note.
- [ ] #644 (feat/native-page-thread, same turns worktree, head 8ba9370): first review BLOCKED on thread access, fixed in 8ba9370; re-review at 8ba9370: MERGE-OK (PR comment); suggestions: make principal required (no fleet default), clear thread_id in the report when refused, and the "earlier" relabel when typing mid-call. Then merge #645 into it, re-point its base to main after #638 merges, CI, card.
- [ ] After the merges: a quiet-minute respawn of the native from a fresh clean checkout of the new main (carries #637's spending_tools expanduser), on gm's GO. Report pid, healthz, START turns=.
- [ ] #635 (feat/agent-call-context-signed, worktree /home/shaw/wt/arturo-context-dev/agents): review was asked of builder; card by 10:00 11-26.
- [ ] Latency plan (08:00): measure open->START, START->greet and end-of-speech->first-audio on real calls (~/.orchestra/state/arturo/native/calls/*.json + arturo-native.log START/END). Include cold vs warm and 39 vs 31 tools.
- [ ] Unified log: ~/.orchestra/state/unified-conversation.jsonl stopped 2026-11-22 06:41 Tulum; find the writer that died; revive vs retire in favour of the contract.
- [ ] Follow-ups: calls=unowned alias + state/operator.json first (gm via builder msg_6dc3cd8c); "the operator" wording PR; SECRET_RE flags.

## 3. Decisions
1. Opening fenced as PAST CONTEXT (both twins + native fenced()): without it, no greet (probe native-90a7e00cd3d1 vs control native-8f4a14b348a8).
2. Native budget default 1500 (calibration: 1500 -> 2.44 s, 3000 -> 3.01 s, 6000 -> 4.3 s). Feed kind default thinking (voiced ~5 s).
3. Probes must stream silence uplink (memory: native-probe-needs-uplink-audio). Probe script: scratchpad probe_native.py (PROBE_* env).
4. Native from a clean worktree, not a pull of the dirty live tree (gm GO msg_8d3b2cf8).

## 4. Declared First Effect
`cd /home/shaw/wt/arturo-context-dev/turns && git switch feat/native-opening-turns && timeout 60 gh api repos/Tulum-DAO/orchestraos/issues/comments/7177841744 --jq .body`

## 5. Next 3 Immediate Actions
1. Fold builder's #638 findings red-first (last-25 after the re-read, boundary test, report fields, Local label), push, note card apr_fe545d12, mail builder the head.
2. Fold builder's #645 findings red-first (keep the tail of a merged turn, role-based merge, same-call fixture case, gap limit), push, note card apr_f7e96424, mail builder.
3. Read the #644 re-review on the PR; merge #645 into #644; CI; card. Then the respawn from new main on gm's GO.

## 6. Canary Questions
1. **Q1:** Which commit sha did #627 squash-merge to, and which approval card authorized it? (msg_a794504c_44057527)
2. **Q2:** From which directory does the live native run, and why not ~/orchestraos? (msg_90b1cede_43812604)
3. **Q3:** What did the thread-off control session show versus the unfenced thread session? (msg_fd4c1f22_39733643)
4. **Q4:** What budget default was chosen and what greet times backed it? (msg_028a2555_41908919)
5. **Q5:** What did Shaw's cutover answer say, and how did gm rule on it? (msg_71c963d4_43642847)

## Late additions (22:20Z)
- main is RED after #627 on operator-identifier-scan + scripts-top (privacy: "Tulum" city-clock in fixtures/tests, /home/shaw in spending_tools.py). orchestraos-builder owns the fix-forward; I review its fixture for twin equivalence (HIGH, tonight). Do NOT push a parallel fix.
- Commits need `Signed-off-by` (dco job): use `git commit -s`.
- Merge cards name every CI job green on the PR head, by job name (gm rule); wait for the run, never merge on an empty rollup.

## 22:40Z: Shaw order, 25 stamped turns (gm msg_3a7542b2)
- (a)+(b) built: PR #638 (feat/native-opening-turns, worktree /home/shaw/wt/arturo-context-dev/turns): default 2500, floor re-read at 4000 if <25 turns, report thread gets turns/summarized/first/last/chat_lines/call_lines/calls. Proof native-70eb24ace3e9: 25 turns, greet 3.14 s. Not live; needs a merge card (all CI jobs green by name, after builder's privacy fix lands) + respawn GO.
- Open question to gm: merge a call's consecutive same-speaker phrases into one turn (25 turns = only 12 min today).
- (c) unified-conversation.jsonl stopped 2026-11-22 06:41 Tulum: find the writer that died; revive vs retire the relay block in favour of the contract. Recommendation with the latency plan, Thu 11-26 12:00 Tulum.

## 22:55Z: page-thread amendment (gm msg_e7402cbb)
- PR #644 (feat/native-page-thread, stacked on #638, worktree /home/shaw/wt/arturo-context-dev/turns): start.thread -> threads.db opening + ThreadFeed; neither -> Arturo calls only, no chat. Waiting on avatar-dev's start-frame "thread" field (contract mailed msg_ce7fd713). Then: proving session (report the turn count to gm), merge cards #638 -> #644 (all CI jobs green by name), respawn GO.
- #637 (builder privacy fix-forward) fixture review: OK, posted. #635 waits on #637 for CI.
- gm hasn't yet confirmed the mapping (msg_0dfcca5e) or answered the phrase-merging question (msg_04543735).
- Session ended on a usage limit at this point.

## gm rulings received at the usage limit (NOT acted on yet)
- msg_5bfe3426: (1) TURN = EXCHANGE: merge consecutive same-speaker phrases within one call into one stamped turn (stamp = first phrase), both twins + the fixture, its own small PR after #637 lands; report the span 25 merged turns cover on the real thread. (2) GO: respawn arturo-native:0.0 at the next quiet minute on the CURRENT pinned checkout with ARTURO_NATIVE_CONTEXT_TOKENS=2500 (rollback = the same command without the var); report the pid, healthz, and the first real call's START turns=. (3) Answer: the thread-source amendment is CODE (PR #644), not a flag. Until it's live, a standalone-page call reads gm chat + Arturo calls (current pinned build).
- msg_ff784fd1: mapping 1-6 CONFIRMED; turn=exchange applies to the page thread too; mapping 4 is the acceptable interim.
- msg_1d4fa8e0 (avatar-dev): the dashboard half is r35, after r34 (tonight); it mails the bundle hash + md5s when r35 is live, and the proving call pairs with that.

## 23:10Z: done after the limit lifted
- Respawned the native at 2500 (gm GO msg_5bfe3426): pid 4106439, same pinned worktree, env ARTURO_NATIVE_CONTEXT_TOKENS=2500. Rollback = the same command without the var. Still owed: the first real call's START line to gm.
- Turn = exchange: PR #645 (feat/turn-is-exchange, worktree /home/shaw/wt/arturo-context-dev/merge, built on #637's branch). Last 25 merged turns on web_ypc88hel span 52 min.
- Merge order: #637 -> #638 -> #645 -> #644 (rebase #644 onto #645), with cards; then a respawn from the new main. #644 needs avatar-dev r35.

## 23:30Z
- #637 merged (257c15a); main green. Signed-off merges of main into #638 (57129bf, CI 19/19 green) and #645 (cc83b28, CI running).
- MERGE card #638: apr_fe545d12_49123648 (decide by 20:00 Tulum). Review asked of builder/codex-gm (msg_a8d8812a).
- Next: #645 card when its CI is all-green and #638 is merged; then re-point #644 to main, merge #645 into it, CI, card; then a quiet-minute respawn from new main (it carries #637's spending_tools expanduser; gm msg_18d19362).

- avatar-dev: the dashboard start.thread half is LIVE since 23:12Z (bundle index-DXsXqetJ.js, msg_562f2fe3). The pinned live native (aa84d24) ignores the field until #644 is merged and respawned; pair the proving call with that bundle.

## 23:50Z: review round (Claude reviewers; codex out of credits)
- #638: MERGE-OK at 57129bf; fixes in 700a51d (floor re-read only with an EARLIER block and a first read of 2 s or less; junk env; anchored describe). Card apr_fe545d12 noted; CI re-running on 700a51d. Merge only after Shaw approves AND every job is green on 700a51d.
- #645: card apr_f7e96424_50102779 (cc83b28, 19/19 green, MERGE-OK). Follow-up: a long merged turn keeps its head (consider keeping the tail).
- #644: BLOCK fixed in 8ba9370 (thread_readable mirrors _thread_refusal; role-based feed; "earlier" grouping; fullmatch). Re-review running. Then merge #645 into #644 and re-point its base to main after #638 merges, then CI, then the card.
- Shaw's new due dates (gm msg_c6dacdfa): 25-turn own-thread opening live 23:00 Tulum tonight; latency plan 08:00 11-26; #634/#635 card 10:00 11-26.

## 00:35Z 11-26 (gen row 2013, promoted 23:59Z)
- #645 review fixes folded: head 652c59b (tail kept on a merged turn, role+name fold, 120 s MERGE_GAP_S, phrase count, 2 new contract cases). 10/10 sabotages red. Re-review asked (msg_8e4be0f9); card apr_f7e96424 noted HOLD.
- #638 review fixes folded: head 94e76a7 (second read sized budget*25/turns cap 4000, keep-first guard, Opening.info budget/reread/source/short, report skipped + chat_error, omitted count, Local labels, floor tests offline). 6/6 sabotages red; 249 passed. PR body has the vendor-data section. Re-review asked (msg_408e602e); card apr_fe545d12 noted HOLD.
- #644: merged #638's head in (0f1981a, LAND_CHAIN=257c15a4e1,700a51dc96 to pass the git guard): one _with_floor(read->(text,source)) serves opening/opening_thread/opening_calls. 266 passed. #645 NOT merged into #644 yet (wait for its MERGE-OK).
- Leftover from the old session: a `while pgrep ... services/arturo` waiter (pid 3406272) loops on memory-shadow-dev's pytest; not mine to kill.
- Latency data so far (for the 08:00 plan). Greet = the journal's first Arturo phrase minus started_at (vc_client_*.json, conv_id = native report).
  - Old tree, no opening, 25 tools: 1.85 / 2.54 / 2.61 s today; the 11-19 spread was 1.4-10.8 s.
  - New native, 1387 tokens, 39 tools: one real call, 3.27 s (native-ef7dc1274bf1). No real call since the 23:01Z 2500 respawn.
  - End of user speech -> first audio: p50 0.31-0.94 s, p90 1.0-1.6 s. Outliers of 9-17 s are tool turns.
  - Tool result -> audio: 2-4 s typical, tail 18-42 s (old tree). These turns are the latency problem more than the greet.
- 00:45Z: #645 re-review MERGE-OK at 652c59b, CI 19/19 green by name; HOLD lifted on card apr_f7e96424. Merge after #638. Follow-up PR after it merges (non-blocking): pin whether the 120 s gap counts from the previous phrase or the turn's first (a third phrase at ~200 s), and a non-ASCII tail-cut case, so a UTF-16 slice in the TS tail path turns red.
- 01:25Z: #638 delta review at 94e76a7 BLOCKED (empty Opening lost its info to `or ""`; the second read had no deadline of its own). Fixed in 01c8481: route-only second read inside READ_DEADLINE_S 5.0, no twin fallback, kept read cut to the newest 25, reread_result field, exact fixture test. 6/6 sabotages red; 252 passed. Delta asked (msg_b673fdaf); card apr_fe545d12 still on HOLD.
- #644 caught up again at e7faee2: second read allowed for sources route|thread|calls (never twin). 270 passed.
- 01:45Z: #638 delta MERGE-OK at 01c8481, CI 19/19 by name; HOLD lifted on card apr_fe545d12. Both cards (#638 and #645) are now waiting on Shaw. Builder's notes on #638 folded into #644 (82eb3e9). #645 merged into #644 (134338b, LAND_CHAIN=981e72f4ee,6aedb217a7); page-thread voice lines carry role (9f4efe7). #644 delta review asked (msg_7324dd63). After Shaw approves: merge #638 (squash), then #645, re-point #644's base to main, CI, then the #644 card.
- The turns worktree has no api/node_modules: tsc there resolves to ~/node_modules and fails on @types/express. Use the merge worktree or CI for tsc.
- 02:10Z: #644 delta MERGE-OK at 9f4efe7; notes 1-5 folded at 0294ce6 (291 passed). Note 6 is moot: spending_tools is not in the diff against main. To land, in order: Shaw approves apr_fe545d12 (#638) and apr_f7e96424 (#645) -> squash-merge #638 at 01c8481 -> #645 at 652c59b (re-run CI on the current main first if main moved) -> re-point #644's base to main (REST PATCH base) -> `git merge --signoff origin/main` in the turns worktree + push -> wait for every CI job -> file the #644 card naming each job -> respawn on gm's GO.
- 06:05Z 11-26: MERGED #638 -> 1f43f67, #645 -> 9a7c9fb (Shaw go via gm msg_f16a6a8d; cards noted + acked). #644 base -> main, main merged in at eac68ad (conflicts = identical #638 content, ours kept); CI running; delta review asked (msg_2005ebfd, Claude reviewer: codex out of credits per gm msg_debd9f8a). #635 card apr_5d280244 filed (gm-approved wording msg_9fa83806). Next: #644 card -> merge -> fresh worktree of main -> respawn on gm GO (quiet check) -> report pid/healthz/START turns=. Latency plan due 08:00 Tulum (13:00Z).
- ciwait lesson: filter CI runs by the push time, not a guessed one (the 02:4 filter missed a 02:31 run and waited 3 h).
- 06:20Z: latency plan + unified-log verdict sent to gm (msg_071d0ad8). Asked for GO on (1) the instrumentation PR (tool name/timings + cold|warm in the report) and (2) about 12 probes on cold vs warm and 39 vs 31 tools. Verdict: retire the relay's cross-channel block (arturo-proxy.py:5404 reads a file last written 11-22 with no age gate, and injects its lines as "RECENT").
- 06:40Z: #661 (unified-log 6 h gate, worktree ~/wt/arturo-context-dev/uni, head b35aaf5) MERGE-OK + CI 19/19, card apr_704ba3ae. #644 (eac68ad) MERGE-OK + CI 19/19, card apr_93693b11. #662 (tool timeline + cold|warm, worktree ~/wt/arturo-context-dev/instr, head 6ef1a5f) CI 19/19, review asked (msg_5453b9fc). #635 card apr_5d280244 pending.
- Probe results (scratchpad probe2.py: PROBE_CALLS / PROBE_TOOLS / PROBE_SPENDING=1 for the live 39). The native greets in 1.1-1.9 s; cold gives no reliable penalty; 39 vs 31 tools costs about +0.3-0.6 s warm. The 3.3-4.95 s live greet is mostly outside the native (client -> API bridge -> gateway). Next: time each leg on a real call. Reported to gm (msg_c9443ac9). Spend $0.13 over 15 sessions.
- On approve: squash-merge #661 / #644 (and #635 after main is merged in + CI), then the native respawn from a fresh main checkout on gm GO. #644 merged -> its PR body names the SECOND_READ_MIN_S removal.
