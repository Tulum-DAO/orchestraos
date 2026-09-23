# Agent provisioning guide

How a seat comes into existence, gets work, and gets replaced.

`prompts/infrastructure.md` points every agent here, so this is often the first document a new
agent reads. It is written to be true of a **fresh install** — every command below was run against
one.

A **seat** is a named, long-lived role (`gm`, `docs-writer`). A **generation** is one process
filling that seat. Rotating a seat replaces the generation and keeps the name, the mail address
and the history.

---

## 1. Create a seat

```
orchestra agent create <name> [--tier T2] [--runtime claude] [--model <id>] [--parent <seat>]
```

One command does the whole thing: fills a role template, registers the seat (with its parent),
validates the runtime/model pair, spawns it, and verifies it is alive.

| Flag | Meaning |
|---|---|
| `--tier` | `T0` the always-on manager · `T1` coordinator · `T2` worker (default) |
| `--runtime` | `claude`, `gemini` or `codex`; defaults to the first enabled in `[runtimes]` |
| `--model` | a model id **belonging to that runtime** — `orchestra doctor` lists them |
| `--parent` | the seat this one reports to, recorded in the registry |
| `--template` | role template to fill; omit for the default |
| `--set KEY=VALUE` | substitutions into that template |
| `--task` | first instruction injected into the pane |

There is no manual step. You do **not** hand-edit `registry.json` to add an agent — the registry
is written for you, and an entry added by hand can disagree with what the spawner validated.

## 2. Spawn, or re-spawn, an existing seat

```
orchestra spawn <seat> [--task "..."] [--runtime ...] [--model ...] [--tier ...] [--prompt ...]
orchestra spawn --gm            # the General Manager: prompts/gm.md, tier T0, always-on
```

`agent create` already spawns. Use `spawn` to bring a seat back up, or to launch one whose prompt
and registry entry already exist.

`--task` is the first thing the agent reads after its system prompt. A seat spawned with no task
sits idle: it is alive and waiting, not broken.

## 3. Write the seat's prompt

A seat's system prompt is `prompts/<seat>.md`. `agent create` writes one from a template; edit
that file to change what the seat is for. Two prompts are always injected alongside it:

- `prompts/FOUNDATION_STATIC.md` — shared foundation, prepended when present.
- `prompts/infrastructure.md` — how to send mail, reach the gateway, find the data dir.

> **Fresh installs:** `FOUNDATION_STATIC.md` is not shipped in this repo. `spawn-agent.sh` warns
> and uses the role prompt alone. That is a working spawn, not a failure — see issue #121.

## 4. Where a seat's things live

Two roots, and confusing them is the most common install-time mistake:

| | |
|---|---|
| `$ORCHESTRA_ROOT` | the **checkout** — `msg_store.py`, `scripts/`, `prompts/` |
| `$ORCHESTRA_DIR` | the **data dir** from `orchestra.toml` `[data] dir` — `registry.json`, `state/`, `queue/`, `logs/` |

Both are exported into the pane by `spawn-agent.sh`. Code lives in the first; anything an agent
writes belongs in the second. A component that defaults to the checkout when it means the data dir
is the bug behind issues #86 and #121.

## 5. Rotate a seat

```
orchestra rotate <seat> [--dry-run] [--synthesize] [--resume] [--runtime ...] [--model ...]
```

Rotation hands a seat from one generation to the next without losing the name or the mail. The
outgoing generation banks a **baton** (a handoff document); the incoming one answers a
comprehension **canary** derived from it and is promoted only if it passes.

| Flag | Use |
|---|---|
| `--dry-run` | check preconditions, change nothing — run this first |
| `--synthesize` | write a minimal baton when the outgoing seat never banked one |
| `--resume` | the successor pane is already up from a held attempt: grade its readback and promote, do not spawn again |

Authoring canary questions has its own rules — see `docs/RUNBOOK_canary-authoring.md`. The short
version: no answer key ever ships in the artifact, and each question carries a `source_pointer`
into the predecessor's transcript.

## 6. Check your work

```
orchestra doctor     # CLIs + auth, ports, tmux, config keys, builds, rotation beat
orchestra status     # what is running now
```

`doctor` is the fastest way to find out whether a runtime is authenticated and whether the model
you passed belongs to it — both are failures that otherwise appear as a seat that spawns and then
does nothing.

## Related

- `orchestra pair` — pair a phone with this gateway.
- `orchestra up` / `down` — run the whole stack under one supervisor.
- `docs/RUNBOOK_canary-authoring.md` — writing canary questions for a rotation.
