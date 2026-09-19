# Release checklist — Saturday flip (v0.1.0-hackathon)

The public release of OrchestraOS (the product), published by Tulum DAO (the publisher), happens at the Build-a-thon (the event), Saturday
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
      business names, operator-as-word. These run in CI as the `secret-scan` job
      (`detect-secrets` against `.secrets.baseline`); the rules they enforce are in
      `CONTRIBUTING.md`'s "What a reviewer checks" and "Running the checks locally".
- [ ] **Stranger-test scan** on `main` for BOTH repos — `scripts/stranger_scan.sh <repo>`.
      The rule is NOT "at zero": accepted rows are non-zero by design. The rule is that every row is
      IDENTICAL to the previous `main`, and every non-zero row has been characterised. Reaching a
      number by deleting something is the wrong remedy.
      Different question from the four scans: not "is a credential in here" but "does this tell a
      stranger WHO this is or WHERE to look" — real names, customer names, live approval/session ids,
      host addresses, and any sentence naming where a credential sits. Record the counts; characterise
      anything non-zero before calling it clean (the product's own vocabulary is not a leak).
- [ ] Seven-step gate re-run by the tester from a fresh container on the release SHA
      (`docs/GATE.md`; step 3 with a throwaway BotFather token). Report filed.
- [ ] `docs/HACKATHON_ISSUES.md` reviewed: every T-track and G-issue has a size label and
      an acceptance line. Take the count from the command below rather than from this line.
- [ ] Brand subset present: `docs/design/brand/` (mark, lockups, favicons, `BRAND.md`).
- [ ] `orchestra upgrade` works from a clone one commit behind (`docs/UPGRADE.md`).

## T-0 (Saturday, before 11:00)

0a. **MEASURE REFS, NEVER THE WORKING TREE — AND NEVER ASSUME WHICH BRANCH THE SHARED CHECKOUT IS ON.**
   Every command in this step names an explicit ref (`origin/main`, `$TESTED..$SHA`, `--all`) for a
   reason: several git commands silently read the WORKING TREE instead, and the shared clone on this host
   is routinely sitting on someone else's feature branch. Measured eight hours before a flip, the shared
   checkout was on an open PR's branch, not `main`.
   • `git apply --check <patch>` tests against the **working tree** — it fails on a correct patch, and
     passes a wrong one, purely because of what is checked out.
   • `git grep <pattern>` with no ref reads the **working tree**; `git grep <pattern> origin/main` reads
     the ref. The two answer different questions and look identical in a terminal.
   • `git diff A..B` where a branch has been deleted silently compares against the working tree.
   **If you are unsure, `git rev-parse --abbrev-ref HEAD` first, or work from a fresh `git clone` /
   `--mirror`.** A control run from a detached mirror costs ten seconds and is the only way to know the
   number you are reading belongs to the tree you think it does.
   **DO NOT "fix" the shared checkout by checking out `main`** — another seat may be working in it, and a
   checkout flips their branch under them.

0. **PRE-FLIP ALL-REFS DISCLOSURE SCAN.** Run this BEFORE making the repo public. It scans the FULL
   COMMIT HISTORY of EVERY BRANCH the flip publishes — not the release sha, not `main`'s tip, not a
   working tree. `git grep` at a clean tip returns zero while the exposure sits one commit back.
   **What it scans for, enumerated** (a step that says only "scan for secrets" is the same defect one
   layer up): operator hostnames of the form `srv<digits>`, any `*.ts.net` tailnet name, and any
   Tailscale CGNAT address in the `100.64/10` range.
   **FETCH THE PULL REFS FIRST — THIS LINE IS THE STEP.** A normal clone does NOT fetch `refs/pull/*`, so
   `git log --all` on a fresh checkout cannot see them. Without this fetch an operator enumerates the
   branches, finds the branch hit, deletes the branch and reports the exposure CLOSED while the PR ref
   still carries it. Measured on this repo: after deleting `web/first-run-connect` AND closing PR #23,
   `9b30d3c` was **still carried by `origin/pr/23`** and the API still resolved it.
   ```bash
   git -C ~/repos/orchestraos fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'
   ```
   **CLASSIFY EVERY HIT. DO NOT COUNT THEM.** The pattern matches any tailnet-SHAPED string, including
   deliberate test fixtures. Open each hit and decide whether it is a real operator host or a fixture —
   a step that reports a number teaches its operator either to panic or to skip it. Measured examples on
   this repo at `d5f6802`: the narrow pattern below returns **0 files on main's tip**, while the guard's
   looser `[a-z0-9-]+\.ts\.net` returns **2 benign lines** in
   `scripts/test_approval_notify_offtailnet.py` (`iphone-15-pro-max.testnet[.]ts[.]net`) — intentional fakes,
   not exposure.
   **THE STEP SHIPS WITH ITS EXPECTED-HIT BASELINE. COMPARE, DO NOT JUDGE.** A scan whose first run hands
   the operator an unexplained hit at 10:55 is a scan that gets overridden or that stops a good release.
   Measured on `main`'s tip at `d5f6802`, outside `docs/`:
   **EXPECTED (benign) on `main`'s tip at `d5f6802`, outside `docs/`: EXACTLY TWO LINES**, both in
   `scripts/test_approval_notify_offtailnet.py` — `iphone-15-pro-max.testnet[.]ts[.]net.` (line 38, `"k1"`)
   and `iphone172.testnet[.]ts[.]net.` (line 40, `"k2"`), both `"HostName": "localhost"` — a fake phone on a
   fake tailnet in an off-tailnet notify TEST. Verified benign: that file has **0** hits for
   `srv[0-9]{6,}` or the operator's real tailnet name. **ANY OTHER HIT, OR MORE THAN TWO, IS A STOP.**
   **The two hostnames above are written with `[.]` breaks ON PURPOSE.** Spelled normally they match the
   very pattern this step scans for, so documenting the expected hits would ADD two more of them — this
   checklist tripped its own scan twice while being written (first on the CGNAT range written in full dotted form, then on these hostnames — and a third time on the sentence describing the first).
   **Any document that quotes a secret-shaped string must break it, or the scanner finds the scanner.**

   **COMPARE THE LINES, NOT THE COUNT.** "Exactly two hits" passes unchanged if a real operator host
   REPLACES one of the fixtures — the count is identical and the exposure is total. The expected set is
   identified by FILE AND LINE, which cannot be spoofed by a substitution:
       `scripts/test_approval_notify_offtailnet.py` line **38** (`"k1"`) and line **40** (`"k2"`),
       both `"HostName": "localhost"`, both a fake phone on a fake tailnet.
   Read the two matched lines and confirm each is one of those. A hit at any other file or line, or a
   different host on those lines, is a STOP.
   **THE TRIPWIRE QUOTES NO EXPECTED CONTENT — it expects ZERO — so it is IMMUNE to the bracket trap
   below. Only the SCAN row needs it.**
   **NOTE ON THE `[.]` BREAKS: the hostnames quoted in this document are written with `[.]` so this file
   does not trip the scan. The real lines contain ordinary dots.** Strip the brackets before comparing —
   a literal string-compare against this document will mismatch, and that mismatch is an artefact of the
   documentation, not a finding.

   **THE BASELINE IS ANCHORED TO `d5f6802`. THE DECISION RULE IF `main` HAS MOVED:**
   re-run at the CURRENT tip, then —
   • count is 2 AND both lines are the two fixture lines named above -> **PROCEED**; the baseline holds.
   • tip differs from `d5f6802` AND the set differs -> **STOP AND ESCALATE.** Do not judge a new hit in
     the release hour. A baseline compared against a sha nobody is on is not a baseline.
   Measured for the one merge already queued: the expected set is **UNCHANGED (2 lines, same file)** at
   this document's own branch tip, because this checklist is self-clean under both patterns. So landing
   the docs PR does not move the baseline — verified, not assumed.

   **WHY THIS PATTERN AND NOT A NARROWER ONE — decision recorded so it is not silently re-litigated:**
   a `tail`-anchored pattern returns ZERO on main (a cleaner baseline) and would still have caught
   tonight's real leak — but it MISSES any custom tailnet name, and "our operator uses the default
   naming" is a premise about today, not a property of the check. The two errors are not symmetric:
   a false positive costs thirty seconds comparing two quoted lines; a false negative costs a
   published hostname that this repo has now PROVEN it cannot retract. Irreversible beats
   inconvenient. The two expected hits are the price, and they are why the baseline above exists.

   **PICKAXE AND PRESENCE ANSWER DIFFERENT QUESTIONS — run BOTH and know which you are reading.**
   `log -S… --pickaxe-regex` finds commits where the count CHANGED (an introduce and its removal: 2).
   A per-commit `git grep` finds every commit where the string is PRESENT (3 — including the one in
   between). **Exposure is a PRESENCE question**: a commit that merely carries an already-introduced
   host is just as fetchable. Use pickaxe to find the introducing commit, presence to size the exposure.
   ```bash
   R=Tulum-DAO/orchestraos
   # LOOSE tailnet half is deliberate: `tail*` is only the AUTO-GENERATED form, so a custom tailnet
   # (`acme[.]ts[.]net`) is invisible to a `tail`-anchored pattern. A false positive costs 30 seconds of
   # comparison; a false negative costs a published hostname that CANNOT BE UNPUBLISHED — measured
   # tonight: branch deleted, PR closed, `refs/pull/23/head` still resolving.
   # ---- TWO INSTRUMENTS, TWO ROLES, TWO EXPECTED READINGS. Run both. Never judge; only compare. ----
   #
   # 1) TRIPWIRE — expects ZERO. ANY hit is an immediate STOP: no allowlist, no comparison, no thought.
   #    It matches only THIS OPERATOR'S OWN identity, so it cannot false-positive on a fixture.
   #    **THE LITERAL IS DELIBERATELY NOT COMMITTED TO THIS REPO.** Writing the operator's tailnet name
   #    into a public checklist would publish half the very host the tripwire exists to keep out — the
   #    check would become the leak. Supply it at run time from the machine, never from this file:
   TAILNET=$(tailscale status --json 2>/dev/null | sed -n 's/.*"MagicDNSSuffix" *: *"\([^.]*\)\..*/\1/p' | head -1)
   [ -n "$TAILNET" ] || { echo "STOP: cannot resolve the operator tailnet name; do not skip this step"; exit 1; }
   #    CROSS-CHECK THE VALUE AGAINST AN INDEPENDENTLY-DERIVED FIELD. `Self.DNSName` is this machine's
   #    own FQDN, produced by a different code path than `MagicDNSSuffix`; requiring one to contain the
   #    other means a wrong resolution FAILS instead of quietly agreeing with itself.
   SELFDNS=$(tailscale status --json | sed -n 's/.*"DNSName" *: *"\([^"]*\)".*/\1/p' | head -1)
   #    POSITIONAL EQUALITY, NOT CONTAINMENT. A containment test passes for ANY substring of the FQDN —
   #    including FIELD 1, the hostname. An off-by-one in the extraction then grabs the host instead of
   #    the tailnet, the check passes, and the tripwire silently collapses to its `srv` half.
   EXPECT=$(printf '%s' "$SELFDNS" | cut -d. -f2)
   [ -n "$EXPECT" ] && [ "$TAILNET" = "$EXPECT" ] \
     || { echo "STOP: resolved tailnet name is not field 2 of this machine's own FQDN — extraction is wrong"; exit 1; }
   TRIPWIRE="srv[0-9]{6,}|$TAILNET"
   #    POSITIVE CONTROL — RUN BEFORE TRUSTING THE ZERO. A zero from a pattern that cannot match is
   #    indistinguishable from a zero from a clean repo. Prove the instrument fires, then believe it.
   #    NOTE WHAT THIS CONTROL DOES AND DOES NOT PROVE: the probe and the pattern share `$TAILNET`, so
   #    on its own it proves the REGEX MACHINERY works, not that the VALUE is right — it fires happily
   #    on a garbage tailnet name. The cross-check above is what makes the value trustworthy. A control
   #    that draws on the same source as the instrument it tests can only agree with itself.
   #    Synthetic, so it depends on no commit and publishes nothing:
   #    NOTE: the probe host is ASSEMBLED at run time, not written out — spelled literally it would
   #    match the SCAN pattern and this checklist would trip its own step (it did, while being written).
   printf 'srv%s.%s.%s\n' 1234567 "$TAILNET" 'ts.net' | grep -qE "$TRIPWIRE" \
     || { echo "STOP: tripwire did not fire on its own positive control — the pattern is broken, not the repo"; exit 1; }
   #    CONTROL EACH HALF SEPARATELY. The whole-pattern control above passes if EITHER half matches, so a
   #    broken tailnet half hides behind a working `srv` half — the same one-signal-two-meanings defect as
   #    an exit code that means MISSING or FAIL. Assert both, against live output neither half built:
   TS_OUT=$(tailscale status --json)
   printf '%s' "$TS_OUT" | grep -qE 'srv[0-9]{6,}' \
     || { echo "STOP: the srv half did not fire against live tailscale output"; exit 1; }
   printf '%s' "$TS_OUT" | grep -qF "$TAILNET" \
     || { echo "STOP: the tailnet half did not fire against live tailscale output — extraction is wrong"; exit 1; }
   #    Corroboration while the ref still exists (optional, and it WILL disappear one day):
   #      git log --all -S"$TRIPWIRE" --pickaxe-regex --oneline   # must list the known-bad commit
   #    EXPECTED, once the control has fired: ZERO hits on every ref. Any hit at all -> STOP.
   #
   # 2) SCAN — expects exactly the two quoted fixture lines recorded above. Answers the broader question
   #    "does this repo carry ANYONE's tailnet host", which the tripwire by construction cannot.
   #    LOOSE tailnet half is deliberate: `tail*` is only the AUTO-GENERATED form, so a custom tailnet
   #    (`acme[.]ts[.]net`) is invisible to a `tail`-anchored pattern. A false positive costs 30 seconds of
   #    comparison; a false negative costs a published hostname that CANNOT BE UNPUBLISHED — measured
   #    tonight: branch deleted, PR closed, `refs/pull/23/head` still resolving.
   PAT='srv[0-9]{6,}|[a-z0-9-]+\.ts\.net|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]+\.[0-9]+'
   git -C ~/repos/orchestraos fetch origin --prune
   for b in $(gh api repos/$R/branches --jq '.[].name'); do
     git -C ~/repos/orchestraos fetch origin "$b" >/dev/null 2>&1
     n=$(git -C ~/repos/orchestraos log --oneline -S"$PAT" --pickaxe-regex "origin/main..origin/$b" | wc -l)
     [ "$n" -gt 0 ] && printf "  HIT  %-32s %s commit(s)\n" "$b" "$n"
   done
   ```
   **Write the range as `100.64/10`, never in full dotted form — the full form MATCHES THIS STEP'S OWN
   PATTERN, so documenting it makes this scan report a HIT on its own checklist forever. A scan that cries
   wolf on its own documentation is a scan people learn to skip.**

   Enumerate branches FROM THE API every time — a hardcoded list goes stale silently (the count moved
   12 -> 14 in one evening). Any HIT: stop and escalate; that branch must not be published as-is.
   **A BRANCH SCAN IS NOT ENOUGH — FETCH `refs/pull/*` AND SCAN EVERY REF.** GitHub keeps pull-request
   refs independently of branches: deleting the branch, force-pushing a scrubbed history or rewriting the
   ref does NOT remove them, and on a public repo the commits stay browsable in the PR's Commits and
   Files-changed views at a stable URL. PR numbers are enumerable. A normal clone does not fetch these,
   so a branch-only scan reports CLEAN while the exposure is still reachable — measured on this repo:
   `9b30d3c` is carried by BOTH `origin/web/first-run-connect` AND `origin/pr/23`.
   ```bash
   git -C ~/repos/orchestraos fetch origin '+refs/pull/*/head:refs/remotes/origin/pr/*'
   git -C ~/repos/orchestraos log --all --oneline -S"$PAT" --pickaxe-regex
   for r in $(git -C ~/repos/orchestraos for-each-ref --format='%(refname:short)' refs/remotes/origin); do
     git -C ~/repos/orchestraos merge-base --is-ancestor <hit-sha> "$r" 2>/dev/null && echo "  CARRIED BY: $r"
   done
   ```
   **Closing a hit therefore means closing the PR ref too, not just the branch.** Escalate rather than
   assuming a branch delete is sufficient.
   **AND A PULL REF MAY SURVIVE EVERYTHING A COMMAND CAN DO.** Measured on this repo, by effect: the head
   branch was deleted AND the PR was closed, and `git ls-remote origin 'refs/pull/23/*'` still resolved
   `refs/pull/23/head`, with the introducing commit reachable through it. Deleting the branch does not
   remove it; closing the PR does not remove it; rewriting history never touched it. **If the remedy has
   to be complete, the only routes left are a support request or not publishing the repo — neither is a
   command, and both are the operator's call, not the releaser's.**
   **RECORD EVERY ACCEPTED HIT HERE, WITH ITS DECISION AND WHO MADE IT.** A hit that the operator has
   knowingly accepted must be written down, or the next person to run this step re-escalates a settled
   question at T-0 and stops a good release. An unrecorded acceptance is indistinguishable from an
   undiscovered exposure.
   **IF A HIT IS REMEDIED BY DELETING A REF: BUNDLE IT FIRST, AND PROVE THE BUNDLE RESTORES — a verify is
   not a restore.** `git bundle create <f> origin/<branch>` verifies clean and then clones as an EMPTY
   repository, because `origin/*` refs land as `refs/remotes/*` which a clone does not materialise.
   Bundle real local branches so the file carries `refs/heads/*`, then `git clone` the bundle and confirm
   the commits resolve:
   ```bash
   git branch -f rescue/<name> origin/<branch>
   git bundle create /home/shaw/repos/_rescue/<name>-$(date -u +%Y%m%dT%H%M%SZ).bundle rescue/<name>
   git bundle verify <file> && git clone -q <file> /tmp/restore-probe && \
     git -C /tmp/restore-probe cat-file -t <sha>      # MUST print "commit"
   ```
   Save the PR's metadata too (`gh pr view <n> --json number,title,body,headRefName,baseRefName`) — a
   deleted head branch CLOSES its PR, and the body is not reconstructable from memory.

1. **Tag the release.**
   **THE TAG FOLLOWS THE RELEASE, NOT THE GATE** (operator ruling): whatever we release is stamped
   `v0.1.0`. The docs-only property below is therefore **DISCLOSURE, NOT A GATE** — it no longer decides
   WHETHER to tag, it decides WHAT THE TAG MESSAGE MUST SAY. **Keep measuring it. A non-empty result is
   NOT a stop.** Silently dropping a measurement whose stop-power was removed produces a tag message
   asserting something nobody measured.

   **RESOLVE THE SHA ONCE.** The earlier form of this step resolved the moving ref `origin/main` three
   separate times — in the measurement, inside the tag message, and as the tag target — so the sha you
   MEASURED was not guaranteed to be the sha you TAGGED. Anything merging in that window gets tagged
   unmeasured. Capture it once and tag THAT OBJECT:
   ```bash
   TESTED=c33e33d          # the sha the Friday seven-step gate run actually measured
   git -C ~/repos/orchestraos fetch origin
   SHA=$(git -C ~/repos/orchestraos rev-parse origin/main)          # resolve ONCE
   SHORT=$(git -C ~/repos/orchestraos rev-parse --short "$SHA")
   # Documentation = docs/** PLUS root-level *.md (README.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md).
   git -C ~/repos/orchestraos diff --name-only "$TESTED..$SHA" | grep -vE '^docs/|^[^/]*\.md$' > /tmp/nondocs.txt
   N=$(git -C ~/repos/orchestraos diff --name-only "$TESTED..$SHA" | wc -l)
   M=$(wc -l < /tmp/nondocs.txt)
   cat /tmp/nondocs.txt      # RECORD these — they go in the tag message
   ```
   **Do not hand-write the directory names into the message.** That sentence has been false three times
   in four hours as merges landed (`dashboard/src` -> plus `api/src` -> plus `orchestra_cli`). Let the
   generated file list speak; the prose says only "product code".
   ```bash
   # Compose the message into a FILE. Never `-F -`: a heredoc in a pipeline binds to the LAST command,
   # and git then blocks on a stdin that never EOFs while holding the index lock.
   MSG=/tmp/tagmsg-v0.1.0.txt
   {
     echo "OrchestraOS v0.1.0 — Build-a-thon release (Tulum DAO)."
     echo ""
     echo "Tagged at $SHORT. The seven-step gate was run against $TESTED and scored 5 of 7:"
     echo "step 6 FAILED — the identity guard correctly refused to rotate a seat with no recorded"
     echo "lineage — and step 3 was NOT COMPLETED, for two host-environment reasons unrelated to"
     echo "the product. See docs/RELEASE_NOTES_v0.1.0.md."
     echo ""
     echo "Step 7 (recall) was proven across a CLI restart in place, NOT across a lineage rotation —"
     echo "the rotation branch was unavailable because step 6 refused."
     echo ""
     echo "This tag is NOT that sha: it carries $N further files, of which $M are product code that"
     echo "has NOT been through the seven-step gate:"
     sed 's/^/  /' /tmp/nondocs.txt
   } > "$MSG"
   cat "$MSG"          # READ IT before tagging — an annotated tag is permanent
   git -C ~/repos/orchestraos tag -a v0.1.0-hackathon -F "$MSG" "$SHA"
   ```
   **POST-TAG READBACK — verify the EFFECT, not the command's exit code.** Resolve-once closes the race;
   it does not prove the tag landed on the measured object. The `^{}` is load-bearing: an annotated tag
   has its own object sha, `git rev-parse <tag>` returns the TAG OBJECT, and `^{}` peels it to the commit.
   A readback written without it fails on a perfectly correct tag — a false STOP in the T-0 hour, which is
   worse than no check because it aborts a good release.
   ```bash
   test "$(git -C ~/repos/orchestraos rev-parse v0.1.0-hackathon^{})" = "$SHA" || { echo STOP; exit 1; }
   git -C ~/repos/orchestraos push origin v0.1.0-hackathon
   ```
   Why each clause exists — an annotated tag is permanent and is read by someone who cannot re-derive our
   intent. "5 of 7" alone flattens a guard WORKING (step 6 refused correctly — a feature) into the same
   bucket as a host problem (step 3), and invites "so recall survived a rotation", which is not what was
   tested.

2. **Flip public** (and apply the naming ruling: Build-a-thon = the event, OrchestraOS = the product, Tulum DAO = the publisher):
   ```bash
   gh repo edit $R --visibility public --accept-visibility-change-consequences
   gh repo edit $R --description "OrchestraOS, by Tulum DAO: an open harness for running a fleet of coding agents as a team. Agents that message each other, remember across restarts, rotate before they run out of context, and put every real decision in front of you on your phone."
   gh api repos/$R --jq '{private,visibility}'      # -> {"private":false,"visibility":"public"}
   ```
3. **Branch protection on `main`** (needs public):
   ```bash
   # NOTE: `contexts` matches CHECK-RUN / JOB names, NOT the workflow's `name:`.
   # There is NO context called "CI" — that is the workflow. Pinning it creates a
   # required check that never reports, so every contributor PR is unmergeable
   # forever while admins (enforce_admins:false) keep merging and never notice.
   # Verify the real names first:
   #   gh api repos/$R/commits/<a recent PR HEAD sha>/check-runs --jq '.check_runs[].name'
   # Use only STABLE non-matrix job names. Do NOT list the python-tests matrix legs:
   # one is exactly 100 chars and ends in a literal "..." (GitHub truncates check-run
   # names there), so it is unpinnable in practice and re-breaks on any matrix edit.
   gh api -X PUT repos/$R/branches/main/protection --input - <<'JSON'
   {"required_status_checks":{"strict":true,"contexts":["dco","secret-scan","api-typecheck","dashboard-build"]},
    "enforce_admins":false,
    "required_pull_request_reviews":{"required_approving_review_count":1},
    "restrictions":null,
    "allow_force_pushes":false,"allow_deletions":false}
   JSON
   gh api repos/$R/branches/main/protection --jq '.required_status_checks.contexts'
   ```
   Then PROVE IT BY EFFECT before the doors open: open a throwaway PR and confirm a
   non-admin path can actually merge it. A protection rule nobody has watched a
   stranger pass is the same class as a gate nobody has watched fail.
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
   python3 scripts/seed_issues.py --repo $R --dry-run     # prints every section's title + labels
   python3 scripts/seed_issues.py --repo $R               # creates them (idempotent by title)
   ```
7. **Discussions**: already on. Pin a "Start here" discussion pointing at
   `docs/BEGINNERS_GUIDE.md` → `docs/GATE.md` → `docs/tracks/README.md`.
8. **Social / README**: `docs/design/brand/readme-header.svg` at the top of `README.md`,
   `docs/design/brand/social-card-orchestraos-1280x640.png` as the repo social preview (Settings → Social preview,
   upload the PNG export).

## Verify (5 minutes, from a laptop that is not the operator's)

```bash
git clone https://github.com/Tulum-DAO/orchestraos.git && cd orchestraos && make install
./bin/orchestra init --yes && ./bin/orchestra doctor   # every required row OK. ./bin/ on purpose: `make install` does NOT put `orchestra` on PATH — bare `orchestra` is exit 127 on a bare box (measured 2026-09-19)
```

Then `docs/GATE.md` steps 1–2 by hand. If anything fails, the fix goes through a PR like
everyone else's — `main` is protected now.

## Rollback

`gh repo edit $R --visibility private` puts it back. The tag and release stay; delete the
release (`gh release delete v0.1.0-hackathon`) only if the SHA was wrong.
