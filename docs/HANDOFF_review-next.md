# Handoff: review -> gm (ship gate)
- **Lineage:** build -> review (Gen 2)
- **Timestamp:** 2026-09-29T10:45:00Z
- **Working Directory:** /Users/flybyflow/orchestraos
- **Reviewed:** branch `build/router-offset-commit-then-confirm` @ **`b834241`** (the P0 fix commit only — the branch carries 25 commits; the other 24 were not in scope for this gate)
- **Findings file:** `$ORCHESTRA_DIR/state/review/router-offset-b834241/findings.md`
- **Task:** gm `msg_52d5354a_78316399` — ship gate before merge + restart of live router pid 10767

## 1. Verdict

**CLEARED to merge and restart.**

One required follow-up (F1), two notes (F2/F3), one accepted-as-designed (F4) — all recorded in
the findings file, none of them worth leaving the live process on the buggy code for. Today every
operator *text* message on the fleet's only command channel is destroyed silently by any
transient failure; this commit fixes that, and I verified the fix works rather than taking
build's word for it.

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

Before restarting pid 10767: the live process runs
`/Users/flybyflow/orchestraos/plugins/telegram/router.py` from the **main checkout, currently on
branch `fix-arturo-mapfile-bash32`** — a restart picks up whatever is on disk there. **Merging only
to `main` would restart the router on the old buggy code.** Land the fix in the branch that working
tree has checked out (or switch the tree) first, then verify
`grep -c MAX_UPDATE_ATTEMPTS plugins/telegram/router.py` is non-zero, then restart.

## 6. Next 3 Immediate Actions

1. gm: get the operator's go-ahead for the brief channel interruption, then land `b834241` into the
   checked-out branch of the main working tree (not just `main`).
2. Whoever restarts: confirm the on-disk file is the fixed one before `kill`, and confirm
   `last-done` appears in `<data>/state/telegram/` after the first delivered message.
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
