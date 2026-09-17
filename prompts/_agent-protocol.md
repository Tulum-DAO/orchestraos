# Agent Protocol — All T2 Specialists

This protocol applies to every specialist agent. Follow it exactly.

## ON STARTUP
0. Read your memory index: `$ORCHESTRA_DIR/memory/<lineage-id>/MEMORY.md` (your spawn prompt names the exact directory). Open only the one-fact files whose index line is relevant to the task in front of you. See **Memory** below.
1. Read predecessor handoff: `docs/HANDOFF_<agent>-next.md` or `~/scripts/omni-context/projects/{project}/handoff.md`
   - If spawned as successor under `[LINEAGE INIT]`: reconcile git state from predecessor's `last_commit_sha`, and author `state/agent-handoffs/{your-id}.readback.md` answering the 5 grounding canary questions with explicit `**Q1**`, `**Q2**`, `**Q3**`, `**Q4**`, `**Q5**` headers.
2. Read your project facts: `~/scripts/omni-context/projects/{project}/facts_db.json`
3. Check your inbox: `~/scripts/agent-orchestra/queue/inbox/{your-id}/`
4. Read any agent_task message to understand your assignment

## WHILE WORKING
Every 10 minutes (or at natural breakpoints), write a progress update:
```json
{
  "type": "progress",
  "from": "{your-id}",
  "to": "{your-pm}",
  "task_id": "{task_id}",
  "status": "working",
  "summary": "What you've done so far and what's next",
  "percent_complete": 50
}
```
Write to: `~/scripts/agent-orchestra/queue/inbox/{your-pm}/`

## ON COMPLETION
1. Write your handoff file: `docs/HANDOFF_{your-agent-id}-next.md`
2. Commit your changes with a descriptive message
3. Write a completion report to your PM's inbox:

```json
{
  "type": "agent_complete",
  "from": "{your-id}",
  "to": "{your-pm}",
  "task_id": "{task_id}",
  "success": true,
  "summary": "One sentence describing what was accomplished",
  "changes": ["List", "of", "specific", "changes", "made"],
  "files_modified": ["path/to/file1.ts", "path/to/file2.tsx"],
  "blockers": [],
  "next_steps": ["Recommended follow-up actions"],
  "git_state": "committed"
}
```

4. Update your agent state: `~/scripts/agent-orchestra/state/{your-id}.json`

## IF BLOCKED
If you can't proceed:
1. Write an escalation to your PM's inbox:
```json
{
  "type": "escalate",
  "from": "{your-id}",
  "to": "{your-pm}",
  "task_id": "{task_id}",
  "summary": "What's blocking you",
  "attempted": ["What you tried"],
  "needs": "What you need to unblock"
}
```
2. Continue with any other work you can do while waiting

## Mandatory Lineage Handoff & Auto-Rotation (70% Soft / 80% Hard)

When context reaches **70%** (or upon receiving `lineage_soft_handoff`):
1. Complete your immediate atomic step.
2. Author and git commit `docs/HANDOFF_{your-agent-id}-next.md` adhering to the Gold Standard schema:
   - Header: agent IDs, generation, timestamp, working dir, last commit SHA.
   - Current Goal & Phase State (`plan_ref`, `phase_n` of `phase_m`, `current_step`).
   - Open Loops & Active Callbacks.
   - Decisions Made & Rationale.
   - Declared First Effect (exact first file modification/command for successor).
   - Next 3 Actions.
   - 5 Grounding Canary Questions (questions and `jsonl:turn-XYZ` source pointers only; NEVER leak answers).
3. If further work is completed before 80%, commit micro-updates to the handoff doc with each atomic commit.
4. If all tasks in `tasks.db` are finished and no work is pending, mark seat as `PARKED` / `STAND_DOWN`.
5. At **80%** context, the Lineage Daemon automatically rotates you to your successor. Finish current commit and stand down cleanly.

## Memory (index + one-fact files + baton)

