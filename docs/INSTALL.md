# Install — the minimum path

One machine, one CLI (claude OR gemini OR codex), no voice, no Telegram.
Target: gateway up, one seat spawned, one approval card answered from the web
dashboard, in under 30 minutes on a clean Ubuntu 22.04/24.04 VPS.

## 0. Prerequisites

```bash
sudo apt update && sudo apt install -y git tmux python3 python3-venv build-essential curl
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

Install and log in to ONE agent CLI (the runtime catalog probes these):

| runtime | binary | login |
|---|---|---|
| claude | `claude` | run `claude`, complete the login; `claude auth status` must report `loggedIn: true` |
| gemini | `agy`    | run `agy` once; token lands in `~/.gemini/antigravity-cli/antigravity-oauth-token` |
| codex  | `codex`  | run `codex login`; `~/.codex/auth.json` gets a `tokens` key |

## 1. Clone, init, doctor

```bash
git clone https://github.com/Tulum-DAO/orchestraos.git orchestraos && cd orchestraos
make install                 # symlinks bin/orchestra into ~/.local/bin
./bin/orchestra init         # data dir (~/.orchestra), orchestra.toml, .venv + pip, npm install, builds;
                             # shows the Claude hook rows it will add to ~/.claude/settings.json and asks (or --yes)
$EDITOR orchestra.toml       # set [runtimes] enabled to the CLI you logged in to, e.g. ["claude"]
./bin/orchestra doctor       # every row OK (WARN/INFO rows are advisory); exit code 0
```

`./bin/orchestra` is spelled out above because `~/.local/bin` only joins your PATH at **login**, and
only if it already existed then — on a clean machine it did not, so a bare `orchestra` in the same
shell as `make install` answers `command not found`. `make install` prints the `export PATH=...` line
when that applies; run it (and add it to `~/.bashrc`) to use the bare `orchestra` everywhere.

`orchestra init` is idempotent: it never overwrites `orchestra.toml`, skips what
exists, and prints did/skipped per step. `--data-dir PATH` moves state elsewhere
(so does `ORCHESTRA_DIR=PATH` in the environment: flag > `ORCHESTRA_DIR` > `[data] dir`
in orchestra.toml > `~/.orchestra`, and the default `~/.orchestra` is never touched when
the env names another dir); `--no-npm` / `--no-venv` / `--no-build` skip the slow steps.

The last step writes hook rows into your Claude Code settings
(`$CLAUDE_CONFIG_DIR/settings.json`, default `~/.claude/settings.json`) — a file every
Claude session on the machine reads. init prints exactly the rows it will add and asks
`y/N`; `--yes` (or `ORCHESTRA_YES=1`) answers yes, and a non-interactive run without it
SKIPS the hooks and says so (unattended installs: `orchestra init --yes`;
`ORCHESTRA_SKIP_HOOKS=1` for a container that runs no Claude seats).

Want gm on your phone? `plugins/telegram/README.md` — a BotFather token in
`TELEGRAM_BOT_TOKEN`, `[plugins.telegram] enabled = true`, and `orchestra up` runs the
channel: texts land in gm's inbox, decision cards arrive with buttons.

If you don't want the voice brain, set `[arturo] enabled = false` — the flask/openai
rows in doctor become INFO and `orchestra up` skips it.

## 2. Up

```bash
orchestra up                 # foreground; Ctrl-C stops everything
# or
orchestra up --detach && orchestra status
```

One supervisor process runs, restarts (with backoff) and logs each child under
`<data>/logs/<name>.log`:

| name | what | port / cadence (orchestra.toml) |
|---|---|---|
| gateway | `scripts/watch_gateway.py` — approvals + verified inject | `[gateway] port` (8890) |
| api | `api/dist/server.js` | `[api] port` (8888) |
| dashboard | `dashboard-proxy.js` — static UI, `/api` proxy, web terminal | `[dashboard] port` (8891) |
| arturo | `services/arturo/run.sh` (optional) | `[arturo] port` (5071) |
| bus_beat | event-bus drain | every `bus_beat_interval_seconds` (60) |
| boundary_delivery | turn-boundary delivery, armed | every 60 s (`boundary_delivery_armed`) |
| cron_beat | autonomous blue-green rotation beat — ON by default | every `cron_beat_interval_seconds` (900) |
| router | `message-router.py --cron` delivery backstop | `[router] interval_seconds` (60) |
| approval_resume | `approval_resume.py` — delivers an answered card to its seat (pane inject + msg_store row) | every 60 s |

No crontab is installed. `orchestra up --dry-run` prints this table without
starting anything. `orchestra down` stops it; `orchestra status` shows pids.

Smoke check: `curl -s http://127.0.0.1:8888/api/health` → `{"status":"ok", "db":{"open":true}, ...}`
(`orchestra doctor` runs the same probe as `api:health` while the supervisor is up).

