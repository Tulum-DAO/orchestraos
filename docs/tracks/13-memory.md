# Track 13 — Memory: see it, prune it, then teach it to extract

Size: S/M · Labels: `track`, `core`, `ui`, `memory` · web

## Problem

Every seat's memory is files on disk — `$ORCHESTRA_DIR/memory/<lineage-id>/`, a
`MEMORY.md` index plus one-fact `.md` files, keyed by lineage so a rotated seat
lands on the same folder (`spawn-agent.sh` lines ~601-610 create the dir, seed the
index and put a read-first instruction in the boot prompt; the contract is
`prompts/_agent-protocol.md` § Memory; the walkthrough is `docs/MEMORY.md`). Gate
step 7 proves one fact survives one restart.

Nobody looks in those folders. Not attendees, not the operator of the reference
install. There is no page that shows what a seat remembers, how much, how old,
or whether its index has grown past what a boot prompt will actually load. The
file that proves it: `api/src/routes/memory.ts` — 364 lines, mounted at
`/api/memory` (`api/src/server.ts:111`) — reads **the wrong stores**. Its
`/search` walks `~/.claude/projects/<mangled-home>/memory` (line 101: the Claude
CLI's *own* auto-memory dir, which is the reference operator's private folder and
does not exist on your box); `/context` reads `process.env.OMNI_DIR/global/
context_layer.json` (line 82: a private tool that does not ship); `/facts`,
`/handoffs` and `/projects` read `OMNI_DIR/global/...` and `OMNI_DIR/projects/...`
(lines 196-229) where `OMNI_DIR` defaults to `$ORCHESTRA_DIR/facts` — a layout
`orchestra init` never creates. Not one endpoint touches
`$ORCHESTRA_DIR/memory/<lineage-id>/`. No dashboard file calls `/api/memory`
(`grep -rn api/memory dashboard/src` → nothing). There is no
`memory.test.ts`. The Facts pane (`dashboard/src/components/agent/FactsPane.tsx`
+ `dashboard/src/lib/factsApi.ts` over `api/src/routes/facts.ts`) shows the
*other* store and is the shape to copy.

Two consequences follow from nobody looking:

1. **Nothing prunes.** The index is "loaded at boot, so keep it short"
   (`_agent-protocol.md`), but no code enforces a size, no code deduplicates, no
   code expires. On the reference install the equivalent shared index is 32 KB
   against a ~24 KB prompt-load cap and is silently truncated at every boot — the
   bottom third of what those seats "remember" is invisible to them. A fresh
   install starts at zero and grows toward the same cliff with no warning.
2. **Nothing extracts.** A fact reaches memory only if the seat decides to write
   it when it learns it. A crash before that write, or a seat that forgets the
   protocol, loses the fact. The CLI transcript (Claude's `.jsonl`, Gemini's and
   Codex's equivalents) already holds every word — nothing reads it back into
   memory.

## Design

Three legs, in order; each is independently mergeable and the first is the one
that makes the other two visible.

### Leg 1 — See it (S): a Memory page, and an API that reads the real store

Repoint `api/src/routes/memory.ts` at `$ORCHESTRA_DIR/memory/` and give it the
same shape as `facts.ts`:

- `GET /api/memory/seats` — one row per lineage dir: `lineage`, `file_count`,
  `index_bytes`, `index_lines`, `newest_file_at`, `oldest_file_at`,
  `over_budget: bool` (index bytes vs. a configurable `[memory] index_budget_bytes`,
  default 24000 — the boot-prompt cap that bit the reference install), and
  `orphans` (files with no index line) + `dangling` (index lines with no file).
- `GET /api/memory/seats/:lineage` — the parsed index (title, file, hook per
  line) and every one-fact file's frontmatter (`name`, `description`, `type`)
  with size and mtime. Body on demand: `GET /api/memory/seats/:lineage/:file`.
- `GET /api/memory/search?q=` — the existing scorer (`scoreMatch`,
  `parseFrontmatter` — keep them, they are fine) over the real per-seat dirs,
  returning `lineage` + `file` + snippet.
- Drop the `OMNI_DIR` endpoints (`/context`, `/handoffs`, `/projects`, `/facts`)
  or make them 404 with a one-line reason; the facts store already has
  `facts.ts`. Read-only in this leg — no `PATCH`/`POST`.

`dashboard/src/pages/Memory.tsx` (new, route `/memory`, nav entry beside Facts):

- **Seats table**: lineage · files · index size with a budget bar (green/amber/
  red at 70/90/100 % of budget) · newest write · orphans/dangling counts. Sort by
  size. A red row is the whole point of the page.
- **Seat drawer**: click a row → the index rendered as a list (title, hook, type
  chip) with a search box; click a line → the one-fact file body, its frontmatter,
  size and age. Orphans and dangling lines each get their own callout with the
  filenames.
- **Search across seats** at the top, backed by `/search`.
- Empty state on a fresh install: "No seat has written a memory yet — gate step 7
  (`docs/GATE.md`) is the two-minute way to see one appear here."

Also add a `memory` row to `orchestra doctor`: dir exists, N lineages, any over
budget → WARN with the lineage names.

### Leg 2 — Prune it (S/M): the index has a budget, and a tool that keeps it under

- `scripts/memory_prune.py <lineage> [--apply]`: parse the index and files;
  report (dry-run default) → duplicates by `name` slug and by near-identical
  `description`, dangling index lines, orphan files, index lines over the
  per-line length the protocol asks for. `--apply` fixes dangling/orphans by
  re-generating the index from the files' frontmatter (the files are the source
  of truth; the index is a projection) and moves exact duplicates aside to
  `memory/<lineage>/.pruned/` — never deletes.
