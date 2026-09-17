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
dashboard loads with an empty Agents list. Inside a Docker container the services
bind `127.0.0.1` by design, so check with `curl -s 127.0.0.1:8891/api/agents` from
inside the container (or set `[dashboard] host = "0.0.0.0"` before publishing a port).

**If it fails, look here:** `orchestra doctor`'s failing row names the exact
fix (a missing CLI, a bad port, an unauthed runtime) — read its remedy line
before anything else. If `orchestra up` won't start, `<data>/logs/<name>.log`
per `docs/INSTALL.md`'s process table has the real error; the supervisor's own
stdout only says which child failed.

## 2. Always-on agent spawned, answers questions in terminal

```bash
orchestra spawn hello --task "Say hello, then park."
tmux attach -t hello
```

`orchestra spawn` registers the seat in the registry **and** seeds its lineage in the
identity store (generation 1), which step 6's rotation requires. Do not use the older
two-command recipe (`scripts/registry-update.py` + `./spawn-agent.sh`) here: it
registers the seat but seeds no lineage, and `orchestra rotate` will refuse it with
"no authoritative generation ... seed the seat via the identity store".

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
`--running` the live ones — if `hello` isn't in either, `orchestra spawn`'s own
output names the failing step. If `tmux attach`
shows a workspace-trust prompt instead of the CLI, answer it once by hand — the
spawner tries to pre-seed it (`scripts/ensure_cwd_trusted.py`) but a fresh CLI
version can add a new prompt shape.

## 3. Telegram bot connected, agent answers from phone

Create a bot with [@BotFather](https://t.me/BotFather) on Telegram, get its
token. Export it — **never** paste it into a committed file or a chat message
the agent can read back:

```bash
export TELEGRAM_BOT_TOKEN=<your token>     # or: echo 'TELEGRAM_BOT_TOKEN=<token>' >> $ORCHESTRA_DIR/.env.telegram
```

Enable the plugin in `orchestra.toml` and restart the supervisor:

```toml
[plugins.telegram]
enabled = true
allowed_chat_ids = []     # [] = the first chat that messages the bot becomes the operator
```

```bash
orchestra doctor | grep plugin:telegram    # OK  token set; open; ...
orchestra down && orchestra up --detach    # starts the `telegram` service (plugins/telegram/router.py)
```

On your phone, open the bot and send `/start` — it replies with your chat id and
remembers it. Then text it a question, e.g. `what seats are running?`. The gm seat
(`orchestra spawn gm --gm` if you haven't) gets it in its inbox as
`from_agent=telegram` and answers with `python3 plugins/telegram/tg_send.py "<text>"`.
Full setup and the what-happens table: `plugins/telegram/README.md`.

Expected: your text shows up in gm's pane within a minute (`tmux attach -t gm`),
and gm's answer arrives on your phone.

**If it fails, look here:** `orchestra doctor`'s `plugin:telegram` row names the
problem (`MISSING` = no token in the environment `orchestra up` runs in; `INFO
disabled` = the toml flag). `orchestra status` must list the `telegram` service
with a pid; its log is `$ORCHESTRA_DIR/logs/telegram.log` (every inbound row id
and every card push is printed there). `tg_send: no chat id` means nobody has
sent the bot `/start` yet.

## 4. Two seats exchange a message, both visible in Inbox

```bash
orchestra spawn hello-2 --task "Say hello, then park."
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
orchestra rotate hello --synthesize
```

`--synthesize` writes a minimal handoff for a seat that has not banked one (a real
seat banks its own baton with canary questions; `hello` has nothing to hand over yet).
The successor then only has to prove it can read its own seat.

Expected: a new generation of `hello` boots, reads the handoff the old one
wrote, answers a short set of canary questions anchored in the predecessor's
own state, and — on a passing grade — is promoted: the registry's canonical
pointer for `hello` now points at the new generation, the old one is retired.

**If it fails, look here:** `rotation REFUSED ... no authoritative generation ...
seed the seat via the identity store` means the seat was not spawned with
`orchestra spawn` (step 2) — the older `registry-update.py` + `spawn-agent.sh`
recipe registers a seat without a lineage. Spawn it again with `orchestra spawn`
(a new name is simplest) and retry. `HOLD_GRADE` means the successor's readback
did not clear the strict grader; with `--synthesize` on a seat this young the
usual cause is a predecessor transcript too thin to anchor against — ask `hello`
to do a little real work first, then retry. The handoff document and the successor's readback
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

Expected: it answers correctly and can say where it read the fact from — its
memory directory at `$ORCHESTRA_DIR/memory/<lineage-id>/` (the seat name with
its `-gN`/`-genN` generation suffix stripped), indexed by `MEMORY.md`.
`spawn-agent.sh` seeds that directory and `MEMORY.md` at spawn time and puts
the path in the new generation's boot prompt with a read-first instruction —
that boot-prompt line, not the handoff document, is what makes the successor
actually read it. See `docs/ARCHITECTURE.md`'s Memory section and
`docs/MEMORY.md` (full walkthrough, once it lands) for the exact shape.

**If it fails, look here:** if the new generation doesn't know, the fact
either wasn't written to disk (check the memory directory directly) or the
successor never read `MEMORY.md` at boot — both are things to fix before
trusting rotation with anything that matters. Don't look in the handoff
document (`docs/HANDOFF_<agent>-next.md`) for the fact — that file carries
the predecessor's position (where it stopped, what's next), never durable
memory; a fact that only exists there will not survive.

## Done

All seven green means you've exercised most of the files any track's doc
will send you to. Pick a track (`docs/tracks/README.md`) or a
`good-first-issue` (`docs/HACKATHON_ISSUES.md`).
