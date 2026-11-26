# Landing gate: who reviews a PR

[CONTRIBUTING.md](../CONTRIBUTING.md) "The merge rule" says what has to be true before a PR merges.
This page says who checks it. The author, or a helper the author started, never counts as the
reviewer.

## Gating review (MERGE-OK comes only from here)

- **First: a Codex reviewer.** It runs fenced and read-only, and argues against the PR. It posts its
  verdict on GitHub at the exact head commit, MERGE-OK or CHANGES NEEDED. A MERGE-OK names every
  CI job green, by name. A new commit after the verdict voids it.
- **Fallback: a fenced Claude reviewer.** Used only while the Codex reviewer is out of usage. The PR
  and its merge request say which reviewer ran and why the fallback was used.

## Supplemental review (never gating)

A Grok reviewer runs after the gating review on every P0, security or auth PR, and on any PR whose
owner asks for it. It posts on GitHub as "supplemental (non-gating)" at the head it read. The owner
folds in each real finding or answers it in one line. A MERGE-OK never depends on it, and a Grok
CHANGES NEEDED never blocks a merge. Grok is held out of gating review because it missed blocking
defects in two calibration reviews, though it also found real issues the gating review had missed.
It is re-calibrated for gating only on the operator's request or when a new Grok model lands.
Each supplemental review reports its wall time.
