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

The seven-step gate (`docs/GATE.md`) was run against this release from a fresh container, in demo
order. Five steps completed as designed. Two did not, for unrelated reasons, and neither is a defect
in what you are installing.

**Steps 1, 2, 4 and 5 passed clean** — install and `orchestra doctor`, an always-on agent spawned and
answering in its terminal, two seats exchanging a message visible in both inboxes, and an approval card
answered from the dashboard.

**Step 7 — write a fact, restart, agent recalls it — PASSED VIA THE RESTART BRANCH** of GATE.md's
"restart or rotate". The rotate branch was unavailable because step 6 refused, so recall was proven
across a real kill-and-respawn: the agent came back and answered from its memory file. It is **not**
evidence of recall across a lineage rotation — only the restart path was exercised.

**Step 6 — manual rotation — FAILED, and the failure is the system refusing to fabricate state.**
`orchestra rotate` was asked to rotate a seat that had no authoritative generation in the identity
store. It refused, with *"A rotation must not invent lineage numbers"*, named what was wrong and how to
fix it, and did not corrupt a lineage, invent one, or half-rotate the seat. The underlying gap is the
known issue `G9` — note in particular that a seat commissioned through Arturo takes that same legacy
registration path.

**Step 3 — phone connected, agent answers from Telegram — NOT COMPLETED**, for two independent causes,
neither of them the notification path's fault. The person the step depends on was not reachable; and
the five-minute paused-router window the step documents is not performable on the host it ran on, where
a service watchdog restarts the router within two minutes of any stop. The path was never exercised, so
this release makes no claim about it either way.

## Start here

1. `docs/BEGINNERS_GUIDE.md`, then the seven-step gate in `docs/GATE.md`.
2. Pick a track from `docs/tracks/README.md` or a `good-first-issue` from the issue list.
3. `CONTRIBUTING.md` for the PR rules (DCO, CI, the four scans).

## Known gaps

Listed as issues (`T1`–`T12`, `G1`–`G20`), seeded from `docs/HACKATHON_ISSUES.md`.
