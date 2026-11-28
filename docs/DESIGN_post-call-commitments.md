# DESIGN: post-call commitments (phase 1 note)

- Seat: postcall-dev (T1, parent gm). Branch `build/post-call-commitments`, worktree `<worktree>`.
- v1 written 2026-11-25 ~17:30Z. **v2 followed codex REJECT round 1 (8 findings); v3 followed codex REJECT round 2 (5 blockers, 3 should-fixes). v4 = the scope correction above; v4.1/v4.2 = codex rounds 4-5 fixes; v4.3 = gm rulings. All are addressed; see §Review.** Status: **DESIGN ONLY, no code yet.** gm approves phase 2 or sends it back.
- Program (msg_3e8d4dad, relayed in `prompts/postcall-dev.md`): when a call is ingested, put up ONE review card
  per call listing Shaw's commitments and intentions, with deadlines and the agent work that could be offloaded.
  On approval, create human tasks, dispatch approved agent work, and tell Shaw once when the results are ready.
- **SCOPE CORRECTION (Shaw via gm, msg_ecea3d6c, 17:36Z), applied in v4:**
  - "Post-call" means **real phone, video and in-person calls with other people**: Pocket recordings, plus any call transcript that lands the same way.
  - It does **not** mean Arturo voice-assistant calls, so that trigger is dropped.
  - Every card carries two kinds of item: **(a) what Shaw said he'd do**, and **(b) what the other participants said they'd do**, which become chase items with the counterpart named.
  - Participants come from the recording's speakers and the calendar event it matches.

Every claim below was read from disk (file:line) or comes from one of four read-only survey passes run today.
Anything inferred is marked **(inferred)**.

---

## 0. What exists today (it changes the shape of the build)

