# Handoff: arturo-context-dev (gen row 2041) -> next
- **Lineage:** arturo-context-dev (gen row 2041 -> next)
- **Timestamp:** 2026-11-26T23:58Z (18:58 Tulum)
- **Working Directory:** /home/shaw/wt/arturo-context-dev/program (branch fix/arturo-call-context; baton only)
- **Last Commit SHA:** `git log -1` on fix/arturo-call-context (this file). Older baton text: git history (fdf68b5 and before).

## HAZARD (read first)
- **LIVE native voice**: pane arturo-native:0.0, pid 3390560, from the detached worktree **/home/shaw/wt/arturo-context-dev/native-main** (main 7c659c7), env ARTURO_NATIVE_CONTEXT_TOKENS=2500. Rollback = the same command with `cd /home/shaw/wt/arturo-context-dev/native-live` (aa84d24). Never remove/move/checkout either worktree. Respawn ONLY on gm's GO, quiet check first (`curl -s 127.0.0.1:5076/native/healthz` calls 0; no ESTAB on :5076; no gptlive lines in ~/.orchestra/logs/gateway.log or [voice-ws] in api.log in the last 60 s), `tmux respawn-pane -k -t arturo-native:0.0 "<cmd>"` with only the cd changed, then verify the NEW pid owns :5076 (`ss -ltnp | grep :5076`). Current cmd: `tmux display -p -t arturo-native:0.0 '#{pane_start_command}'`.
- ~/orchestraos (the live tree, proxy + API) is far behind main and dirty: never pull it. Proxy changes go live only via orchestraos-builder's main-forward.
- Git guard: merging main into a branch needs `LAND_CHAIN=<every non-merge sha in HEAD..origin/main, 10-char, comma>` as its own command, no `2>&1`.
- One `msg_store.py send` per Bash call (send-shape guard). `--body-file` always.
- Card `note` subcommand: ~/scripts/agent-orchestra/scripts/approval.py; `request`/`get`/`ack`/`pending --grep`: /home/shaw/orchestraos/scripts/approval.py.
- **#635 is HELD, never merge it**: Shaw's approve on apr_5d280244 was a MIS-TAP = deny (gm msg_a3b953b0). The approval loop keeps re-delivering it; ack and ignore.
- **Codex**: gate = `CODEX_HOME=<codex_home in ~/.orchestra/state/codex-availability.json> codex exec --sandbox read-only -C <worktree> "<prompt>"` (background). At ~23:50Z BOTH logins said "workspace out of credits": retry after 15-30 min; Claude fallback only if it stays out, naming the window (memory review-provider-and-limit-reporting).

## 1. Current Goal & Phase State
- **Goal:** Shaw's voice-call dead air (gm msg_14008183, HIGH). Leg 1 = gm_command held the call 25 s polling gm (FIX #674). Leg 2 = the backend tool decision slows with call length (T2 arturo-latency-dev). Findings sent to gm msg_f724c3c4.
- **Dates (gm msg_d9158653, Shaw: accelerate):** #674 live 23:00 Tulum TONIGHT (needs Shaw's approve by ~22:30 + respawn); leg-2 probe numbers 21:00 Tulum tonight (T2), fix PR 10:00 tomorrow.

