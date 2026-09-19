# Deck 6 companion — Memory

> Companion to slide deck 6 from the Tulum Build-a-thon, 2026-09-19. Everything the slides claim is here with the file that proves it, the effect observed, and the known gaps. Give this page to your agent; it is written to be read by one.


Written for the Tulum Build-a-thon, Sat 2026-09-19. Same rules as deck 1.
Short, because the mechanism is simple and the honesty is that
recall on a clean install is file-based.

## The one-sentence version
Memory is markdown on disk, indexed by one file loaded at boot, shared across
every generation of a seat by stripping the generation suffix from its name.

## Mechanism
- Layout: memory/<seat>/MEMORY.md is the index, one line per memory with a
  title, a link, and a hook, never content. Each memory is a one-fact file
  with frontmatter name, description, type (user | feedback | project |
  reference). Feedback and project entries carry a Why and a How-to-apply line
  (docs/MEMORY.md).
- Boot: spawn-agent.sh creates the directory and an empty index on first
  spawn and puts the exact path in the seat's boot prompt with the instruction
  to read the index first (docs/MEMORY.md line 24).
- The one line, spawn-agent.sh line 606:
      memory_root="$(printf '%s' "$agent_id" | sed -E 's/-(g|gen)[0-9]+$//')"
  hello-g4 and hello-gen12 both resolve to memory/hello/. Nothing is copied at
  rotation; the successor opens the same folder.
- Position versus knowledge: the handoff (docs/HANDOFF_<seat>-next.md) is
  where the seat stopped and what is next; the memory directory is what it
  learned. docs/MEMORY.md line 62 and docs/GATE.md line 237 say so. A tester
  who writes a fact only into the handoff fails gate step 7.
- Extensions on the reference fleet, not required on a clean install: a dream
  scheduler that collects activity every fifteen minutes and delivers a daily
  report; a brain indexer and distiller; semantic recall over a SQLite store.
  Semantic recall is an optional extension: probe once, warn once, never
  raise on a clean install (public commit 2e8dde6, 2026-09-18).

## Proof by effect
- Gate step 7, write a fact, restart, recall it: PASSED in the unattended run
  of 2026-09-18 via the restart branch. Recall was proven across
  kill-and-respawn, NOT across a rotation (gm ruling, 20:15 Tulum).
- 2026-09-17: a sandbox run, spawn, three turns, rotate with synthesize, PASS,
  generation 2 recalled a planted fact from memory/ (public commit e3b1a30).
- This seat, 2026-09-19: read its predecessor's index at boot and applied
  twenty-plus of its recorded rules during the night without re-deriving
  them (shared-tree reads through the sha, never commit with -F-, one body
  per recipient).

## Known gaps, measured
- Recall across a ROTATION is proven only by the sandbox run, not by the
  release gate. Step 7's pass is restart-only.
- The index has a size limit; the reference seat's index passed it on
  2026-09-18 and was only partly loaded. Long index lines cost real memory.
- Frontmatter differs between the public docs (three flat fields) and the
  reference fleet (type nested under metadata). A tool written for one does
  not read the other. [Observed on this seat's own memory files.]
- Memory is knowledge, not truth. A recalled note can be stale; the rule is
  verify a named file, function, or flag still exists before recommending it.
- Nothing prunes. Wrong memories are deleted by hand or persist.