| Thing | Where | State |
|---|---|---|
| **Commitments miner** (Shaw's own micro-commitments: quote-verified, `cmt_` ids, ledger, human-task cards) | `~/repos/pocket-agent/commitment_miner.py` (prompt `cmt-v1` :29-42, `resolve_due_ts` :185, `ground_commitments` :269, `mine_recording` :340, `commitment_id` :498) | **OFF.** `~/.orchestra/dream/COMMITMENT_MINER_DISABLED` (2026-09-13: "keep OFF until Shaw validates first live batch"). No cron line. Ledger `state/commitments_ledger.json` has 50 rows, the last from 09-13. |
| Dream facts pipeline (`[FROM RECORDING: <title>] …`) | `dream/pocket-extract.py:43-53` (LLM), `collect-activity.py:341-342` (tagging), ingested to `state/brain/facts.db` at 04:15 and 08:30 ET | Runs nightly. The output is free text. |
| Pocket recordings (real calls with other people) | `~/pocket-recordings/<date>-<slug>/{meta.json,transcript.md,transcript.json,audio.m4a}`; ingested by `pocket-agent/drainer.py:406-411` via reconcile cron every 15 min (crontab:41) | 638 dirs. `meta.json` has `recording_id`, `created_at`, `duration`, `category`, `transcript_hash`, `ingested`. `transcript.md` carries `_[MM:SS]_` offset markers. |
| Arturo voice calls (`state/voice-calls/`) | assistant calls, not calls with people | **Out of scope** (scope correction). No participant fields, no audio link. |
| Other call transcripts | `~/.orchestra/queue/transcript-player-inbox/<call>/fathom-segments.json` = `[{start, speaker, text}]` with **named** speakers (1 call there today); `queue/bridged/meetings/*.json` = a pasted free-text body (1 file); the ElevenLabs post-call dir is empty | Rare today. A second adapter covers the segments format (§1). Pasted free-text meetings aren't covered in v4 because they have no speakers or offsets. |
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
| `segments` | `seg:<inbox dir name>`, which is stable; a content change bumps `revision` and keeps the id | a `*segments.json` that's parseable and unchanged for 120 s | fewer than 2 distinct speakers or fewer than 60 words |

- **Arturo voice calls are not inputs** (scope correction). The supersession and identity handling v3 needed for them is gone.
- **No backfill.** Only calls whose `ended_at`/`created_at` falls after `live_start_ts` (§7; set once at activation) are considered. The 638 historical recordings are ignored. Mining the past would be a separate gm or Shaw decision.
- **Mechanism.** A cron sweeper, `flock -n` + `nice -n 19 taskset -c 6,7`, every 5 min. It touches no code owned by pocket-service and covers first ingest and re-ingest alike. A dropped segments file is picked up on the same pass.

**Ledger: `~/.orchestra/state/postcall/{live,shadow}/ledger.db` (sqlite, WAL; one per mode, §7), with a lease on each call and an intent row for each side effect**
- **`calls`** `(call_id PK, revision, content_hash, status, lease_owner, lease_until, attempts, last_error, tz_at_call, created_at, updated_at)`.
  - **Claiming:** a single `UPDATE … SET lease_owner=?, lease_until=now+10m WHERE call_id=? AND (lease_until IS NULL OR lease_until<now) AND status IN (...)`. A crashed sweeper's lease expires and the next sweep takes over.
  - **States:** `new → extracting → extracted → filing → carded | no_card | no_card_dupes | failed_retry(n) | failed_final `.
- **`effects`** `(effect_id PK, call_id, kind, payload_json, state, external_id, created_at, confirmed_at)`. Every external write gets a **deterministic `effect_id`** and an intent row, committed *before* the external call:
  - `card:<call_id>:r<revision>:p<page>` is a questionnaire, with op_key = effect_id.
  - `task:<cmt>` is a human task, op_key = `cmt`.
  - `dispatch:<cmt>`: at most once per commitment, ever.
  - `patch:<cmt>:s<seq>` / `duenote:<cmt>:s<seq>`: one per due change, carrying an immutable payload (§5).
  - `results:<card_id>`
  The states are `planned → sent → confirmed | obsolete`. Payloads are written when an effect is planned and never rebuilt.
- **Concurrency contract (v3).** Exactly one writer exists at a time.
  - The sweeper takes `flock` on `state/postcall/sweep.lock` for its whole run, and opens that fd **inheritable**, passing it to every CLI child it spawns (`pass_fds`). The lock is therefore held until the last child exits, even if the parent dies. Reconciliation and all effect writers run only under this lock.
  - Children are awaited with a timeout. On timeout the sweeper kills its *own* child (a process we spawned) and marks the effect `sent` (outcome unknown), so the next sweep reconciles it.
  - Leases are kept only to make a crashed run visible. They aren't a second concurrency mechanism.
- **Recovery.** At the start of each sweep, every effect still in `planned` or `sent` is reconciled against the outside world before anything is retried:
  - **Cards and tasks:** a **read-only** SQL query (`sqlite3` URI `mode=ro`) against tasks.db, any status:
    - `SELECT id,status FROM questionnaires WHERE from_agent=<AUTHOR> AND op_key=?`
    - `SELECT id,status FROM approval_requests WHERE from_agent=<AUTHOR> AND op_key=?`
    - `<AUTHOR>` is `postcall-dev` for now, and the service identity after rollout (§9 ruling 1).
  - **Patches:** a read-only read of that row's `evidence`. Equal to the payload means `confirmed`; otherwise re-apply, but only if it's still the latest `seq` (§5).
    `approval.py get` takes an id, not an op_key (`questionnaire_schema.py:245`), and `pending` covers pending rows only, so neither works for this. Reading the DB isn't writing it, so the surface-decision "never write ApprovalStore directly" rule still holds.
  - **Dispatches and result notices:** msg_store lookup by the effect id carried in the subject.
  If found, record `external_id` and set `confirmed`. If not found (and the lock guarantees no in-flight child), re-send. op_key dedupe protects only *pending* rows (`questionnaire_schema.py:177-182`), and the 900 s guard counts from *creation* (`approval_schema.py:585-588`). So **this reconcile step under the lock is the real idempotency guard; op_key is only a second line of defence.**
- **Revisions.** If the transcript or turns hash changes after carding, `revision` goes up. The call is re-extracted, and only *new* items get a new card, `card:<call>:r<n>:p<k>`.
  - An item is new when its **source identity** (§5: canonical call + normalized quote hash) isn't already on the ledger.
  - Recurring items use the same identity, so re-extraction never re-cards an item already shown.

## 2. Extraction: reuse the commitments miner and extend it; don't add a third pass

- **The dream facts pipeline can't carry this.** Its output is `[FROM RECORDING: <title>] <free text>`.
  - The recording id is dropped at `collect-activity.py:342`.
  - `entities` is empty in facts.db.
  - There's no due field and no offset, and the `file:line` `source_id` drifts when the daily jsonl is re-sorted (`collect-activity.py:192-222`).
  - It runs nightly.
  - Carrying due dates, participants and citations would mean changing its schema and every reader, with the latency still left over.
- **The commitments miner is the existing pipeline closest to this.** It's per recording, quote-grounded, has a deadline resolver vetted under DEC-1789352893701528, has `cmt_` ids, and its cards already match the commitment card contract.
- **Plan:**
  - Lift its pure functions into `scripts/postcall/extract.py` and bump the prompt to **`cmt-v2`**.
  - The pocket-agent copy is untouched and stays OFF.
  - Phase 2 doesn't remove the miner's disable flag. Our pipeline replaces its card path, so running both would double-card.
- **The prompt widens in v4.** cmt-v1 says "ignore other people's promises" (`commitment_miner.py:31-32`); cmt-v2 asks for both directions.
- **`cmt-v2` adds these fields to each item:**
  - `owner`: `shaw` | `participant`. Each card has two sections: (a) Shaw's, (b) theirs.
  - `kind`: commitment | intention. An intention can only belong to Shaw.
  - `counterpart`: for Shaw's items, who it's for; for theirs, **who promised**. Resolved against the call's participant list (§2a).
  - `project`
  - `explicit_date`, which must be a substring of the quote, otherwise it's discarded
  - `recurring`: bool
  - `agent_takeable` + `agent_work`, one line, always a draft, research or an internal artefact; agents never send as Shaw (FOUNDATION §3)
  - `matches_existing`
  - `deliverable`: the normalized object noun phrase, used for matching (§5)
  - `operation`: `draft | send | do | review | schedule | decide` (§5)
  - `same_as_occ`: set only on re-extraction (§5)
  - `speaker`: which diarization label or turn role said the quote
  - The quote is still verbatim.
- **Scope (v4):** Shaw's commitments and intentions, **and** each participant's promises to Shaw or his side. Promises between two other people are **kept in the ledger as context rows (`owner=third_party`) and never put on the card** (gm ruling).

### 2a. Participants: who was on the call
Two sources are combined, and each participant gets a trust level.
1. **Calendar match:** the event whose start-end window overlaps the recording (`created_at` to `created_at + duration`, ±10 min), using the §3 ICS feed.
   - Its attendee list (CN + email, minus Shaw and `DECLINED`) is the **invited set**. An invitee counts as a **participant** only once a speaker is mapped to them. The card's `With:` line marks spoken participants ✓ and invited-but-unmapped people `(invited)`.
   - Two overlapping events: the one with the larger overlap wins, and a tie means no match.
   - No calendar feed, or no event: participants mapped only by evidence are unverified. Pocket-named speakers (2 below) are verified without any calendar.
2. **Speakers:**
   - Segments files carry **names** (`speaker`), which are verified when they match a calendar attendee or a people or client contact.
   - **Pocket-named speakers (Shaw, 2026-11-27: "We need to not require a calendar integration in order to match speakers or trigger a post call workflow"):** a Pocket label shaped like a person's name ("Dana Reyes"; letters, spaces and `.'-` only, no generic word such as speaker/unknown/guest/person/host) is that participant and is **verified** with no calendar, provided `POSTCALL_SHAW_NAMES` is set and Shaw's own label is proven (a generic label with a quoted proof, or his configured name); otherwise the name is shown but unverified. Neither the extractor nor the calendar can rename it. An invitee whose name is exactly the label lends its email (for "by the next call"); aliases and contacts never do. A label naming Shaw (`POSTCALL_SHAW_NAMES`) is never a participant, and a call where such a label exists but the extractor claimed another one is an attribution failure (`failed_retry`). A doubtful label is treated as generic.
   - Generic Pocket labels (`SPEAKER_00/01`, "Speaker A", "Unknown"):
     - The LLM proposes a mapping from label to name using in-call evidence (self-introductions, "thanks, <name>"), and must quote the evidence.
     - Code accepts a mapping only if the quoted evidence is in the transcript AND the name is in the verified participant set.
     - Otherwise the label stays `participant (unidentified)`.
   - **A two-party shortcut** (only when no label is Pocket-named): if the calendar event has exactly one non-Shaw attendee and the transcript has exactly 2 labels, then once Shaw's label is established, the other label is that attendee.
- **Counterpart on an item** = the speaker's mapped name for theirs; the named person for Shaw's. An item whose counterpart doesn't resolve shows `with: unidentified` and is ⚠ (needs Shaw's own answer).
- **Grounding is tighter than the miner's:**
  1. **Exact normalized match only.** The miner also accepts fuzzy ≥0.90 (`commitment_miner.py:269-276` → `goal_grounding.quote_in_transcript`). v2 drops the fuzzy path.
  2. **Speaker binding.** There are two trust levels, and the card shows which applies.
     - **verified:** a segments-file item whose `speaker` name resolves to Shaw (as the owner of a Shaw item) or to a verified participant (as the owner of a their item).
     - **inferred:** every Pocket item.
       - Diarized transcripts carry only `SPEAKER_00`/`SPEAKER_01` labels (checked on disk 2026-11-25), and nothing on the box maps a label to Shaw.
       - When a transcript is diarized, the LLM proposes Shaw's label with quoted evidence, and code requires that label to agree across the call's items; if it doesn't, the call goes `failed_retry` and then to manual review.
       - **Named labels (gm msg_ac5f49bf, 11-26):** some recorder transcripts label speakers by name. A label equal to a name in `POSTCALL_SHAW_NAMES` (set in the sweep's environment, never in code) proves itself when it is the label claimed. **Every** label carrying a configured form of his name is his voice (Shaw 2026-11-28, "Shaw Cole is my name": Pocket can split him into "Shaw Cole" + "Cole"); this replaces the "only one label naming Shaw" clause of gm msg_ac5f49bf. His items may come from any of those labels, and none of them is ever a participant. If a label names Shaw and the extractor claimed a label that does not, the call is an attribution failure whatever the evidence. A claimed label that is neither generic (proven by a quote) nor his configured name leaves Pocket names unverified.
       - **Shaw's own items** are verified when Shaw's label is one of the voices and every OTHER voice is verified (Pocket-named, or a verified invitee). A matched calendar event is no longer required (11-27). One unnamed generic voice keeps Shaw's items inferred (⚠).
       - **Quote-in-segment rule:** the grounded quote's span must lie entirely inside **one** speaker turn (the text between two `**SPEAKER_xx**` / segment boundaries), and that turn's speaker determines the attribution. An item's `owner` must match it: Shaw's label for `shaw`, any other label for `participant`. A quote that spans turns, or an owner mismatch, drops the item. The same rule applies to segments files, using the `speaker` field.
       - When a transcript isn't diarized, the item is marked `speaker unknown`.
     - **Inferred and unknown items are never covered by Q1 accept-all (§4).** Each needs its own explicit answer, so no task or dispatch comes from an unconfirmed attribution.
       - **(Inferred:** with Pocket-named speakers, calls whose voices are all named are verified with no calendar. Calls with a generic voice stay ⚠ for the items it touches.)
     - A trusted voiceprint or speaker-name mapping is out of scope; that's a pocket-service feature.
  3. **Citation** is computed in code from where the quote sits: `pocket:<id>@MM:SS` (nearest preceding `_[MM:SS]_` in `transcript.md`) or `seg:<id>@<start s>`.
  4. **A substance filter** drops items whose `commitment_text` is under 3 content words or is a pure pleasantry ("talk soon"). The list is fixture-tuned.
- **Failure vs. empty.** The miner turns a timeout, OSError or bad JSON into `[]` (`commitment_miner.py:411-416`). v2's extractor returns `(ok, items)`:
  - `ok=False` → `failed_retry`, with **no card and no `no_card` mark**;
  - `ok=True, items=[]` after grounding → `no_card`.
  **An empty call never yields a card** (2026-11-19 lesson):
  - Calls under the §1 floors are never sent to the LLM.
  - A call that has items in neither section yields nothing.
  - The filing step refuses an item list of length 0.
  - Tests pin both.

  *What this does not guarantee:* whether a grounded quote is really a commitment is the LLM's judgement. The card is the human check on that judgement; it doesn't certify it. **(Stated plainly so nobody reads "grounded" as "true".)**
- **LLM.** One headless `claude -p` per call, using the miner's model, niced and pinned, one at a time. Transcripts over 60k chars are split into overlapping chunks and merged. **Cost:** roughly 5-15 calls per day (inferred). Only stubbed responses are used in tests.

## 3. Deadlines

Precedence, first hit wins; `due_source` names the rule.
1. **Explicit date or clock**, grounded in the quote:
   - Resolved in **`tz_at_call`**. `shaw-location.json` keeps only the *current* zone plus `since`.
     - If `since` ≤ call start, that zone is used.
     - Otherwise (he moved after the call and there's no history), the date is kept but the clock time isn't: `due_ts` = 17:00 in `home_base.tz`, and `due_source` gets the suffix ` (zone assumed: <label>)`, which the card shows as `Due: <date> (zone assumed)`. **That's visible uncertainty, not implied certainty.** Calls are processed within minutes of ingest, so this path is rare.
     - **DST:** `zoneinfo`; an ambiguous wall time takes `fold=0` (first occurrence), and a nonexistent one moves forward to the first valid minute. Both add ` (DST-adjusted)` to `due_source`.
     - The zone is stored on the call row, so a later move never shifts an old deadline.
   - **Composition:** a relative date plus an explicit clock ("tomorrow at 3") takes the date from rule 2 and the clock from the quote.
   - **Ambiguity is terminal:** `due_ts=null` and `due_source="ambiguous: <phrase>"`, shown as unknown and never re-guessed later.
   - The miner hardcodes America/New_York (`commitment_miner.py:127`); the lifted copy fixes that.
   - It also ignores a clock inside a weekday phrase (`commitment_miner.py:226-230`, "Friday at 2pm" → 17:00). v2 honours the clock.
2. **Relative phrase** against the call start, using `resolve_due_ts`'s rules (17:00 default, tonight 21:00, before-weekday 09:00, in N days).
3. **"By the next call"** with a verified counterpart, against the calendar:
   - **Source:** none exists today. gm is asking Shaw for a secret ICS URL at `~/.secrets/shaw-calendar.ics.url` (0600). Our cron reads it **by reference** (never printed or logged) and writes `~/.orchestra/state/calendar/shaw.ics`, which our own Python reader parses (`read-ics.mjs` drops attendee emails). **If the file is absent, the deadline is unknown and stays unknown.** Phase 2 doesn't wait for it.
   - **Verified** means **email only (v4):** the counterpart is a participant whose email came from the matched call event's attendee list (§2a) or a people or client contact, and that email is an ATTENDEE of a later event. **Name-only matching is dropped.** No email means the deadline is unknown.
   - **Calendar parsing:** RRULE/RDATE/EXDATE are expanded over the next 60 days (python `icalendar` + `recurring_ical_events`); overrides by `RECURRENCE-ID` win; `STATUS:CANCELLED` events and attendees with `PARTSTAT=DECLINED` are excluded; times are converted with the event's TZID.
   - The event is the earliest match, identified by `UID` + `RECURRENCE-ID`. Then `due_ts` = event start − 1 h, and `due_source` = `next call with <name> (<event>, <date>, uid <short>)`.
   - The free-text `client.json` `next_call` isn't used.
4. **Otherwise `due_ts` stays null**, and the card reads `Due: unknown (<phrase>)` or `Due: none stated`. There is never a silent default.

## 4. The card: one questionnaire per call (paged only beyond 19 items)

- **Filed as:** `approval.py questionnaire --from postcall-dev --feature post_call --op-key card:<call>:r<n>:p<k> --title "<call title>: N items" --summary … --questions-json @file`.
- **Two sections, in order (v4):** **(a) Yours**, Shaw's commitments and intentions, then **(b) Theirs**, each participant's promises, shown as `<name> owes you: <action>`. Within each section, items are ordered earliest due first.
- **Summary**, line 1 (FOUNDATION §2): `Decide by: <earliest due_ts − 2h, else call end + 24h, Shaw-local + label> · If no answer: nothing is created or dispatched · Cost of waiting: <earliest due item> slips`.
  - Line 2: `From: <recording title> · <Shaw-local start> · <duration> · With: <participants, verified ones marked ✓>`, plus `Page k of n` when paged.
  - Line 3: the Q1 rules in one sentence. **Questions store no per-question detail** (`questionnaire_schema.py:194-202` persists prompt, kind, menu and required), so the rules live in the summary and option `detail` fields.
  - Line 4, if there were due changes: `Already tracked, due date moved: <n>`.
- **Q1 is the bulk control,** a menu and required:
  - `1 Accept every (suggested) choice`
  - `2 Decide per item`
  - `3 Drop all`
  Each option's `detail` states its rule.
- **Q2..Qn, one per item,** each a menu and **optional**:
  - **The prompt itself carries the evidence**, since there's no question-level detail field: `[⚠ ]<action> · <counterpart> · Due: <display>\n"<quote, cut to 200 chars>" (<citation>)`.
  - **Section (a) Yours.** Option 1 is always the suggestion, labelled "(suggested)": `Assign to <seat>: <agent_work>` when the item is agent-takeable and a live seat resolves, otherwise `Take it myself`. Then `Take it myself` (if it isn't option 1) and `Drop`.
  - **Section (b) Theirs** (chase items):
    - `Track it: chase <name> on <chase_date>` (suggested), which becomes a human task `Chase <name>: <their promise>`.
      - **`chase_date` is a separate field from their `due_ts`:** their `due_ts` + 1 business day at 10:00 when known; otherwise the call date + 2 business days at 10:00, in `tz_at_call`.
      - `due_source` is `chase: their due <x> + 1bd` or `chase default: call + 2bd`.
      - The option label shows the exact date, so the task's evidence `due_ts` = the `chase_date` Shaw approved. Their own `due_ts` stays null when unknown.
    - `Assign to <seat>: draft a follow-up nudge for me to send` (only when a seat resolves; a draft only, never sent as Shaw);
    - `Drop`.
  - **Uncertain-match items** (§5) also get `Same as "<existing>" (<date>): update it`.
  - The full option list and the suggested option are **frozen in the ledger when the card is filed** (`card_items(card_id, q_n, cmt, options_json, suggested_n)`), and answers are interpreted only against that frozen copy.
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
- **No auto-expiry; retire at 14 days (gm ruling).** Questionnaires never auto-expire (`questionnaire_schema.py:8-9`). A card still pending **14 days after its Decide-by** is retired by its author (`questionnaire_schema.py:400`) with reason `no answer`. Its items stay in the ledger as `unreviewed`, and chase items are kept for context. Nothing is created from them.
- **No new surface:** this is the existing questionnaire lane and the H1 commitment human-task render. Chase tasks use the same render with the task text `Chase <name>: …` and `--blocks <name>`; whether H1 wants a distinct chip for them is a question for gm and avatar-dev (§9).

## 5. Dedupe across calls and reschedules (v4)

**Identities, all persisted and none recomputed from LLM text:**
- **Occurrence** `occ_<random>`: one per extracted item, stored with `(call_id, owner, deliverable, action_type, operation, quote, span_start, span_end)`.
  - The span is the character offsets of the grounded quote in the transcript.
  - **Two commitments inside one quote** are two occurrences, distinguished by `deliverable`, `action_type` and `operation`.
- **Re-extraction of the same call (v4.1)** reconciles before minting anything:
  1. Every existing occurrence's stored quote **text** is relocated in the new transcript by exact normalized search, which gives its new span. Raw offsets are never compared across revisions, so a prefix inserted into the transcript can't break the match.
  2. The re-extraction prompt receives the existing occurrences (id, quote, owner, action) and must return `same_as_occ` for every item that restates one. Code accepts `same_as_occ` only when **all** of these hold: owner, `action_type`, `operation` and normalized `deliverable` are equal to the old occurrence's; the new quote overlaps the relocated old span (or the quote text is equal); and the mapping is **one-to-one**, meaning no other new item in this extraction claims the same old occurrence. If two new items claim one old occurrence, or a claim fails any field check, **every** item involved goes to the uncertain path in step 3. Nothing is merged silently.
  3. A new item that has no accepted `same_as_occ` but shares its owner and overlaps a relocated span is **uncertain**. It gets the `Same as "<existing>"` option and ⚠, and it's excluded from accept-all, so Shaw sees it instead of a silent duplicate.
  4. Anything else is a new occurrence.
  Two deliverables in one sentence stay two occurrences: their `deliverable` (or `operation`) values differ, so at most one can pass the field check for a given old occurrence, and a double claim is routed to review. The call id is stable for both sources (`recording_id`, inbox dir name).
- **Commitment** `cmt_<random>`: the thing Shaw tracks, which is also the human-task op_key. Each occurrence points at exactly one commitment.

**Linking an occurrence in a later call to an existing commitment:**
- **Candidates:** commitments for the call's resolved participants that are open, or done or dropped within 14 days. They're passed to the prompt, and the LLM may return `matches_existing`.
- **Auto-link** requires ALL of these:
  - **both** occurrences have *verified* attribution (§2);
  - same `owner`;
  - same resolved counterpart id;
  - same `action_type` (the miner's enum: email, message, calendar_invite, document_send, review, follow_up) **and** same `operation`. That's a new cmt-v2 field: `draft | send | do | review | schedule | decide`. The enum alone can't tell "draft the email" from "send the email" (`commitment_miner.py:34-36`); `operation` can;
  - same normalized `deliverable`;
  - neither item is `recurring`.
- **Anything else the LLM calls a match is *uncertain*.** The item goes on the new card with an extra option, `Same as "<existing>" (<date>): update it`, and ⚠, and it's excluded from accept-all. **No merge effect (patch, seat notice, suppression) happens before Shaw picks that option.** Inferred-attribution items therefore never merge on their own.

**Applying a decision to a commitment.** This is the authorization record for the item, and the only place effects are decided:
- `commitments(cmt, decision, decision_src, gen)`. `decision` is null, `take`, `assign:<seat>` or `drop`.
- **First applied answer wins:** `UPDATE commitments SET decision=?, decision_src=<card:q>, gen=gen+1 WHERE cmt=? AND decision IS NULL`, done in the same transaction that writes the resulting effect rows.
- A later card's answer for an **already-decided** commitment can only change its *due date*. It never creates a second task or dispatch, and the ledger records `answer ignored: already <decision> via <src>`.
- An **old, unanswered question** for a commitment that a newer card has since decided is a no-op when the old card is submitted. Applying the newer card doesn't retire the old one.
- **Neither order loses an approval or duplicates work.** Dispatch and task effects are keyed per commitment (`task:<cmt>`, `dispatch:<cmt>`), not per card question, so they can happen at most once.

**Due-date changes** (an auto-link, or Shaw choosing "update it"):
- Every change becomes an immutable row `due_changes(cmt, seq, due_ts, due_source, from_occ)`, with `seq` increasing.
- **The task's date is derived, not copied.** For a Shaw item, task `due_ts` = the commitment `due_ts`. For a **chase** item (owner = participant), task `due_ts` = `chase_date(their due_ts)` (§4 rule), recomputed from each new `seq`. The `patch` payload always carries the *derived* task date, and its `due_source` says `chase: their due <x> + 1bd`.
- Its effects are `patch:<cmt>:s<seq>` and `duenote:<cmt>:s<seq>`. Payloads are stored when the effect is planned and never rebuilt.
- **Ordering:** before running any `patch`/`duenote` effect, every older planned effect for that commitment is marked `obsolete`, so a stale retry can't overwrite a newer date.
- **Patch reconciliation:** a read-only SQL read of the row's `evidence` JSON. If it equals the payload, `confirmed`; otherwise re-apply, but only if this is still the latest `seq`.

| commitment state | effect of a newer due (latest `seq` only) |
|---|---|
| no decision yet, question still unanswered | the new card shows it visibly as `Due changed: <old> → <new>` (questions are immutable, `questionnaire_schema.py:173`); whichever card's answer is applied first decides (above), and the latest `seq` due date is used |
| decided `take`, human task pending | `patch` the evidence and the `Due:` line. **Gated** behind flag `POSTCALL_RESCHEDULE_PATCH` (off) until the guarded-UPDATE fix lands; while off, record `due_pending_patch` and report it in the results notice |
| decided `assign`, awaiting results | `duenote` msg to the seat with the dispatch id + `seq`; the seat applies it only if `seq` is newer than any it has seen |
| done or dropped < 14 days ago | not re-asked; the summary says `already done/dropped: <n>` |

- **Patch race.** `update_fields` checks status, then runs an unguarded UPDATE (`approval_schema.py:648-663`). **Live patching is gated on the owner's fix** (a guarded `UPDATE … WHERE status='pending'` plus a rowcount check, ticketed to orchestraos-builder), and this is a recorded rollout prerequisite (§7 step 6).
- **All duplicates:** when every item auto-links and needs no visible question, the only effects are due changes and citations. Status becomes `no_card_dupes` and no card is filed.

## 6. Dispatch and results

- **Applying an answer,** only after §4's status and final-answer checks:
  - **Take it myself / Track it:** (evidence `due_ts` = the commitment's due for Shaw items, or the approved `chase_date` for chase items, §5) effect `task:<cmt>` runs `approval.py human-task --from postcall-dev --task <short> --op-key <cmt> --feature commitment --blocks <counterpart> --summary <miner 4-line format + citation> --evidence {due_ts,due_source}`. One human task per commitment.
  - **Assign to agent:** effect `dispatch:<cmt>`.
  - Every effect is written in the same transaction as the first-applied-wins decision (§5).
  - **Drop:** ledger `dropped`.
- **Authorization check before every dispatch,** in code, which refuses on any mismatch:
  1. The questionnaire row is `submitted`/`resumed` and the answer for `q_n` is `final`, or Q1=1 applied to an unanswered item.
  2. The chosen option index maps, in the **frozen** `card_items.options_json`, to `Assign to <seat>`. The seat and `agent_work` scope come from that frozen row, never from a fresh resolve.
  3. The `dispatch:<cmt>` intent row exists, was created **by the decision transaction** for this card and question (its payload's `decision_src` equals the commitment's), and is `planned` or `sent` (being reconciled). An intent that's already `confirmed` means it was delivered, so skip it. No intent at all, or a `decision_src` mismatch, means refuse and alert gm. Because the decision is set once, at most one such intent can ever exist.
  4. The seat is live in the registry right now. If it isn't, there's no silent reroute: we send the item to gm with the card id so gm picks the seat, and the ledger records `rerouted`.
- **Message:**
  - `msg_store.py send --from postcall-dev --to <seat> --type task --priority high --subject "[postcall dispatch:<cmt>] <action>" --body-file <f>`.
  - The body carries the frozen scope, quote, citation and due date, plus "Approved by Shaw on card <card_id> Q<n> (check: `approval.py get <card_id>`). Later due changes arrive as `duenote:<cmt>:s<seq>`; apply only a higher seq. Treat dispatch id as idempotency key: if you already have it, reply with the existing result. Reply to: postcall-dev."
  - **There's no `--card` flag on msg_store** (`msg_store.py:1992-2025`). Adding one would be fleet infrastructure, so it isn't proposed; if gm wants it, it's an orchestraos-builder ticket.
- **Seat resolution,** done before filing so the option names the seat:
  1. counterpart (a participant for theirs, the named person for Shaw's) → `client.json` `pm_agent`;
  2. else project → `protocol.py determine_pm()`;
  3. it must be live in `registry.json`, where the PM seats in `gm.md:283-293` are absent today;
  4. else gm. The option reads `Assign to gm to route: <work> (you send it)` and the dispatch asks gm to pick the seat; sending stays Shaw's (dry run 11-26, gm msg_feff515d). Step 2 (project → `determine_pm()`) is not built yet: the extractor's `project` field came back empty on all three real calls.
- **Results:** replies to `postcall-dev` (the sweeper takes only messages whose subject carries a `[postcall dispatch:<cmt>]` / `duenote` tag) are matched to their item by the dispatch id in the subject. When every dispatched item on a card has a result, or 48 h pass (report partial), effect `results:<card_id>` sends **one** message to gm: `Post-call results ready: <title>: <k>/<m> done, <unreviewed u>, <links>`. gm relays one line to Shaw; postcall never texts him.

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
  - **pocket:**
    - a 2-party diarized call with `_[MM:SS]_` markers (2 of Shaw's items, 2 of theirs);
    - a 3-party call;
    - a non-diarized call;
    - a 90 s fragment;
    - a non-call category;
    - a re-ingest whose changed hash shifts quote boundaries;
    - a sentence holding two deliverables;
    - a reschedule pair across two calls (call A "send the deck Friday", call B "push the deck to Tuesday");
  - **segments:** named speakers, one of whom matches a calendar attendee;
  - **`llm/*.json`:** canned outputs, including a fabricated quote, a fuzzy-only quote, owner/speaker mismatch, a label mapping with no quoted evidence, malformed JSON and a timeout;
  - **`calendar.ics`:** an event overlapping the call (2 attendees), a decoy overlap, a recurring series with an EXDATE and an override, a cancelled event, a declined attendee, and an event across a DST change.
- **Tests,** each written to fail first:
  - Empty call, extractor failure (no `no_card`, retried) and genuine empty are each distinguished, and empty never yields a card.
  - Fuzzy-only quotes are dropped; an owner/speaker mismatch is dropped; theirs and yours land in the right sections.
  - Participants: the overlapping event gives a verified set; the two-party shortcut works; an unevidenced label mapping stays unidentified.
  - Every item has a citation.
  - Lease takeover after a crash.
  - Effect recovery: kill between `planned` and `sent`, and between `sent` and `confirmed` → exactly one card, task and message.
  - Re-ingest with shifted quote boundaries → the same occurrence, no new question.
  - Two deliverables in one quote → two occurrences.
  - Deadline precedence; `tz_at_call` preserved; "Friday at 2pm" → 14:00; ambiguous → null; unknown is shown as unknown.
  - Summary line 1 is `Decide by:`.
  - Q1 rules: accept-all, per-item with blanks, explicit-beats-bulk, drop-all.
  - Answers ignored while the questionnaire is still a draft.
  - Paging at 20 items.
  - Dispatch refused on a non-final answer, a changed option, a repeat, or a dead seat (reroute to gm).
  - Recurring never merges; inferred attribution never auto-links; drafting ≠ sending (`action_type`) doesn't auto-link.
  - Reschedule patches both evidence and the Due line; patch exit 4 handled; all-dupes → no card.
  - Ordering: old card answered first then new, new first then old, both unanswered → exactly one task or dispatch per commitment, latest `seq` due date.
  - A stale `patch` retry is marked obsolete and can't overwrite a newer date; patch reconciliation.
  - Calendar: RRULE/EXDATE/override, cancelled, declined, DST fold and gap, no name-only match.
  - One results notice, and a partial at 48 h.
  - Shadow mode makes zero calls to approval.py and msg_store.
  - Live activation ignores calls from the shadow window, and the two ledgers can't cross.
  - Concurrency: a second sweeper blocks on the inherited lock while a child is still alive.
  - An uncertain match is excluded from accept-all; a `Due changed` row is visible and wins over the old card.
- **Proof:** each fix gets a sabotage run (revert it, test goes red).
- **Rollout gate.** These are recorded prerequisites, checked off in `state/postcall/ROLLOUT.md` by gm:
  1. gm approves the cron line (fleet infra), at rollout, against the proven ledger.
  2. Shadow runs at least 24 h on live calls.
  3. gm reviews `shadow.jsonl` and the would-be cards.
  4. **Shaw's validation is recorded.** The miner's kill-switch says Shaw validates its first live batch. Once the shadow ledger holds 3+ real calls, postcall-dev drafts the validation card **to gm**, gm files it, and Shaw's answer is recorded. **A waiver needs Shaw's own word**; gm can't waive on its own authority.
  5. gm writes `MODE=live`, which creates the live ledger and sets `live_start_ts`.
  6. *(reschedule patching only)* orchestraos-builder's guarded `update_fields` has landed; gm queued it as LOW. Until then `POSTCALL_RESCHEDULE_PATCH` stays off and that one path is reported, not applied (§5).
  7. Service identity: `--from` moves from `postcall-dev` to a runtime=service identity (spawn_adopt), which changes `<AUTHOR>` in recovery.
  8. *(phase-2 review round 3)* Every ledger starts empty at this schema. The occurrence key gained the owner, and commitments gained `quote` and calls `card_hash`; a ledger written before that would re-card every item of a re-ingested call with no ⚠. No shadow or live ledger exists yet, so this holds by construction. If one is ever created before this lands, it is deleted, not migrated.
- **Sweep environment (PR #655).** The cron line sets `POSTCALL_SHAW_NAMES` (Shaw's name as recorders label him) and, once Shaw supplies it, the 0600 calendar URL file. Neither value is ever written in the repo. It must list EVERY form the recorder uses for Shaw (today "Shaw Cole", "Shaw", "Cole"): a form it misses is just another name. Without `POSTCALL_SHAW_NAMES` the named-label proof is off, quoted evidence is required as before, and Pocket-named speakers are never verified.
- **Known spec gap (review round 3).** If a re-transcription changes an old quote's words so it can't be relocated, and the model makes no `same_as_occ` claim, §5 step 4 treats the item as new, with no ⚠. Accepted for now: a re-ingest needs a changed transcript and the extractor is shown the old occurrences. Revisit if the shadow ledger shows a duplicate.
  **No generated post-call card reaches Shaw before step 5.** The only Shaw-facing card before then is the deliberate step-4 validation card that gm files.

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
| overlay `<overlay>/scripts/postcall/` | synced copy at rollout, via the normal overlay path | postcall-dev, after gm's go |

## 9. Rulings (gm msg_92221c61, 2026-11-25)

1. **Author:** `--from postcall-dev` now; a runtime=service identity (spawn_adopt) is a rollout item.
2. **Calendar:** gm asks Shaw for the ICS URL (§3). Phase 2 isn't blocked on it.
3. **Short-call floor:** 120 s. Confirmed.
4. **Promises between two other people:** ledger context rows only, never on the card.
5. **No backfill:** the first run starts at activation time.
6. **Stale cards:** retired 14 days after Decide-by, reason "no answer", chase items kept in the ledger.
7. **Chase-task render:** phase 3 with avatar-dev. postcall-dev drafts the spec, and doesn't mail avatar-dev yet.
8. **Pasted free-text meetings:** out of scope.
9. **Rollout:** the cron OK comes at rollout; the update_fields guard is queued LOW to orchestraos-builder, with the reschedule path behind a flag; the validation card is drafted to gm at 3+ shadow calls.
10. **Pocket speaker identity:** no change. Unverified items stay ⚠ until a calendar feed exists.
11. **Congruence:** round 6 is a second Claude reviewer (codex has no credits). APPROVE there = CONSENSUS_REACHED for phase 2.

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
- **Codex round 3 (v3 note): `REJECT`.** It raised 4 blockers and 3 should-fixes; v4 addresses them:
  1. Unstable source identity → §5 persisted occurrences (span + deliverable + action_type), with two deliverables in one quote kept apart.
  2. Auto-merge bypassing attribution → auto-link only when both sides are verified, `action_type` is equal and the deliverable is equal; anything else needs Shaw's pick before any effect.
  3. Reschedule ordering → per-commitment first-applied-wins decision, with effects keyed per commitment and written in the same transaction.
  4. Patch recovery and ordering → immutable `due_changes` seq, an `obsolete` state, and reconciliation by reading `evidence`.
  5. No question-level detail → the evidence goes in the prompt and the rules in the summary and option details.
  6. Calendar and zone → email-only matching, RRULE/EXDATE/cancelled/declined handling, DST rules, a visible "zone assumed".
  7. Shaw validation → it needs his own word on a card; gm can't waive it.
- **Scope correction (msg_ecea3d6c), applied in v4:** Arturo calls dropped; Pocket and segments sources; a (b) Theirs section with chase items; §2a participants from calendar overlap and speakers.
- **Codex round 4 (v4 note): `REJECT`.** It confirmed round-3 blockers 3 and 4 resolved, then raised 3 new blockers and 3 should-fixes, all fixed in v4.1:
  1. Re-extraction identity, and a segments id that was a content hash → a stable dir-name id; quote text relocated instead of comparing offsets; `same_as_occ`; uncertain matches shown to Shaw.
  2. Drafting vs. sending → a new `operation` field required for auto-link.
  3. Dispatch check vs. the intent row → the check now requires the decision-created intent and skips one that's `confirmed`.
  4. Quote-in-segment rule.
  5. Invited vs. spoke.
  6. An explicit `chase_date`, separate from their due date.
- **Codex round 5 (v4.1 note): `REJECT`.** It confirmed round-4 blockers 2 and 3 and the segments id as resolved, then raised one blocker: `same_as_occ` lacked the field and one-to-one checks. Fixed in v4.2:
  - full field equality plus a one-to-one mapping, with double claims sent to review;
  - (should) the chase task's date is derived from each due change;
  - (nit) rollout wording.
- **Codex round 6 (v4.2 note): NOT RUN.** `codex exec` exited with "Your workspace is out of credits" (17:45Z). agy is not used, per the role.
- **Round 6 = a second Claude reviewer (gm ruling msg_92221c61): `APPROVE`.** It ran as a fenced read-only `claude -p` (Read/Grep/Glob only, no MCP), briefed only with the round-5 blocker and the v4.1→v4.2 diff. Verdict in `docs/design/postcall-claude-round6.md`. It noted one non-blocking gap, that `operation` was missing from the stored occurrence tuple, and that's fixed in v4.3.
- **Congruence DEC-1795627933880541:** a second-reviewer APPROVE (round 6) on the blocker that codex's last REJECT rested on; postcall-dev APPROVE. **Per gm's ruling this = CONSENSUS_REACHED for phase 2.**
