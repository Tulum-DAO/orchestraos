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
| approval_resume | `approval_resume.py` — delivers an answered card to its seat (pane inject + msg_store row) | every 60 s |

No crontab is installed. `orchestra up --dry-run` prints this table without
starting anything. `orchestra down` stops it; `orchestra status` shows pids.

Smoke check: `curl -s http://127.0.0.1:8888/api/health` → `{"status":"ok", "db":{"open":true}, ...}`
(`orchestra doctor` runs the same probe as `api:health` while the supervisor is up).

Open the dashboard: `http://127.0.0.1:8891` (ssh -L 8891:127.0.0.1:8891 if remote,
or set `[dashboard] host` / `[public] host`).

## 3. Spawn one seat

Register a seat in the data-dir registry, then spawn it in tmux:

```bash
source scripts/orchestra-env.sh          # exports ORCHESTRA_DIR etc. from orchestra.toml
REGISTRY_PATH=$ORCHESTRA_DIR/registry.json python3 scripts/registry-update.py hello \
    --field name=hello --field tmux_session=hello \
    --field tier=T2 --field runtime=claude --field machine=vps --field cwd=$PWD
AGENT_RUNTIME=claude ./spawn-agent.sh hello --task "Say hello, then park."
tmux attach -t hello                     # detach with Ctrl-B D
```

- `machine=vps` is a label. On a single-machine install (`[machines]` left blank in
  `orchestra.toml`) the spawner never dispatches elsewhere, so any label works; the
  label only matters once you fill in `[machines]` for a two-host setup.
- `tmux_session` defaults to the seat id if you leave it out (the spawner records it).
- The spawner pre-seeds Claude Code's workspace-trust bit for `cwd`
  (`scripts/ensure_cwd_trusted.py`), so the seat does not stop at "Is this a project
  you trust?". If you see that prompt anyway, answer it once in `tmux attach`.
- `runtime=gemini` / `codex` for the other CLIs. `./spawn-agent.sh --list` shows
  registered seats, `--running` the live ones.

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

## 5. Check the rotation beat is armed (default ON)

`orchestra doctor` rows `rotation:beat` (armed, cadence, e-brake) and
`rotation:seats` (which T2 claude seats are eligible; the
`<runtime_dir>/self_retire_armed` allowlist — one lineage root per line — enables
hard rotation for a seat). E-brake: `touch ~/runtime/FLEET_BEAT_DISABLED`.

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