Open the dashboard: `http://127.0.0.1:8891` (ssh -L 8891:127.0.0.1:8891 if remote,
or set `[dashboard] host` / `[public] host`).

## 3. Spawn one seat

The one-command way: `orchestra spawn` registers the seat in the data-dir registry (if it is
new) and launches it in tmux with the install env carried into the pane.

```bash
orchestra spawn gm --gm                  # the General Manager: prompts/gm.md, tier T1, always-on
orchestra spawn hello --task "Say hello, then park."   # a worker seat (prompts/hello.md if present)
tmux attach -t gm                        # talk to it; detach with Ctrl-B D
```

Options: `--runtime claude|gemini|codex` (default: first of `[runtimes] enabled`), `--model`,
`--tier`, `--prompt path/relative/to/checkout`. An existing registry row is kept as-is.

Mail a seat and watch it act with no keypress (the shipped hooks + the router beat under
`orchestra up`; see docs/HOOKS.md):

```bash
python3 msg_store.py send --from you --to gm --subject hi --body-file note.txt
```

The lower-level pieces are still there (but a seat made this way has no lineage and
cannot be rotated — use `orchestra spawn` for anything you will rotate): `scripts/registry-update.py` writes the row,
`./spawn-agent.sh <seat> --task ...` launches an already-registered seat, `--list` / `--running`
show registered and live seats.

- `machine` is a label. On a single-machine install (`[machines]` left blank in
  `orchestra.toml`) the spawner never dispatches elsewhere.
- The spawner pre-seeds Claude Code's workspace-trust bit and the bypass-permissions
  acceptance for `cwd` (`scripts/ensure_cwd_trusted.py`; honors `CLAUDE_CONFIG_DIR`), so the
  seat does not stop at a first-run dialog. If you see one anyway, answer it once in `tmux attach`.

Verify through the dashboard proxy (the same list the UI shows):

```bash
curl -s http://127.0.0.1:8891/api/agents | python3 -m json.tool | grep -E '"id"|"alive"|"state"'
```

The `hello` row appears immediately; `alive`/`state` follow within ~15 s from the
status detector. Only registered seats are listed — tmux is host-global, see "Sharing a
host" below.

## 4. Answer one approval card from the dashboard

From a shell (or let the seat run it):

```bash
source scripts/orchestra-env.sh
python3 scripts/approval.py request "Ship the hello change?" --from hello --worker-kind pane --options approve,deny
# -> prints the card id, e.g. apr_1a2b3c4d_567
```

The card appears under Approvals in the dashboard (`GET /api/approvals` through the
proxy lists it under `pending`); answer it there, or from a shell:

```bash
curl -s -X POST http://127.0.0.1:8891/api/approvals/<card id>/approve
```

What happens next, and how to see it:

1. The answer is recorded in `<data>/state/tasks.db` (`python3 scripts/approval.py get <card id>`
   shows `status: answered`).
2. Within a minute the `approval_resume` beat (see the `orchestra up` table) delivers it:
   because the card came `--from hello --worker-kind pane`, the decision is typed into the
   `hello` tmux pane as a message and a durable row is written for the seat
   (`python3 msg_store.py inbox --agent hello`). `approval.py get` then shows
   `status: resumed`; `tmux capture-pane -p -t hello | tail -20` shows the delivered
   decision; `<data>/logs/approval_resume.log` has the delivery line.
3. `GET /api/approvals` keeps the card out of `pending` from the moment it is answered.

A card requested from an ambient shell behaves exactly like one a seat requested for
itself: the seat named in `--from` is the one that receives the answer.

## 5. Rotate a seat (manual, lossless)

A seat near its context ceiling banks a handoff (its prompt knows the format:
`<data>/docs/HANDOFF_<seat>-next.md` with a `## canary_questions` block anchored in its own
state). Then:

