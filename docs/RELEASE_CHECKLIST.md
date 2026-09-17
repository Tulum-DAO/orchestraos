# Release checklist — Saturday flip (v0.1.0-hackathon)

The public release of OrchestraOS happens at the Tuluminator build-a-thon, Saturday
2026-09-19, 11:00 Tulum. Everything below is one command or one click, in order. Items
marked **(needs public)** are refused by GitHub while the repo is private on a free org —
do them right after the flip, before the first attendee clones.

All `gh` calls use the org's own auth config, never the shared one:

```bash
export GH_CONFIG_DIR=$HOME/.config/gh-tulum     # ShawCole = org admin; see docs/REFERENCE_INSTALL.md
R=Tulum-DAO/orchestraos
```

## T-1 (Friday night)

- [ ] `main` is green: `gh run list --repo $R --branch main --limit 1` → `completed success`.
- [ ] Four scans at zero on `main` (the merge rule): env literals, credential shapes,
      business names, operator-as-word. Recipe in `docs/GATE.md`'s "Done" section.
- [ ] Seven-step gate re-run by the tester from a fresh container on the release SHA
      (`docs/GATE.md`; step 3 with a throwaway BotFather token). Report filed.
- [ ] `docs/HACKATHON_ISSUES.md` reviewed: every T-track and G-issue has a size label and
      an acceptance line (24 today).
- [ ] Brand subset present: `docs/design/brand/` (mark, lockups, favicons, `BRAND.md`).
- [ ] `orchestra upgrade` works from a clone one commit behind (`docs/UPGRADE.md`).

## T-0 (Saturday, before 11:00)

1. **Tag the release** (on the SHA the tester passed):
   ```bash
   git -C ~/repos/orchestraos fetch origin && git -C ~/repos/orchestraos tag -a v0.1.0-hackathon -m "OrchestraOS v0.1.0 — Tuluminator build-a-thon release" origin/main
   git -C ~/repos/orchestraos push origin v0.1.0-hackathon
   ```
2. **Flip public**:
   ```bash
   gh repo edit $R --visibility public --accept-visibility-change-consequences
   gh api repos/$R --jq '{private,visibility}'      # -> {"private":false,"visibility":"public"}
   ```
3. **Branch protection on `main`** (needs public):
   ```bash
   gh api -X PUT repos/$R/branches/main/protection --input - <<'JSON'
   {"required_status_checks":{"strict":true,"contexts":["CI"]},
    "enforce_admins":false,
    "required_pull_request_reviews":{"required_approving_review_count":1},
    "restrictions":null,
    "allow_force_pushes":false,"allow_deletions":false}
   JSON
   gh api repos/$R/branches/main/protection --jq '.required_status_checks.contexts'
   ```
   Merge discipline after this: PRs only; the maintainer seat merges with the four scans
   at zero and CI green (`docs/GATE.md` "Done", `CONTRIBUTING.md`).
4. **Secret scanning + push protection** (needs public):
   ```bash
   gh api -X PATCH repos/$R --input - <<'JSON'
   {"security_and_analysis":{"secret_scanning":{"status":"enabled"},
                             "secret_scanning_push_protection":{"status":"enabled"}}}
   JSON
   gh api repos/$R --jq '.security_and_analysis'
   ```
5. **Create the GitHub release** from the tag (release notes = the "What you get" list in
   `README.md` + the seven-step gate + the track list):
   ```bash
   gh release create v0.1.0-hackathon --repo $R --title "v0.1.0-hackathon" --notes-file docs/RELEASE_NOTES_v0.1.0.md
   ```
6. **Seed the issues** from `docs/HACKATHON_ISSUES.md` — one issue per `## T<n>` / `## G<n>`
   heading, labels from its `labels:` line, body = the section text:
   ```bash
   python3 scripts/seed_issues.py --repo $R --dry-run     # prints the 24 titles + labels
   python3 scripts/seed_issues.py --repo $R               # creates them (idempotent by title)
   ```
7. **Discussions**: already on. Pin a "Start here" discussion pointing at
   `docs/BEGINNERS_GUIDE.md` → `docs/GATE.md` → `docs/tracks/README.md`.
8. **Social / README**: `docs/design/brand/readme-header.svg` at the top of `README.md`,
   `social-card-orchestraos.svg` as the repo social preview (Settings → Social preview,
   upload the PNG export).

## Verify (5 minutes, from a laptop that is not the operator's)

```bash
git clone https://github.com/Tulum-DAO/orchestraos.git && cd orchestraos && make install
orchestra init --yes && orchestra doctor        # every required row OK
```

Then `docs/GATE.md` steps 1–2 by hand. If anything fails, the fix goes through a PR like
everyone else's — `main` is protected now.

## Rollback

`gh repo edit $R --visibility private` puts it back. The tag and release stay; delete the
release (`gh release delete v0.1.0-hackathon`) only if the SHA was wrong.
