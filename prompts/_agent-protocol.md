# Agent Protocol — All T2 Specialists

This protocol applies to every specialist agent. Follow it exactly.

## ON STARTUP
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