```bash
orchestra rotate gm --dry-run            # preconditions only
orchestra rotate gm                      # spawn successor -> it authors a readback -> strict grade -> promote
orchestra rotate hello --synthesize      # a seat that never banked: minimal baton (sid, ports, last mail ids)
orchestra rotate gm --resume             # a held attempt whose successor pane is still up
```

What "lossless" means here: the successor answers the canary questions from the handoff and
the repo alone; a generic readback HOLDs (predecessor keeps the seat, nothing is renamed). On
PASS the readback is committed in the data-dir repo (`orchestra init` made it one), the
registry flips to the new generation, the old pane is kept as `<seat>-gen<N>` for one
generation, and the successor gets its promotion prompt. Verify by effect:

```bash
python3 -c "import json;print(json.load(open('$ORCHESTRA_DIR/registry.json'))['agents']['gm'])"
tmux ls | grep gm
```

## 6. Check the rotation beat is armed (default ON)

`orchestra doctor` rows `rotation:beat` (armed, cadence, e-brake) and
`rotation:seats` (which T2 claude seats are eligible; the
`<runtime_dir>/self_retire_armed` allowlist — one lineage root per line — enables
hard rotation for a seat). E-brake: `touch ~/runtime/FLEET_BEAT_DISABLED`.

## Dev container / Docker (no VPS)

The repo ships a `Dockerfile` and `.devcontainer/devcontainer.json` that reproduce the
clean-machine recipe the B1 walkthroughs were proven on (ubuntu 24.04, non-root user
with sudo, §0 prerequisites, node 22, the Claude CLI preinstalled). `orchestra init`
runs at image build time, so `doctor` is instant on first open.

```bash
docker build -t orchestraos .
docker run -it --rm -p 8891:8891 -p 8888:8888 -p 8890:8890 orchestraos
# inside:  claude            # log in ONCE — your login, never baked into the image
#          orchestra doctor  # all required rows OK
#          orchestra up      # then open http://127.0.0.1:8891 on the laptop
```

- The login is yours: the image contains no credentials. To keep it across containers,
  mount your CLI config: `-v ~/.claude:/home/orchestra/.claude`. The dev container does
  that mount for you and keeps the data dir in a named volume (`orchestraos-data`).
- VS Code / GitHub Codespaces: "Reopen in Container". The workspace is bind-mounted over
  the image's copy, so `postCreateCommand` re-runs `orchestra init` once (~1 min) to
  rebuild `.venv` and `node_modules` for the mounted tree.
- Other CLIs: `docker build --build-arg AGENT_CLIS="@anthropic-ai/claude-code @openai/codex"`.
- The container is one instance on one host: tmux inside it is its own, so the
  registry-scoping rules below apply per container.

### Machine image (VPS snapshot) — 20-minute job once the provider is chosen

Same recipe, no Docker: on a fresh Ubuntu 24.04 VPS as a non-root sudo user, run the
`RUN` steps of the `Dockerfile` in order (§0 prerequisites, node 22, `npm i -g
@anthropic-ai/claude-code`, clone, `make install`, `orchestra init`), leave the agent CLI
logged OUT, then snapshot. A team booting the snapshot logs in, edits `[runtimes]
enabled`, and runs `orchestra doctor && orchestra up --detach`. Pre-provision one snapshot
per team (P5).

## Sharing a host with other tmux sessions

tmux is host-global. The dashboard's agent list, `agent-status.py --all` and the
rotation beat are **registry-scoped**: they only see sessions that resolve to a seat in
`<data>/registry.json` (its id or its `tmux_session`). `orchestra doctor` warns
(`tmux:foreign-sessions`) about the sessions it is ignoring. Set `[dashboard]
show_unregistered_sessions = true` to list them anyway; `agent-status.py --all
--all-sessions` is the host-wide escape hatch. One instance per host is still the
simplest setup.

## Where things live

- config: `orchestra.toml` (or `$ORCHESTRA_CONFIG`) — every key documented in `orchestra.example.toml`; secrets only via env
- data: `[data] dir` → `registry.json`, `state/` (sqlite, sessions, gateway token), `logs/`, `queue/`
- code: the checkout; `ORCHESTRA_ROOT` / `PYTHONPATH` are exported to every child by the supervisor

## Reference install (the operator's own setup: VPS + Mac over Tailscale, ntfy, Telegram, voice)

Not needed for the minimum path. See `orchestra.example.toml` `[machines]`,
`[notify]` and `services/arturo/run.sh` for the knobs; a step-by-step is tracked as
checklist item B2.
