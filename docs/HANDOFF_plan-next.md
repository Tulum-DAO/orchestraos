# Handoff: plan -> (STOPPED FOR OPERATOR DECISION — brief only, no build authorized)
- **Lineage:** plan (Gen 1)
- **Timestamp:** 2026-09-29T10:15:00Z
- **Working Directory:** /Users/flybyflow/orchestraos
- **Last Commit SHA:** f1d1b8b

## 1. Current Goal & Phase State
- **Goal:** gm task "Product brief: replace OrchestraOS's built-in Telegram bridge
  with Hermes+Matrix" (msg_1f3acb80_74023124) — BSHR research, product brief,
  CEO review, eng review, report back to gm. Explicitly brief-only, no implementation.
- **Plan Reference:** `docs/PLAN_hermes-matrix-telegram-bridge.md`
- **Phase:** Complete. Both CEO review and eng review ran, each caught and fixed a
  real error (outside-voice caught a wrong risk framing in the original
  recommendation; eng review then caught that CEO review's own CRITICAL GAP was
  itself overstated). Reported to gm via msg_store reply to msg_1f3acb80_74023124.
- **Current Step:** None — this round's work is done. Waiting on the operator's
  one remaining decision (brief §5): harden `router.py` in place (path a,
  recommended, ships now) vs. build a connector against Hermes's experimental
  relay contract (path b, real new engineering, only if the operator specifically
  wants fleet-command traffic on Hermes's own infra).

## 2. Open Loops & Active Callbacks
- [ ] Wait for gm/operator to pick path (a) or (b) from brief §5. Do not self-scope
  further engineering work from this brief without that answer — it determines
  almost everything downstream (whether the eng review's 4 proposed tests are the
  whole job, or whether a much bigger Node/TS connector build is in scope).
- [ ] If (a): the brief's own Test Review section already has 4 concrete pytest
  tests scoped and one real open verification item (does `router.py`'s
  `handle_update` drop-vs-halt on a mid-batch exception?) — ready to hand to build
  once approved, no further review needed.
- [ ] If (b): needs its own eng review at implementation-ready depth before any
  build — this brief only reviewed it strategically (it's a new client-server
  service against an EXPERIMENTAL contract, not an extension of router.py).
- [ ] Separately unresolved, not blocking: the "Elon orchestration layer" claim
  couldn't be verified — the brief recommends the operator clarify the source
  directly rather than have it repeated as rationale.

## 3. Decisions Made & Rationale
1. **Decision:** recommended path (a) — harden `router.py` in place — over the
   originally-drafted "point Hermes's native Telegram adapter at the bot token."
   **Rationale:** outside-voice review (dispatched via Agent tool, Plan subagent,
   read-only) verified Hermes is agent-first by design (its own README: "the
   self-improving AI agent... closed learning loop") with no passive/relay-only
   mode in its Telegram adapter (grepped, confirmed none) — pointing it at the
   bot token would make Hermes itself the responding persona, not a transport for
   gm. Verified this myself against the code before accepting the correction,
   per this seat's re-derive standard, rather than taking the review at its word.
2. **Decision:** corrected the CEO review's own "CRITICAL GAP" (offset/missed-
   message persistence) during eng review. **Rationale:** read `router.py`'s
   `State` class directly — it already does atomic tmp-file + `os.replace`
   durable writes (lines 101-112) and `poll_once()` only advances the offset
   after processing each update (line 323). The gap was never real for path
   (a); it only would have mattered for a *new* relay (path b).
3. **Decision:** HOLD SCOPE mode for CEO review, self-decided non-interactively.
   **Rationale:** `prompts/plan.md` instructs deciding toward the narrowest scope
   when no human is present to answer AskUserQuestion; this is a transport-layer
   infra swap, not a new user-facing feature.
4. **Decision:** did not re-run a second full outside-voice pass for eng review.
   **Rationale:** the same document had already been through one adversarial
   pass minutes earlier that materially changed the plan; a second pass against
   largely the same content would be redundant cost without new signal — logged
   explicitly as a deliberate efficiency call, not a silently skipped step.

## 4. Declared First Effect
None — this round has no "first effect" in the build sense; it's a completed
brief awaiting an operator decision. The next agent to touch this thread should
read `docs/PLAN_hermes-matrix-telegram-bridge.md` in full (especially §4/§4a/§5
and the GSTACK REVIEW REPORT's UNRESOLVED DECISIONS) before doing anything, and
should NOT self-select path (a) or (b) — that's the operator's call, explicitly
left open by both reviews.

## 5. Next 3 Immediate Actions
1. Wait for gm/operator direction on path (a) vs (b) (brief §5). Do not
   self-initiate a build handoff from this thread alone.
2. If path (a) is confirmed: hand `docs/PLAN_hermes-matrix-telegram-bridge.md`'s
   ENG REVIEW → Section 3 (Test Review) directly to build — it's already scoped
   at implementation-ready detail (4 tests, file:line references, one open
   verification item to resolve first).
3. If path (b) is confirmed: this needs a fresh eng review at implementation-
   ready depth before any build — the current brief only covers it strategically.

## 6. Grounding Canary Questions (Questions Only — No Answers!)
1. **Q1:** What three pieces of file-level evidence (README framing, adapter
   callback-handler dispatch, absence of a passive-mode flag) does the outside-
   voice review cite to establish Hermes's Telegram adapter cannot act as a dumb
   relay (jsonl regarding the CEO review's §4a correction)?
2. **Q2:** What specific code (class, method, line range) in `router.py` did eng
   review read to overturn the CEO review's own CRITICAL GAP finding about
   offset/missed-message persistence (jsonl regarding the Eng Review Scope
   Challenge section)?
3. **Q3:** Why did this seat choose NOT to re-run a second outside-voice pass for
   the eng review stage, and where in the plan file is that choice recorded
   (jsonl regarding the Outside Voice section under ENG REVIEW)?
4. **Q4:** What is the one real open verification item eng review flagged that it
   could NOT resolve by reading code alone, and why does the brief call it
   higher-value than anything Hermes-related (jsonl regarding Section 3's
   closing paragraph)?
5. **Q5:** What prior, already-written decision in a different config file (not
   this brief) did this seat cite as direct evidence for the dedicated-instance
   topology recommendation, rather than reasoning from first principles (jsonl
   regarding brief §2's "Operational-boundary risk" finding)?