- Budget enforcement at the only place the index is consumed: `spawn-agent.sh`'s
  boot line. When the index exceeds `[memory] index_budget_bytes`, the seat's boot
  prompt says so explicitly (`"your index is N bytes over budget — run
  scripts/memory_prune.py <lineage> first"`) instead of silently loading a
  truncated view. Truncation without a warning is the failure mode this leg
  exists to kill.
- A **Prune** button on the seat drawer runs the dry-run and shows the report;
  applying it stays a CLI action this weekend (see out of scope).

### Leg 3 — Extract it (M, stretch): transcript → candidate memories, reviewed

Read the seat's own CLI transcript on a cadence (the same per-runtime transcript
locator the rotation adapters use — `scripts/lineage_daemon/adapters/*` exposes
`locate_transcript`), ask the seat's own runtime CLI (no API key — the same
zero-key posture as Track 2) to propose one-fact files for anything that reads as
a durable fact, preference, correction or decision, and write them to
`memory/<lineage>/.candidates/` — **not** to the index. The Memory page shows
candidates with accept / discard; accept moves the file up and adds the index
line. Nothing is remembered without a human or the seat itself accepting it.
This is the piece that turns "the agent must remember to write it" into "the
agent reviews what it should have written".

## Files you will touch

- `api/src/routes/memory.ts` — repoint at `$ORCHESTRA_DIR/memory`; new `seats`
  endpoints; keep `scoreMatch`/`parseFrontmatter`. `api/src/routes/memory.test.ts`
  (new) — the same pattern as `facts.test.ts`, against a scratch data dir.
- `dashboard/src/pages/Memory.tsx` (new), `dashboard/src/lib/memoryApi.ts` (new,
  mirror `factsApi.ts`), `dashboard/src/App.tsx` — route + nav.
- `orchestra_cli/doctor.py` — the `memory` row.
- `orchestra.example.toml` + `orchestra_cli/settings.py` — `[memory]
  index_budget_bytes`.
- `scripts/memory_prune.py` (new) + `scripts/test_memory_prune.py`.
- `spawn-agent.sh` — the over-budget line in the boot prompt (lines ~601-610).
- `docs/MEMORY.md` — a "Seeing it" section pointing at the page and the doctor
  row; `docs/GATE.md` step 7 — one sentence: "it also appears on the Memory page".
- Leg 3 only: `scripts/memory_extract.py` (new), `prompts/memory-extractor.md`
  (new — the persona proposes facts and writes no index lines).

## Steps

1. Read `docs/MEMORY.md`, `prompts/_agent-protocol.md` § Memory and the current
   `api/src/routes/memory.ts` cold. Run gate step 7 on a scratch data dir
   (`ORCHESTRA_DIR` set, never the live one) so you have one real lineage dir with
   one real fact to develop against.
2. Repoint the API. Prove by effect: `curl /api/memory/seats` lists the scratch
   lineage with `file_count: 1`; `/seats/hello` returns the parsed index and the
   file's frontmatter; `/search?q=blue` finds it. Then write `memory.test.ts`
   against the same scratch dir — including an over-budget index and a dangling
   line.
3. The Memory page: table → drawer → file body → search. Screenshot the red
   budget bar against a deliberately padded index before calling it done.
4. `memory_prune.py` dry-run, then `--apply`, with tests for each defect class.
   Then the boot-prompt warning: spawn a seat over budget and show the warning
   in its pane.
5. (Stretch) Leg 3 against one real transcript; the acceptance bar is *no* index
   line written without an explicit accept.

## Acceptance test

On a clean install: gate step 7 (`hello`, favorite color, rotate, recall). Open
`/memory`: `hello` is listed with 1 file, the index bar is green, the drawer
shows the `user_favorite_color` line and its body. Pad `hello`'s index past
`index_budget_bytes` with junk lines → the row turns red, `orchestra doctor`
WARNs naming `hello`, and the next `hello` generation's boot prompt says it is
over budget and names the prune command. Run `memory_prune.py hello` → the
report lists every junk line as dangling; `--apply` regenerates the index from
the one real file; the row is green again and nothing was deleted (`.pruned/`
holds nothing, because nothing was a duplicate). Leg 3, if landed: a transcript
containing "remember my dentist is on Tuesdays" produces a candidate, not an
index line, until accepted.

## Start prompt

```
I'm working Track 13 (Memory: see it, prune it) for the OrchestraOS hackathon.
Read docs/tracks/13-memory.md, then docs/MEMORY.md and the Memory section of
prompts/_agent-protocol.md. Start with step 1: run gate step 7 on a scratch
data dir (export ORCHESTRA_DIR to a temp path — never the live one) so I have
one real lineage directory to build against. Then step 2: repoint
api/src/routes/memory.ts at $ORCHESTRA_DIR/memory/ and prove
/api/memory/seats, /seats/:lineage and /search by curl before touching the
dashboard. Do not delete anything under memory/ in any step.
```

## Out of scope

- Editing or deleting memory files from the page (read-only + dry-run prune
  this weekend; `--apply` is a CLI action).
- Cross-seat shared memory or a global index — per-lineage scoping is the
  design, keep it.
- Semantic / vector recall over memory — that is the `semantic_memory` extension
  point in `docs/MEMORY.md`, and the facts store, not this track.
- The iOS app — web only.
