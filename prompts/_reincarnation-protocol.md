# Lineage Reincarnation Protocol — Universal Auto-Rotation

When your context window reaches operational thresholds, the Lineage Daemon coordinates seamless reincarnation to your successor across Claude Code, Gemini CLI, and Codex runtimes. Follow this protocol EXACTLY.

## 1. DETECT: Context Thresholds

Watch your context percentage (via status bar or token telemetry):
- **< 70% (Green / OK):** Normal execution. Maintain clean atomic commits.
- **70% - 79.9% (Yellow / SOFT TIER):** Soft authoring threshold. Lineage Daemon emits `lineage_soft_handoff`. Complete your immediate atomic step and author your structured handoff document.
- **>= 80% (Red / HARD TIER):** Hard rotation threshold. The Lineage Daemon automatically spawns your successor and initiates the rotation sequence.

## 2. WRITE: Structured Handoff (`docs/HANDOFF_<agent>-next.md`)

When context crosses 70% (or upon `lineage_soft_handoff`), write and git commit `docs/HANDOFF_<agent>-next.md` adhering to the Gold Standard schema:

```markdown
# Handoff: {your-agent-id} -> {successor-agent-id}
- **Lineage:** {lineage_root} (Gen {N} -> Gen {N+1})
- **Timestamp:** {ISO8601}
- **Working Directory:** {cwd}
- **Last Commit SHA:** {sha}

## 1. Current Goal & Phase State
- **Goal:** {High level active objective}
- **Plan Reference:** `docs/PLAN_{feature}.md`
- **Phase:** Phase {N} of {M} ({Phase Name})
- **Current Step:** {Exact step in progress}

## 2. Open Loops & Active Callbacks
- [ ] Loop 1: {Pending item / waiting response / callback}
- [ ] Loop 2: {Task awaiting external event}

## 3. Decisions Made & Rationale
1. **Decision:** {What was decided} — **Rationale:** {Why}
2. **Decision:** {Architecture choice} — **Rationale:** {Why}

## 4. Declared First Effect
`{Exact first action, file edit, or verification command the successor must produce}`

## 5. Next 3 Immediate Actions
1. `{First concrete action}`
2. `{Second action}`
3. `{Third action}`

## 6. Grounding Canary Questions (Questions Only — No Answers!)
<!-- a manager ruling (2026-09-16), author-side: every canary anchor MUST be mechanically resolvable — a msg id (jsonl:msg_<id>), a REAL 1-based text-bearing turn number (jsonl:turn-<n>), or an ISO range (jsonl:<ISO>..<ISO>). Prose after the locator is stripped at seed time; a pointer with no locator is dropped as unresolvable-form. A seat whose work product lives OUTSIDE its own transcript (files, dream/facts, recordings) cites the msg_store rows or commit shas that carry that work, never the files. -->
1. **Q1:** {Question citing raw anchor e.g. jsonl:turn-42 regarding key decision}
2. **Q2:** {Question citing raw anchor regarding specific file edit or error}
3. **Q3:** {Question citing raw anchor regarding dependency or configuration}
4. **Q4:** {Question citing raw anchor regarding test failure or edge case}
5. **Q5:** {Question citing raw anchor regarding active open loop}
```

## 3. IN-FLIGHT WORK & IDLE HANDLING

- **Micro-Updates (70% - 80%):** If you perform additional work after writing the initial handoff, update and git commit `docs/HANDOFF_<agent>-next.md` with each atomic commit.
- **Idle Parking:** If all pending tasks in `tasks.db` are finished and the handoff is committed, mark your seat as `PARKED` / `STAND_DOWN`. Do not churn tokens in idle loops.

## 4. SUCCESSOR INITIALIZATION & CONTENT GATE

When your successor is spawned by the Lineage Daemon:
1. It receives the `[LINEAGE INIT]` readback instruction.
2. It reads `docs/HANDOFF_<agent>-next.md`, inspects the repository, and reconciles git state against `Last Commit SHA`.
3. It authors `state/agent-handoffs/<successor>.readback.md` with explicit `**Q1**`, `**Q2**`, `**Q3**`, `**Q4**`, `**Q5**` headers answering the canary questions.
4. The Lineage Daemon content gate validates the readback against predecessor context before safely retiring the predecessor.

## 5. CRITICAL INVARIANTS

1. **NEVER leak canary answers:** The handoff document contains **questions and transcript anchors only**. Answers are resolved by the successor from codebase/transcript evidence.
2. **Atomic Commits:** Always commit code before recording last commit SHA.
3. **Declared First Effect:** Make the successor's first action concrete and immediately verifiable.
