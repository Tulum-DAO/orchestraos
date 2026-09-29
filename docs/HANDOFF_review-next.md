# Handoff: review -> gm (ship gate)

## ⏳ READ FIRST — the only thing outstanding: FIVE restart-gated checks

As of 2026-09-29T15:15Z **no restart has happened** (api still pid 51440 @ 12:58:47, router still
pid 3445 @ 11:22:59). Five changes are merged, built, and **not running**. Each has a positive proof
that only the new code can produce — run all five in one pass once the supervisor cycles:

| # | Change | Proof it is actually live |
|---|---|---|
| 1 | access log `fc55a89` | `grep -c '\[access ' ~/.orchestra/logs/api.log` > 0 after any identity-header request (**currently 0**) |
| 2 | router F6 `cfc420c` | a caption + unsupported-media message names the media in the body |
| 3 | TMUX-strip `df92625` | a supervised daemon's env has no `TMUX`/`TMUX_PANE` |
| 4 | session_index beat `d4725b9` | `~/.orchestra/state/agent-sessions.json` mtime advances within 120 s |
| 5 | agents-live-status `3383805` | `curl -s localhost:8888/api/agents` → a working seat shows `status_source=transcript` (**currently `detector`/`idle` for a provably-working seat**) |

4 and 5 are distinct: 5 needs the API restart to appear at all; 4 keeps 5 accurate as seats rotate.

**Also open, not mine to drive:** Cloudflare dashboard confirm on tunnel
`43f26a47-9bc1-43c3-80ab-6222a44daac4` (the only control holding that vector shut — a loopback bind
does **not** close it, cloudflared dials its origin over loopback); rotate the tunnel token (readable
from root's argv via `ps`); 3 files still failing to import under python3.9 with the same PEP 604 bug
(`approval_adapters.py`, `lineage_daemon/boundary_delivery.py`, `spawn_permission_rules.py` — two on
the **approval** and **spawn** paths); ntfy push is down (config gap, deprecated, cards still reach
Telegram with working buttons — do not revive it, see the diagnosis); `prefers-reduced-motion` nit on
the new working badge.

**PR #133** (`main <- fix-arturo-mapfile-bash32`, Tulum-DAO) carries everything and is **pushed** —
the branch is `0 0` against its remote, so nothing lives only on this laptop any more. Waiting on a
merge click; gm lacks merge rights.

**Today's records:** `$ORCHESTRA_DIR/state/review/router-offset-b834241/` (router P0 + F1/F5/F6, the
API security incident, the cso baseline) and `$ORCHESTRA_DIR/state/review/ship-2026-09-29/` (the ship
report, session-index and CI-coverage gates). Eleven gates and one ship today; every verdict is in
those two directories.

---

- **Lineage:** build -> review (Gen 2)
- **Timestamp:** 2026-09-29T10:45:00Z
- **Working Directory:** /Users/flybyflow/orchestraos
- **Reviewed:** branch `build/router-offset-commit-then-confirm` @ **`b834241`** (the P0 fix commit only — the branch carries 25 commits; the other 24 were not in scope for this gate)
- **Findings file:** `$ORCHESTRA_DIR/state/review/router-offset-b834241/findings.md`
- **Task:** gm `msg_52d5354a_78316399` — ship gate before merge + restart of live router pid 10767

## 1. Verdict

**CLEARED to merge and restart — CONDITIONAL on the §1a deploy gate below passing.**

The code is cleared. The *deploy* is not cleared until §1a is walked, because the working tree
that actually feeds the live process does not currently contain the fix. A sign-off that stops at
"the merge looks good" would be a green light over a silently-still-broken deploy — the exact
failure class this commit exists to kill.

## 1a. DEPLOY GATE — mandatory, walk it in order, immediately before and after the restart

Folded in at gm's request (`msg_1567b7d7_78676841`), and independently confirmed by me before
writing this. **Current state, re-verified 2026-09-29T10:47Z:**

