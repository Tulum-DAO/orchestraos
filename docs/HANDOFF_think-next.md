# Handoff: think -> plan
- **Lineage:** think (Gen 1)
- **Timestamp:** 2026-09-21T09:58:00Z
- **Working Directory:** /Users/flybyflow/orchestraos (task target repo: /Users/flybyflow/duelo-de-dibujo)
- **Last Commit SHA (orchestraos):** 6ce2aca

## 1. Current Goal & Phase State
- **Goal:** gm task "Gamify duelo-de-dibujo Duolingo-style — 10x round 3"
  (msg_6597ae35_83688579). Duolingo-inspired gamification pass targeting a real,
  specific reluctant-to-study kid the operator knows personally. Pipeline runs
  through Ship (branch + PR) only — no land-and-deploy/merge, gated behind gm's
  operator approval card, same hard-stop as prior rounds.
- **Plan Reference:** design doc at
  `~/.gstack/projects/flybyflow-duelo-de-dibujo/flybyflow-main-design-20260921-094639.md`
  (repo copy: `/Users/flybyflow/duelo-de-dibujo/DOCS/designs/gamification-round3.md`,
  currently **untracked/uncommitted** — Build should commit it on whatever new
  branch it cuts; current checkout is on `main`, clean, v3 already merged).
- **Phase:** Think stage complete. Handing off to Plan.
- **Current Step:** None (Think's work is done)

## 2. Open Loops & Active Callbacks
- [ ] Plan should run `/plan-ceo-review` on **Open Question #1**: whether Approach
      B (a forgiving, device-level weekly practice counter — the closest safe
      analog to a "streak" the retention research actually supports) ships this
      round or is held pending a real-kid playtest of the core fix. Default in the
      doc is: hold it.
- [ ] **This round has a real ethical dimension CEO review needs to weigh
      explicitly, not just an ambition dimension.** I'm recommending the design
      hold a hard line: no loss-framed streak mechanics, no guilt-based push
      notifications, no cloud data collection — even though the operator's brief
      used strong "addictive/game fight the fuck out of this" language that could
      be read as asking for exactly those. I diverged from the most literal
      reading of the brief on purpose (see the design doc's Constraints, Premise
      5, and "What I noticed about how you think" section) because the app
      structurally cannot support a solo daily streak today (requires a co-present
      second player) and the retention research itself says naive streaks backfire.
      Plan/CEO review can override this, but it should be an explicit decision,
      not something that happens by default because the brief was emphatic.
- [ ] `/plan-eng-review` needs to lock: the solo-mode server response schema (no
      `precision_winner`/`vibe_winner` pair when there's one drawer — Open
      Question #2), and confirm the client-side "blast radius" fix (solo verdicts
      skip the p1/p2 tally and share card, per Approach A) before Build starts —
      this was a real gap the adversarial review caught in round-1 of my own
      review loop and is now fixed in the doc, but it's exactly the kind of thing
      worth Eng review double-checking given it touches 4 existing functions
      (`recordDuel`, `renderVerdict`, `shareCard`, history storage).
- [ ] Flagged but not re-litigated: PDR item #5 (cross-instance KV rate limiting)
      was one of the PDR's own "recommended for v2" items and was **never actually
      shipped** — `api/judge.js`'s rate limiter is still in-memory/single-instance.
      Not gamification-relevant, but it's an unshipped prior commitment, not a
      deliberate deferral, and worth someone picking up eventually.

## 3. Decisions Made & Rationale
1. **Decision:** Recommended Approach A (a genuine solo-practice mode for the
   trace/letter verticals, `drawingB` optional) as the load-bearing change, NOT a
   Duolingo-style daily streak or XP/level treadmill. — **Rationale:** the app's
   core loop structurally requires two co-present players (`judgeBtn` stays
   disabled until both photos exist; the server hard-requires both). Duolingo's
   single most effective retention lever assumes solo, on-demand access. Porting
   it literally means her "streak" breaks whenever her duel partner isn't
   available — not her choice, and exactly the kind of unforgiving-streak
   abandonment failure mode current retention research flags, worse for a kid. An
   independent cold-read subagent reached the same diagnosis and proposed the same
   technical fix independently enough to treat as corroboration (see the doc's own
   transparency note on how "independent" that read actually was).
2. **Decision:** Held a firm ethical line (no loss-framing, no guilt notifications,
   no cloud data) even though it means not fully complying with the most literal
   reading of the operator's "addictive like crack" language. — **Rationale:**
   this targets one specific, personally-known real child, which raises the
   stakes on manipulative engagement mechanics rather than lowering them for being
   "just a hobby app." The app already has a "never mean, these are children" rule
   baked into the judge prompt; I extended that principle to the gamification
   layer rather than treating strong language in the brief as license to override
   it. Flagged explicitly for CEO review to weigh, not silently decided.
3. **Decision:** Ran 2 rounds of adversarial subagent review (6/10 → 9/10) before
   treating the design doc as ready, same methodology as the prior two rounds. —
   **Rationale:** the first pass caught real gaps (an entire client-side rendering
   blast radius left unaddressed, an arithmetic error, an ambiguous
   recommendation, understated technical complexity, an unresolved data-model
   question) that would have cost real rework if handed to Plan/Build as-is.

## 4. Declared First Effect
Plan reads `~/.gstack/projects/flybyflow-duelo-de-dibujo/flybyflow-main-design-20260921-094639.md`
(or the identical repo copy) in full and runs `/plan-ceo-review` against it,
specifically on Open Question #1 (Approach B) and the ethical-line question named
above, as its first action.

## 5. Next 3 Immediate Actions
1. Plan seat: read the design doc, run `/plan-ceo-review` on the two flagged
   decisions (Approach B inclusion, the ethical-line divergence from the literal
   brief), then `/plan-eng-review` to lock the solo-mode response schema and
   verify the client-side blast-radius fix.
2. Plan seat: on completion, write `docs/HANDOFF_plan-next.md`, message `build`
   per its own handoff contract, and report to gm's inbox at its own handoff
   (gm specifically flagged that a prior round's seat skipped this and needed a
   manual nudge — don't repeat that).
3. Build seat (later): cut a fresh branch off `main` (clean, v3 already merged)
   and commit the design doc's repo copy on it.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** Why does the design doc treat Duolingo's daily streak as structurally
   incompatible with Duelo's core loop, and what specific two functions in
   `public/index.html` does it cite as proof the app requires two co-present
   players (jsonl regarding the Problem Statement / Premise 2 edits)?
2. **Q2:** What did the first adversarial review pass score the design doc, and
   what six issues brought that score down before the fix pass (jsonl regarding
   the "Review Duelo de Dibujo gamification design doc" agent result)?
3. **Q3:** Which four functions in `api/judge.js` does the finalized Approach A
   name as needing genuine solo variants, not just an optional field (jsonl
   regarding the Approach A Cons edit)?
4. **Q4:** What arithmetic error did the first review pass catch regarding the
   replay-history ring buffer, and what is the corrected number (jsonl regarding
   the "29th duel" / "9th duel" edit)?
5. **Q5:** What ethical lines does the design doc recommend holding even under
   direct instruction to do otherwise, and why does it treat this round as higher
   ethical stakes than the prior two (jsonl regarding Constraints / Premise 5 /
   the Cross-Model Perspective's refusal list)?
