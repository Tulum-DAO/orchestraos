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
git clone <repo-url> orchestraos && cd orchestraos
make install                 # symlinks bin/orchestra into ~/.local/bin (or use ./bin/orchestra)
orchestra init               # data dir (~/.orchestra), orchestra.toml, .venv + pip, npm install, builds
$EDITOR orchestra.toml       # set [runtimes] enabled to the CLI you logged in to, e.g. ["claude"]
orchestra doctor             # every row OK (WARN/INFO rows are advisory); exit code 0
```

`orchestra init` is idempotent: it never overwrites `orchestra.toml`, skips what
exists, and prints did/skipped per step. `--data-dir PATH` moves state elsewhere;
`--no-npm` / `--no-venv` / `--no-build` skip the slow steps.

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

No crontab is installed. `orchestra up --dry-run` prints this table without
starting anything. `orchestra down` stops it; `orchestra status` shows pids.

Open the dashboard: `http://127.0.0.1:8891` (ssh -L 8891:127.0.0.1:8891 if remote,
or set `[dashboard] host` / `[public] host`).

## 3. Spawn one seat

Register a seat in the data-dir registry, then spawn it in tmux:

```bash
source scripts/orchestra-env.sh          # exports ORCHESTRA_DIR etc. from orchestra.toml
REGISTRY_PATH=$ORCHESTRA_DIR/registry.json python3 scripts/registry-update.py hello \
    --field tier=T2 --field runtime=claude --field machine=vps --field cwd=$PWD
AGENT_RUNTIME=claude ./spawn-agent.sh hello --task "Say hello, then park."
tmux attach -t hello                     # detach with Ctrl-B D
```

(`runtime=gemini` / `codex` for the other CLIs. `./spawn-agent.sh --list` shows
registered seats, `--running` the live ones.)

## 4. Answer one approval card from the dashboard

From a shell (or let the seat run it):

```bash
source scripts/orchestra-env.sh
python3 scripts/approval.py request "Ship the hello change?" --from hello --worker-kind pane --options approve,deny
```

The card appears under Approvals in the dashboard; answer it there. The answer is
recorded in `<data>/state/tasks.db` and the gateway resumes the seat with the
decision.

## 5. Check the rotation beat is armed (default ON)

`orchestra doctor` rows `rotation:beat` (armed, cadence, e-brake) and
`rotation:seats` (which T2 claude seats are eligible; the
`<runtime_dir>/self_retire_armed` allowlist — one lineage root per line — enables
hard rotation for a seat). E-brake: `touch ~/runtime/FLEET_BEAT_DISABLED`.

## Where things live

- config: `orchestra.toml` (or `$ORCHESTRA_CONFIG`) — every key documented in `orchestra.example.toml`; secrets only via env
- data: `[data] dir` → `registry.json`, `state/` (sqlite, sessions, gateway token), `logs/`, `queue/`
- code: the checkout; `ORCHESTRA_ROOT` / `PYTHONPATH` are exported to every child by the supervisor

## Reference install (Shaw's setup: VPS + Mac over Tailscale, ntfy, Telegram, voice)

Not needed for the minimum path. See `orchestra.example.toml` `[machines]`,
`[notify]` and `services/arturo/run.sh` for the knobs; a step-by-step is tracked as
checklist item B2.
