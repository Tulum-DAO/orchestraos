# Handoff: arturo-context-dev (gen row 1997) -> next
- **Timestamp:** 2026-11-25T22:05Z (17:05 Tulum)
- **Working Directory:** /home/shaw/wt/arturo-context-dev/program
- **Merged:** #627 -> main aa84d24 (squash). D1+D4+meta+D2(flag off) = PR #634 (branch feat/agent-call-context, worktree /home/shaw/wt/arturo-context-dev/agents, head 9f57291), review asked of orchestraos-builder (codex-gm); shapes mailed to builder + avatar-dev.

## HAZARD (read first)
- **LIVE native voice runs from /home/shaw/wt/arturo-context-dev/native-live** (detached worktree at aa84d24), pane arturo-native:0.0, pid 3555327 at cutover. DO NOT remove, move or check out anything in that worktree while the pane runs from it (gm condition, card apr_0886cf3e_42878839). The repoint to ~/orchestraos rides orchestraos-builder's main-forward card later.
- Rollback command (old tree), on the card: `ARTURO_NATIVE_GPTLIVE=1 bash /home/shaw/scripts/agent-orchestra/services/arturo/native/run.sh 2>&1 | tee -a /home/shaw/scripts/agent-orchestra/logs/arturo-native.log` via `tmux respawn-pane -k -t arturo-native:0.0`, quiet check first.
- ~/orchestraos (live tree) is 41 commits behind main with 432 dirty entries; never pull it yourself.

## 1. Current Goal & Phase State
- Goal: Arturo's context during/between calls (role prompt). Phases done: gaps 1-4 + past day (#627), native cutover (16:59 Tulum).
- Current step: LATENCY (Shaw's binding condition on the cutover card): measure open->START, START->greet, caller end-of-speech -> first audio on real calls tonight; plan with numbers to gm by Thu 11-26 12:00 Tulum. No latency change live before gm OKs it.

## 2. Open Loops
- [ ] Latency plan to gm by 11-26 12:00 Tulum (proving call greeted 4.95 s live vs 2.44 s probe; live has 39 tools vs 31).
- [ ] PR #634 review -> merge card (mine) -> :5071 relay restart on gm GO at a quiet minute -> prove with one tagged call on a non-Arturo takes-calls agent (opening_tokens > 0, a previous call named). D2 flag: one tagged relay session before default on.
- [ ] Follow-up PR 1 (gm ruling via builder msg_6dc3cd8c): calls=unowned alias for ownerless journals + journal carries agent id; operator name from state/operator.json first.
- [ ] "the operator" wording PR (no name in public repo), separate.
- [ ] Builder follow-up (2): SECRET_RE \S and /i without u flag: its own reviewed change.
- [ ] After cutover: gm wants real-call greet timing logged for a day; raise budget to 3000 only if 1500 shows headroom (msg_b17efda9).
- [ ] codex-arturo PR #630 (test-call finalize guard) rides the packet; not ours to merge.

## 3. Decisions
1. Opening fenced as PAST CONTEXT (both twins + native fenced()): without it, no greet (probe native-90a7e00cd3d1 vs control native-8f4a14b348a8).
2. Native budget default 1500 (calibration: 1500 -> 2.44 s, 3000 -> 3.01 s, 6000 -> 4.3 s). Feed kind default thinking (voiced ~5 s).
3. Probes must stream silence uplink (memory: native-probe-needs-uplink-audio). Probe script: scratchpad probe_native.py (PROBE_* env).
4. Native from a clean worktree, not a pull of the dirty live tree (gm GO msg_8d3b2cf8).

## 4. Declared First Effect
`curl -s http://127.0.0.1:5076/native/healthz && grep -E ": (START|END) " ~/.orchestra/logs/arturo-native.log | tail -6`

## 5. Next 3 Actions
1. Read real-call START/END lines + reports in ~/.orchestra/state/arturo/native/calls/ for greet + turn latency.
2. Chase the PR #634 review; on MERGE-OK file the merge card.
3. Draft the latency plan to gm.

## 6. Canary Questions
1. **Q1:** Which commit sha did #627 squash-merge to, and which approval card authorized it? (msg_a794504c_44057527)
2. **Q2:** From which directory does the live native run, and why not ~/orchestraos? (msg_90b1cede_43812604)
3. **Q3:** What did the thread-off control session show versus the unfenced thread session? (msg_fd4c1f22_39733643)
4. **Q4:** What budget default was chosen and what greet times backed it? (msg_028a2555_41908919)
5. **Q5:** What did Shaw's cutover answer say, and how did gm rule on it? (msg_71c963d4_43642847)
