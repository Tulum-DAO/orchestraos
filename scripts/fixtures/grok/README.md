# Grok Build session fixtures

Scrubbed copies of one real Grok Build session store
(`$GROK_HOME/sessions/<url-encoded-cwd>/<sid>/`, grok 1.0.50, a headless `grok -p` run):

* session id replaced by `0199aaaa-0000-7000-8000-000000000001`
* MCP server names/targets/tool names and error text replaced (`mcpN`, `<scrubbed>`)
* summary.json `agent_id` / `attempt_id` / `request_id` / `grok_home` replaced
* nothing from `auth.json`, no tokens, no chat history, no prompts, no tool definitions

Files:

| file | what it is |
|---|---|
| `events_turn_ended.jsonl` | the full real events stream: MCP lifecycle, `turn_started`, phases, `turn_ended` |
| `events_turn_running.jsonl` | the same real stream cut after `first_token` + first `phase_changed` (a turn in flight) |
| `events_booting.jsonl` | the first 5 real lines: MCP lifecycle only, no turn boundary yet |
| `signals.json` | real `signals.json` (counters; `contextTokensUsed`/`contextWindowTokens`) |
| `summary.json` | real `summary.json`, ids scrubbed |

The interactive TUI writes the same `turn_started` / `turn_ended` pair per prompt (the seat
readers in scripts/grok_context.py and the mail watcher depend on it).
