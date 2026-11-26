# Handoff: arturo-context-dev (gen row 2013) -> next
- **Timestamp:** 2026-11-26T07:10Z (02:10 Tulum)
- **Working Directory:** /home/shaw/wt/arturo-context-dev/program (branch fix/arturo-call-context; baton only)
- **Last Commit SHA:** see `git log -1` on fix/arturo-call-context (this file). The previous baton text is in git history (a3c2f10).

## HAZARD (read first)
- **LIVE native voice** runs from the detached worktree **/home/shaw/wt/arturo-context-dev/native-live** (aa84d24), pane arturo-native:0.0, pid 4106439, env ARTURO_NATIVE_CONTEXT_TOKENS=2500. Do not remove, move or check out anything in that worktree while it runs. The next respawn is from a FRESH clean checkout of main, on gm's GO at a quiet minute (quiet check: healthz calls 0, no gateway gptlive lines and no api.log [voice-ws] lines in the last 60 s, no sockets on :5076). Use `tmux respawn-pane -k` only.
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
  The bee255c commit (legs) hasn't been reviewed yet. Mail builder the new head for one delta review.
- Probe runner: /home/shaw/wt/arturo-context-dev/probe2.py (run from a worktree root). Knobs: PROBE_CALLS, PROBE_TOOLS, PROBE_SPENDING=1 (the live 39 tools, declare-only); a live run needs `live` + PROBE_LIVE_OK=1.

## 2. Open loops (in order)
- [ ] Fold #662's 2 blockers, push, mail builder, then CI, then its card (draft to gm first; gm approved the earlier card style).
- [ ] On each approval: squash-merge with `--match-head-commit`, note the card with the sha, ack it, and tell gm the sha.
- [ ] After #644 (and #662 if approved) merge: one respawn from a fresh worktree of new main on gm's GO. Report pid, healthz, the first real call's START (turns=, start=, connect_s=) and the report's legs.
- [ ] From the first real call after the respawn: split the 3.27 s after-START leg into provider (session_started -> first_transcript) vs return path. Then propose to gm. 2(b) (8 spending tools, +0.3-0.6 s) waits on those numbers; 2(a) warm-up connect was DROPPED; 3(a)/3(b) wait for tool_timeline from real calls.
- [ ] gm msg_4d4c80f1: 3 probes with a recorded ROOM-NOISE uplink (20-30 s of quiet room, no speech) instead of digital silence, before the next real call. If open -> words moves toward 3 s, the provider's noise wait is the leg (fix: speak on session.started). If it stays under 2 s, the leg is the return path or the post-START appends. Report with the #662 result, one mail. LIVE GO (gm msg_72f2b0ba): 3 test-tagged sessions, ONE set, cap 3 (more needs a new GO), PROBE_LIVE_OK=1, about $0.03, side port, nothing to Shaw's phone or chat. UPLINK: from ~/pocket-recordings/<date>-<slug>/ (not the Mac): cut 20-30 s of NON-speech (the lead-in before anyone talks, or a pause), confirm with that recording's transcript that the window has no words, then convert with ffmpeg to 16 kHz mono s16le. Add a PROBE_UPLINK=<file> knob to probe2.py that streams that file in 100 ms frames instead of zeros. Label the probe with the source file. ORDER: #662's two test fixes first, then this set; ONE mail to gm with both.
- [ ] Follow-ups (non-blocking review notes): #645's gap-from-previous-phrase + non-ASCII tail case; #661's per-line ts / doctor-row test / dated comment; calls=unowned alias + state/operator.json first; "the operator" wording; SECRET_RE flags.
- [ ] A leftover waiter (pid 3406272, `while pgrep ... services/arturo`) from gen 1997's session loops on other seats' pytest; it isn't mine to kill.

## 3. Decisions
1. Second read = route-only, budget*25/turns capped 4000, inside READ_DEADLINE_S 5.0, cut to the newest 25 (#638). Page thread and calls are local reads and keep their second read (#644).
2. Turn = exchange: fold by role+name, same call, 120 s gap, a long merged turn keeps its tail (#645).
3. Unified log: GATED (6 h), not deleted (gm agreed): the relay's own turns aren't call journals.
4. Latency: the honest zero is the API's /api/voice/native/ready line. Client started_at = receipt of the native "state" frame (Mac clock, after START).
5. CI waits: filter runs by the push time (a guessed filter once cost 3 h).

## 4. Declared First Effect
`cd /home/shaw/wt/arturo-context-dev/instr && git log --oneline -1 && timeout 60 gh api repos/Tulum-DAO/orchestraos/issues/662/comments --jq '.[-1].body' | head -40`

## 5. Next 3 Actions
1. Fold #662's two blockers red-first (distinct times for requested/ready/result; ready_s None; drop the msg id), push, mail orchestraos-builder the head.
2. Watch for Shaw's answers on apr_93693b11 / apr_704ba3ae / apr_5d280244; merge each on approve (#635 needs main merged in + CI first).
3. After #644 merges: draft the respawn GO request to gm (fresh worktree of main, quiet check, rollback = the same command from native-live).

## 6. Canary Questions
1. **Q1:** Which squash shas did #638 and #645 merge to, and which heads were merged? (msg_fb2558d1_72999770)
2. **Q2:** What was the honest zero for live greet timing, and what was the after-START leg? (msg_054524cc_75089803)
3. **Q3:** What did the probe set show for cold vs warm and for 39 vs 31 tools, and what did it cost? (msg_c9443ac9_74528544)
4. **Q4:** Why was #661 gated rather than deleted, and what window does it use? (msg_c9443ac9_74528544)
5. **Q5:** What were the two blockers on #662 at 6ef1a5f? (msg_b53b5a7b_74988286)
