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
- **Who gives it.** First a Codex reviewer, running fenced and read-only and arguing against the PR.
  A fenced Claude reviewer stands in only while Codex is out of usage. The merge request names the
  reviewer, and says why when the fallback ran.
- **First-pass reading, never gating.** Before the gating review, every PR gets a Grok first pass, so
  the gating rounds start from a cleaner head and there are fewer of them. The owner asks for it once
  CI is running, and Grok reads in a fresh session, never inside a long-lived agent's context. Grok
  posts its findings on the PR as "first pass (non-gating)" at the head it read, and sends the same
  list to the owner. The owner folds in each real finding, or answers it in one line on the PR, then
  asks for the gating review. `MERGE-OK` never depends on the first pass, and its findings never stop
  a merge. Each first pass reports its wall time, and the token count from its session's usage record.
- Grok is held out of gating review because it missed blocking defects in two calibration reviews,
  though both also found real issues the gating review had missed. It is re-calibrated for gating
  only on the operator's request or when a new Grok model lands.

## 3. Head-matched merge

- Merge with the full sha of the reviewed, green head: `gh pr merge <n> --squash --match-head-commit <sha>`
  (a squash, so `main` carries one signed-off commit per PR; the PR keeps the commit history). If the head
  moved, the merge is refused: stop, re-review, re-run, re-request.
- Any push to the branch (a fix, a fold, `git rebase --signoff` to repair a red `dco`) makes a **new head**.
  That is normal review work, and it restarts this gate for that head: a new run, a delta verdict, and a new
  landing note. What is never done is pushing to the branch *between* the landing note and the merge, or
  merging a head other than the one the note names.

## 4. The scans are part of the gate, not a courtesy

`operator-identifier-scan`, `secret-scan` and `secret-fallback-scan` are jobs like any other. A green
secret scan is not a privacy clearance: the identifier scan is the one that catches a city clock or a
home path. Run them the way CI runs them (`--git-ls`, with the baseline) on the head you are about to
merge, not on a branch you tested earlier.

## 5. What a landing note looks like

A real one (PR #637, landed 2026-11-25; the job names are the workflow's, read off run 45713480796):

```
PR #637 at 981e72f4: dco ✓, operator-identifier-scan ✓, secret-scan ✓, secret-fallback-scan ✓, contract-ts ✓,
api-typecheck ✓, dashboard-build ✓, push-relay ✓, python-tests (root) ✓, python-tests (scripts-top) ✓,
python-tests (scripts-lineage-daemon) ✓, python-tests (scripts-identity-store) ✓,
python-tests (scripts-focus-registry) ✓, python-tests (orchestra-cli) ✓, python-tests (services-arturo) ✓,
python-tests (services-egress-proxy) ✓, python-tests (contract) ✓, python-tests (hooks) ✓,
python-tests (plugins) ✓ (run 45713480796). Verdict: MERGE-OK by the independent reviewer at 981e72f4
(PR comment). Merge: gh pr merge 637 --squash --match-head-commit 981e72f4ee82f8fe94860b881955d33a4596b3a2
```

The PR template's checklist points here; the template is where a contributor first meets these rules.
[CONTRIBUTING.md](../CONTRIBUTING.md) is the process this gate sits at the end of.
