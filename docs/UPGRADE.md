# Upgrading — pulling latest `main` without losing your seats

The harness is under active development during the hackathon. This doc covers
the safe way to pick up new code without killing your running agents or losing
what they remember.

## Why this is safe at all

Your seats' state lives in the **data dir** (`[data] dir` in `orchestra.toml`,
default `~/.orchestra`), completely separate from the **checkout** (the git
clone you're pulling into): `registry.json`, `state/` (sqlite, sessions, the
gateway token), `logs/`, `queue/`. Pulling new code into the checkout never
touches the data dir directly — a code change only affects a running seat once
something restarts and reads the new code. See `docs/INSTALL.md`'s "Where
things live" section for the full split.

That means the actual risk isn't "losing" a seat — the registry entry and its
memory directory survive a `git pull` untouched. The risk is a **contract
change**: new code that reads or writes `registry.json`, `msg_store.py`'s
schema, or the approvals tables in an incompatible way, running against state
written by the old code. `docs/ARCHITECTURE.md`'s Invariants section names
the contracts other things depend on — check changes there specifically.

## `orchestra upgrade`

One command:

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
   Claude Code hook rows are re-written against the new checkout path, venv/npm/builds
   refresh (`--no-venv` / `--no-npm` / `--no-build` are passed through).
5. `orchestra doctor`; its exit code is the command's.

It never restarts anything. Spawned seats keep the code they were spawned with until
their next spawn or rotation; the supervisor's services and beats pick the new code up on:

```bash
orchestra down && orchestra up --detach
```

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
orchestra init --yes    # idempotent: re-runs npm/venv/build steps only, never
                         # touches orchestra.toml or the data dir; --yes re-writes the
                         # same hook rows without the prompt
orchestra doctor         # confirm every row is still OK
```

If `orchestra doctor` is still green, the checkout is upgraded and nothing
running was disturbed. To actually pick up the new code in a live process:

```bash
orchestra down && orchestra up --detach   # restarts gateway/api/dashboard/beats
```

Spawned seats (tmux sessions like `hello`) are **not** part of the supervisor
and are not touched by `orchestra down`/`up` — they keep running the CLI
session they were spawned with. A seat only picks up harness-side code changes
(a changed `spawn-agent.sh`, a changed rotation beat behavior) the next time
it's respawned or rotated. If a change specifically requires every seat to
restart (rare — the commit message should say so), rotate each one by hand
(`docs/GATE.md` step 6) rather than killing panes directly.

## If something breaks after upgrading

`orchestra doctor` is the first check, same as always — a broken row after a
pull almost always points at the exact file that changed shape. If a spawned
seat starts behaving strangely after a harness change, check
`<data>/logs/` for the relevant beat before assuming the seat itself is at
fault; the beats (router, rotation, approval_resume) are what most contract
changes actually land in.