```
branch:                             fix-arturo-mapfile-bash32
git merge-base --is-ancestor b834241 HEAD   ->  NO      # tree does NOT contain the fix
grep -c MAX_UPDATE_ATTEMPTS plugins/telegram/router.py  ->  0   # on-disk file is the OLD code
live pid 10767 started              Tue Sep 29 08:57:58 2026   # predates b834241 (10:35)
<data>/state/telegram/              chat-id, notified.json, offset   # no last-done
```

The shared checkout was switched to another branch mid-session, so `b834241` is safe in git but
absent from disk. Restarting right now would relaunch the **old, buggy** router.

1. **Merge** `b834241` into the branch the working tree actually has checked out (or switch the
   tree to a branch containing it). Merging to `main` alone does nothing for pid 10767.
2. **Confirm the checkout contains the fix:**
   `git merge-base --is-ancestor b834241 HEAD && echo CONTAINS-FIX` → must print `CONTAINS-FIX`.
3. **Confirm on disk, immediately before the restart** (this is the step that catches a
   mid-session branch switch, which git-level checks alone will not):
   `grep -c MAX_UPDATE_ATTEMPTS plugins/telegram/router.py` → must be **≥ 1**. It is `0` right now.
4. **Confirm the running process actually picked it up, after the restart:**
   - new pid ≠ `10767`, and `ps -p <newpid> -o lstart=` postdates the merge;
   - then have the operator send **one** message and check that
     **`<data>/state/telegram/last-done` now exists** — that file does not exist today and the old
     code can never create it, so its appearance is positive proof the new code is the one running.
     `offset` alone proves nothing; both versions write it.

Do not report the deploy as done on steps 1–2 alone. Steps 3 and 4 are the ones that fail loudly
when the trap has been stepped in.

### Gate status — last re-checked 2026-09-29T10:51Z

| Step | Status | Evidence |
|---|---|---|
| 1. merge | **PASS** | `7109aaa` "Merge branch 'build/router-offset-commit-then-confirm'" on `fix-arturo-mapfile-bash32` (gm merged it) |
| 2. `--is-ancestor` | **PASS** | prints `CONTAINS-FIX` |
| 3. grep on disk | **PASS** | returns `5` (was `0` pre-merge) — **but re-run it at the literal last second before the restart; this tree is shared and unstable** |
| 4. process picked it up | **PASS — closed 2026-09-29T11:56:50Z** | pid 10767 gone; router is now **pid 3445, started 11:22:59**. `last-done` appeared at 11:56:50 — a file only the fixed code can create. Detail below. |

### Step 4 closed — P0 verified in production, not just in the harness

Two operator messages landed at 11:56, and all three P0 properties are observable on real traffic:

```
last-done = 589340061          (mtime 11:56:50)
offset    = 589340062          (mtime 11:56:50)
msg_store rows:  tg-589340061  11:56:50   "And make sure you follow the ontology of…"
                 tg-589340060  11:56:18   "Thank you for sending back the latest up…"
```

1. **`last-done` exists at all** → the new code path executed. The old code cannot create this file.
2. **`offset == last_done + 1`** → commit-then-confirm's invariant holds live.
3. **One row per `update_id`, keyed `tg-<update_id>`** → the idempotency key working on real traffic;
   two updates in, two rows out, no gap and no duplicate.
4. **No `escalate` row and no `dropped` metadata from `telegram`** → `_alert_stepped_over` never
   fired; nothing was stepped over.

Minor correction to gm's read of the same evidence: `last-done 589340061` pairs with **`tg-589340061`**,
not `tg-589340060` (off by one). The substance is unaffected and the actual evidence is *stronger* than
a single message — two consecutive updates were both delivered with matching ids.

**Still not deployed: F6.** pid 3445 started 11:22:59; F6's merge (`6816ad0`) landed 11:26:36, so the
running process is **P0 + F1 + F5 only**, by design ("rides the next natural restart"). The on-disk
file *does* contain F6, so a `grep` of `router.py` today would wrongly suggest F6 is live — check pid
3445's start time against 11:26:36 instead. Disk is currently **ahead of** the process, the inverse of
the trap in §1a.

