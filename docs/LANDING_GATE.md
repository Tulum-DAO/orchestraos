# The landing gate: what has to be true before a PR merges

A pull request lands on `main` only when all of this holds **on the exact head that is merged**. The
rules exist because each one was missed at least once and cost a red `main` (2026-11-19: a red `dco`
job survived seven review rounds; 2026-11-25: a PR merged with no completed CI run on its head and
carried 19 operator identifiers onto `main`, which turned every other PR's checks red for an hour).

## 1. Every CI job green, named, on the merged head

- The merge request (the card the operator answers, or the merge commit's note) names **every job** of
  the PR's workflow run **by job name** with its conclusion, and the head sha it ran on. "CI green" is
  not a statement; `dco: success, operator-identifier-scan: success, python-tests (scripts-top): success,
  …` is.
- A missing run, an **empty rollup** (no checks reported yet) or an **in-progress** run is a red. A draft
  PR has no runs on its head until it leaves draft: lift the draft first, wait for the run, then read it.
- Checks run on the merge of the head with `main`. A red that comes from `main` itself (another PR broke
  it) is still a red for landing: fix `main` first, re-run, then land.

## 2. An independent verdict on that same head

- A second reviewer, not the author's own session, posts `MERGE-OK` or `BLOCK` with findings **on the PR,
  at a named head**. A verdict on an earlier head does not carry forward: a new head gets a delta verdict.
- The verdict names the CI jobs it saw green on that head (rule 1 applies to the reviewer too).

## 3. Head-matched merge

- Merge with the full sha of the reviewed, green head: `gh pr merge <n> --squash --match-head-commit <sha>`.
  If the head moved, stop: re-review, re-run, re-request.
- Never rebase or push to a branch under an open merge request; report instead.

## 4. The scans are part of the gate, not a courtesy

`operator-identifier-scan`, `secret-scan` and `secret-fallback-scan` are jobs like any other. A green
secret scan is not a privacy clearance: the identifier scan is the one that catches a city clock or a
home path. Run them the way CI runs them (`--git-ls`, with the baseline) on the head you are about to
merge, not on a branch you tested earlier.

## 5. What a landing note looks like

```
PR #639 at 5a1ed463: dco ✓, operator-identifier-scan ✓, secret-scan ✓, secret-fallback-scan ✓,
python-tests (scripts-top) ✓, python-tests (scripts-lineage-daemon) ✓, …, api-typecheck ✓,
dashboard-build ✓ (run 45712345678). Verdict: MERGE-OK by <reviewer> at 5a1ed463 (comment link).
Merge: gh pr merge 639 --squash --match-head-commit 5a1ed463…
```

The PR template's checklist points here; the template is where a contributor first meets these rules.
