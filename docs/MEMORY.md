# Memory — how a seat remembers, and how the voice brain recalls

Two stores, two jobs:

| Store | Who writes it | Who reads it | Path |
|---|---|---|---|
| **Per-seat memory** | the seat itself, as it learns things | the same seat's next generation, at boot | `$ORCHESTRA_DIR/memory/<lineage-id>/` |
| **Facts store** | you (dashboard Facts pane) or any seat (`POST /api/facts`) | Arturo, the voice brain, on every turn | `$ORCHESTRA_DIR/facts/facts_db.json` |

`$ORCHESTRA_DIR` is the `[data] dir` in `orchestra.toml` (`orchestra init` prints it). Gate step 7 exercises the first store; the second is what makes "what's going on with X" answerable by voice.

## 1. Per-seat memory (index + one-fact files + baton)

### Layout

```
$ORCHESTRA_DIR/memory/hello/            <- lineage id: seat name minus any -gN / -genN suffix
├── MEMORY.md                           <- the index: one line per memory, no content
├── user_favorite_color.md              <- one fact per file
├── project_pixel_rollout.md
└── feedback_never_force_push.md
```

- `spawn-agent.sh` creates the directory and an empty `MEMORY.md` on the first spawn and puts the exact path in the seat's boot prompt, with the instruction to read the index before anything else.
- The directory is keyed by the **lineage id** (`hello-g4` and `hello-gen12` both map to `hello`), so a rotated or restarted seat lands on the same files. That is the whole trick behind gate step 7.
- The data dir is a small git repo (`orchestra init` sets it up); `memory/` is whitelisted in its `.gitignore` alongside the handoffs, so memory changes are versioned with the readbacks.

### `MEMORY.md` — the index

```markdown
# hello memory index
- [Favorite color](user_favorite_color.md) — operator's favorite color is blue
- [Pixel rollout](project_pixel_rollout.md) — Acme Dental pixel live on staging 2026-09-17, prod pending DNS
- [Never force-push](feedback_never_force_push.md) — operator correction 2026-09-15
```

One line per memory: `- [Title](file.md) — hook`. The index is loaded at every boot, so it stays short; the content lives in the files.

### One-fact files

```markdown
---
name: user-favorite-color
description: The operator's favorite color (asked in gate step 7)
type: user
---

The operator's favorite color is blue.
```

Frontmatter fields: `name` (kebab slug), `description` (one line — the seat uses it to decide relevance without opening the file), `type` ∈ `user | feedback | project | reference`.

- `user` — who the operator is: role, preferences, how they like to work.
- `feedback` — a correction or a confirmed approach. Add **Why:** and **How to apply:** lines so the next generation applies it, not just knows it.
- `project` — ongoing work, goals, constraints that the repo and git history do not already record. Convert "tomorrow" to a date.
- `reference` — pointers: URLs, tickets, dashboards.

Link related memories with `[[name]]`.

### The baton

`docs/HANDOFF_<lineage-id>-next.md` is the lineage handoff (see `prompts/_reincarnation-protocol.md`): where the seat stopped, what is next, the canary questions. Memory is **durable knowledge**; the baton is **position**. Keep them apart — a successor reads the baton to resume and the memory index to know.

### Rules the seat follows (from `prompts/_agent-protocol.md`, Memory section)

1. At boot, read `MEMORY.md`; open only the files whose index line matters for the task at hand.
2. Write a fact **when you learn it**, not at handoff time — a crash or hard rotation may mean the handoff never gets written.
3. Before writing, check the index for a file that already covers it and update that one; delete a memory that turns out to be wrong.
4. Add the index line in the same step as the file.
5. Do not save what the repo already records, or what matters only to the current conversation.

### Copy-paste prompt (gate step 7)

Inside the `hello` seat's pane:

```
Remember that my favorite color is blue. Write it to your memory directory:
create a one-fact file user_favorite_color.md with the frontmatter from
docs/MEMORY.md, then add its one-line pointer to MEMORY.md in the same step.
Show me both files when done.
```

Restart or rotate the seat (gate step 6), then in the new generation:

```
What's my favorite color, and where did you read that from?
```

Expected: "blue", citing `$ORCHESTRA_DIR/memory/hello/user_favorite_color.md` via the index. If the new generation does not know, check the directory directly — either the file was never written, or the successor skipped the index at boot.

