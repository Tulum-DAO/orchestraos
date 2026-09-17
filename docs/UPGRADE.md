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

## `orchestra upgrade` (target)

**Lands in PR: orchestra-builder.** Not on `main` as of this writing — check
`orchestra --help` before relying on it. Once it ships, the intended sequence
is a single command:

```bash
orchestra upgrade
```

Target behavior, per orchestra-builder (who owns this command's build): `git
pull --ff-only`, then `orchestra init` — idempotent, so config is kept, the
Claude Code hook layer is re-installed against the new checkout path, and the
data-git step stays present — then `orchestra doctor`. Spawned seats are
untouched by design: their tmux sessions keep running the code they were
spawned with, and pick up the new checkout only at their next spawn or
rotation. Showing what changed before pulling (`git log --oneline
HEAD..origin/main`, flagging `scripts/lineage_daemon/`, `msg_store.py`, or
`scripts/approval*.py` specifically — the contract-bearing paths) is this doc's
recommendation for the by-hand path below; confirm whether the shipped
`orchestra upgrade` does the same before assuming it. It does **not** restart your
running supervisor or any spawned
seat on its own — a code change only takes effect for a component once that
component restarts, and restarting a live seat's tmux session is your call,
not the upgrade command's.

## Doing it by hand today

Until `orchestra upgrade` lands, the same sequence run manually:

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
