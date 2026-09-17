# The seven-step gate

Everyone completes these seven steps before picking a track or adding a feature —
see `docs/tracks/README.md` and `docs/HACKATHON_ISSUES.md`. Each step below is
command → expected output → what to check if it doesn't match. Copy-paste
versions of the same steps (to hand to your own agent instead of typing commands
yourself) are in `docs/PROMPTS.md`. The short summary version lives in
`docs/BEGINNERS_GUIDE.md`; this page is the operational reference.

Do the steps in order — each one depends on state the last one created (a data
dir, a running supervisor, a spawned seat).

## 1. Install, doctor green, dashboard open

```bash
git clone https://github.com/Tulum-DAO/orchestraos.git orchestraos && cd orchestraos
make install
orchestra init --yes     # unattended; drop --yes to review the Claude hook rows first
orchestra doctor
```

Expected: `orchestra doctor` prints one line per check, every required row `OK`
(a `WARN`/`INFO` row is advisory, not blocking), exit code `0`.

```bash
orchestra up --detach && orchestra status
```

Expected: `orchestra status` shows every supervised process (`gateway`, `api`,
`dashboard`, `router`, the beats) with a live pid. Open
`http://127.0.0.1:8891` (or `ssh -L 8891:127.0.0.1:8891` if remote) — the
dashboard loads with an empty Agents list.

**If it fails, look here:** `orchestra doctor`'s failing row names the exact
fix (a missing CLI, a bad port, an unauthed runtime) — read its remedy line
before anything else. If `orchestra up` won't start, `<data>/logs/<name>.log`
per `docs/INSTALL.md`'s process table has the real error; the supervisor's own
stdout only says which child failed.

## 2. Always-on agent spawned, answers questions in terminal

```bash
source scripts/orchestra-env.sh
REGISTRY_PATH=$ORCHESTRA_DIR/registry.json python3 scripts/registry-update.py hello \
    --field name=hello --field tmux_session=hello \
    --field tier=T2 --field runtime=claude --field machine=vps --field cwd=$PWD
AGENT_RUNTIME=claude ./spawn-agent.sh hello --task "Say hello, then park."
tmux attach -t hello
```

Expected: the tmux pane shows the CLI's normal interactive UI, having already
said hello per the task. Type a question directly into the pane (e.g. "what
files are in this folder?") and get a real, current answer — not a canned one.
Detach with `Ctrl-B D`.

```bash
curl -s http://127.0.0.1:8891/api/agents | python3 -m json.tool | grep -E '"id"|"alive"'
```

Expected: `hello` appears with `"alive": true` within about 15 seconds of
spawning (the status detector polls).

**If it fails, look here:** `./spawn-agent.sh --list` shows registered seats,
`--running` the live ones — if `hello` isn't in either, the registry write
failed (check the `registry-update.py` command's exit code). If `tmux attach`
shows a workspace-trust prompt instead of the CLI, answer it once by hand — the
spawner tries to pre-seed it (`scripts/ensure_cwd_trusted.py`) but a fresh CLI
version can add a new prompt shape.

## 3. Telegram bot connected, agent answers from phone

Create a bot with [@BotFather](https://t.me/BotFather) on Telegram, get its
token. Export it — **never** paste it into a committed file or a chat message
the agent can read back:

```bash
export TELEGRAM_BOT_TOKEN=<your token>
```

Set `[notify] channel = "telegram"` in `orchestra.toml`, restart the
supervisor (`orchestra down && orchestra up --detach`), then send yourself a
test:

```bash
python3 scripts/tg-notify.sh "test from the gate"
```

Expected: the message arrives on your phone within a few seconds.

**If it fails, look here:** `orchestra doctor` should show a `notify:telegram`
(or equivalent) row once this track lands — until then, check
`<data>/logs/*.log` for the send attempt's HTTP response; a 401 means the
token is wrong, a timeout means the bot was never started with `/start` from
your phone first.

## 4. Two seats exchange a message, both visible in Inbox

```bash
REGISTRY_PATH=$ORCHESTRA_DIR/registry.json python3 scripts/registry-update.py hello-2 \
    --field name=hello-2 --field tmux_session=hello-2 \
    --field tier=T2 --field runtime=claude --field machine=vps --field cwd=$PWD
AGENT_RUNTIME=claude ./spawn-agent.sh hello-2 --task "Say hello, then park."
python3 msg_store.py send --from hello --to hello-2 --type task --subject test --body-file <(echo "hi from hello")
python3 msg_store.py inbox --agent hello-2
```

Expected: the `inbox` call shows the row you just sent (`subject: test`,
`from_agent: hello`). The dashboard's Inbox view lists the same row.

**If it fails, look here:** `msg_store.py send` always writes the row — if
`inbox` doesn't show it, you queried the wrong `--agent` name (must match
`to_agent` exactly) or the wrong data dir (`echo $ORCHESTRA_DIR`).

## 5. One approval card answered from Telegram or dashboard

```bash
python3 scripts/approval.py request "Ship the hello change?" --from hello --worker-kind pane --options approve,deny
```

Expected: prints a card id (`apr_...`). It appears under Approvals in the
dashboard, and — once step 3 is done — as a push on your phone.

```bash
curl -s -X POST http://127.0.0.1:8891/api/approvals/<card id>/approve
python3 scripts/approval.py get <card id>
```

Expected: within a minute, `approval.py get` shows `status: resumed`, and
`tmux capture-pane -p -t hello | tail -20` shows the decision delivered into
the `hello` pane.

**If it fails, look here:** `<data>/logs/approval_resume.log` has the
delivery attempt; a card stuck at `status: answered` (never `resumed`) means
the `approval_resume` beat hasn't run yet (every 60s) or `hello`'s pane
wasn't idle when it tried.

## 6. Manual rotation of the always-on agent completed, nothing lost

```bash
orchestra rotate hello   # if this command exists on your checkout — check `orchestra --help` first
# otherwise:
python3 scripts/rotate_agent.py hello
```

`orchestra rotate` lands in PR: orchestra-builder (branch `tier0/spawn-rotate`
as of this writing) — until it merges, `scripts/rotate_agent.py` runs the same
sequence and is what's actually on `main` today.

Expected: a new generation of `hello` boots, reads the handoff the old one
wrote, answers a short set of canary questions anchored in the predecessor's
own state, and — on a passing grade — is promoted: the registry's canonical
pointer for `hello` now points at the new generation, the old one is retired.

**If it fails, look here:** the handoff document and the successor's readback
are both real files (see `docs/ARCHITECTURE.md`'s Vocabulary section for
where) — read them; a failed promotion almost always shows up as a readback
answer that doesn't match an anchor in the predecessor's transcript, which
`rotate_agent.py`'s own output names.

## 7. One fact written, restart, agent recalls it

Inside the `hello` pane:

```
Remember that my favorite color is blue. Write it to your memory files.
```

Restart or rotate `hello` (step 6), then inside the new generation:

```
What's my favorite color?
```

Expected: it answers correctly and can say where it read the fact from (its
memory directory, indexed by `MEMORY.md` — see `docs/ARCHITECTURE.md`'s
Memory section).

**If it fails, look here:** if the new generation doesn't know, the fact
either wasn't written to disk (check the memory directory directly) or the
successor never read `MEMORY.md` at boot — both are things to fix before
trusting rotation with anything that matters.

## Done

All seven green means you've exercised most of the files any track's doc
will send you to. Pick a track (`docs/tracks/README.md`) or a
`good-first-issue` (`docs/HACKATHON_ISSUES.md`).
