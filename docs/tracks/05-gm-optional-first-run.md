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
- **Registration is the drain.** `orchestra spawn gm --gm` (landed on `main`;
  `orchestra_cli/seats.py`'s `register_seat()` + `cmd_spawn()` is where the `--gm`
  flag selects `prompts/gm.md`, tier T1, always-on) registers the seat's charter.
  No bespoke drain call is needed: once `gm` has a registry row, a
  `parked_no_target` row addressed to it is an ordinary pending row again, and the
  existing delivery paths already move it — the Claude Code hook layer's Stop-hook
  digest drains an idle seat's own pending mail on its own first turn, and
  `scripts/message-router.py`'s 60-second beat delivers any row it still finds
  parked. Arturo triggering `orchestra spawn gm --gm` itself when a second seat
  appears and no `gm` exists yet needs no special-cased follow-up call either — the
  same two paths pick the rows up the moment the registry row exists.
- **Router treats "missing target" as park, never dead-letter.**
  `scripts/message-router.py`'s dead-letter path (search `dead-letter` in that
  file, ~line 1443-1512, still accurate) currently escalates unreachable targets
  after enough retries; a target with zero registry rows must be excluded from
  that escalation path entirely — it is a park state, not a delivery failure,
  until a `gm` is spawned or the sender is told (once) that no manager exists yet.
  The router's idle-oracle reads the Claude Code hook layer's pane-event files
  from the data dir (`<data>/state/agent-events/panes/<N>.json`, not the
  checkout — `ORCH_EVENTS_DIR` in `scripts/message-router.py`), so a
  `parked_no_target` row and an ordinary busy-seat park are read the same way once
  the target exists.

## Files you will touch

- `msg_store.py` — `charter_exempt()` (~line 169) and the `send()` path (~line
  449): add the `parked_no_target` state; narrow the refusal condition.
- `scripts/message-router.py` — the dead-letter escalation logic (~line 1443
  onward, `<checkout>/msg_store.py` per `CODE_ROOT` is what the reply template
  points at): exclude rows whose target has no registry row from escalation; keep
  them parked. The router's hold file lives under `<data>/state`, not the
  checkout — match that if you touch its file-location code.
- `orchestra_cli/seats.py` — `register_seat()` / `cmd_spawn()` (the real
  `orchestra spawn gm --gm` implementation, landed) — this is where charter
  registration happens; no drain call to add here per the design above, but
  confirm registration itself is what unblocks delivery (Step 2).
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
4. `orchestra spawn gm --gm` — confirm the three parked rows deliver in send order
   (`msg_store.py inbox --agent gm` shows them, oldest first) via the ordinary
   Stop-hook-digest / router-beat paths, with no special drain code involved.
   `orchestra rotate <seat>` (also landed, `docs/INSTALL.md` §5) is the same-shape
   command if you need to rotate `gm` afterward.
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
design. `orchestra spawn gm --gm` and `orchestra rotate <seat>` are
already on main (orchestra_cli/seats.py) -- no need to gate on them
landing. Files to touch: msg_store.py (charter_exempt + send path: new
parked_no_target state), scripts/message-router.py (exclude no-registry
targets from dead-letter escalation; note the idle-oracle now reads pane
events from the data dir, not the checkout).
Start by reproducing today's actual failure mode per Step 1 of the doc —
don't assume it errors; confirm what really happens on a fresh data dir
before writing the fix. Then confirm registration alone (no bespoke
drain call) is enough to unblock delivery via the existing hook-digest
and router-beat paths.
```

## Out of scope

- Auto-spawning `gm` without an explicit command or Arturo-driven trigger (no
  silent background spawn).
- Multiple concurrent `gm`-equivalent seats (one manager lineage per install, same
  as today).
- Retroactively draining rows parked before this track existed (only new sends
  after the change use `parked_no_target`).
