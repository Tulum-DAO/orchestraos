# Deck 2 companion — Seats, identity, and messaging

> Companion to slide deck 2 from the Tulum Build-a-thon, 2026-09-19. Everything the slides claim is here with the file that proves it, the effect observed, and the known gaps. Give this page to your agent; it is written to be read by one.


Written for the Tulum Build-a-thon, Sat 2026-09-19. Same rules as
deck 1: every claim names the file that proves it or the effect observed, with
a date. Unverified claims are marked [UNVERIFIED]. Do not soften.

## The one-sentence version
A seat is a named, registered, supervised agent identity with a lineage, and
seats talk to each other only through a durable mailbox.

## Mechanism

### A seat is a row, not a window
- A seat is a tmux session plus a registry entry. `orchestra spawn <name>`
  writes the registry row and seeds generation 1 in the identity store
  (orchestra_cli/seats.py). The spawn script (spawn-agent.sh) builds the boot
  prompt, points the seat at its memory directory, and launches the CLI the
  operator chose: Claude, Gemini, or Codex, gated by AGENT_RUNTIME.
- Two stores. The SQLite identity store (state/orchestra-registry.db: tables
  lineages, generations, canonical, swaps) is the truth. The flat registry.json
  is a PROJECTION of it and can lag (docs/AGENT_STATUS_SURFACES.md, item D2). A
  reconciler on a ten-minute cron re-aligns them
  (scripts/identity_store/identity_reconciler.py).
- Tiers: T0 is the general manager, T1 project managers, T2 workers. The tier
  is a registry field, not a capability difference; it decides who routes work
  to whom (prompts/gm.md).

### Messages are rows
- msg_store.py is a SQLite-backed store. A message has an id, a thread, a
  sender, a recipient, a subject, a body, and a status (pending, delivered,
  acknowledged). Sending never touches the recipient's screen.
- The router (scripts/message-router.py, every minute) probes the recipient's
  pane. Idle: inject the row and mark it submitted. Busy: park it and retry.
  Parked rows are retried, never dead-lettered (docs/ARCHITECTURE.md line 40).
- Reading is separate from acknowledging. `inbox` shows pending rows; `ack`
  marks one processed. A Stop hook (agent-queue-drain.py) blocks a seat from
  going idle while it has unread rows older than 30 seconds.
- A message id is the unit of attribution. Corrections cite the id they
  correct.

## Proof by effect
- 2026-09-19, 02:52Z: this seat's own generation 2 was recorded in the identity
  store with canonical.generation_id pointing at it, generation 1 retired at
  the identical microsecond, both rows carrying a resume command. Read from the
  database, not from the rotation's report.
- 2026-09-18 night: across four seats, every correction of a false claim
  (misattributed PR, guard grade, DRAINED state, a broadcast concession) cited
  the message id it answered. The chain is reconstructible from the store alone.
- 2026-09-19, 02:5xZ: three copies of the same upload reached one seat while
  its status read "working"; all three sat as pending rows and none was lost.

## Known gaps, measured
- The flat registry recorded a seat's model as claude-opus-5 while the seat
  ran Fable 5.1 (2026-09-18). No component is the authoritative writer of the
  generations.model field. Roadmap P0.
- The router once dead-lettered the GM's inbox for about two hours because a
  live busy seat read as dead (message-router.py comment, line 393). Fixed by
  a guard; the class remains.
- One body sent to many recipients in the second person misattributes claims
  to whoever did not do the thing. Four seats produced this in one day
  (2026-09-18). The product fix (stamp each copy with its addressee) is queued;
  today it is a practice, not a guard.
- `msg_store send` does not wake an idle seat by itself; the router does, on
  its next minute. [Fleet memory note; mechanism verified in the router.]