**The verdict transfers to what will actually run:**
`git diff b834241 HEAD -- plugins/telegram/router.py plugins/telegram/test_router_offset.py` is
**empty** — the merge altered the reviewed code by zero bytes — and the post-merge tree passes
**27/27**. No re-review is needed after the merge.

Blocked on: the operator's go-ahead for the brief channel interruption (gm asked, not yet
answered). gm will ping review for a second pair of eyes on step 4 after restarting.

One required follow-up (F1), two notes (F2/F3), one accepted-as-designed (F4) — all recorded in
the findings file, none of them worth leaving the live process on the buggy code for. Today every
operator *text* message on the fleet's only command channel is destroyed silently by any
transient failure; this commit fixes that, and I verified the fix works rather than taking
build's word for it.

## 1b. Second gate — F1 fix `a062d57` (`build/telegram-attachment-placeholder`): **CLEARED**

Task: gm `msg_54e5f57c_79450344`. Findings:
`$ORCHESTRA_DIR/state/review/router-offset-b834241/findings-f1-a062d57.md`.
Merge target `fix-arturo-mapfile-bash32`; no restart decision needed — it rides along on the next
router restart. Based on `7109aaa` (`--is-ancestor` YES), so it applies on top of the merged P0.

I proposed this fix in the P0 review, so I tried to break it rather than confirm it:

- **F1 is genuinely closed** — re-ran **my own original reproduction** (the harness that found the
  bug), not build's tests: photo-only message, no caption, `fetch` raises. Was `delivered: 0`;
  now **`delivered: 1`** with body `Attachments:\n  photo: [download failed — ask the operator to
  resend]`. The reproduction no longer reproduces.
- **`plugins/` suite: 30 passed** (27 existing + 3 new) — matches build.
- **Mutation claim reproduced exactly** — stubbing out the failure-recording branch turns **2 of
  the 3** new tests red; the third is the happy-path guard, which correctly stays green because the
  mutation doesn't touch it. Restored → 30 pass.
- **F4 undisturbed** — `router.py:305` `download_file(obj["file_id"], ...)` is unchanged, so
  `photo: [{}]` still raises `KeyError` *before* the download and is still TRANSIENT → step-over.
- Compiles under the live Python 3.12.13. `msg_id` derivation untouched → the P0's exactly-once
  guarantee is unaffected. The only newly-delivered messages are ones previously discarded silently.
- **Agreed with build's call not to raise on download failure** — raising would recover a transient
  blip but head-of-line block the channel for the full attempt budget on a permanently unfetchable
  file (>20 MB Bot API limit). Build named the trade-off and corrected the P0 commit's overclaim
  rather than quietly widening scope.

**F5 — NEW, out of scope, gm to scope separately.** Same symptom as the P0, different cause:
the attachment loop enumerates only `photo, document, voice, video, audio`, so a message whose only
content is an unenumerated kind never attempts a download, has no failure to record, and hits
`if not text: return`. Verified — `sticker`, `animation` (GIF) and `video_note` (round video) all
give `delivered=0 offset=701 warned=0`, i.e. silent drop. Pre-existing in both commits, not a
regression. Cheap fix in the same spirit: when a message yields no text *and* no attachments at all,
deliver an "(unsupported message type — resend as text)" placeholder instead of returning silently.
That closes the last silent-drop path I can find in `handle_message`. Not proposing enumerating
every Bot API media kind — that list grows; the catch-all covers it permanently.

F2 and F3 remain open and non-blocking, unchanged by this commit.

## 1c. Third gate — F5 fix `649cb44` (`build/telegram-unsupported-placeholder`): **CLEARED**

Task: gm `msg_6df0cc87_80058153`. Findings:
`$ORCHESTRA_DIR/state/review/router-offset-b834241/findings-f5-649cb44.md`.
Stacked on F1's `a062d57` (`--is-ancestor` YES); merge target `fix-arturo-mapfile-bash32`.

