# Hooks: how a seat acts on mail with no keypress

OrchestraOS seats are Claude Code sessions in tmux. Claude Code fires **hooks** at lifecycle
events (session start, every tool call, every stop). `orchestra init` prints the rows and asks
before writing (`--yes` / `ORCHESTRA_YES=1` to skip the prompt; non-interactive without
`--yes` skips the step) — the settings file is shared by every Claude session on the host. It installs the shipped
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

## The status line (opt-in): context numbers for the apps and for rotation

`orchestra init` also offers a Claude Code status line (`hooks/statusline.sh` in front of
`hooks/statusline.py`). It shows `model │ project ███░░░░░░░ 39% used` and, more to the point,
writes each session's context reading to `$TMPDIR/claude-ctx-<session_id>.json` (`/tmp` when
`TMPDIR` is unset; on macOS that is a per-user `/var/folders/...` dir). The apps' context numbers
(`context_pct_of_window`, `context_pct_of_budget`) and the rotation engine read that file. Without
it they stay empty, and rotation falls back to reading the screen.

- **No status line yet:** init asks; `--yes` installs it.
- **You already have one:** init shows your command and asks whether to keep it and add context
  tracking. Yes means yours keeps running, gets the same input from Claude Code, and its output is
  shown unchanged; `--yes` alone answers **no**, so your line is never replaced or wrapped silently.
  If the checkout is moved or deleted, your own command still runs.
- **Cost:** a shell per redraw. Python runs at most once per 30 s per session (the shim serves its
  cache in between), so a chained line also refreshes at most every 30 s.
- `orchestra doctor` reports `statusline:claude`: installed, chained, declined (your own line is
  kept, so app context numbers will stay empty), or not installed.

## Install / inspect by hand

```bash
python3 hooks/install.py --data-dir ~/orchestra          # same thing orchestra init does
python3 hooks/install.py --status                          # installed vs missing
python3 hooks/install.py --remove                          # take out every #orchestraos-hook row and our status line
                                                           # (a chained one goes back exactly as it was), nothing else
python3 hooks/install.py --statusline status               # installed | chained | theirs | absent
python3 hooks/install.py --statusline chain                # keep your status line, add context tracking
orchestra init --yes                                       # unattended: write the rows without the y/N prompt
ORCHESTRA_SKIP_HOOKS=1 orchestra init                      # containers that run no Claude seats
```

Narrow the drain with `<data>/state/queue-drain-allowlist.json`:
`{"enabled": true, "default_on": false, "agents": ["gm"], "exclude": []}`. No file = on for
every registered seat.

## Safety

The installer refuses to write a row whose script is missing on disk (a broken row errors on
every tool call and blocks the host), never overwrites a settings file it cannot parse, and
under pytest refuses to touch the real `~/.claude/settings.json` (`CLAUDE_CONFIG_DIR` is set to
a temp dir by the test fixtures). Outside pytest it also refuses to install a checkout or data dir
that lives under a temp dir into any settings file that is not itself under a temp dir: a scratch clone's rows would run in every Claude
session on the host, out of a directory that is deleted later. Point `CLAUDE_CONFIG_DIR` at a
scratch dir for a throwaway install, or set `ORCHESTRA_ALLOW_TEMP_INSTALL=1` if you mean it. If
`HOME` itself is under a temp dir (some containers), `~/.claude/settings.json` counts as scratch
too, so a checkout under a temp dir does install into it. A test
run that changes the real file's tagged rows anyway fails as a whole (root `conftest.py`).

## Testing on a host that runs a live fleet

Never spawn or rotate test seats on the shared tmux server or against the real Claude config
dir. `source scripts/test_sandbox_env.sh <name>` gives an isolated tmux server, config dir and
data dir; the pytest suites apply the same sandbox automatically and kill the server on
teardown. Installed hook commands fail OPEN at runtime: if a script is missing the hook exits 0.

## Gemini CLI and Codex: not yet

Gemini CLI and Codex seats have no hook layer here. Their idle detection falls back to the
router's pane heuristics and mail is delivered by pane injection on the router's schedule.
Contributions welcome: `hooks/` is the contract, `hooks/install.py` the installer.
