# Handoff: review -> gm (ship gate)
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
| 4. process picked it up | **PENDING** | pid 10767 still `STARTED Tue Sep 29 08:57:58` → old code still in memory; `<data>/state/telegram/` still has only `chat-id, notified.json, offset` (no `last-done`). Correct — awaiting the restart. |

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
