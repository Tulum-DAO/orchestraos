# Reference install — the operator's full setup

`docs/INSTALL.md` is the **minimum path**: one machine, one CLI, one supervisor process,
no push, no voice. This page is the **reference install**: the setup the harness runs on
every day. Do the minimum path first; every section below is a knob you *add* to it, and
each one says why it exists and what breaks if you leave it out. Hosts and ids are
placeholders — `<vps>`, `<laptop>`, `<tailnet>`, `<chat-id>` — fill in your own.

| | Minimum path | Reference install |
|---|---|---|
| machines | 1 | 2: a VPS (`<vps>`, always on) + a laptop (`<laptop>`) joined over Tailscale |
| process model | `orchestra up` supervisor | supervisor **or** crontab + systemd units (this page) |
| seats | one, spawned by hand | 20–50 tmux seats, registry-driven, auto-rotated |
| decisions reach you via | web dashboard | dashboard + phone/watch app, push (ntfy or APNs), Telegram |
| voice | off | Arturo voice brain (BYO vendor keys) |
| data dir | `~/.orchestra` | the checkout itself (`[data] dir` = repo root) |

## 0. Topology

```
          Tailscale (<tailnet>)
 <laptop> ───────────────────── <vps>  (Ubuntu 24.04, non-root sudo user)
  - agent CLIs logged in         - the checkout, tmux server, all seats
  - browser -> dashboard         - gateway :8890, api :8888, dashboard :8891
  - phone/watch app (paired)     - arturo :5071 (optional), ntfy :9080 (optional)
```

Only the VPS runs seats. The laptop is a client (browser, phone, and the
"resume auth on the other machine" helper). `[machines]` in `orchestra.toml` records the
two Tailscale addresses; leave it blank on a single machine.

```toml
[machines]
mac_tailscale_ip = "<laptop tailnet ip>"
mac_ssh_user     = "<laptop user>"
vps_tailscale_ip = "<vps tailnet ip>"
remote_auth_host = "<laptop user>@<laptop tailnet ip>"   # blank disables the helper
vps_hostname     = "<substring of the VPS hostname>"      # spawn-agent's machine dispatch
```

**Why:** `spawn-agent.sh` refuses to spawn a seat whose registry row says `machine=vps`
on a box that is not the VPS once `[machines]` is filled in. With `[machines]` blank
(minimum path) it never dispatches elsewhere.

## 1. Data dir = checkout

The reference install keeps `registry.json`, `state/`, `logs/` **inside the checkout**
(`[data] dir` = the repo root) because 40 seats and 45 cron entries were written before
the split existed. The minimum path uses `~/.orchestra`. Both work: every process reads
the data dir from `ORCHESTRA_DIR` (exported by `scripts/orchestra-env.sh` and by the
supervisor) and code paths from the checkout. If you copy the reference layout, keep
`state/` and `logs/` out of git (they are in `.gitignore`).

## 2. Long-running services

The minimum path runs everything under one supervisor. The reference install runs the
same processes as crontab entries plus two systemd units, because they predate the
supervisor and are restarted by a watchdog cron. Pick one model; do not run both.

| process | supervisor (`orchestra up`) | reference install |
|---|---|---|
| gateway `scripts/watch_gateway.py` | service | tmux service pane, watchdog-restarted |
| api `api/dist/server.js` | service | tmux service pane |
| dashboard `dashboard-proxy.js` | service | tmux service pane |
| arturo `services/arturo/run.sh` | optional service | tmux service pane (voice keys, §6) |
| `lineage_daemon/bus_beat.py` | beat 60 s | `* * * * *` |
| `lineage_daemon/boundary_delivery.py` | beat 60 s, armed | `* * * * *` with `BOUNDARY_DELIVER_ARMED=1` |
| `lineage_daemon/cron_beat.py` (rotation) | beat 900 s | `*/15` after `identity_reconciler --cron` |
| `message-router.py --cron` | beat 60 s | `* * * * *` |
| `approval_resume.py` | beat 60 s | `* * * * *` with `EXPIRE_PENDING=0` |
| `approval_notify.py` (push backstop) | — (no push in the minimum path) | `* * * * *` |
| `identity_store/orphan_pane_scan.py` | — | `*/15` (flags raw tmux spawns, chip only) |
| `identity_store/identity_reconciler.py --cron` | — | `*/15` |
| `agent-recovery.sh --boot` | — | `@reboot sleep 45` (re-attaches seats after a reboot) |
| `lineage_daemon/telemetryd.py` | — | systemd unit (see issue 3 in `HACKATHON_ISSUES.md`: the unit needs templating) |

A minimal reference crontab (user crontab on the VPS; `cd` into the checkout so
`orchestra-env.sh` finds `orchestra.toml`):

```cron
* * * * *     cd <checkout> && python3 scripts/lineage_daemon/bus_beat.py >> logs/bus-beat.log 2>&1
* * * * *     cd <checkout> && BOUNDARY_DELIVER_ARMED=1 python3 scripts/lineage_daemon/boundary_delivery.py >> logs/boundary-delivery.log 2>&1
* * * * *     cd <checkout> && python3 scripts/message-router.py --cron >> logs/message-router.log 2>&1
* * * * *     cd <checkout> && EXPIRE_PENDING=0 python3 scripts/approval_resume.py >> logs/approval-watchdog.log 2>&1
* * * * *     cd <checkout> && python3 scripts/approval_notify.py >> logs/approval-notify.log 2>&1
*/15 * * * *  cd <checkout> && python3 -m scripts.identity_store.identity_reconciler --cron >> logs/identity-reconciler-cron.out 2>&1 ; python3 scripts/lineage_daemon/cron_beat.py >> logs/fleet-beat.log 2>&1
*/15 * * * *  cd <checkout> && python3 scripts/identity_store/orphan_pane_scan.py >> logs/orphan-scan.log 2>&1
@reboot       sleep 45 && bash <checkout>/scripts/agent-recovery.sh --boot >> <checkout>/logs/agent-recovery.log 2>&1
```

