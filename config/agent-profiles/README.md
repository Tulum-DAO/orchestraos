# Agent profiles

One file per agent CLI: what OrchestraOS needs to know to run it, find its work, and which of its
features work today. An agent with a profile can be described without changing any code; the
fields say where each piece comes from, so a reader never has to guess per provider.

| Field | Meaning |
|---|---|
| `id` | The runtime id a registry row declares (`runtime`). |
| `proven` | `true` once every claim in the file was checked against the installed CLI. |
| `process.basenames` | Program names that mean "this agent is running in the pane". |
| `process.node_scripts` | For agents started through `node`: the script path suffix that identifies them. |
| `prompt_char` | The composer's prompt glyph, or `null` when the agent reports its own state. |
| `launch` | Binary, model flag, and the resume argv (`{session_id}` is filled in), or `null`. |
| `state.source` | `hooks`, `screen`, `events-file`, or `report` (the agent writes its own pane state file). |
| `session_log.format` | Which reader understands its saved conversation (`scripts/session_turns.py`). |
| `session_log.find` | Where to look, in order: `claude-projects`, `session-files`, `grok-sessions`, `reported` (the `transcript_path` in its pane state file), or `glob:<pattern>`. |
| `context`, `tokens` | Where the context reading and token counts come from, or `none`. |
| `model_switch` | `live`, `next-rotation`, or `none`. |
| `menus` | `chat` (answered from the dashboard), `single-choice`, `terminal-only`, or `none`. |
| `rotation` | `supported`, `opt-in`, or `none`. |

`private-needs-upstreaming` in any field: the feature works in a private fleet tree and has to be
ported here; until then this repository behaves as `none`.

## Reporting state yourself

An agent whose screen OrchestraOS cannot read can write its own pane state file,
`<data dir>/state/agent-events/panes/<pane number>.json`, the same file Claude's hooks write:

```json
{"pane": "%12", "state": "working", "event": "tool_execution_start", "tool": "bash", "ts": 1793217620.3,
 "runtime": "pi", "session_id": "…", "cwd": "/path", "transcript_path": "/path/to/session.jsonl",
 "model": "provider/model", "context": {"tokens": 41000, "window": 200000, "percent": 20.5}}
```

`state` is `working`, `idle` or `stopped`. Write it atomically (a temporary file, then rename).
`integrations/pi/orchestra-report.ts` is the reference reporter.