## 2. The facts store (what Arturo recalls)

`$ORCHESTRA_DIR/facts/facts_db.json` is a plain JSON file: `{"facts": [{"id", "fact", "timestamp", "verified_at", "source", "category"?}], "last_updated"}`. Two ways in:

- **Dashboard** → Facts pane → type in "Add a fact Arturo should know…" → Add.
- **API**, from a seat or a script:
  ```bash
  curl -s -X POST http://127.0.0.1:8888/api/facts \
    -H 'Content-Type: application/json' \
    -d '{"text": "Acme Dental pixel went live on staging today", "category": "clients"}'
  ```
  (port = `[api] port` in `orchestra.toml`). `GET /api/facts` lists them with a freshness badge; `PATCH /api/facts/:id` edits; `POST /api/facts/:id/fresh` re-stamps `verified_at`.

Every write is appended to `$ORCHESTRA_DIR/logs/facts-edits.log`.

### How it reaches Arturo

`services/arturo/facts_recall.py` — standard-library only, no private imports — runs on every voice turn when `ARTURO_FACTS_RECALL=1` (the `run.sh` default):

1. Pull topic keywords from the latest user turn.
2. Read the facts store (and `state/brain/facts.db` too, if some ingest job has created that sqlite store — a fresh install has none) read-only, inside a 250 ms budget wall on a single-flight worker thread.
3. Score by keyword density, kind and recency; drop prompt-echo noise.
4. Append a `FACTS (...)` block of the top 5 to the system context. The `knowledge` tool Arturo calls for people/projects reads the same merged sources, so a fact surfaces whether the model consults context or calls the tool.

Every miss degrades to *no block* — never an error into the turn. The proxy logs one line per turn, `facts-recall: facts=N ms=X`, so you can prove recall from `logs/arturo.log (the supervisor's per-service log)`, and `facts-recall: cache warm in Nms` at boot (the query cache is re-warmed every 30 min and after 15 min idle so a first turn after a quiet stretch does not miss the budget).

### Prove it end to end

```bash
# 1. write a fact
curl -s -X POST http://127.0.0.1:8888/api/facts -H 'Content-Type: application/json' \
  -d '{"text": "The launch codename for the dental campaign is BLUEBIRD"}'
# 2. restart the voice brain
orchestra down && orchestra up
# 3. ask Arturo (bearer = CUSTOM_LLM_BEARER in $ORCHESTRA_DIR/.env.secrets)
curl -sN -X POST http://127.0.0.1:5071/v1/chat/completions \
  -H "Authorization: Bearer $CUSTOM_LLM_BEARER" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"what is the launch codename for the dental campaign"}],"stream":true}'
# 4. proof in the log
grep 'facts-recall: facts=' $ORCHESTRA_DIR/logs/arturo.log | tail -1   # the supervisor's per-service log
```

Expected: the streamed answer says BLUEBIRD and the log line reads `facts-recall: facts=1 ms=<small>`.

## What is *not* here — and the extension point

The private reference install layers two things above the facts store: a belief/worldview derivation and a vector **semantic recall** over indexed documents. Neither ships in this tree, and facts recall does not depend on either.

`services/arturo/semantic_recall.py` *is* in the tree as the seam for the second one. It treats the `semantic_memory` package as an **optional extension**: on a clean install it probes once at the first voice turn, logs a single line — `semantic recall unavailable: the optional semantic_memory package is not installed (facts recall still works) — extension point: docs/MEMORY.md` — and returns nothing, forever quiet after that. To plug your own in:

- Put a `semantic_memory` package at `<repo>/scripts/semantic_memory/` (beside the code, not under `$ORCHESTRA_DIR`).
- It must expose `semantic_memory.query.query(db_path, text, scope=None, k=..., ...)` returning rows with `path` + `snippet` (see the seam's `recall_preamble` for the exact call), `semantic_memory.embed.embed_query(text)`, and `semantic_memory.db.connect(path)`; the index lives at `$ORCHESTRA_DIR/state/semantic-memory.db`.
- `ARTURO_SEMANTIC_RECALL=1` (the `run.sh` default) then adds a `RECALL` block of document pointers beside the `FACTS` block, under the same 250 ms budget-wall and single-flight rules.
