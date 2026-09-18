# Track 9 — Installer + doctor + supervisor hardening

Size: S · Labels: `track`, `install` · DONE, hardening welcome

## Problem

The core of this track already shipped: `orchestra init` / `doctor` / `up` / `down`
/ `status` exist (`orchestra_cli/__main__.py`, `init_cmd.py`, `doctor.py`), the
Dockerfile and `.devcontainer/devcontainer.json` reproduce a clean-machine install,
and `orchestra init --demo` seeds fixture seats + one card of each kind
(`init_cmd.py` `seed_demo_registry` ~line 129, demo card seeding ~line 253). An
an outside tester proved a clean container reaches `orchestra doctor`
green in under 4 minutes, unattended, three runs in a row. Also since shipped:
`orchestra init` installs the Claude Code hook layer into
`$CLAUDE_CONFIG_DIR/settings.json` (merge/idempotent/fail-open, doctor row
`hooks:claude`, `ORCHESTRA_SKIP_HOOKS=1` for containers with no Claude seats), and
makes the data dir a small git repo (the `data-git` step) so rotation can prove a
successor's readback by commit. What's left is hardening, not the installer
itself — the problem this doc solves is that three concrete gaps remain,
documented in `docs/HACKATHON_ISSUES.md`'s T9/G3/G6 sections.

## Design

Three independent pieces of hardening, each small enough to land separately:

1. **Doctor rows for push, and any plugin Tier 0 item 6 doesn't cover** (overlaps
   `good-first-issue` G6 — check who has picked it up before duplicating).
   `orchestra doctor` today knows CLIs, ports, config keys, builds, the rotation
   beat, foreign tmux sessions, and (since Tier 0 item 6) a
   `plugin:telegram` row — this track does not re-add that one; it adds
   `notify:ntfy` (reachability when `NTFY_BASE` is set) for Track 8's push work
   and `plugin:whatsapp` once Track 6 ships that plugin.
2. **`make image` / machine-image step.** `docs/INSTALL.md`'s "Machine image (VPS
   snapshot)" section documents the manual recipe (run the Dockerfile's `RUN` steps
   in order on a fresh VPS, leave the CLI logged out, snapshot). This piece turns
   that into a scriptable `make image` target that either builds the same
   Dockerfile for a registry push or drives the manual steps idempotently — pick
   whichever fits the chosen VPS provider once the operator's provider decision
   (Hetzner vs DigitalOcean) lands; do not block on it, write the target against
   whichever is easier to script and note the other as a follow-up.
3. **Templated systemd unit** (`good-first-issue` G3, listed separately in
   `docs/HACKATHON_ISSUES.md` — coordinate rather than duplicate). The unit file
   was excluded from the public tree because it hardcoded an install-specific user
   and checkout path; producing a `.service.template` + an `orchestra install-unit`
   substitution step un-blocks re-adding it along with its test
   (`scripts/lineage_daemon/unit_file_test.py`, currently also excluded).

**If you're testing any of this on a machine that also runs a live fleet** (not a
throwaway container): use `scripts/test_sandbox_env.sh` (isolated tmux server +
config dir) rather than spawning test seats against the real one — `tmux
kill-server` is refused on a host with a live fleet, on purpose, so cleanup
without the sandbox script will strand sessions you can't tear down.

## Files you will touch

- `orchestra_cli/doctor.py` — new rows: `notify:ntfy` (reachability when
  `NTFY_BASE` is set, Track 8), `plugin:whatsapp` once Track 6 ships that plugin.
  `plugin:telegram` already exists (Tier 0 item 6) — do not duplicate it.
- `Makefile` — new `image` target (or documented equivalent script under
  `scripts/`).
- `deploy/systemd/orchestra.service.template` (new; the directory does not exist in
  the public tree yet) + `orchestra install-unit` (or a `make` step) that
  substitutes the running user and `ORCHESTRA_DIR` into it.
- `scripts/lineage_daemon/unit_file_test.py` (re-add once the template exists;
  currently excluded from the public tree alongside the unit file it tested).
- `docs/INSTALL.md` — update the "Machine image" section once `make image` exists;
  the manual recipe becomes the fallback, not the only path.

## Steps

1. `orchestra doctor` on a machine with `[notify] channel = "telegram"` configured
   but the bot token unset — confirm what it reports today (baseline) before adding
   rows.
2. Add the `notify:*` and `plugin:*` doctor rows; write fixture-probe tests in
   `orchestra_cli/tests/test_doctor.py` (fake probes, matching the file's existing
   pattern) rather than hitting real services.
3. Build the `.service.template` with `{{ORCHESTRA_USER}}` / `{{ORCHESTRA_DIR}}`
   placeholders and the substitution step; re-add `unit_file_test.py` against the
   substituted output, confirm it passes.
4. Confirm a fresh install enables the unit without hand-editing it
   (`systemctl --user enable` or the system equivalent, depending on the unit's
   `[Install]` section).
5. Script or document `make image`; if the provider decision hasn't landed, target
   the generic Dockerfile-based path and note the provider-specific snapshot step
   as a follow-up rather than guessing.

## Acceptance test

Each new `orchestra doctor` row shows OK / MISSING / INFO with a one-line remedy,
covered by a fake-probe test in `orchestra_cli/tests/test_doctor.py`. A fresh
install enables the systemd unit via the templated path with no hand-editing, and
`unit_file_test.py` passes against the substituted file. `make image` (or its
documented equivalent) produces a bootable image/snapshot recipe that a teammate
can run without asking a maintainer what to fill in.

## Start prompt

```
I'm working Track 9 (installer + doctor hardening) for the OrchestraOS
hackathon. This is DONE at the core — orchestra init/doctor/up already
work and are proven on a clean container. This track is the three
remaining hardening items from docs/HACKATHON_ISSUES.md's T9 section:
doctor rows for plugins/push, a `make image` step, and the templated
systemd unit (also tracked as good-first-issue G3 — check who has it
before duplicating).
Read docs/tracks/09-installer-doctor.md in this repo for the full design.
Start with the doctor rows (orchestra_cli/doctor.py) since Tracks 6 and 8
depend on them existing to be checkable; the systemd template and make
image step are independent and can be picked up by someone else in
parallel.
```

## Out of scope

- Re-litigating the installer's core design (init/doctor/up) — that shipped and is
  proven; this track only hardens the edges.
- The VPS provider decision itself (Hetzner vs DigitalOcean) — see `docs/COSTS.md`
  for the comparison; this track's `make image` target should work against
  whichever is chosen without a rewrite.
