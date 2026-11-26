# Round 6: second Claude reviewer (fenced read-only claude -p), 2026-11-25T17:51:04Z
Brief: round-5 blocker + v4.1->v4.2 diff only (gm msg_92221c61).

VERDICT: APPROVE

v4.2 fixes the round-5 blocker. Under step 2 (`docs/DESIGN_post-call-commitments.md:217`), a `same_as_occ` claim is accepted only if owner, `action_type`, `operation` and normalized `deliverable` all match the old occurrence, the quote overlaps the old span (or the quote text is equal), and no other new item claims the same old occurrence. If two items claim one occurrence, or a claim fails a field check, every item involved goes to the uncertain path. That path is marked ⚠, excluded from accept-all, and becomes `unreviewed` if Shaw doesn't answer it. Re-extraction never deletes or supersedes old occurrences. So two commitments in one sentence can't collapse into one without Shaw's explicit pick, and nothing is dropped. The chase-date derivation and the rollout wording don't change task, dispatch or effect keys, which are still one `task:<cmt>`/`dispatch:<cmt>` per commitment. They create no new duplication path.

Non-blocking gap to fix in the text: the occurrence row at :212 is stored as `(call_id, owner, deliverable, action_type, quote, span_start, span_end)`. It has no `operation` field, but :217 compares `operation` against "the old occurrence's" value. Here's how that could lose a commitment if an implementer skips that comparison because the field isn't stored:
- Old occurrence A is "draft email".
- Re-extraction returns x (draft) and y (send), and the LLM maps only y to A.
- y is then accepted into A, and the "send" commitment is silently absorbed.

Add `operation` to the stored tuple at :212, and to the "distinguished by" line at :214.
