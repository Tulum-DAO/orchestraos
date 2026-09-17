# Hooks: how a seat acts on mail with no keypress

OrchestraOS seats are Claude Code sessions in tmux. Claude Code fires **hooks** at lifecycle
events (session start, every tool call, every stop). `orchestra init` installs the shipped
hooks into your `~/.claude/settings.json` (merge, never clobber, idempotent). `orchestra
doctor` reports `hooks:claude`.

| hook | events | what it does |
|---|---|---|
| `hooks/state-event-hook.py` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Notification, Stop, SessionEnd | writes `<data>/state/agent-events/panes/<pane>.json` — the working/idle truth the message router's idle oracle and `agent-status.py` read |
| `hooks/agent-queue-drain.py` | Stop | when the seat goes idle with pending mail in `msg_store` older than 30 s, blocks the stop once with a `[QUEUE-DIGEST]` so the agent reads and acts without anyone pressing Enter |
| `hooks/rotation-self-trigger.js` | PostToolUse | near the context ceiling, injects a one-time "author your handoff / rotate" instruction (`ORCH_SELF_ROTATE=off|warn_only|full`) |
| `scripts/lineage_daemon/bus_feeder.py` | Stop, SessionEnd, Notification | feeds the lineage event stream used by blue-green rotation |

Each installed row is tagged `#orchestraos-hook` and carries `ORCHESTRA_DIR=<data dir>` and
`ORCHESTRA_ROOT=<checkout>`, so a seat whose cwd is a client repo still finds
`registry.json` and `state/tasks.db`.

## Install / inspect by hand

```bash
python3 hooks/install.py --data-dir ~/orchestra          # same thing orchestra init does
python3 hooks/install.py --status                          # installed vs missing
ORCHESTRA_SKIP_HOOKS=1 orchestra init                      # containers that run no Claude seats
```

Narrow the drain with `<data>/state/queue-drain-allowlist.json`:
`{"enabled": true, "default_on": false, "agents": ["gm"], "exclude": []}`. No file = on for
every registered seat.

## Safety

The installer refuses to write a row whose script is missing on disk (a broken row errors on
every tool call and blocks the host), never overwrites a settings file it cannot parse, and
under pytest refuses to touch the real `~/.claude/settings.json` (`CLAUDE_CONFIG_DIR` is set to
a temp dir by the test fixtures).

## Testing on a host that runs a live fleet

Never spawn or rotate test seats on the shared tmux server or against the real Claude config
dir. `source scripts/test_sandbox_env.sh <name>` gives an isolated tmux server, config dir and
data dir; the pytest suites apply the same sandbox automatically and kill the server on
teardown. Installed hook commands fail OPEN at runtime: if a script is missing the hook exits 0.

## Gemini CLI and Codex: not yet

Gemini CLI and Codex seats have no hook layer here. Their idle detection falls back to the
router's pane heuristics and mail is delivered by pane injection on the router's schedule.
Contributions welcome: `hooks/` is the contract, `hooks/install.py` the installer.
