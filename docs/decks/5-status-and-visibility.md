# Deck 5 companion — Status and visibility

> Companion to slide deck 5 from the Tulum Build-a-thon, 2026-09-19. Everything the slides claim is here with the file that proves it, the effect observed, and the known gaps. Give this page to your agent; it is written to be read by one.


Written for the Tulum Build-a-thon, Sat 2026-09-19. Same rules as deck 1.
as doc 01. Lead with the failure the operator hit himself on 2026-09-19.

## The one-sentence version
Every surface that says what an agent is doing must agree, and today they do
not; the ground truth exists, and the readers do not all use it.

## The failure, observed 2026-09-19 ~03:00 Tulum
The operator could not send a file to a seat because the dashboard showed it
"working" while it sat idle. The seat was in fact mid-turn: every message had
arrived as a mid-turn message and each was answered with tool calls, so the
pane never returned to its prompt until the turn ended. The status was true
of the pane and useless to the operator. Two readings of one signal.

## Mechanism (what exists)
- Ground truth, push-based: scripts/state-event-hook.py fires on every
  SessionStart, PreToolUse, PostToolUse, Stop, UserPromptSubmit, SessionEnd,
  and Notification, and writes one small JSON per tmux pane to
  state/agent-events/panes/<pane>.json. Always exits 0, never prints. Consumed
  by scripts/agent-status.py. This is Tier 0 truth
  (docs/agent-state-truth-audit.md).
- Pull-based readers: pulse.py (two-minute liveness), state-snapshot-agents.sh
  (ten-minute snapshot, writes only timestamp fields), the composer probe
  (reads the pane screen for the prompt), the watch gateway's fleet feed, the
  dashboard API.
- Liveness by effect, not by name: pane_current_command measures the shell
  WRAPPER, not the agent; a live seat can read as "sh". A dead pane has three
  meanings: crashed, exited, never booted.
- Watchdogs: service-watchdog.sh revives port-holding services every two
  minutes; a Telegram watchdog revives the router; the blue-green build
  watchdog, the stranded alert, the blocker-surface watchdog.

## Proof by effect
- 2026-09-19: this seat's own registry rows read correctly from the identity
  store (generation, session id, model, promoted and retired timestamps) while
  its state/agents JSON carried no idle or busy field at all. The truth was in
  one store and not in the other.
- 2026-09-18: gm applied a fleet-level finding (a successful swap leaves the
  root DRAINED) to a seat that had never armed. The seat's own bg.json read
  SOLO with zero history. Reading the file corrected the inference within
  minutes. Rule recorded: a fleet-level finding is not a seat-level
  measurement.
- 2026-09-18: a "web port is DOWN" alert was forwarded without a probe; the
  port was up. An IP probe fails TLS and reads as nothing listening. Probe by
  hostname.

## Known gaps, measured
- Roadmap P0 "Agent Status Truth", added 2026-09-18 by the operator: one
  machine-readable status per seat; measure liveness at the point of the
  question; separate intent from capability; one signal one meaning; delete
  or wire instruments that cannot answer; an authoritative writer for the
  model field; acceptance criteria for every surface.
- Revival hides failure: a crash-looping service reads healthy on every
  surface because the watchdog brings it back inside two minutes.
- The blue-green census reads arming FLAGS, so a seat disarmed mid-flight is
  invisible in any state.
- bg.json history rows carry only reason and state, no timestamp; ordering
  cannot be reconstructed after the fact (docs/AGENT_STATUS_SURFACES.md C9).
- "Press up to edit queued messages" in the Claude TUI is status lag, not a
  stuck submit; escalating Enters makes it worse.
- A rotation report can say predecessor_archived null when the pane is still
  live. Verify the pane, never the field.