- **F5 is genuinely closed** — re-ran **my own F5 reproduction**: `sticker`, `animation`,
  `video_note` all now `delivered=1` with `(unsupported message type: <kind> — resend as text)` and
  `meta["unsupported"]` set. Was `delivered=0`, no signal.
- **`plugins/` suite: 38 passed** — matches build. Compiles under the live Python 3.12.13.
- **Mutation claim reproduced exactly** — restoring the bare `return` turns **6 of 8** red (the 5
  parametrised kinds + the naming test). The 2 staying green are the guards, correctly unaffected.
- **F1/F5 don't double-report** — a failed photo download delivers the F1 placeholder only, with
  `attachment_failures` and no `unsupported`. Ordinary text passes through verbatim.
- The reflection approach (`set(msg) - _ENVELOPE_KEYS`) is right and keeps the anti-rot property: an
  unknown future Bot API kind gets *reported*, never dropped, so a stale key set degrades the
  wording and nothing else.

**On gm's flagged service-message concern — agreed, and the exposure is smaller than the flag
implies.** (1) This is a 1:1 private DM, so the group-only service messages
(`new_chat_members`, `left_chat_member`, `group_chat_created`, `new_chat_title`, `video_chat_*`,
`forum_topic_*`, …) **cannot occur at all**; what stays reachable is essentially `pinned_message`
and `message_auto_delete_timer_changed`, both operator-initiated and rare (verified: a
`pinned_message` yields exactly one row). (2) `router.py:469` passes
`allowed_updates=["message","callback_query"]`, so `edited_message`/`my_chat_member`/`chat_member`
never arrive. **And the dangerous version of "new traffic" is absent — no feedback loop:** the bot's
own outgoing sends, including `_alert_stepped_over` warnings and pushed cards, are not echoed back
through `getUpdates`, so a placeholder cannot beget placeholders. Right trade, and not
pre-emptively suppressing is also right — guessing which service messages matter recreates the
rotting enumeration this commit removed.

**F6 — NEW, out of scope, gm to scope.** Same structural pattern that produced F1 and F5: *the guard
only fires when `text` is empty.* Add a caption to an unsupported kind and the media vanishes
unmentioned. Verified: `animation + caption` → delivers `look at this bug`; `video_note + caption` →
`urgent, see this`; neither carries `unsupported` or `attachment_failures`. **Not a regression** —
identical pre-F1/pre-F5 — and milder than F1/F5 since the operator's words do arrive. But it
misleads in a way a pure drop does not: gm reads "urgent, see this" with nothing to see. Cheap fix
(~3 lines): compute `kinds` **unconditionally** and *append* a named line the way F1 appends its
failure line, instead of only substituting for empty text. That collapses F1/F5/F6 into one rule —
anything the router could not render gets named in the body — and removes the seam that has now
produced three findings in a row.

F2 and F3 remain open and non-blocking, unchanged.

## 1d. Fourth gate — F6 consolidating fix `cfc420c` (`build/telegram-name-unrendered-always`): **CLEARED**

Task: gm `msg_a3be1126_80839422`. Findings:
`$ORCHESTRA_DIR/state/review/router-offset-b834241/findings-f6-cfc420c.md`.
Stack: P0 `b834241` → F1 `a062d57` → F5 `649cb44` (merged) → F6 `cfc420c`.

**Correction to my own F6 finding — build was right.** I framed F6 as "the same seam that produced
F1 and F5". F1 was never gated on `if not text:` — it used `if lines:` and appended, so caption +
failed download already named the photo pre-F6. Verified on the merged code:
`caption + FAILED photo` → `see this crash \n\n Attachments: \n photo: [download failed — …]`. So F6
was **specific to F5**, and "three findings in a row from one seam" overstated it. The finding's
substance was right; the attribution wasn't. (One nuance the other way: F5 didn't *introduce* the gap
either — pre-F5 that case also delivered the caption alone. F6 is an **incompleteness in F5's fix**.)