Every seat keeps a memory directory that survives restarts and rotations. The full walkthrough with a copy-paste prompt is `docs/MEMORY.md`; this is the contract.

**Where:** `$ORCHESTRA_DIR/memory/<lineage-id>/` — `<lineage-id>` is your seat name with any `-gN` / `-genN` generation suffix stripped (`hello-g4` → `hello`), so every generation of a seat reads and extends the same files. `spawn-agent.sh` creates the directory and an empty `MEMORY.md` on first spawn and tells you the path.

**Three parts:**
1. `MEMORY.md` — the index. One line per memory, no content: `- [Title](file.md) — hook`. It is loaded at boot, so keep it short and skimmable.
2. One-fact files — `<type>_<slug>.md`, one fact per file, with frontmatter:
   ```markdown
   ---
   name: <short-kebab-slug>
   description: <one line — what this is, used to decide relevance>
   type: user | feedback | project | reference
   ---
   <the fact. For feedback/project add **Why:** and **How to apply:** lines. Link related memories with [[name]].>
   ```
   `user` = who the operator is; `feedback` = corrections and confirmed approaches; `project` = ongoing work, goals, constraints (convert relative dates to absolute); `reference` = pointers (URLs, tickets, dashboards).
3. The baton — `docs/HANDOFF_<lineage-id>-next.md` (the lineage handoff above). Memory is *durable knowledge*; the baton is *where I stopped and what is next*. Never put one in the other.

**Rules:**
- Before writing, check the index for a file that already covers it — update that file; delete a memory that turns out to be wrong.
- Do not save what the repo already records (code structure, git history, CLAUDE.md) or what matters only to this conversation.
- Write the fact when you learn it, not at handoff time — the handoff may never come (OOM, crash, hard rotation).
- After writing a file, add its one-line pointer to `MEMORY.md` in the same step.
- Shared facts the voice brain should know (client details, project state) go to the **facts store** instead: `POST /api/facts {"text": "..."}` or the dashboard Facts pane. `services/arturo/facts_recall.py` reads that store on Arturo's next turn.

## Brief Updates

When working on a task, send brief updates to the operator via Telegram. Use:
```bash
python3 ~/scripts/agent-orchestra/brief.py <your-agent-id> <stage> "<message>"
```

Stages:
- `ack` — When you receive a task: "On it — reading the codebase now"
- `checkpoint` — At milestones: "Fixed the endpoint. Running tests."
- `result` — When done: "Done. 3 files changed. Tests passing."
- `blocker` — When stuck: "GENERATE returns 500. Need the operator's input on auth format."

Rules:
- Send ack IMMEDIATELY when you start a task
- Send checkpoint every 10-15 minutes of work
- Send result when done
- Send blocker when stuck — don't wait
- Keep messages under 100 words
- DO NOT send "still working" or "thinking about it" — only real updates

## Multi-Model Congruence (mandatory for high-stakes changes)

Before any high-stakes change — architectural decisions, structural refactors, editing files >500 lines, multi-tenant/API-contract changes, or when multiple valid designs exist — do NOT act alone. Use the `multi-model-congruence` skill to seek peer consensus first:
1. Draft `.workspace/proposals/<feature>-spec.md`.
2. `python3 ~/.agents/skills/multi-model-congruence/scripts/consensus.py request --dir "." --topic "..." --proposal "..." --models "<peers>" --agent "{your-id}"`.
3. Peers vote; build only once `consensus.py status` = `CONSENSUS_REACHED`. Resolve REJECT/COUNTER_PROPOSE and re-vote otherwise.

Triggers: "seek congruence", "get consensus", "congruence check".

## RULES
1. Only modify files in your working directory
2. Commit changes before session end
3. ALWAYS write a completion report — your PM is waiting for it
4. ALWAYS write a handoff file — the next agent needs it
5. Never contact clients directly — escalate to PM
6. For high-stakes changes, seek multi-model congruence BEFORE building (see above)
