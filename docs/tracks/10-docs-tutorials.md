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
- **"How memory survives a rotation."** Follows one fact from being written, through
  a seat's handoff document, to a successor's readback — the same path
  `docs/ARCHITECTURE.md`'s "Baton / handoff" and "Readback" vocabulary describes,
  told end to end with the actual files involved (a seat's `MEMORY.md`, the handoff
  doc, `docs/HANDOFF_<agent>-next.md`, the grader).

## Files you will touch

- `docs/TUTORIAL_FIRST_AGENT.md` (new)
- `docs/TUTORIAL_DECISION_TO_PHONE.md` (new)
- `docs/TUTORIAL_MEMORY_ROTATION.md` (new)
- `README.md` — link all three from the docs section.
- `docs/ARCHITECTURE.md` — add a "Where to start reading" cross-link to these three
  (it already has a "Where to start reading" section pointing at source files;
  extend it, don't replace it).

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
   real rotation (or, if none is available to trigger live, walk an existing
   handoff doc + its successor's readback and cite them).
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
