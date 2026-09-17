# Contributing to OrchestraOS

Thanks for building the harness with us. This page is the whole process; the merge rule
at the bottom is what a reviewer actually checks.

## Before you start

- Get a working install first: `docs/INSTALL.md` (the minimum path takes a few minutes on a
  clean machine or in the dev container). Every change is proven against a running install,
  not only against tests.
- Pick an issue (the `good-first-issue` and `track` labels are seeded for the hackathon) or
  open one describing what you want to change and why. Big changes: open the issue first.

## Fork, branch, PR

1. Fork the repo and clone your fork.
2. Branch from `main`: `git switch -c <topic>` (short, kebab-case).
3. Commit early with clear messages. Each commit must be **signed off** (DCO, below).
4. Push and open a pull request against `main`. Fill the PR template — the
   "Proven by effect" section is required, not decorative.
5. CI must be green (tests per package, secret scan, DCO check). A maintainer reviews within
   the hackathon review window; expect questions, and expect to be asked for a by-effect line.

## Developer Certificate of Origin (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/) instead of a
CLA. By signing off you certify that you wrote the change or have the right to submit it
under the project license (Apache-2.0).

Sign every commit:

```bash
git commit -s -m "fix(gateway): ..."
```

which adds a line like `Signed-off-by: Your Name <you@example.com>` using your git identity.
Forgot one? `git commit --amend -s` for the last commit, or `git rebase --signoff main` for a
branch. The `dco` CI job fails the PR until every commit carries the trailer.

## How we build: RED first, then prove it by effect

- **RED-first.** Write the failing test that describes the bug or the missing behavior,
  watch it fail for the right reason, then make it pass. Commit messages say what was red.
- **Proven by effect.** A test passing is necessary, not sufficient. Run the thing: the
  supervisor (`orchestra up`), a spawned seat, a card round-trip, a `doctor` row — whatever
  the change touches — and put the observed line in the PR ("`/api/agents` shows the seat
  `idle` with `detector_age_ms` 1"). Several of the fixes that made the install work were
  found only by running it.
- **Code vs data.** `ORCHESTRA_DIR` is the DATA dir (registry, state, logs); code paths are
  resolved from the checkout (`ORCHESTRA_ROOT`, `__file__`). Never join a script path onto the
  data dir.
- **Hermetic tests.** No live tmux, no network, no operator paths, no `~` assumptions (isolate
  `HOME` when a test touches CLI config). The root `conftest.py` snapshots `os.environ` per
  test; `pytest.ini` sets `asyncio_mode = auto` repo-wide.
- **One instance per host** is the supported layout; the fleet is registry-scoped (only
  sessions that resolve to a registry row are ever touched).

## The merge rule

A PR is merged when all of the following hold on the merged tree:

1. **Per-package tally green**: every CI test job (`orchestra_cli`, root, `services/arturo`,
   `scripts` top level, `scripts/lineage_daemon`, `scripts/identity_store`,
   `scripts/focus_registry`, `contract`), plus `api` typecheck and `dashboard` build.
2. **Secret scan clean**: `detect-secrets` reports nothing new; no operator literals (home
   paths, hostnames, chat ids, business names) enter the tree.
3. **DCO check green**: every commit signed off.
4. **Proven by effect** section filled in, and the reviewer could reproduce it from the PR.
5. No new default that points at a private machine, and no credential in any shipped file.

Reviewers merge with a merge commit that repeats the tally and scan counts.

## Running the checks locally

```bash
make test                      # the per-package python suites, like CI
(cd api && npx tsc --noEmit)   # api typecheck
(cd dashboard && npm run build)
pip install detect-secrets && detect-secrets-hook --baseline .secrets.baseline $(git ls-files)
```

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be kind; assume good
faith; report problems to the maintainers listed in the README.
