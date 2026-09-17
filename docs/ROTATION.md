# Rotation (blue-green)

Rotation is how a seat survives running out of context. An **agent** is a *seat* with a
*lineage*; each **generation** is one CLI session (Claude Code, Gemini CLI, or Codex).
When a generation nears its context ceiling it writes a handoff, a successor boots, proves
it understood the handoff by answering questions anchored in the predecessor's own state,
and is promoted. This is **autonomous and on by default** — the beat drives it; you do not
have to babysit it.

This document covers: what the beat does, how to watch it, how to trigger a manual
rotation, and the known per-runtime gaps. The Claude runtime is the supported path and the
one proven end-to-end on a clean install; Gemini and Codex are **experimental** (see
[Runtime support](#runtime-support-and-known-gaps)).

---

## What the beat does

The rotation beat lives in `scripts/lineage_daemon/`. It runs on two cadences (configured
in `orchestra.toml` under `[rotation]`):

| Beat | Default interval | Config key | Job |
|------|------------------|------------|-----|
| bus beat  | 60 s  | `bus_beat_interval_seconds`  | drains the inter-agent message bus |
| cron beat | 900 s | `cron_beat_interval_seconds` | the fleet rotation pass (`cron_beat.py`) |

Each cron beat, for every **armed** seat (wave-1 armed tier = `T2`), the beat runs one
pure decision function, `decide_bg` (`scripts/lineage_daemon/wal/decide_bg.py`), over an
observation of the seat (context %, idle/busy/death state, how long it has held that state,
whether anyone is attached, pending cards, composer text). The decision is one of:

- **noop** — nothing to do.
- **prewarm** — boot the green successor now so it is ready before the swap.
- **swap** — promote the green to canonical, retire the blue with a resume command.
- a **suppress**/**defer** reason — a safe hold when the beat cannot measure honestly.

The full sequence for a rotation (from `docs/ARCHITECTURE.md`):

> For an armed seat it **prewarms** a green successor, **waits for readiness** (the
> successor ingested the baton and its composer is live), **gates on vendor quota**,
> **swaps** canonical to the green, **retires the blue** with a resume command, **verifies
> by effect**, and **reaps** archive panes older than one generation. Every step **holds
> rather than guesses** when it cannot measure: unknown context, a stale screen, a depleted
> credit balance.

### The context ceiling and the retention window

`decide_bg` fires a ctx-driven swap on a hard backstop (context ≥ 0.80) or an earlier idle
lull (≥ 0.70 while idle). A context reading only counts if it is **calibrated**:

- A **fresh** read (from the runtime's live detector/status bar), in range `0..1`, is
  always calibrated.
- A **retained (stale) last-valid** read — used when the live read is missing — is
  calibrated **only on an IDLE seat, and only when the seat has been idle at least as long
  as the read is old** (`state_age_s >= ctx_age_s`). This is the *retention window*: the
  read must have been captured *inside the current idle stretch*, so "its ctx cannot have
  moved" still holds. A read that predates a busy stretch (the seat went busy→idle after
  the read) is fail-closed and the beat **defers** with the auditable reason
  `uncalibrated:stale-predates-idle` rather than swapping on a value that may be wrong.
- A **busy** seat's stale read, or a **wholly-unknown** read (no retained value), never
  calibrates.

Death always dominates: a dying seat is rescued regardless of context.

---

## How to watch it

The beat runs as a child of the supervisor. Start everything with:

```sh
orchestra up            # gateway + api + dashboard + beats, one supervisor (foreground)
orchestra up-detached   # same, in the background
orchestra status        # the supervisor's process table (is the beat alive?)
orchestra doctor        # checks CLIs+auth, ports, tmux, config keys, builds, and the beat
```

`orchestra doctor` reports a `rotation:beat` check: `OK` when it is enabled, `WARN` if
`[rotation] beat_enabled=false` or an emergency-brake file (`FLEET_BEAT_DISABLED` in the
runtime dir) is present.

Watch the beat's decisions live (paths are under the **data dir**, `ORCHESTRA_DIR`, default
`~/.orchestra`):

```sh
# every seat's per-beat decision line
tail -f "$ORCHESTRA_DIR/logs/fleet-beat.log"

# the log-only shadow pass: the decide_bg decision + a `justified` audit flag per armed
# seat, with zero side effects — the safest place to see what the beat WOULD do
tail -f "$ORCHESTRA_DIR/state/wal/decide_bg_shadow.jsonl"
```

A fleet-beat line reads like:

```
[fleet-beat] my-seat tier=T2 disposition=beat:noop action=noop reason=ctx:ok mode=ARMED -> beat:noop
```

Read the **BG decision line** (`action`/`reason`), not the `skip:*` line — an armed seat is
excluded from the legacy path by design and still rotates via the BG decision.

---

## How to trigger a manual rotation

Rotation is normally automatic, but you can force one seat to rotate now:

```sh
# ALWAYS dry-run first — it prints the plan and touches nothing
python3 scripts/rotate_agent.py <seat_name> --dry-run

# then, for real
python3 scripts/rotate_agent.py <seat_name> [--runtime RUNTIME] [--model MODEL] [--task TASK] [--force]
```

- `--runtime` / `--model` override what the successor boots on (default: inherit the seat's).
- `--force` bypasses soft holds (use sparingly; the readiness/quota gates still apply).
- The successor must pass the **comprehension readback** (it answers canary questions
  anchored in the predecessor's own state) before it is promoted; a failing readback holds
  the rotation instead of shipping a successor that did not understand the handoff.

---

## Runtime support and known gaps

| Runtime | Rotation | Notes |
|---------|----------|-------|
| **Claude Code** | **Supported** | Honest live context read; the end-to-end proof runs here. |
| **Gemini CLI**  | **Experimental** | No honest context read (see below). Death-driven rotation works; ctx-driven does not. |
| **Codex**       | **Experimental** | Credits/quota gate (see below). |

### Gemini — no honest context read *(experimental)*

The Gemini adapter has no reliable live context signal: the `jsonl` context source returns
out-of-range values, so the beat falls back to the retained last-valid read. The retention
window above keeps this conservative (it will `defer`/hold rather than swap on a value it
cannot trust after a busy stretch), but **ctx-driven rotation on Gemini is not dependable**
until the adapter reports an honest fraction. Rotate Gemini seats **on death** or
**manually** (`rotate_agent.py`), not on the context ceiling.

### Codex — credits gate *(experimental)*

A premium Codex model can have its workspace credits depleted independently of the 5-hour
usage window. A green booted on such a model comes up but stalls (it cannot ingest the
baton), so a swap onto it strands the seat. The **vendor-quota gate** (the beat's
"gates on vendor quota" step) checks credit availability at readiness before arming/swapping
a premium Codex green. Until that gate is fully wired (`quota_oracle`), treat Codex ctx
rotation as experimental: confirm the green actually woke (its composer went live) after any
Codex swap.

---

## Reproduce the Claude proof on a clean install

A tester should be able to reproduce one autonomous Claude rotation from a fresh checkout:

1. `orchestra init` — creates the data dir (`~/.orchestra` by default), `orchestra.toml`, venv, installs, builds.
2. `orchestra doctor` — confirm `rotation:beat` is `OK` and the Claude CLI is authed.
3. `orchestra up-detached` — start the supervisor (gateway + api + dashboard + beats).
4. Seed a demo seat: `orchestra init --demo` (three fixture seats), or register a real T2 Claude seat.
5. Force a rotation: `python3 scripts/rotate_agent.py <seat> --dry-run`, then without `--dry-run`.
6. Watch `tail -f "$ORCHESTRA_DIR/logs/fleet-beat.log"` and the shadow log; confirm the sequence **arm → prewarm → readiness → swap → verify**, and that the successor passed its readback and is now canonical (`orchestra status`).

The retention-window behavior is covered by
`scripts/lineage_daemon/wal/test_idle_ceiling_last_valid.py`
(`test_idle_seat_stale_read_predates_idle_not_calibrated`,
`test_idle_seat_read_within_idle_stretch_calibrated`); run
`python3 -m pytest scripts/lineage_daemon/wal/test_idle_ceiling_last_valid.py`.
