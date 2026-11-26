# Phase-2 code review, round 1: Claude reviewer (codex out of credits, gm msg_62f55733), 2026-11-26T00:13:04Z
Fenced read-only claude -p (Read/Grep/Glob only); brief = 4 blocker classes + shoulds.

VERDICT: REJECT

There are two blockers, both in the re-extraction and identity paths. I found no class-1 or class-2 defect: the floor runs before extraction, `card.build` refuses an empty list, MODE falls back to shadow, and the test fence covers child processes too.

**[blocker] Re-ingesting a call adds to the old card instead of filing a new one** (classes 3 and 4)
- **Where:** `pipeline.py:69` always uses `card:<call>:r0:p<n>`. Nothing increments `revision`. `ledger.py:94` then does `INSERT OR IGNORE` into `card_items`, keyed on `(card_id, q_n)`.
- **Scenario:** a call is carded with 3 items (Q2–Q4). Pocket re-ingests it with a new `transcript_hash`, and re-extraction finds 5 items.
  - The card effect already exists, so no new questionnaire is filed.
  - The rows for Q2–Q4 are silently kept. New rows for Q5 and Q6 are frozen onto the old card, which Shaw never saw with them.
  - If the old card was already acked, those items are lost.
  - If it is still pending and the calendar upgrades the items to verified (`participants.py:205-220`), Shaw picking Q1=1 makes `apply_answers` (`ledger.py:202-208`) create a task or dispatch for Q5/Q6 without any answer from him.
- **Test gap:** no re-ingest test exists.
- **Fix:** when the hash changes, increment `revision` and file `card:<call>:r<rev>` holding only items with no existing commitment row. Refuse to add `card_items` to a card that already exists.

**[blocker] Two commitments in one quote collapse into one** (class 3)
- **Where:** `pipeline.py:81-83` builds the key from call id + normalized quote only. `ledger.py:98` then does `INSERT OR IGNORE` on the commitments table.
- **Scenario:** "I'll send you the deck and the contract Friday" gives two items with the same key. The card shows two questions that point at one commitment. Shaw answers Take on Q2 and Assign on Q3, and first-applied-wins discards Q3. `meta[ci.key]` (`dedupe.py:49`) is also overwritten. This breaks the §5 rule that these are two occurrences.
- **Test gap:** no two-deliverables test exists.
- **Fix:** add the normalized `deliverable`, `action_type` and `operation` to the key hash.

**[should]**
- **One failing effect stops the whole pipeline:** `ledger.py:280-290` and `sweep.py:157-162`. `run_pending` aborts on the first exception, and the sweep returns `recovery_failed` before it applies any answers or files any cards.
  - Example: a patch planned while `POSTCALL_RESCHEDULE_PATCH` was on, then retried by a cron process without the flag (`effects.py:235`), raises on every run. No submitted card is ever applied again.
  - Fix: catch errors per effect, record them, and continue; mark the effect failed after N attempts.
- **The lock is not passed to child processes:** `effects.py:143,154,178,187` call `subprocess.run` without `pass_fds`. Because `close_fds=True` is the default, the inheritable lock fd is closed in each child, so the §1 contract (lock held until the last child exits) doesn't hold.
  - Example: a dead parent with a live child lets the next sweep re-send.
  - Fix: pass `pass_fds=(lock_fd,)` and add the concurrency test §7 lists.
- **Non-diarized calls lose every item:** at `extract.py:111`, `turns()` returns nothing, so the speaker is None and every item is dropped, giving `no_card`. §2 says to keep them as `speaker unknown` (⚠).
  - Fix: when the call isn't diarized, keep the item with `trust="unknown"`.
- **Retries never stop:** at `pipeline.py:24,38-47`, `failed_retry` (including `AttributionError`) re-runs `claude -p` every 5 minutes with no limit, and `failed_final` and manual review don't exist.
  - Fix: set `failed_final` after N attempts and alert gm.
- **The dispatch seat checks aren't implemented:**
  - The §6 step 4 check (seat live in the registry, otherwise reroute to gm) is missing from `effects.py:201-211` and `ledger.py:255-264`.
  - `pipeline.py:115` never sets `seat` or `agent_work`, so no card ever offers "Assign".
  - Fix: resolve the seat before filing, and check the registry before sending, rerouting to gm when the seat isn't live.
- **Results notices are always partial:** nothing in production calls `record_result`, so seat replies are never ingested. Every notice is a "partial, 48 h" one.
  - In addition, `ledger.py:323` leaves out `due_pending_patch`, which §5 says must be reported.
  - Cards with no dispatches get no notice at all (`ledger.py:312`), so the unreviewed items from Q1=2 are never listed for gm (§4).
  - Fix: ingest `[postcall dispatch:…]` replies, and add the pending-patch and unreviewed counts to the notice.
- **14-day retirement isn't implemented:** no code retires a card 14 days after Decide-by (§4 / ruling 6).
  - Fix: add the author-retire step to the sweep.
- **Calls that span activation are skipped:** `sweep.py:95` compares `created_at` with the start time, but §1/§7 compare the call's end. A call in progress at activation is skipped, which is conservative but differs from the spec.
  - Fix: compare `created_at + duration` with `start_ts`.
