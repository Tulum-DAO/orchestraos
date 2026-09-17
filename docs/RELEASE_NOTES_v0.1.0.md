# OrchestraOS v0.1.0-hackathon

The first public release, cut for the Tuluminator build-a-thon (Tulum, 2026-09-19/20).

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

## Start here

1. `docs/BEGINNERS_GUIDE.md`, then the seven-step gate in `docs/GATE.md`.
2. Pick a track from `docs/tracks/README.md` or a `good-first-issue` from the issue list.
3. `CONTRIBUTING.md` for the PR rules (DCO, CI, the four scans).

## Known gaps

Listed as issues (`T1`–`T11`, `G1`–`G13`), seeded from `docs/HACKATHON_ISSUES.md`.
