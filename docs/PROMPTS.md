# Prompts

Copy-paste prompts for the seven-step gate and the tracks. Paste one into your own
agent CLI (Claude Code, Gemini CLI, or Codex) with this repo as its working
directory. Each entry says why in two lines before the prompt. `docs/GATE.md` has
the same seven steps as commands you run yourself, with expected output and a
troubleshooting line per step.

## Install

Why: the harness never guesses your runtime or your data directory — an agent
running `orchestra init` for you needs to see the actual machine state first,
not assume a clean box.

```
Run `orchestra doctor` first and show me every row before changing anything.
Then run `orchestra init` (idempotent — safe to re-run), open orchestra.toml
and set [runtimes] enabled to whichever CLI I'm logged into right now, then
run `orchestra doctor` again and confirm every required row is OK. Show me
the exit code.
```

## First agent

Why: `orchestra spawn` registers the seat, seeds its lineage (what a later
rotation needs) and launches it in one step — an agent running it for you also
verifies the tmux session and the API row.

```
Spawn one seat named "hello" per docs/GATE.md §2: `orchestra spawn hello --task
"Say hello, then park."` (runtime = whatever I have authed). Do NOT use
scripts/registry-update.py + spawn-agent.sh — that older recipe registers a seat
with no lineage and step 6's rotation refuses it. Show me the tmux session is
running and that GET /api/agents (through the dashboard proxy) lists it.
```

## Connect Telegram

Why: the bot token is a secret that must never land in a committed file or a
chat message — walk through the env-only path deliberately rather than
pasting the token where it could get logged.

```
Help me connect Telegram per the [notify] section of orchestra.example.toml.
Do not ask me to paste the bot token into chat or into any file you write —
tell me the exact environment variable name and where to export it, then
verify the connection by sending one test notification and confirming it
arrived on my phone.
```

## Two-seat message

Why: this is the step that proves messaging actually works end to end, not
just that both seats exist — insist on inbox evidence, not just "sent
successfully."

```
Register and spawn a second seat named "hello-2" the same way as "hello".
From "hello", send a message to "hello-2" with
`python3 msg_store.py send --from hello --to hello-2 --type task --subject test
--body-file <a file, not --body inline>`. Then show me
`python3 msg_store.py inbox --agent hello-2` proving the row arrived, and that
the dashboard's Inbox view lists it too.
```

## Answer a card

Why: the whole approvals loop — request, render, answer, resume — is the
mechanism every other decision in the harness goes through, so proving it
once by hand is worth the two minutes.

```
Fire one approval card per docs/INSTALL.md §4:
`python3 scripts/approval.py request "Ship the hello change?" --from hello
--worker-kind pane --options approve,deny`. Show me the card id, then answer
it either from the dashboard or with curl against
/api/approvals/<id>/approve, then show me `approval.py get <id>` reporting
status "resumed" and the decision actually landed in the "hello" seat's tmux
pane.
```

## Rotate

Why: rotation is the mechanism that keeps a long-running seat from silently
dying when it runs out of context — trigger one manually once so you have
seen a handoff and readback before you ever need to trust it unattended.

```
Rotate the "hello" seat: use `orchestra rotate hello` if that command exists
on this checkout (check `orchestra --help` first), otherwise
`python3 scripts/rotate_agent.py hello`. Show me the handoff document the old
generation wrote, the successor's readback answering its canary questions,
and confirm the registry's canonical pointer now points at the new
generation.
```

## Write a fact and recall it

Why: memory surviving a restart is the thing that makes a long-running agent
trustworthy — write one fact, restart the seat (or rotate it), and prove the
new generation actually reads it rather than starting blank.

```
Have the "hello" seat write one fact to its memory directory (a short
one-fact file plus an update to its MEMORY.md index — see
docs/ARCHITECTURE.md's Memory section and docs/MEMORY.md, once it lands, for
the shape; this is NOT the handoff document -- that carries position, not
facts). Restart or rotate the seat, then have the new generation recall that
exact fact without me telling
it again, and show me where it read it from.
```

## Upgrade to latest main

Why: this harness is under active development during the hackathon — pulling
`main` without checking what changed can silently break a running install, so
have the agent read before it merges.

```
Fetch and show me what changed between my current checkout and origin/main
(git log --oneline HEAD..origin/main) before pulling anything. Flag any
change under scripts/lineage_daemon/, msg_store.py, or scripts/approval*.py
specifically, since those are the contracts other things depend on
(docs/ARCHITECTURE.md's Invariants section). Then merge, re-run
`orchestra doctor`, and confirm every row is still OK.
```
