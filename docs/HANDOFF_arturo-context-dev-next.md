# Handoff: arturo-context-dev (gen row 2041) -> next
- **Timestamp:** 2026-11-26T07:15Z (02:15 Tulum)
- **Working Directory:** /home/shaw/wt/arturo-context-dev/program (branch fix/arturo-call-context; baton only)
- **Last Commit SHA:** see `git log -1` on fix/arturo-call-context (this file). The previous baton text is in git history (a3c2f10).

## HAZARD (read first)
- **LIVE native voice** runs from the detached worktree **/home/shaw/wt/arturo-context-dev/native-main** (7c659c7) since 07:18Z 11-26, pid 3390560; the rollback is **/home/shaw/wt/arturo-context-dev/native-live** (aa84d24, formerly live as pid 4106439), pane arturo-native:0.0, env ARTURO_NATIVE_CONTEXT_TOKENS=2500. Do not remove, move or check out anything in that worktree while it runs. The next respawn is from a FRESH clean checkout of main, on gm's GO at a quiet minute (quiet check: healthz calls 0, no gateway gptlive lines and no api.log [voice-ws] lines in the last 60 s, no sockets on :5076). Use `tmux respawn-pane -k` only.
- ~/orchestraos (the live tree) is far behind main and dirty: never pull it yourself.
- The git guard refuses merges that drag commits. Name them: `LAND_CHAIN=<sha>,<sha> git merge --no-ff --no-edit --signoff <ref>` (run it as its own command, no `2>&1`).
- **Codex is out of workspace credits** (gm msg_debd9f8a, 00:53 Tulum 11-26): gating reviews go to a fenced Claude reviewer via orchestraos-builder; cards say "Claude reviewer (codex out of credits)".
- The live API log is ~/.orchestra/logs/api.log (pid on :8888). The native log rotated 01:03; older days are in arturo-native.log-*.gz.
- The card `note` subcommand exists only in ~/scripts/agent-orchestra/scripts/approval.py; `request` / `get` / `ack` work from /home/shaw/orchestraos/scripts/approval.py.

## 1. State
- MERGED today: #638 -> 1f43f67, #645 -> 9a7c9fb (Shaw's go via gm; cards noted and acked).
- Cards on Shaw's phone (they ride the 07:57 briefing; gm does not re-ask):
  - apr_93693b11 **#644** page thread: head eac68ad, MERGE-OK, CI 19/19. Worktree ~/wt/arturo-context-dev/turns.
  - apr_704ba3ae **#661** unified-log 6 h gate: head b35aaf5, MERGE-OK, CI 19/19. Worktree ~/wt/arturo-context-dev/uni.
  - apr_5d280244 **#635** takes-calls agents' opening: head e290b1e, codex MERGE-OK 59baa8a, CI 19/19. Worktree ~/wt/arturo-context-dev/agents. Main has moved since: merge main in and re-run CI before merging.
- **#662** (tool timeline + cold|warm + per-leg timing, head bee255c, worktree ~/wt/arturo-context-dev/instr): **BLOCKED at 6ef1a5f** (builder msg_b53b5a7b, two cheap fixes, NOT done yet):
  1. test_native.py around 1449: requested, ready and result all land at 1.0. Feed the call, the terminal and the release at three distinct times and assert each; assert ready_s is None in the no-result test.
  2. test_native.py around 1434: drop `msg_be42d949` from the comment.
  Non-blocking (your call): an offline first call counts as cold; no session.started gives raw monotonic values (return None); speech already under way at result time is never stamped; first_audio_s adds two rounded numbers; `start` beside `started`.
  **UPDATE: bee255c reviewed, BLOCK(3), all test-only (builder msg_4b0f2254; the legs code is clean):**
  1. ready_s, as above: three distinct times (settle= with a real quiet_s, or a tool that returns RUNNING first); assert each; ready_s None in the no-result test.
  2. Remove BOTH msg ids from new test text: `msg_be42d949` (~:1434) and `msg_3a21b3e6` (~:1507).
  3. The legs test (~:1538) passes even when all six legs are equal. Have transport_factory and thread_loader sleep ~0.05 s, assert each leg >= 0.04 s after the one before, and assert START carries connect_s=<provider_connected_s> start_s=<start_s>.
  Non-blocking: first_transcript_s counts only Arturo's shown transcript (document it); legs.first_audio_s is provider arrival, not the send to the phone; two first_audio_s keys with different zeros; server.py ~:884 line is ~170 chars (wrap it).
  Next head = one delta review; card after MERGE-OK + CI green by name.
- Probe runner: /home/shaw/wt/arturo-context-dev/probe2.py (run from a worktree root). Knobs: PROBE_CALLS, PROBE_TOOLS, PROBE_SPENDING=1 (the live 39 tools, declare-only); a live run needs `live` + PROBE_LIVE_OK=1.

