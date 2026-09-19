# OrchestraOS v0.1.0-hackathon

The first public release of OrchestraOS, published by Tulum DAO for the Build-a-thon (Tulum, 2026-09-19/20).

## What you get

- **Seats and generations.** Agents with lineage; a seat hands off, a successor proves it
  read the handoff, and is promoted. Rotation is on by default for Claude seats; Gemini and
  Codex are experimental (`[rotation] experimental_runtimes`).
- **Inter-agent messaging**, a durable store with inboxes, acks and a router.
- **Memory** that survives rotation: a facts store plus per-seat memory directories.
- **Approvals surface**: every decision is a card on the dashboard and, with the plugin, on
  your phone via Telegram with buttons.
- **Arturo**, the assistant, as the main page.
- **`orchestra init / doctor / up / spawn / rotate / upgrade`** — one CLI, one config file,
  one data dir.

## Release gate: 5 of 7

The seven-step gate (`docs/GATE.md`) was run end to end against this release. The commit it ran against
and the commit tagged here differ only in documentation — no product code changed between them. Five
steps passed. Two did not, for unrelated reasons, and **neither is a defect in what you are
installing** — one of them is the system refusing to do something dangerous.

**Steps 1, 2, 4, 5 — install and doctor, spawn an agent, agent-to-agent mail, a decision card that
resumes the agent — PASSED, clean.** A stranger can install this and run it: `doctor` exited 0, every
process came up, the dashboard served.

**Step 3 — push notification to a phone — was RUN AND UNMET. It was not skipped.** The runner paused
the notification router, opened the documented window and sent the ping. Nothing arrived, for two
independent reasons, and neither is the notification path's fault. First, the step depends on a person
being reachable and they were not. Second — and this is the one worth your attention if you are
reproducing it — **the documented five-minute paused-router window is not performable as written on a
host that runs the service watchdog**, because the watchdog restarts the router within about two
minutes of it being stopped. The path was therefore never exercised, so we are not claiming it works
and we are not claiming it is broken. We know nothing about it either way, which is why it is reported
as unmet rather than passed or failed.

**Step 6 — rotate a seat — FAILED, and the failure is the system refusing to fabricate state.**
`orchestra rotate` was asked to rotate an agent that had been registered without identity-store
lineage. It refused, with: *"A rotation must not invent lineage numbers."* It did not corrupt a
lineage, invent one, or half-rotate the agent — it stopped, said exactly what was wrong, and told the
operator how to fix it. We would rather ship that than a rotation that guesses.
Two things behind it, both known and both deliberately deferred to after this release:
the underlying lineage gap is issue **G9**; and separately, **an agent commissioned through Arturo's
own `spawn_agent` tool currently takes an older registration path that never records lineage**, so any
agent created that way cannot be rotated until it is registered properly. If you spawn from the CLI
you will not hit this.

**Step 7 — memory survives — PASSED VIA RESTART, not via rotation.** Because step 6's rotation
refused, recall was proven by killing an agent and respawning it: its memory came back. What was
**not** demonstrated is recall across a lineage rotation. Those are two different paths and only the
first was exercised. If you hear "they rotated an agent and its memory survived" — that is the
stronger claim, and it is not the one this run supports.

## Start here

1. `docs/BEGINNERS_GUIDE.md`, then the seven-step gate in `docs/GATE.md`.
2. Pick a track from `docs/tracks/README.md` or a `good-first-issue` from the issue list.
3. `CONTRIBUTING.md` for the PR rules (DCO, CI, the four scans).

## Known gaps

Listed as issues (`T1`–`T12`, `G1`–`G20`), seeded from `docs/HACKATHON_ISSUES.md`.
