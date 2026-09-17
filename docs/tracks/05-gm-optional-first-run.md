# Track 5 — Manager-seat-optional first run

Size: M · Labels: `track`, `core`

## Problem

Arturo's commission tool and the router both assume a `gm` (general-manager) seat
already exists. `msg_store.py`'s `charter_exempt()` (line ~169) refuses mail to a
seat with "no charter yet — a newborn cannot be [addressed]", and
`outbound_brake()` (line ~188) reasons about a lane already having unread mail —
neither path considers "the target seat has never existed." A fresh install with
one worker seat and no `gm` has nowhere for Arturo's "commission an agent to X" to
land: the send either errors or silently vanishes, and the front page's routing
(`dashboard/src/pages/AgentPage.tsx` defaults `agentId` to `'gm'`) has nothing to
show.

## Design

Two changes, both additive:

- **Park instead of refuse.** `msg_store.py`'s send path gets a new terminal state,
  `parked_no_target`, used when `to_agent` has no registry row at all (distinct
  from "has a charter but is busy," which already parks correctly via the router).
  A parked-no-target row is visible in Inbox like any other pending row — it is not
  dead-lettered and not silently dropped. `charter_exempt()`'s refusal path
  narrows to only the cases that are genuinely errors (self-addressed, malformed
  target), not "doesn't exist yet."
- **Spawn drains on first effect.** `orchestra spawn gm` (new CLI path — check
  whether it has landed in main; `orchestra_cli/__main__.py`'s subcommand list is
  the source of truth) registers the seat, then, as part of its own first-boot
  sequence, queries `msg_store.py` for every `parked_no_target` row addressed to
  `gm` and delivers them in send order (oldest first) the same way the router
  delivers any other parked row. Arturo can also trigger this spawn itself when a
  second seat appears and no `gm` exists yet — same drain call, just a different
  caller.
- **Router treats "missing target" as park, never dead-letter.**
  `scripts/message-router.py`'s dead-letter path (search `dead-letter` in that
  file, ~line 1443-1512) currently escalates unreachable targets after enough
  retries; a target with zero registry rows must be excluded from that escalation
  path entirely — it is a park state, not a delivery failure, until a `gm` is
  spawned or the sender is told (once) that no manager exists yet.

## Files you will touch

- `msg_store.py` — `charter_exempt()` (~line 169) and the `send()` path (~line
  449): add the `parked_no_target` state; narrow the refusal condition.
- `scripts/message-router.py` — the dead-letter escalation logic (~line 1443
  onward): exclude rows whose target has no registry row from escalation; keep them
  parked.
- `orchestra_cli/__main__.py` / wherever `orchestra spawn gm` lands — add the
  drain-on-first-effect call; if `spawn` hasn't merged yet, this hooks into
  whatever the successor of `spawn-agent.sh`'s registration step is.
- `services/arturo/dispatcher.py` — confirm the commission tool call still writes
  the `msg_store` row unconditionally, regardless of whether `gm` currently exists
  (it should — the parking happens inside `msg_store.py`, not in the caller).
- `dashboard/src/pages/AgentPage.tsx` — confirm the `/agent` route degrades sanely
  (empty-state, not a crash) when `gm` has no registry row yet.

## Steps

1. `python3 msg_store.py send --from arturo --to gm --type task --subject test
   --body "hello"` on a fresh data dir with no `gm` registered — baseline today's
   behavior (confirm what actually happens: error, silent drop, or something else)
   before changing anything.
2. Add `parked_no_target` and the narrowed refusal condition; repeat step 1 —
   expect a row visible in Inbox with that status, no error.
3. Send two more commissions the same way — confirm all three are visible as
   parked, in order.
4. Register and spawn `gm` (`orchestra spawn gm` once it exists, or the manual
   registry-update + spawn-agent.sh path from `docs/INSTALL.md` §3 in the
   meantime) — confirm the drain delivers all three rows in send order
   (`msg_store.py inbox --agent gm` shows them, oldest first).
5. Confirm the router's dead-letter path never fires for a target with zero
   registry rows: send a commission, wait past whatever retry window would
   normally escalate a busy-seat park, confirm no dead-letter notice.

## Acceptance test

Fresh install, one worker seat registered, no `gm`. Three commissions sent via
Arturo (or directly via `msg_store.py send --to gm`) are all visible as parked in
Inbox, not errored and not dead-lettered. Spawning `gm` delivers all three, in
order, on its first effect.

## Start prompt

```
I'm working Track 5 (manager-seat-optional first run) for the OrchestraOS
hackathon.
Read docs/tracks/05-gm-optional-first-run.md in this repo for the full
design. Files to touch: msg_store.py (charter_exempt + send path: new
parked_no_target state), scripts/message-router.py (exclude no-registry
targets from dead-letter escalation), and wherever `orchestra spawn gm`
lands (drain parked_no_target rows on first effect).
Start by reproducing today's actual failure mode per Step 1 of the doc —
don't assume it errors; confirm what really happens on a fresh data dir
before writing the fix.
```

## Out of scope

- Auto-spawning `gm` without an explicit command or Arturo-driven trigger (no
  silent background spawn).
- Multiple concurrent `gm`-equivalent seats (one manager lineage per install, same
  as today).
- Retroactively draining rows parked before this track existed (only new sends
  after the change use `parked_no_target`).