- **F6 is genuinely closed** — re-ran **my own two repro cases**: `animation + caption` and
  `video_note + caption` now append `  <kind>: [unsupported type — resend as text]` with
  `meta["unsupported"]` set. Both previously delivered the caption alone.
- **No double-labelling** — the risk this consolidation creates, and it's handled: `caption + FAILED
  photo` names the photo **once**, as a download failure only, never also "unsupported". `handled.add(key)`
  fires right after `if not obj: continue`, so an enumerated key counts as handled whether its
  download succeeded or failed, and `set(msg) - _ENVELOPE_KEYS - handled` excludes it. Right decomposition.
- **Mixed message** (photo + sticker + caption): each outcome named exactly once, no overlap in `meta`.
- **Guards hold** — plain text passes through undecorated; an envelope-only message still yields
  `(unsupported message type — resend as text)` with `meta["unsupported"]=['unknown']`.
- **`plugins/` suite: 45 passed**; mutation reproduces exactly (**5 red** when re-gated behind
  `if not text` — the 4 parametrised caption kinds + the mixed-message test); compiles under 3.12.13.

Nit, no action: the new comment inherits my overstatement ("three findings in a row"). If the file is
touched again, "F5's blind spot" is the accurate wording.

**With F6 merged I know of no remaining silent-drop path in `handle_message`.** F2 and F3 remain open
and non-blocking.

## 2. Verified independently (not from build's report)

- **Bug is real and still live:** `plugins/telegram/router.py:323` in the main checkout advances
  the offset outside the `try/except`. `ps -p 10767` → Python **3.12.13** running that exact file,
  which has no `MAX_UPDATE_ATTEMPTS`/`last_done` → still old code.
- **`plugins/` suite: 27 passed** at `b834241` in a clean worktree (Python 3.9.6) — matches build.
- **Mutation claim reproduced exactly:** reintroducing the unconditional advance turns **6 red,
  21 pass**; the 6 are the ones build named. Restored → 27 pass.
- **Exactly-once under the worst crash:** deleted **both** `offset` and `last-done` (router has no
  memory of the update at all), let Telegram redeliver from 0 → **one row, `tg-77`**. The
  store-level `tg-<update_id>` dedupe, not the offset file, is the real guarantee. It holds.
- **`msg_store` API the fix needs exists:** `send(..., msg_id=)` and `get(msg_id)`.
- **Under the live 3.12 interpreter:** compiles and imports, shape classifier behaves as
  documented. **pytest is not installed on 3.12** — so build's "compiled and smoke-tested under
  3.12" is precisely worded; nobody has run the suite on the live interpreter, me included.

## 3. Open Loops

- [ ] **F1 (required follow-up, NOT a merge blocker).** The "attachment fetch error" case the
  commit message lists as fixed is **still a silent permanent loss**. `download_file` swallows its
  own exception and returns `None`, so a photo-only message (no caption) whose fetch fails never
  raises → `if not text: return` → `poll_once` sees success → offset advances past it. Reproduced:
  `delivered 0, offset 91→92, attempts 0, operator warned: []`. Pre-existing and identical in the
  old code, so not a regression — but the commit message overstates coverage. Small fix: when an
  attachment object existed but `path` is `None`, append a placeholder line so the message still
  reaches gm (preferred), or raise so commit-then-confirm retries it.
- [ ] **F2 (note).** The 5-attempt budget buys **~22 ms measured** (~0.5–1.5 s in production), not
  a real outage window: `run()` has no backoff and `getUpdates` returns immediately while an update
  is pending. Weaker than it first looks — `msg_store` sets `busy_timeout=30000`, so the likeliest
  blip (sqlite lock contention) blocks in-call instead of raising and never touches the budget.
  Exposed class is fast-raising environment failures. `time.sleep(min(2 ** n, 30))` closes it.
- [ ] **F3 (note).** `_alert_stepped_over` writes gm's critical row through the same `msg_store`
  whose failure likely caused the step-over, so that signal goes missing exactly when it's needed
  (hit this in my harness; the code logs and continues, correctly). The operator warning rides
  Telegram, an independent path, and worked in every probe — loudness survives where it counts.
- [ ] **F4 (accepted, no action).** `photo: [{}]` → `KeyError('file_id')` is classified TRANSIENT
  and burns all 5 attempts before a loud step-over. Verified; safe outcome. Deliberately NOT
  widening `unprocessable_reason` — each shape check added there is a new chance to misclassify a
  transient failure as permanent, and that direction loses messages.

## 4. Decisions Made & Rationale

1. **CLEARED rather than NOT CLEARED despite F1.** F1 is a pre-existing sibling case, byte-identical
   in the old code, and holding the merge would keep the live router destroying the operator's text
   messages — the more common and more important path. Fixing F1 is a follow-up commit, not a gate.
2. **Answered gm's question on bounded-retry-then-escalate: the design is right.** Retry-forever on
   the only command channel is strictly worse — one poison update blocks every later approval. The
   seam (shape → permanent/never retried, effect → transient/never confirmed) is drawn correctly,
   and `unprocessable_reason` inspecting *only* shape is the load-bearing asymmetry that keeps a
   store outage from being misread as malformed. My one change is F2's backoff, which tunes the
   budget, not the design.
3. **Reviewed `b834241` alone, not the 25-commit branch.** That is what the gate was asked about.
   The other 24 commits have not been reviewed by this seat and this verdict says nothing about them.

## 5. Declared First Effect

Run **§1a step 3** — `grep -c MAX_UPDATE_ATTEMPTS plugins/telegram/router.py` — and do not touch
pid 10767 until it returns ≥ 1. It returns `0` as of this writing, so the very first action is the
merge in §1a step 1, not the restart.

## 6. Next 3 Immediate Actions

1. gm: get the operator's go-ahead for the brief channel interruption, then walk **§1a steps 1–2**
   (land `b834241` into the branch the main working tree has checked out, not just `main`).
2. Whoever restarts: **§1a steps 3–4** — grep on disk before `kill`, then new-pid check and confirm
   `last-done` appears in `<data>/state/telegram/` after the operator's first message.
3. build: F1 follow-up commit (attachment-fetch placeholder) + optionally F2's one-line backoff.

## 7. Grounding Canary Questions (Questions Only — No Answers!)

1. **Q1:** Which two state files did review delete to prove exactly-once survives a crash where the
   router retains no memory of the update, and what single row id came back (jsonl regarding the
   probe2 harness run against the real msg_store)?
2. **Q2:** How many tests went red under review's own mutation, and which branch of `poll_once` was
   mutated to produce that (jsonl regarding the independent mutation check)?
3. **Q3:** What measured wall-clock number did the 5-attempt budget survive for, and which
   `msg_store` PRAGMA is the reason that number is less alarming than it looks (jsonl regarding F2)?
4. **Q4:** Which function's internal `try/except` is the reason the commit's claimed
   "attachment fetch error" coverage does not actually exist (jsonl regarding F1's reproduction)?
5. **Q5:** Why does merging `b834241` to `main` alone fail to deploy the fix to pid 10767 (jsonl
   regarding the `ps -p 10767` read and the main checkout's current branch)?

---

## Superseded: prior verdict of record (Gen 1, 2026-09-20) — duelo-de-dibujo

Kept for lineage only; consumed by Test long ago. Branch `build/arabic-letter-tracing-vertical`
@ `86fca3e` in `/Users/flybyflow/duelo-de-dibujo`: **CLEARED**, no blocking findings, two
Test-stage open items (real-Claude `qa-judge.mjs` cases needing a Production-scoped
`ANTHROPIC_API_KEY`; real-kid playtest + RTL/TTS + watch video `fKwOMa3r1_c`). Full detail in
git history of this file at `231f1d0` and earlier.