## 2. Open loops (in order)
- [ ] #662: MERGE-OK at e8f92ff (builder msg_0ee9aac9); flake margin closed at dc01594 (pushed 07:11Z, test-only). NEXT: CI green by name on dc01594 -> draft the card to gm -> file. Follow-ups queued: test_native_uplink freeze test 0.3 s budget; test_spending_native timestamp-float flake (builder msg_8a12f0ef: compare content fields, not the ledger dump).
- [x] MERGED 07:12Z: #644 -> 03c4506, #661 -> 81bcb24 (cards apr_93693b11 / apr_704ba3ae noted + acked). RESPAWNED 07:18:37Z on gm GO msg_18217dde: pid 3390560 owns :5076 from ~/wt/arturo-context-dev/native-main (detached 7c659c7); pane pid 3390558; reported msg_25ee2419. LIVE NOW = native-main; rollback = same command with cd native-live (keep both worktrees). Owed: the first real call's START (turns=, start=, connect_s=) + report thread source/legs to gm = the page-thread proving call; if it fails to open, roll back without asking and tell gm. #662 ships in a SECOND quiet respawn after its card. #661 reaches the relay only via a proxy restart (builder's main-forward).
- [ ] On each approval: squash-merge with `--match-head-commit`, note the card with the sha, ack it, and tell gm the sha.
- [ ] After #644 (and #662 if approved) merge: one respawn from a fresh worktree of new main on gm's GO. Report pid, healthz, the first real call's START (turns=, start=, connect_s=) and the report's legs.
- [ ] From the first real call after the respawn: split the 3.27 s after-START leg into provider (session_started -> first_transcript) vs return path. Then propose to gm. 2(b) (8 spending tools, +0.3-0.6 s) waits on those numbers; 2(a) warm-up connect was DROPPED; 3(a)/3(b) wait for tool_timeline from real calls.
- [x] Room-noise set DONE (3 live sessions, cap used; $0.125, ~4x the estimate). Uplink: pocket 2026-11-23-guest-bed-and-towels-setup 27:15-27:40, file scratch roomnoise.s16le (re-cut with ffmpeg -ss 1635 -t 25 -ac 1 -ar 16000 -f s16le). Greet 2.67 cold / 2.18, 2.38 warm vs zeros 1.54 / 1.68, 1.88. session.started->first transcript 0.73-1.06 s. Reported to gm msg_eb90874f. Controls DONE on gm GO msg_95a63fab (3 zeros, $0.125; cost is billed seconds at the 50 s default, NOT noise: use PROBE_DURATION_S=15). Table + read sent msg_c3ce9616: START->session.started grows most with noise (+0.35 s) = provider setup, leave it; one zero call held START 4.19 s on the opening read (twin). Asked gm OK for an opening_read_s leg follow-up PR. No more live probes without a new GO.
- [ ] Follow-ups (non-blocking review notes): #645's gap-from-previous-phrase + non-ASCII tail case; #661's per-line ts / doctor-row test / dated comment; calls=unowned alias + state/operator.json first; "the operator" wording; SECRET_RE flags.
- [ ] A leftover waiter (pid 3406272, `while pgrep ... services/arturo`) from gen 1997's session loops on other seats' pytest; it isn't mine to kill.

## 3. Decisions
1. Second read = route-only, budget*25/turns capped 4000, inside READ_DEADLINE_S 5.0, cut to the newest 25 (#638). Page thread and calls are local reads and keep their second read (#644).
2. Turn = exchange: fold by role+name, same call, 120 s gap, a long merged turn keeps its tail (#645).
3. Unified log: GATED (6 h), not deleted (gm agreed): the relay's own turns aren't call journals.
4. Latency: the honest zero is the API's /api/voice/native/ready line. Client started_at = receipt of the native "state" frame (Mac clock, after START).
5. CI waits: filter runs by the push time (a guessed filter once cost 3 h).

## 4. Declared First Effect
`python3 /home/shaw/.orchestra/msg_store.py inbox --agent arturo-context-dev --limit 10` (builder's #662 delta verdict on e8f92ff; gm's GO on the control probes)

## 5. Next 3 Actions
1. Read builder's #662 delta verdict at e8f92ff; on MERGE-OK + CI green by name, draft the card to gm.
2. Watch for Shaw's answers on apr_93693b11 / apr_704ba3ae / apr_5d280244; merge each on approve (#635 needs main merged in + CI first).
3. After #644 merges: draft the respawn GO request to gm (fresh worktree of main, quiet check, rollback = the same command from native-live).

## 6. Canary Questions
1. **Q1:** Which squash shas did #638 and #645 merge to, and which heads were merged? (msg_fb2558d1_72999770)
2. **Q2:** What was the honest zero for live greet timing, and what was the after-START leg? (msg_054524cc_75089803)
3. **Q3:** What did the probe set show for cold vs warm and for 39 vs 31 tools, and what did it cost? (msg_c9443ac9_74528544)
4. **Q4:** Why was #661 gated rather than deleted, and what window does it use? (msg_c9443ac9_74528544)
5. **Q5:** What were the two blockers on #662 at 6ef1a5f? (msg_b53b5a7b_74988286)