**Why `EXPIRE_PENDING=0`:** approval cards never expire on their own (operator ruling);
the resume watchdog re-delivers until the seat acks. **Why the beat rides after the
reconciler:** the rotation beat reads the identity store the reconciler just repaired.

## 3. Seats: registry, tmux, rotation

- Every seat is a row in the data-dir `registry.json` (id, tier, runtime, model, machine,
  cwd, `tmux_session`) and one tmux session of that name; `spawn-agent.sh <id>` is the
  only sanctioned way to start one (it pre-seeds the CLI's first-run dialogs, injects the
  init prompt, records the session id).
- The fleet is **registry-scoped**: the beat, `agent-status.py --all` and the dashboard
  only see sessions that resolve to a registry row. Service panes (gateway, api, arturo,
  ntfy) live in tmux too but are not seats.
- Rotation (blue-green, `[rotation]` in `orchestra.toml`) ships ON by default: at
  `ctx_ceiling_pct` a successor is spawned, reads the predecessor's baton
  (`docs/HANDOFF_<seat>-next.md`), answers its canary questions, and is promoted;
  `reap_after_generations` keeps the previous pane as a fallback. Per-seat arming for
  hard rotation is the `<runtime_dir>/self_retire_armed` allowlist; the e-brake is
  `<runtime_dir>/FLEET_BEAT_DISABLED`. `runtime_dir` is a NON-synced directory (the
  reference install uses `~/runtime`) so a sync tool never copies an e-brake between machines.
- Tiers: T0 (the always-on manager seat), T1 (coordinators), T2 (workers, the armed tier).

## 4. Push: ntfy or APNs (choose one, or none)

The minimum path has no push: cards wait in the dashboard. The reference install pushes
every card to a phone/watch.

**ntfy (self-hosted, the reference default).** Run the `binwiederhier/ntfy` container on
the VPS (`:9080`, reachable only over the tailnet) and point the notifier at it:

```bash
export NTFY_BASE=http://127.0.0.1:9080          # default
export NTFY_APPROVALS_TOPIC=approvals          # default
export NTFY_ANSWERS_TOPIC=approval-answers     # default
export NTFY_TOKEN_FILE=~/.config/<app>/ntfy-token   # a token for the topics above
```

`approval_notify.py` publishes one message per pending card with three id-bound action
buttons; the phone app subscribes. **Why tailnet-only:** the push carries card ids and
question text; keeping the server off the public internet is the whole security model.
The known gap: a phone off the tailnet gets no push until it is back.

**APNs (track T8).** `scripts/apns_notify.py` + `apns_devices.py` send directly to paired
devices with a bundled key; no ntfy server. Device tokens come from pairing (track T1).

## 5. Telegram (plugin, optional)

Informational messages (task done, links) and the notify backstop go to a Telegram bot.
Put the bot token and your chat id in `<data>/.env.telegram`
(`TELEGRAM_BOT_TOKEN=…`, `<OPERATOR>_TELEGRAM_ID=…`), set `[notify] channel = "telegram"`
and `[notify.telegram] chat_id = "<chat-id>"` (the token itself is only ever read from the
env file / the env var named by `bot_token_env`, never from `orchestra.toml`).
`scripts/tg-notify.sh` is the one sender every script uses: it verifies delivery and
exits non-zero on failure. Track T6 moves this under `plugins/`.

## 6. Voice: Arturo (BYO keys)

`services/arturo` is the voice brain (`[arturo] enabled = true`, `:5071`). It needs vendor
keys in the environment of `services/arturo/run.sh`: a Gemini API key for the
conversational turn today (track T2 makes the authed CLI the zero-key brain) and an
ElevenLabs or Cartesia key for speech. Without keys the supervisor child restarts until
you set `enabled = false`. The run script's `ARTURO_*` switches (stream relay, partials,
speaking flip, semantic and facts recall, gm injection) default to the reference values;
leave them unless you are working on the voice lane.

## 7. Phone and watch app

The iOS app talks to the gateway (`[gateway]` host/port, bearer from
`<data>/state/watch-gateway-token`) over the tailnet. Today the base URL and token are
built into the app (track T1 replaces that with pairing). Cards render natively on the
watch; answers post back to the gateway, which resumes the seat exactly like a dashboard
answer.

## 8. What else the reference crontab runs (not part of the harness)

The operator's crontab also carries ~25 entries that are private to that fleet
(knowledge-base indexers, client pipelines, snapshots, reminders, a disk guard). None of
them are needed for the harness and none ship in this repo; if you see a script name in a
log line that is not in `scripts/`, that is why.

## 9. Which knobs differ from the minimum path, in one list

1. `[machines]` filled in (two hosts over Tailscale) — enables machine dispatch and the
   remote-auth helper.
2. `[data] dir` = the checkout instead of `~/.orchestra`.
3. Beats and services from crontab/systemd instead of `orchestra up`.
4. `approval_notify.py` on a per-minute cron + an ntfy server (or APNs) — push.
5. `[notify] channel = "telegram"` + `.env.telegram` — Telegram.
6. `[arturo] enabled = true` + vendor keys — voice.
7. The phone/watch app pointed at the gateway over the tailnet.
8. `runtime_dir` on a non-synced path; per-seat `self_retire_armed` allowlist.

Everything else — the registry model, msg_store, the approval ledger, the gateway
contract, rotation — is identical in both installs.
