# Deck 1 companion — What OrchestraOS is

> Companion to slide deck 1 from the Tulum Build-a-thon, 2026-09-19. Everything the slides claim is here with the file that proves it, the effect observed, and the known gaps. Give this page to your agent; it is written to be read by one.


Written for the Tulum Build-a-thon, Sat 2026-09-19, 11:00.
Audience: ~20 developers who have never seen this system.
Every claim here was verified against the public code at origin/main on
2026-09-19 by reading the file named beside it, or by an effect observed that
day. Claims that are NOT verified are marked [UNVERIFIED]. Nothing in this
document should be softened.

## The one-sentence version

OrchestraOS is an open harness for running a team of AI coding agents: they
message each other through a durable mailbox, they remember things across
restarts, they can be replaced by a successor that proves it absorbed the
handoff, and every real decision lands in front of a human on their phone.

## The problem it was built for

One operator, ten-plus projects in parallel, a fleet of coding agents. The
problems were never the models. They were four things:

1. The operator was the bottleneck — every agent needed work assigned by hand
   and context pasted in by hand.
2. Continuity — copy-pasting entire conversation histories into new terminals
   to pick up where the last session left off.
3. Skill loss — teach an agent exactly what a client needs, spin up a new one
   next week, and it has none of that.
4. Nothing asked. Agents either stopped and waited where nobody could see, or
   guessed and kept going.

Operator's own words, from the working logs [UNVERIFIED against the original
logs; the quotes and dates are carried from the operator's own run-of-show
record, not re-read at source]:
- "I was the bottleneck — every agent needed me to assign it work and paste in
  the context by hand." (2026-03-29)
- "Copy-pasting entire conversation histories into new terminals to pick up
  where we left off." (2026-03-29)
- "I teach an agent exactly what I need for a client, and the next time I spin
  one up it doesn't have the same skill level." (2026-04-15)
- "Crashes, RAM runs out, agents forget, the tmux inject sometimes submits and
  sometimes doesn't." (2026-06-09)
- "If an agent needs a decision, it surfaces that decision directly to me."
  (2026-08-26) — this became the approvals surface.

## The four ideas

### 1. Seats, not chat windows
An agent is a "seat" — a named, registered, supervised identity with a lineage.
Seats do not chat with each other; they MAIL each other. A message is a row in
a SQLite store (msg_store.py). If the recipient is busy or crashed, the row
waits: the router (scripts/message-router.py, on a schedule) probes the target
pane, injects when it is idle, parks and retries when it is busy. Parked rows
are retried, not dead-lettered. This is the difference between a conversation
and a system.

Proof by effect: on 2026-09-18 four seats corrected one another across a
night of work, and every correction cited the message id it was answering.
That chain is reconstructible from the store alone.

### 2. Memory that survives restarts
Memory is plain markdown files on disk under memory/<seat>/. An index file
(MEMORY.md) is loaded at boot and carries only a title, a link, and a hook per
memory; the content lives in separate one-fact files, opened only when the
index line looks relevant. Frontmatter is three fields: name, description,
type (user | feedback | project | reference). (docs/MEMORY.md)

The trick that makes memory survive an agent replacing itself is ONE LINE OF
SED, spawn-agent.sh line 606:
    memory_root="$(printf '%s' "$agent_id" | sed -E 's/-(g|gen)[0-9]+$//')"
The spawn script strips the generation suffix off the seat name, so hello-g4
and hello-gen12 both resolve to the same memory/hello/ directory. Nothing is
copied at rotation. The successor simply opens the same folder.

Critical distinction, written into docs/MEMORY.md and docs/GATE.md: THE HANDOFF
IS POSITION, THE MEMORY DIRECTORY IS KNOWLEDGE. Where I stopped and what is
next goes in the handoff. What I learned goes in memory. A tester who writes a
fact only into the handoff fails the gate's recall step (gate step 7). [The
date this was corrected in our own docs is UNVERIFIED; the current wording is
verified.]

### 3. Rotation: a successor that proves it absorbed the handoff
The public product ships TWO rotation paths. They are not the same mechanism
and the difference matters.

**The operator-run path, `orchestra rotate <seat>` — this is what gate step 6
proves.** The predecessor banks a handoff document with five canary questions
whose answers live only in its own state (docs/HANDOFF_<seat>-next.md). The
command refuses to run without that block. A successor boots, reads the
handoff, authors a readback answering the canaries with anchors (message ids,
turn numbers, timestamp ranges), a machine grader checks the readback against
the predecessor's transcript, and only on PASS is the successor promoted
atomically (registry, sessions, state) and the predecessor parked with a
resume command. (orchestra_cli/seats.py cmd_rotate → scripts/rotate_agent.py
steps T2, T4, T5, T7; grader in scripts/lineage_daemon/auto_grade.)
Proof by effect: on 2026-09-19 at 02:52Z the seat writing this doc was itself
produced this way — canary file, 38-line readback, grade PASS in strict mode,
gen 1 retired at the same microsecond gen 2 was promoted. It is operator-
invoked. Nothing in this path watches the context window for you.

**The automated blue-green path — the beat in scripts/lineage_daemon/wal/.**
For a seat that has been ARMED, a beat on a schedule prewarms a green
successor, hydrates it with the predecessor's write-ahead log, and grades a
PROBE: green must report the last five log events and the working-set paths,
and the grader checks that answer against the log itself. There is no canary
and no LLM grader on this path; the probe proves log recall, not judgment
(scripts/lineage_daemon/wal/probe.py says so in its own header). On PASS the
canonical pointer swaps to green and blue is retired with a resume command.
Proof by effect: one seat swapped through this path on 2026-09-18 and left a
probe file and no readback.

**What is true of both:** the successor does not inherit a pasted transcript.
It inherits a baton (position), the shared memory directory (knowledge), and
the mailbox rows that arrived while it booted.

**What is NOT yet true, measured 2026-09-18/19, private reference fleet:**
- After a SUCCESSFUL automated swap the seat's root is left in a terminal
  DRAINED state and nothing re-arms it, so the automated path buys one
  rotation per seat until that is fixed. A manual re-arm buys one more.
- A seat spawned through the Arturo chat front door uses a legacy spawn that
  seeds no lineage, so `orchestra rotate` refuses it. Spawn from the CLI.
- "Blue-green" describes the automated path only. The gate-proven path is a
  gated hand-off, not a live swap.

### 4. Every real decision reaches a human
When an agent hits something only a human can decide, it files a card and
stops (scripts/approval.py request). The card reaches the human's phone or
watch. They approve, deny, hold, pick from a menu, or write back in free text
(--text; questionnaire kind free_text). The answer is then VERIFIED-INJECTED
into that agent's own live pane by scripts/approval_resume.py, which marks a
request DONE only at "resumed", never at "answered", and re-resolves the seat
after a rotation so the answer lands on the live generation. Nothing to paste.
Proof by effect: gate step 5 (answer an approval card from the phone) passed on
the release candidate on 2026-09-18.

## How honest we are being about the state

This runs one operator's fleet every day — about 45 resident seats on one
VPS. That is the reference install and it is real. It is not a finished
product. Specific gaps are named in the source doc "known gaps", and every doc
in the repo says where it is thin. Two worth saying here because they touch
the ideas above: the status surfaces (dashboard, registry, watch) can disagree
about whether a seat is idle or working, which is the top open roadmap item;
and the automated rotation defect above means "auto-rotation" is a claim about
the mechanism, not yet about the fleet.
