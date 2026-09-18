# Track 10 — Docs and tutorials

Size: S · Labels: `track`, `docs`, `good-first-issue`

## Problem

`docs/ARCHITECTURE.md` exists and is a solid one-page map (vocabulary, component
list, invariants), but the rest of this track — a 15-minute first-agent tutorial,
"how a decision reaches your phone," "how memory survives a rotation" — either
doesn't exist yet as a standalone doc or is scattered across `docs/INSTALL.md`
(which is install-focused, not narrative). A newcomer today has to read
`ARCHITECTURE.md` plus `INSTALL.md` plus infer the rest from source to understand
how the pieces connect end to end.

## Design

This track is mostly writing, verified the same way every doc in this hackathon is
verified: read it cold as the target reader and follow it literally. Three
short, narrative docs, each answering one question end to end rather than listing
features:

- **"Your first agent in 15 minutes."** Not a restatement of `INSTALL.md`'s steps —
  a narrative walk from `orchestra init` through a spawned seat answering a
  question in its terminal, told as a story with the actual commands inline. This
  overlaps `docs/BEGINNERS_GUIDE.md` (a separate deliverable, written for someone
  who isn't sure the command line is a website) — this doc assumes the reader
  already has a terminal open and knows what a shell command is; point to
  `BEGINNERS_GUIDE.md` for anyone who needs the layer below that.
- **"How a decision reaches your phone."** Follows one approval card from
  `approval.py request` through `msg_store`/the gateway to a push notification and
  back — the same path `docs/ARCHITECTURE.md`'s "Approvals" section names, told as
  a trace with real file/function references, so a reader can go verify each hop
  themselves.
- **"How memory survives a rotation."** Follows one fact from a one-fact file +
  its `MEMORY.md` index line in the seat's memory directory
  (`$ORCHESTRA_DIR/memory/<lineage-id>/` — the seat name with a trailing `-gN`/
  `-genN` generation suffix stripped, so `hello-g4` and `hello-gen12` both map to
  `memory/hello/`), through a rotation, to the successor reading that index at
  boot — `spawn-agent.sh` creates the memory dir, seeds `MEMORY.md`, and puts the
  path in the boot prompt with a read-first instruction, which is what actually
  makes the successor read it. **The handoff document is a different, adjacent
  mechanism — it does not carry the durable fact.** It carries the baton
  (position: where the predecessor stopped, what's next, the canary questions);
  the readback is the successor proving it read *both* the handoff and its own
  memory directory. Getting this distinction backwards is a real trap — see
  `docs/MEMORY.md`'s walkthrough (lands via
  https://github.com/Tulum-DAO/orchestraos/pull/1) for the exact paths and an
  end-to-end proof recipe before writing this doc, so the tutorial doesn't repeat
  the mistake of routing the fact through the handoff.

## Files you will touch

- `docs/TUTORIAL_FIRST_AGENT.md` (new)
- `docs/TUTORIAL_DECISION_TO_PHONE.md` (new)
- `docs/TUTORIAL_MEMORY_ROTATION.md` (new)
- `README.md` — link all three from the docs section.
- `docs/ARCHITECTURE.md` — add a "Where to start reading" cross-link to these three
  (it already has a "Where to start reading" section pointing at source files;
  extend it, don't replace it).
- `docs/MEMORY.md` (PR #1) — the memory-rotation
  tutorial's primary source; cite it rather than re-deriving the mechanism.

## Steps

1. Read `docs/ARCHITECTURE.md`, `docs/INSTALL.md`, and this repo's
   `docs/tracks/README.md` index cold, as a newcomer would, before writing anything
   — note every place a reader would have to guess or go read source to continue.
2. Write "your first agent in 15 minutes" against a fresh install (actually run
   every command as you write it — no paraphrased commands).
3. Write "how a decision reaches your phone" by tracing one real card: request it,
   watch it move through `msg_store`, the gateway, and (if Track 8 has landed) a
   push, and write down what you actually observed, with file/function references.
4. Write "how memory survives a rotation" the same way: trigger or read through one
   real rotation, tracing the fact through the memory directory + `MEMORY.md`
   index (not the handoff doc — that's the position/baton mechanism, a separate
   trail worth mentioning but not the one that carries the fact). Cite
   `docs/MEMORY.md` and the real `spawn-agent.sh` code that seeds the memory dir
   and points the successor at it in its boot prompt.
5. Have a newcomer (or a fresh agent with no prior context on this repo) follow the
   15-minute tutorial literally, with no maintainer help — note every place they
   get stuck and fix the doc, not the newcomer.

## Acceptance test

A newcomer follows "your first agent in 15 minutes" without asking a maintainer
anything, and ends with one seat spawned and answering a question in its terminal.
The other two docs each let a reader independently verify every hop they describe
against real files.

## Start prompt

```
I'm working Track 10 (docs and tutorials) for the OrchestraOS hackathon.
Read docs/tracks/10-docs-tutorials.md in this repo for the full design.
Files to write: docs/TUTORIAL_FIRST_AGENT.md, docs/TUTORIAL_DECISION_TO_PHONE.md,
docs/TUTORIAL_MEMORY_ROTATION.md, plus linking them from README.md and
docs/ARCHITECTURE.md's "Where to start reading" section.
Start by actually running the first-agent flow on a fresh install and
writing down exactly what you did and saw — do not paraphrase or invent
commands; every command in these docs must be one you ran yourself while
writing it.
```

## Out of scope

- `docs/BEGINNERS_GUIDE.md` and `docs/PROMPTS.md` (separate deliverables, covering
  the layer below "already has a terminal open" and a copy-paste prompt library,
  respectively).
- Rewriting `docs/ARCHITECTURE.md` or `docs/INSTALL.md` themselves — this track
  adds narrative docs alongside them and cross-links, it does not restructure what
  already works.