## 2. Open Loops (in order)
- [ ] **#674** (fix/native-gm-no-wait-main, worktree ~/wt/arturo-context-dev/gm-main, head 5886187, base MAIN; #673 closed as its stacked twin). codex MERGE-OK (same lines at 70fa227 + 1e6b77c). CI was 17/19 at 23:53Z. NEXT: when 19/19 by name -> file the card with gm's approved draft (scratch gm-dead3.txt item NEXT-1, text also in msg_f724c3c4; keep "Decide by: 22:00 Tulum") via surface-decision lane 1, `--from arturo-context-dev --op-key pr674-merge-5886187`, list every CI job by name -> on approve `gh pr merge 674 --squash --match-head-commit 588618743a31b919a67ceae62d89423ed2db9013`, note + ack the card -> fresh `git worktree add --detach ~/wt/arturo-context-dev/native-<sha> origin/main` -> quiet check -> respawn (rollback = native-main) -> report pid/healthz to gm.
- [ ] **#662** card apr_3867bdee (head 1263773, codex MERGE-OK, CI 19/19) is PENDING with Shaw. On approve: `gh pr merge 662 --squash --match-head-commit 12637730ae5506744701a6bb5218725746bd317c`, note + ack, then a second quiet respawn. #662's text-side line (arturo-proxy build_context) needs the relay restart (builder).
- [ ] **#675** (fix/native-journal-tools, worktree ~/wt/arturo-context-dev/journal, head 8b96927, base main): a native call's journal counts its tool runs from the native tool ledger; dedup by call_id after codex probed a re-notify double count. Codex review was CUT OFF (credits): re-run codex on the full diff `git diff origin/main..HEAD`, then CI by name, then mail orchestraos-builder (it rides their packet; told in msg_56ecc6ab; builder g64 plan msg_38d54b72: verdict at the EXACT head + CI 19/19 by name -> my card -> after merge the relay restart rides their next packet window on its own gm GO; builder noted c3c0d1a, head is now 8b96927, so the gate must be at 8b96927). gm wants it on the same card lane as the proxy main-forward.
- [ ] **T2 arturo-latency-dev** (spawned by me, cwd ~/wt/arturo-latency-dev/instr = feat/native-opening-read c3e15e4 pushed: opening_read_s + delegated_s, stacked on #662). GO sent msg_39ee300a: 3 spoken-uplink probes, PROBE_DURATION_S=40, cap 3; scope (a) backend reasoning setting, (b) trim the backend's copy of the opening, (c) instrumentation PR after #662 merges. Numbers 21:00 Tulum; it warns by 20:30 if late. Relay its results to gm.
- [ ] Parked: 2 s DOWNLINK-BACKLOG at call start (gm: park). Framing fix (#662) proof = the next real call greets from its thread.

## 3. Decisions Made & Rationale
1. **gm_command detaches after 1.5 s; a detached id stays detached** — Rationale: the 25 s poll was most of each silence; codex BLOCK showed gm's answer could arrive twice.
2. **#674 rebased onto main as a fresh PR (no force-push)** — Rationale: ship without waiting on #662's card; same changed lines verified per file.
3. **Journal tool count read from the native ledger in finalize (proxy), not a native-written shard** — Rationale: a non-ended shard is picked up by the journal watchdog and injected into gm; the ledger has no race.
4. **Parallel tracks on named T2s** — gm rule msg_92b9405e (Shaw).

## 4. Declared First Effect
`timeout 60 gh pr checks 674 --repo Tulum-DAO/orchestraos | awk -F'\t' '{print $2"\t"$1}' | sort`

## 5. Next 3 Immediate Actions
1. If #674 is 19/19 by name: file its card (draft in msg_f724c3c4), then watch for Shaw's answer.
2. Retry codex on #675 (`git -C ~/wt/arturo-context-dev/journal diff origin/main..HEAD`), then CI, then mail builder the verdict.
3. Read inbox for arturo-latency-dev's 21:00 numbers and gm's replies; relay leg-2 numbers to gm.

## 6. Grounding Canary Questions
1. **Q1:** Which tool held each silence on Shaw's 18:13 call, and for how long? (msg_77cd078d_35276618)
2. **Q2:** What did codex's BLOCK on #673 find, and how was it fixed? (msg_f724c3c4_36813155)
3. **Q3:** Why is #635 held even though the approval loop says "approve"? (msg_a3b953b0_78129236)
4. **Q4:** Which seat owns the backend tool-decision leg, and what probe limits did it get? (msg_39ee300a_37124719)
5. **Q5:** When must #674 be live, and on what condition? (msg_446079f3_37128040)
