# Upgrading — pulling latest `main` without losing your seats

The harness is under active development. This doc covers
the safe way to pick up new code without killing your running agents or losing
what they remember.

## Why this is safe at all

Your seats' state lives in the **data dir** (`[data] dir` in `orchestra.toml`,
default `~/.orchestra`), completely separate from the **checkout** (the git
clone you're pulling into): `registry.json`, `state/` (sqlite, sessions, the
gateway token), `logs/`, `queue/`. Pulling new code into the checkout never
touches the data dir directly — a code change only affects a running seat once
something restarts and reads the new code. See [docs/INSTALL.md](INSTALL.md)'s "Where
things live" section for the full split.

That means the actual risk isn't "losing" a seat — the registry entry and its
memory directory survive a `git pull` untouched. The risk is a **contract
change**: new code that reads or writes `registry.json`, `msg_store.py`'s
schema, or the approvals tables in an incompatible way, running against state
written by the old code. [docs/ARCHITECTURE.md](ARCHITECTURE.md)'s Invariants section names
the contracts other things depend on — check changes there specifically.

## `orchestra upgrade`

One command, run from inside your orchestraos checkout (`cd orchestraos`), like
every command on this page:

```bash
orchestra upgrade              # fetch, show what's coming, pull --ff-only, init --yes, doctor
orchestra upgrade --dry-run    # only the "what's coming" part
```

What it does, in order:

1. `git fetch origin`, then prints the incoming commits (`git log HEAD..@{u}`) and flags
   any **contract-bearing paths** in the diff — `scripts/lineage_daemon/`, `msg_store.py`,
   `scripts/approval*.py` — the ones running components trust not to change shape.
   `--dry-run` stops here.
2. Refuses if the checkout has uncommitted changes to tracked files (exit 2) — a
   fast-forward would fail anyway, and you should know why.
3. `git pull --ff-only` (exit 1 with the git message if the branch diverged).
4. `orchestra init --yes`: idempotent — `orchestra.toml` and the data dir are kept, the
   Claude Code hook rows are re-written against the new checkout path, venv and npm
   refresh, and the API and dashboard are rebuilt when any of their source is newer than
   the last build (`--no-venv` / `--no-npm` / `--no-build` are passed through).
   When `<data>/state/tailscale-users` is absent, it also writes the Tailscale account this server is
   signed in with as **admin** of the web terminal and voice call. If someone else's account (or a pre-auth key they made) signed the
   server in, that account becomes admin: check the login the step prints. While `ORCHESTRA_TAILSCALE_USERS` is set,
   it overrides the file.
5. `orchestra doctor`; its exit code is the command's.

Then restart, so the running services use the new code:

```bash
orchestra down && orchestra up --detach
```

(By-hand installs only. If `systemctl is-enabled orchestra` says `enabled`, see
"Restarting a systemd install" below.)

**If you upgraded between #208 and #218 (2026-10-08):** back then `init` did not rebuild an
existing API build, so your API may still lack the web terminal origin check from #208.
Run `orchestra upgrade` once more (or `rm -rf api/dist && orchestra init --yes`, which removes only the API's build output inside
your checkout; `init` rebuilds it straight away), then the
restart above. From #218 on, a plain upgrade rebuilds correctly.

`orchestra upgrade` never restarts anything itself. Spawned seats keep the code they were
spawned with until their next spawn or rotation.

## Restarting a systemd install

The restart above (`orchestra down && orchestra up --detach`) is for a checkout you run by hand.
An install made by `install-client.sh`, or any box where you ran `orchestra install-unit --now`,
runs the stack under systemd as `orchestra.service`. On such a box `orchestra up --detach` starts
a **second** supervisor that systemd does not know about, and `orchestra down` leaves the unit
stopped. Use systemd instead.

Which kind of install you have:

```bash
systemctl is-enabled orchestra   # "enabled": a systemd install; "not-found" or "disabled": by hand
```

On a systemd install, as root (or with `sudo`):

```bash
systemctl restart orchestra      # the supervisor and its children (gateway, api, dashboard, beats)
systemctl status orchestra       # active (running)
```

Restarting the unit does not stop your seats; they keep running in tmux as before.

`install-client.sh` makes the checkout belong to a normal user (`orchestra` by default), not to
root. When you are logged in as root, run the upgrade as that user, then restart the unit as root:

```bash
su - orchestra -c 'cd ~/orchestraos && ~/.local/bin/orchestra upgrade'   # replace "orchestra" with the user that owns the checkout
systemctl restart orchestra
```

`orchestra` is on that user's `PATH` through `~/.local/bin`; the full path avoids depending on
the login shell's profile.

After either kind of restart, reload the dashboard once in the browser (a plain reload is enough).
An open tab keeps the old page's code until it is reloaded, so the first request after a restart
can show stale screens or fail against the new API.

## Doing it by hand

The same sequence, step by step:

```bash
git fetch origin
git log --oneline HEAD..origin/main
```

Read the list. Anything touching `scripts/lineage_daemon/`, `msg_store.py`, or
`scripts/approval*.py` — read that commit's message before merging; those are
the paths other running components trust not to change shape underneath them.

```bash
git pull
orchestra init --yes    # idempotent: re-runs npm/venv steps and rebuilds what changed, never
                         # touches orchestra.toml or the data dir; --yes re-writes the
                         # same hook rows without the prompt
orchestra doctor         # confirm every row is still OK
```

If `orchestra doctor` is still green, the checkout is upgraded and nothing
running was disturbed. To actually pick up the new code in a live process:

```bash
orchestra down && orchestra up --detach   # restarts gateway/api/dashboard/beats
```

On a systemd install, `systemctl restart orchestra` instead (see "Restarting a systemd install" above).

Spawned seats (tmux sessions like `hello`) are **not** part of the supervisor
and are not touched by `orchestra down`/`up` — they keep running the CLI
session they were spawned with. A seat only picks up harness-side code changes
(a changed `spawn-agent.sh`, a changed rotation beat behavior) the next time
it's respawned or rotated. If a change specifically requires every seat to
restart (rare — the commit message should say so), rotate each one by hand
([docs/GATE.md](GATE.md) step 6) rather than killing panes directly.

## If something breaks after upgrading

`orchestra doctor` is the first check, same as always — a broken row after a
pull almost always points at the exact file that changed shape. If a spawned
seat starts behaving strangely after a harness change, check
`<data>/logs/` for the relevant beat before assuming the seat itself is at
fault; the beats (router, rotation, approval_resume) are what most contract
changes actually land in.
