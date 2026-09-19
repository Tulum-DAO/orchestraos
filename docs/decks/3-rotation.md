# Deck 3 companion — Rotation

> Companion to slide deck 3 from the Tulum Build-a-thon, 2026-09-19. Everything the slides claim is here with the file that proves it, the effect observed, and the known gaps. Give this page to your agent; it is written to be read by one.


Written for the Tulum Build-a-thon, Sat 2026-09-19. Same rules as deck 1.
as doc 01. This is the deck a wrong slide came from; it carries the most proof.

## The one-sentence version
A seat can be replaced by a successor that proves it absorbed the handoff, and
the public product ships TWO different mechanisms for that with different
guarantees.

## Path A: the operator-run gated handoff (`orchestra rotate`)
This is what gate step 6 proves (docs/GATE.md section 6).
1. The predecessor banks a handoff, docs/HANDOFF_<seat>-next.md: goal, phase,
   open loops, decisions with rationale, next actions, and FIVE CANARY
   QUESTIONS whose answers exist only in the predecessor's own state. Anchors
   must be message ids, real turn numbers, or ISO time ranges. No answers
   anywhere (prompts/_reincarnation-protocol.md).
2. `orchestra rotate <seat>` refuses without a canary block. `--synthesize`
   builds one from the transcript for a seat that never banked
   (orchestra_cli/seats.py, cmd_rotate).
3. scripts/rotate_agent.py: T2 authors the canary artefact; a successor boots
   and reads the handoff; T4 the successor authors a readback answering each
   canary in its own words with two or more anchors; T5 a machine grader
   (scripts/lineage_daemon/auto_grade) keys sections by question id and checks
   anchors against the predecessor's transcript; T7 promote_successor.py
   atomically updates registry, sessions, and state; the predecessor is parked
   with a resume command.
4. A HOLD renames the readback aside and reissues a blank template; the
   successor tries again.
- It is operator-invoked. Nothing in this path watches the context window.

## Path B: the automated blue-green swap (scripts/lineage_daemon/wal/)
143 files at origin/main. Runs from a fifteen-minute beat for seats that are
ARMED (a per-seat flag; a global kill file disables all).
1. Arm: capture the predecessor's write-ahead log of events (bg_arm.py).
2. Prewarm: spawn a green successor and hydrate it with the log as delivered
   msg_store rows (spawn_green.py, hydrate_green.py).
3. Probe: green must report the sequence numbers and summaries of the last
   five log events plus the working-set paths, built from what it was
   DELIVERED, not from the log file. The grader checks against the log itself.
   No canary, no LLM grader. The module header says it "replaces the
   LLM-authored canary/readback comprehension exam" and "proves WAL-recall,
   not judgment" (wal/probe.py, wal/green_boot_probe.py).
4. Swap: canonical moves to green; blue retires with a resume command
   (swap_executor.py). Fails closed if no probe file appears.

## What both paths share
The successor inherits a baton (position), the shared memory directory
(knowledge; see deck 6), and the mailbox rows that arrived while it booted. It
does not inherit a pasted transcript.

## Proof by effect
- Path A, 2026-09-19 02:49Z to 02:52Z: canary file with five questions,
  38-line readback, grade PASS in strict mode, promotion recorded in the
  identity store with a lossless retire/promote stamp. The successor is the
  seat writing this.
- Path A in the gate, 2026-09-17: spawn, three real turns, `rotate
  --synthesize`, PASS, generation 2 recalled a fact from memory (public commit
  e3b1a30).
- Path B, 2026-09-18: one seat swapped through the beat and left a probe file
  (five system events, empty working set) and no readback. The swap counted as
  successful.
- Cross-runtime: a Gemini seat completed a lossless Path B rotation on
  2026-09-15; a Codex green booted and prewarmed the same day. [Fleet record;
  not re-run by this seat.]

## Known gaps, measured
- After a SUCCESSFUL Path B swap the root is left in DRAINED (reason
  swap-complete) and nothing re-runs arm after promote (bg_state.py line 137).
  Twelve roots sat there on 2026-09-18. A manual terminal reset buys exactly
  one more rotation per seat (one seat: reset, rotated, DRAINED
  again). The real fix, swap-complete clearing the wait, is not built.
- The write-ahead-log capture is one-shot at arm and never re-keyed to the
  live session on promote, so a second Path B rotation of the same seat
  refuses. Eight of eight rotated seats showed it (2026-09-18).
- A seat spawned through the Arturo chat front door uses a legacy path that
  seeds no lineage; `orchestra rotate` refuses it. Gate step 6 failed on
  exactly this (2026-09-18, gate run). Spawn from the CLI.
- A passed comprehension grade proves the successor ABSORBED the baton, not
  that the baton is TRUE. A false claim in a baton launders into inherited
  fact. Batons now tag each claim measured or told.
- The probe proves the last five events arrived. It cannot detect that three
  messages sent to the predecessor's bare name never reached the successor;
  that happened on 2026-09-18.
- Path B's green boots with the environment set but nothing yet injected into
  the agent for about four minutes (2026-09-18). Held to post-flip.
- The public README describes Path B as "on by default" [UNVERIFIED for a
  clean install; on the reference fleet it is per-seat armed].
