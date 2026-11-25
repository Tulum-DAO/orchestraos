# DESIGN: post-call commitments (phase 1 note)

- Seat: postcall-dev (T1, parent gm). Branch `build/post-call-commitments`, worktree `/home/shaw/wt/postcall-dev/program`.
- v1 written 2026-11-25 ~17:30Z. **v2 followed codex REJECT round 1 (8 findings); v3 followed codex REJECT round 2 (5 blockers, 3 should-fixes). All are addressed; see §Review.** Status: **DESIGN ONLY, no code yet.** gm approves phase 2 or sends it back.
- Program (msg_3e8d4dad, relayed in `prompts/postcall-dev.md`): when a call is ingested, put up ONE review card
  per call listing Shaw's commitments and intentions, with deadlines and the agent work that could be offloaded.
  On approval, create human tasks, dispatch approved agent work, and tell Shaw once when the results are ready.

Every claim below was read from disk (file:line) or comes from one of four read-only survey passes run today.
Anything inferred is marked **(inferred)**.

---

## 0. What exists today (it changes the shape of the build)

| Thing | Where | State |
|---|---|---|
| **Commitments miner** (Shaw's own micro-commitments: quote-verified, `cmt_` ids, ledger, human-task cards) | `~/repos/pocket-agent/commitment_miner.py` (prompt `cmt-v1` :29-42, `resolve_due_ts` :185, `ground_commitments` :269, `mine_recording` :340, `commitment_id` :498) | **OFF.** `~/.orchestra/dream/COMMITMENT_MINER_DISABLED` (2026-09-13: "keep OFF until Shaw validates first live batch"). No cron line. Ledger `state/commitments_ledger.json` has 50 rows, the last from 09-13. |
| Dream facts pipeline (`[FROM RECORDING: <title>] …`) | `dream/pocket-extract.py:43-53` (LLM), `collect-activity.py:341-342` (tagging), ingested to `state/brain/facts.db` at 04:15 and 08:30 ET | Runs nightly. The output is free text. |
| Pocket recordings (real calls with other people) | `~/pocket-recordings/<date>-<slug>/{meta.json,transcript.md,transcript.json,audio.m4a}`; ingested by `pocket-agent/drainer.py:406-411` via reconcile cron every 15 min (crontab:41) | 638 dirs. `meta.json` has `recording_id`, `created_at`, `duration`, `category`, `transcript_hash`, `ingested`. `transcript.md` carries `_[MM:SS]_` offset markers. |
| Arturo voice calls (Shaw talking to his assistant) | `~/.orchestra/state/voice-calls/*.json`. Finalized in `services/arturo/arturo-proxy.py:790-931` (`finalize_from_client`) and by the idle watchdog at :1062-1101 | 492 records. Turns are `{role,text,ts}`. **There are no participant fields and no link to any audio.** 46 calls have 0 turns and 29 have 1. |
| Cards | `scripts/approval.py` (`human-task`, `questionnaire`, `patch`). Commitment card = `human_task` + `feature=commitment` + `cmt_` op_key + evidence `{due_ts,due_source}` (DEC-1789352893701528; render contract `DOCS/SURFACE_CONTRACTS.md:37`) | Live |
| Calendar | none on disk. The Google Calendar MCP isn't connected for headless use. `read-ics.mjs` parses a manual `inbox/calendar.ics` and drops attendee emails | **Gap** |

Four facts the build depends on, found in the survey:
1. **`msg_store.py` has no `--card` flag.** The only `--card` gate is in `scripts/im.py:71-110`.
2. **Bulk approve doesn't exist** in any card lane. Questionnaires take 1-20 questions, are single-select, and are submitted once.
3. **op_key dedupe discards a second filing; it never updates.** A pending row with the same `(from, op_key)` returns the old id, and after 900 s a closed one lets a fresh card through (`approval_schema.py:376-399`). `patch` can only update a *pending* `human_task`, and it sends no push. Re-filing over an existing pending card may push Shaw again (`approval_notify.notify` doesn't check `notified_at`; **inferred from code, not tested**). So cross-call dedupe needs our own ledger, and we must never re-file in order to "update".
4. The "Decide by · If no answer · Cost of waiting" opener is fleet law (FOUNDATION §2). It isn't in the surface-decision skill.

---

## 1. Trigger: one idempotent seam per call, with idempotency at the level of each effect

**One entry point, `postcall.process(call_id)`.** Two adapters normalise their source into a call.

| source | canonical `call_id` | "ready" means | skip |
|---|---|---|---|
| `pocket` | `pocket:<recording_id>` | `meta.ingested == true` and a transcript exists | `category` outside the miner's call/meeting set, or `duration < 120 s` (the miner uses 5 min; **gm call**) |
| `voice` | `voice:<conv_id>` when present, else `voice:<call_id>` | `status=="ended"`, no `superseded_by`, record unchanged for 120 s | `test:true`, `_quarantine/`, fewer than 2 user turns, or fewer than 15 user words |

- **Voice identity across late supersession.** Server records can be marked `superseded_by` a client record after the fact (`arturo-proxy.py:892-902`). Keying on `conv_id` collapses the pair, and the client record (the full-text one) is preferred. A server record with no `conv_id` that is later superseded gets an alias row pointing at the client key, and its own ledger row goes to `merged`. A server record that was already carded gets no second card: its items join the client key's dedupe (§5).
- **No backfill.** Only calls whose `ended_at`/`created_at` falls after `postcall.start_ts`, written once at rollout, are considered. The 561 historical voice records and 638 recordings are ignored. Mining the past would be a separate gm or Shaw decision.
- **Mechanism.** A cron sweeper, `flock -n` + `nice -n 19 taskset -c 6,7`, every 5 min. It touches no code owned by codex-arturo or pocket-service, and it covers every way a call can end (gateway POST, watchdog finalize, `vc_live`, re-ingest).

**Ledger: `~/.orchestra/state/postcall/{live,shadow}/ledger.db` (sqlite, WAL; one per mode, §7), with a lease on each call and an intent row for each side effect**
- **`calls`** `(call_id PK, revision, content_hash, status, lease_owner, lease_until, attempts, last_error, tz_at_call, created_at, updated_at)`.
  - **Claiming:** a single `UPDATE … SET lease_owner=?, lease_until=now+10m WHERE call_id=? AND (lease_until IS NULL OR lease_until<now) AND status IN (...)`. A crashed sweeper's lease expires and the next sweep takes over.
  - **States:** `new → extracting → extracted → filing → carded | no_card | no_card_dupes | failed_retry(n) | failed_final | merged`.
- **`effects`** `(effect_id PK, call_id, kind, payload_json, state, external_id, created_at, confirmed_at)`. Every external write gets a **deterministic `effect_id`** and an intent row, committed *before* the external call:
  - `card:<call_id>:r<revision>:p<page>` is a questionnaire, with op_key = effect_id.
  - `task:<item_key>` is a human task, op_key `cmt_…`.
  - `patch:<item_key>:<due_ts>`
  - `dispatch:<card_id>:<q_n>`
  - `results:<card_id>`
  The states are `planned → sent → confirmed`.
- **Concurrency contract (v3).** Exactly one writer exists at a time.
  - The sweeper takes `flock` on `state/postcall/sweep.lock` for its whole run, and opens that fd **inheritable**, passing it to every CLI child it spawns (`pass_fds`). The lock is therefore held until the last child exits, even if the parent dies. Reconciliation and all effect writers run only under this lock.
  - Children are awaited with a timeout. On timeout the sweeper kills its *own* child (a process we spawned) and marks the effect `sent` (outcome unknown), so the next sweep reconciles it.
  - Leases are kept only to make a crashed run visible. They aren't a second concurrency mechanism.
- **Recovery.** At the start of each sweep, every effect still in `planned` or `sent` is reconciled against the outside world before anything is retried:
  - **Cards and tasks:** a **read-only** SQL query (`sqlite3` URI `mode=ro`) against tasks.db, any status:
    - `SELECT id,status FROM questionnaires WHERE from_agent='postcall' AND op_key=?`
    - `SELECT id,status FROM approval_requests WHERE from_agent='postcall' AND op_key=?`
    `approval.py get` takes an id, not an op_key (`questionnaire_schema.py:245`), and `pending` covers pending rows only, so neither works for this. Reading the DB isn't writing it, so the surface-decision "never write ApprovalStore directly" rule still holds.
  - **Dispatches and result notices:** msg_store lookup by the effect id carried in the subject.
  If found, record `external_id` and set `confirmed`. If not found (and the lock guarantees no in-flight child), re-send. op_key dedupe protects only *pending* rows (`questionnaire_schema.py:177-182`), and the 900 s guard counts from *creation* (`approval_schema.py:585-588`). So **this reconcile step under the lock is the real idempotency guard; op_key is only a second line of defence.**
- **Revisions.** If the transcript or turns hash changes after carding, `revision` goes up. The call is re-extracted, and only *new* items get a new card, `card:<call>:r<n>:p<k>`.
  - An item is new when its **source identity** (§5: canonical call + normalized quote hash) isn't already on the ledger.
  - Recurring items and supersession aliases use the same identity, so re-extraction never re-cards an item already shown.

## 2. Extraction: reuse the commitments miner and extend it; don't add a third pass

- **The dream facts pipeline can't carry this.** Its output is `[FROM RECORDING: <title>] <free text>`.
  - The recording id is dropped at `collect-activity.py:342`.
  - `entities` is empty in facts.db.
  - There's no due field and no offset, and the `file:line` `source_id` drifts when the daily jsonl is re-sorted (`collect-activity.py:192-222`).
  - It runs nightly.
  - Carrying due dates, participants and citations would mean changing its schema and every reader, with the latency still left over.
- **The commitments miner is the existing pipeline built for this.** It's per recording, quote-grounded, has a deadline resolver vetted under DEC-1789352893701528, has `cmt_` ids, and its cards already match the commitment card contract.
- **Plan:**
  - Lift its pure functions into `scripts/postcall/extract.py` and bump the prompt to **`cmt-v2`**.
  - The pocket-agent copy is untouched and stays OFF.
  - Phase 2 doesn't remove the miner's disable flag. Our pipeline replaces its card path, so running both would double-card.
- **`cmt-v2` adds these fields to each item:**
  - `kind`: commitment | intention
  - `counterpart`
  - `project`
  - `explicit_date`, which must be a substring of the quote, otherwise it's discarded
  - `recurring`: bool
  - `agent_takeable` + `agent_work`, one line, always a draft, research or an internal artefact; agents never send as Shaw (FOUNDATION §3)
  - `matches_existing`
  - `deliverable`: the normalized object noun phrase, used for matching (§5)
  - `speaker`: which diarization label or turn role said the quote
  - The quote is still verbatim.
- **Scope:** Shaw's own commitments and intentions only. Other people's promises to Shaw are out of v2.
- **Grounding is tighter than the miner's:**
  1. **Exact normalized match only.** The miner also accepts fuzzy ≥0.90 (`commitment_miner.py:269-276` → `goal_grounding.quote_in_transcript`). v2 drops the fuzzy path.
  2. **Speaker binding.** There are two trust levels, and the card shows which applies.
     - **verified:** a voice call, where the quote sits inside a `role=="user"` turn. The Arturo client only records the device owner as `user`.
     - **inferred:** every Pocket item.
       - Diarized transcripts carry only `SPEAKER_00`/`SPEAKER_01` labels (checked on disk 2026-11-25), and nothing on the box maps a label to Shaw.
       - When a transcript is diarized, the LLM proposes Shaw's label, and code requires that label to agree across the call's items; if it doesn't, the call goes `failed_retry` and then to manual review.
       - When a transcript isn't diarized, the item is marked `speaker unknown`.
     - **Inferred and unknown items are never covered by Q1 accept-all (§4).** Each needs its own explicit answer, so no task or dispatch comes from an unconfirmed attribution.
     - A trusted voiceprint or speaker-name mapping is out of scope; that's a pocket-service feature.
  3. **Citation** is computed in code from where the quote sits: `pocket:<id>@MM:SS` (nearest preceding `_[MM:SS]_` in `transcript.md`) or `voice:<id>#turn<n>`.
  4. **A substance filter** drops items whose `commitment_text` is under 3 content words or is a pure pleasantry ("talk soon"). The list is fixture-tuned.
- **Failure vs. empty.** The miner turns a timeout, OSError or bad JSON into `[]` (`commitment_miner.py:411-416`). v2's extractor returns `(ok, items)`:
  - `ok=False` → `failed_retry`, with **no card and no `no_card` mark**;
  - `ok=True, items=[]` after grounding → `no_card`.
  **An empty call never yields a card** (2026-11-19 lesson):
  - Calls under the §1 floors are never sent to the LLM.
  - The filing step refuses an item list of length 0.
  - Tests pin both.

  *What this does not guarantee:* whether a grounded quote is really a commitment is the LLM's judgement. The card is the human check on that judgement; it doesn't certify it. **(Stated plainly so nobody reads "grounded" as "true".)**
- **LLM.** One headless `claude -p` per call, using the miner's model, niced and pinned, one at a time. Transcripts over 60k chars are split into overlapping chunks and merged. **Cost:** roughly 5-15 calls per day (inferred). Only stubbed responses are used in tests.

## 3. Deadlines

Precedence, first hit wins; `due_source` names the rule.
1. **Explicit date or clock**, grounded in the quote:
   - Resolved in **`tz_at_call`**. `shaw-location.json` keeps only the *current* zone plus `since`.
     - If `since` ≤ call start, that zone is used.
     - Otherwise (he moved after the call), `home_base.tz` is used and `due_source` gets the suffix ` (zone assumed: <label>)`.
     - The zone is stored on the call row, so a later move never shifts an old deadline.
   - **Composition:** a relative date plus an explicit clock ("tomorrow at 3") takes the date from rule 2 and the clock from the quote.
   - **Ambiguity is terminal:** `due_ts=null` and `due_source="ambiguous: <phrase>"`, shown as unknown and never re-guessed later.
   - The miner hardcodes America/New_York (`commitment_miner.py:127`); the lifted copy fixes that.
   - It also ignores a clock inside a weekday phrase (`commitment_miner.py:226-230`, "Friday at 2pm" → 17:00). v2 honours the clock.
   - Ambiguous dates ("the 3rd" with no month context, "next Friday" at the turn of a week) store `due_ts=null` and `due_source="ambiguous: <phrase>"`.
2. **Relative phrase** against the call start, using `resolve_due_ts`'s rules (17:00 default, tonight 21:00, before-weekday 09:00, in N days).
3. **"By the next call"** with a verified counterpart, against the calendar:
   - **Source:** none exists today. Phase 2 reads `~/.orchestra/state/calendar/shaw.ics` with our own Python reader, because `read-ics.mjs` drops attendee emails. How that file gets filled is **NEEDS-YOU**.
   - **Verified** means the counterpart resolves to a people or client contact **that has an email**, and that email equals an ATTENDEE email on an event starting after the call. A name-only match is used only when exactly one future event within 30 days has an attendee whose CN matches the contact's name or alias exactly; two or more candidates leave the deadline unknown.
   - The event is the earliest match, identified by `UID` + `RECURRENCE-ID`. Then `due_ts` = event start − 1 h, and `due_source` = `next call with <name> (<event>, <date>, uid <short>)`.
   - The free-text `client.json` `next_call` isn't used.
4. **Otherwise `due_ts` stays null**, and the card reads `Due: unknown (<phrase>)` or `Due: none stated`. There is never a silent default.

## 4. The card: one questionnaire per call (paged only beyond 19 items)

- **Filed as:** `approval.py questionnaire --from postcall --feature post_call --op-key card:<call>:r<n>:p<k> --title "<call title>: N items" --summary … --questions-json @file`.
- **Summary**, line 1 (FOUNDATION §2): `Decide by: <earliest due_ts − 2h, else call end + 24h, Shaw-local + label> · If no answer: nothing is created or dispatched · Cost of waiting: <earliest due item> slips`.
  - Line 2: `From: <title or "Arturo call"> · <Shaw-local start> · <duration>`, plus `Page k of n` when paged.
  - Line 3, if there were patches: `Already tracked, due date moved: <n>`.
- **Q1 is the bulk control,** a menu and required:
  - `1 Accept every (suggested) choice`
  - `2 Decide per item`
  - `3 Drop all`
  Its detail text states the rules below.
- **Q2..Qn, one per item,** each a menu and **optional**:
  - The prompt is `<action> · <counterpart> · Due: <display>`, and the detail is the quote, the citation and `speaker unverified` where that applies.
  - **Option 1 is always the suggested choice, labelled "(suggested)":** `Assign to <seat>: <agent_work>` when the item is agent-takeable and a live seat resolves, otherwise `Take it myself`. The other options follow: `Take it myself` (if it isn't already option 1), `Drop`.
  - The full option list and the suggested option are **frozen in the ledger when the card is filed** (`card_items(card_id, q_n, item_key, options_json, suggested_n)`), and answers are interpreted only against that frozen copy.
- **How answers resolve:**
  - **An explicit per-item answer always wins over Q1.**
  - Q1=1: unanswered items get their suggested option, **except inferred or unknown-speaker items and uncertain-match items (§5).** Unanswered ones of those become `unreviewed`. Q1's label says "(items marked ⚠ need your own answer)".
  - Q1=2: **unanswered items get no action.** They're recorded as `unreviewed` and listed in the gm result notice, and nothing is created.
  - Q1=3: unanswered items are dropped.
- **Overflow:** more than 19 items are split into pages of 19 (`…:p1`, `…:p2`, …), each its own questionnaire titled `Page k of n`. Items are ordered earliest-due first. **(Inferred:** no single call is likely to reach this; it's specified so nothing is silently cut.)
- **Reading answers:**
  - The sweeper acts on a questionnaire only when its row status is `submitted` or `resumed`, and reads only `final` answers, never drafts (`questionnaire_schema.py:74-79, 382-394`).
  - The `questionnaire_submitted` msg_store row is a wake hint, not the source of truth.
  - Once its answers are applied, the sweeper **acks** the delivery (`qack`) and the msg row, so the resume watchdog stops retrying (`questionnaire_resume.py:86-126, 267-315`).
- **No expiry.** Questionnaires never auto-expire (`questionnaire_schema.py:8-9`), so the v1 claim of a 72 h expiry is withdrawn. A card that's still pending stays pending, and nothing happens. At 7 days the sweeper lists stale cards in one gm line; gm decides whether postcall retires them (author retire, `questionnaire_schema.py:400`).
- **No new surface:** this is the existing questionnaire lane and the H1 commitment human-task render.

## 5. Dedupe across calls and reschedules

- **Two identities, never derived from generated text (v3):**
  - **Source identity** `src_` + `sha256(canonical_call_id | norm(quote))[:12]`.
    - It's the same item when the same call is re-extracted. The verbatim quote is stable; the LLM's action text and the offsets aren't.
    - Supersession aliases resolve to the canonical call first.
    - This is what stops re-carding within a call, recurring items included.
  - **Commitment key** `cmt_` + 12 hex, minted at random on first sight and stored against the source identity.
    - It's used as the human-task op_key.
    - A later call's item links to an existing `cmt_` **only** through a match that is confirmed (by rule or by Shaw), never through a hash.
- **Matching across calls.** Candidates are ledger items for any counterpart named in the transcript that are open, or done or dropped within 14 days. They're passed to the prompt, and the LLM may return `matches_existing: <cmt>`.
  - **Auto-merge** happens only if ALL of these hold:
    - same resolved counterpart id (a people id or client slug, not a bare name);
    - same normalized **deliverable object** (the noun phrase the LLM extracts, e.g. "pricing deck", compared exactly after normalization);
    - neither item is `recurring`;
    - the new quote contains a schedule-change cue ("move", "push", "instead", "reschedule", "by <new date>") or repeats the deliverable without a new task.
  - **Uncertain match** (the LLM says match but the rules don't all pass): the item goes on the new card as a normal question with an extra option, `Same as "<existing action>" (<date>): update it`, and a ⚠ marker. It's excluded from accept-all, so Shaw decides.
  - **No match:** a new item.
  - **A wrong merge can only happen when Shaw picks it or all four rules pass.** A wrong split costs one question.
- **When a new mention is merged** (auto or by Shaw) into an existing item:

| existing state | new mention has a resolved `due_ts` that differs | new mention has no or unknown `due_ts` |
|---|---|---|
| human task pending | `patch` evidence `{due_ts,due_source}` and the summary's `Due:` line, both in **one** `patch` call. Check the exit code, then re-read. Exit 4 (no longer pending): the ledger takes the task's real status and nothing is re-carded. **Gated (see Patch race below).** | keep the known due; append the citation |
| still on an *unanswered* questionnaire (questions are immutable, `questionnaire_schema.py:173`) | the new call's card carries the item **visibly** as `Due changed: <old> → <new> (from <call>)` with the same three options. The answer given there wins, and the old card's question for that item is marked superseded in the ledger and ignored when the old card is applied. **Nothing changes silently.** | append the citation; no new question |
| dispatched, awaiting results | update the ledger due; tell the seat once (`dispatch:<card>:<n>:due2`); the results notice to gm shows the change | append the citation |
| done or dropped < 14 days ago | not re-asked; the summary lists it as `already done/dropped: <n>` | same |
| done or dropped ≥ 14 days ago | not a candidate, so it becomes a new item | same |

- **Patch race.** `update_fields` checks pending status, then runs an unguarded UPDATE (`approval_schema.py:648-663`). **Live patching is gated on the owner's fix**: a guarded `UPDATE … WHERE status='pending'` plus a rowcount check, ticketed to orchestraos-builder.
  - Until that lands, the reschedule path for a pending human task files **nothing**. It records `due_pending_patch` in the ledger and lists the change in the gm results notice.
  - The rollout checklist (§7) names this fix as a prerequisite for turning on reschedule patching. Everything else can go live without it.
- **All duplicates:** if every item is an auto-merged duplicate that needs no visible question (no `Due changed` row and no uncertain match), the only effects are patches and citations. Status becomes `no_card_dupes` and no card is filed.

## 6. Dispatch and results

- **Applying an answer,** only after §4's status and final-answer checks:
  - **Take it myself:** effect `task:<item_key>` runs `approval.py human-task --from postcall --task <short> --op-key <item_key> --feature commitment --blocks <counterpart> --summary <miner 4-line format + citation> --evidence {due_ts,due_source}`. One human task per item.
  - **Assign to agent:** effect `dispatch:<card_id>:<q_n>`.
  - **Drop:** ledger `dropped`.
- **Authorization check before every dispatch,** in code, which refuses on any mismatch:
  1. The questionnaire row is `submitted`/`resumed` and the answer for `q_n` is `final`, or Q1=1 applied to an unanswered item.
  2. The chosen option index maps, in the **frozen** `card_items.options_json`, to `Assign to <seat>`. The seat and `agent_work` scope come from that frozen row, never from a fresh resolve.
  3. No `dispatch:<card_id>:<q_n>` effect is already `sent` or `confirmed`.
  4. The seat is live in the registry right now. If it isn't, there's no silent reroute: we send the item to gm with the card id so gm picks the seat, and the ledger records `rerouted`.
- **Message:**
  - `msg_store.py send --from postcall --to <seat> --type task --priority high --subject "[postcall dispatch:<card_id>:<q_n>] <action>" --body-file <f>`.
  - The body carries the frozen scope, quote, citation and due date, plus "Approved by Shaw on card <card_id> Q<n> (check: `approval.py get <card_id>`). Treat dispatch id as idempotency key: if you already have it, reply with the existing result. Reply to: postcall."
  - **There's no `--card` flag on msg_store** (`msg_store.py:1992-2025`). Adding one would be fleet infrastructure, so it isn't proposed; if gm wants it, it's an orchestraos-builder ticket.
- **Seat resolution,** done before filing so the option names the seat:
  1. counterpart → `client.json` `pm_agent`;
  2. else project → `protocol.py determine_pm()`;
  3. it must be live in `registry.json`, where the PM seats in `gm.md:283-293` are absent today;
  4. else gm.
- **Results:** replies to `postcall` are matched to their item by the dispatch id in the subject. When every dispatched item on a card has a result, or 48 h pass (report partial), effect `results:<card_id>` sends **one** message to gm: `Post-call results ready: <title>: <k>/<m> done, <unreviewed u>, <links>`. gm relays one line to Shaw; postcall never texts him.

## 7. Fixtures, tests, shadow and rollout

- **Effect adapters.** All external writes go through one interface, `postcall/effects.py`, with three implementations:
  - `live` (approval.py and msg_store CLIs),
  - `shadow` (append to `state/postcall/shadow.jsonl` and **never** call approval.py, msg_store, ntfy, Telegram or any subprocess except the extractor),
  - `test` (in-memory recorder).
  The mode comes from `~/.orchestra/state/postcall/MODE`, which is `shadow` unless gm writes `live`. **The default is shadow; a missing or unreadable file means shadow.** Filing a questionnaire pushes immediately (`approval.py:744-748` → `approval_notify.py:75-85`), so a scratch DB alone isn't a fence. That's why shadow never reaches approval.py at all.
- **Shadow and live state are separate (v3).**
  - Shadow writes only to `state/postcall/shadow/ledger.db` and `shadow.jsonl`. The live ledger is `state/postcall/live/ledger.db`, which is **created empty at activation**.
  - When gm writes `MODE=live`, the activation step records `live_start_ts = now` in the live ledger. Only calls that end after `live_start_ts` are eligible. Calls seen during shadow are **never replayed** into live, so there's no unreviewed first batch.
  - Replaying the shadow window would be an explicit, separate gm order, run as `sweep.py --replay-shadow --since …`, which itself files through the live path.
  - Code refuses to open the live ledger while the mode is `shadow`, and refuses the reverse too.
- **Test fence, set up before collection (v3).**
  - `tests/postcall/conftest.py` sets the fence environment **at module level**: `APPROVAL_DB_PATH` in a fresh tmp dir, `NTFY_TOKEN_FILE` at a missing path, `ORCHESTRA_DIR`/`MSG_DB_PATH` in scratch, `POSTCALL_MODE=test`, and `TELEGRAM_*` unset.
  - Pytest loads conftest.py before it imports any test module, the same way root `conftest.py:56-67` works, so it holds under `-k`, `-p xdist` and single-file runs.
  - A module-level assert aborts collection if any fence value points at a live path.
  - **Child processes inherit the environment, and the environment is the fence for them:**
    - an approval.py child writes only to the scratch DB;
    - `notify_questionnaire` raises without a token (`approval_notify.py:80-82`);
    - msg_store goes to scratch.
  - In-process monkeypatches cover the parent only: `subprocess.run`/`Popen` raise unless the call is on a per-test allowlist (empty by default), and `approval_notify._http_post` and `tg_shaw` raise.
  - **One integration test** allowlists exactly `python3 <repo>/scripts/approval.py` to prove the `live` adapter's argument shapes. Its child is fenced by the inherited environment, and the test asserts that `notified_at` stays null afterwards.
  - Pattern sources: `scripts/test_approval_ack_idempotent.py:15-30` and root `conftest.py:56-69`.
- **Fixtures** live in `tests/postcall/fixtures/`, all sanitized with synthetic names and numbers:
  - voice: 0-turn `vc_live`, 2 s 1-turn, `test:true`, a superseded server/client pair with a late `superseded_by`, a long call with 3 commitments + 1 intention, a reschedule pair;
  - pocket: diarized with `_[MM:SS]_` markers, non-diarized, a non-call category, a re-ingest with a changed hash;
  - `llm/*.json`: canned outputs, including a fabricated quote, a fuzzy-only quote, another speaker's promise, malformed JSON and a timeout;
  - `calendar.ics`: an attendee match and a decoy.
- **Tests,** each written to fail first:
  - Empty call, extractor failure (no `no_card`, retried) and genuine empty are each distinguished, and empty never yields a card.
  - Fuzzy-only and other-speaker quotes are dropped.
  - Every item has a citation.
  - Lease takeover after a crash.
  - Effect recovery: kill between `planned` and `sent`, and between `sent` and `confirmed` → exactly one card, task and message.
  - Re-ingest → only new items.
  - Late supersession → one card.
  - Deadline precedence; `tz_at_call` preserved; "Friday at 2pm" → 14:00; ambiguous → null; unknown is shown as unknown.
  - Summary line 1 is `Decide by:`.
  - Q1 rules: accept-all, per-item with blanks, explicit-beats-bulk, drop-all.
  - Answers ignored while the questionnaire is still a draft.
  - Paging at 20 items.
  - Dispatch refused on a non-final answer, a changed option, a repeat, or a dead seat (reroute to gm).
  - Recurring never merges; reschedule patches both evidence and the Due line; patch exit 4 handled; all-dupes → no card.
  - One results notice, and a partial at 48 h.
  - Shadow mode makes zero calls to approval.py and msg_store.
  - Live activation ignores calls from the shadow window, and the two ledgers can't cross.
  - Concurrency: a second sweeper blocks on the inherited lock while a child is still alive.
  - An uncertain match is excluded from accept-all; a `Due changed` row is visible and wins over the old card.
- **Proof:** each fix gets a sabotage run (revert it, test goes red).
- **Rollout gate.** These are recorded prerequisites, checked off in `state/postcall/ROLLOUT.md` by gm:
  1. gm approves the cron line (fleet infra).
  2. Shadow runs at least 24 h on live calls.
  3. gm reviews `shadow.jsonl` and the would-be cards.
  4. **The Shaw-validation decision is recorded.** The miner's kill-switch says Shaw validates its first live batch. gm records either "Shaw reviewed shadow batch <link>" or "gm waives, reason <x>". Shadow review by gm does not silently satisfy it.
  5. gm writes `MODE=live`, which creates the live ledger and sets `live_start_ts`.
  6. *(reschedule patching only)* orchestraos-builder's guarded `update_fields` has landed. Until then that one path is a no-op that gets reported (§5).
  No real card reaches Shaw before step 5.

## 8. Files and owners

| File | Change | Owner |
|---|---|---|
| `scripts/postcall/{sweep,extract,deadlines,calendar_ics,card,dedupe,dispatch,effects,ledger}.py` | new | postcall-dev |
| `tests/postcall/**` (conftest fence, fixtures, tests) | new | postcall-dev |
| crontab line (sweeper, every 5 min, flock, niced) | new | **gm approval** (fleet infra) |
| `~/.orchestra/state/postcall/{live/ledger.db,shadow/ledger.db,shadow.jsonl,MODE,ROLLOUT.md,sweep.lock}` | new state; MODE and ROLLOUT are written only by gm | postcall-dev / gm |
| `~/.orchestra/state/calendar/shaw.ics` | new feed | **NEEDS-YOU** (§9.1) |
| `~/repos/pocket-agent/commitment_miner.py` | **untouched** (pure functions copied, flag stays OFF) | pocket-service |
| `scripts/approval_schema.py` `update_fields` | **not edited by us.** One-line guarded-UPDATE ticket (§5) | orchestraos-builder |
| `services/arturo/*`, `scripts/watch_gateway.py`, `api/**`, `dashboard/**`, `msg_store.py`, `approval.py` | **untouched** (CLI use only) | codex-arturo / orchestraos-builder / avatar-dev |
| overlay `~/scripts/agent-orchestra/scripts/postcall/` | synced copy at rollout, via the normal overlay path | postcall-dev, after gm's go |

## 9. Open questions for gm (and NEEDS-YOU)

1. **Calendar feed.** Who supplies `shaw.ics`, and how (a secret ICS URL fetched by cron, or a Calendar MCP)? Without it, "by the next call" is always "unknown". Shaw-gated if it needs his Google account.
2. **Author identity.** `--from postcall` (a service mailbox, like commitment-miner) or `--from postcall-dev`? The skill says `--from` must be the filing seat.
3. **Voice-call scope.** Should Arturo calls be mined at all? I propose yes, user-turn quotes only, skipping quotes that are already a dispatched tool turn.
4. **Short-call floor.** Pocket minimum of 120 s, versus the miner's 5 min.
5. **Other people's promises to Shaw.** Out of v2. Confirm.
6. **No backfill.** Only calls after `start_ts`. Confirm.
7. **Shaw validation of the first live batch** (the miner kill-switch's condition). Is that needed, or does gm's shadow review suffice?
8. **Stale cards.** Pending 7+ days: does postcall retire its own card, or leave it?

## Review

- **Congruence decision:** DEC-1795627933880541 (`DOCS/SHARED_DECISIONS.json`); reviewers postcall-dev-claude and codex.
- **Codex round 1 (v1 note): `REJECT`.** It raised 6 blockers and 2 should-fixes, all of which I checked against the code and found accurate:
  1. Claiming wasn't end-to-end idempotent.
  2. Shadow mode could still push to Shaw.
  3. A 72 h expiry was claimed that doesn't exist.
  4. Token-hash dedupe could corrupt intent; the patch race went unhandled.
  5. Bulk approve was underspecified.
  6. Dispatch had no authorization check.
  7. Fuzzy grounding and failure-equals-empty.
  8. Deadline timezone and clock, backfill, supersession, and the miner's Shaw gate.

  Where each was fixed in v2: 1 → §1 leases and effects; 2 → §7 adapters and MODE; 3 → §4 no expiry; 4 → §5 occurrence identity and state table; 5 → §4 resolution rules and frozen options; 6 → §6 authorization check; 7 → §2 exact match, speaker binding and `(ok, items)`; 8 → §1 no backfill and identity, §3 `tz_at_call` and clock, §7 gate step 4.
- **Codex round 2 (v2 note): `REJECT`.** It raised 5 blockers and 3 should-fixes, all accurate. Where each was fixed in v3:
  1. The recovery race → §1 concurrency contract (an inherited flock, held by children) and a read-only op_key SQL lookup.
  2. A silent reschedule on an unanswered card → §5's visible `Due changed` row; live patching gated on the owner's guarded-UPDATE fix.
  3. Lost commitments from a loose merge → §5's two identities (source identity from the quote, random `cmt_`), a 4-rule auto-merge, and uncertain matches shown to Shaw.
  4. Speaker trust → §2's verified vs. inferred levels, where inferred items need an explicit answer; plus a plain statement of what grounding does not prove.
  5. Shadow and live state → §7's separate ledgers, `live_start_ts` and no replay.
  6. The fence → module-level conftest and children fenced through the environment.
  7. Deadlines → zone from `since`, composition, terminal ambiguity, matching by attendee email and UID.
  8. Rollout → the recorded Shaw-validation prerequisite and `qack`.
- **Codex round 3 (v3 note):** see below.
