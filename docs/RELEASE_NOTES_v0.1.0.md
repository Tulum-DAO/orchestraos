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

The seven-step gate (`docs/GATE.md`) was run end to end against this release. **The commit it ran
against and the commit tagged here are NOT the same, and they do not differ only in documentation:**
product code was merged after the gate ran, at the operator's direction, so that the release matches the
build he tested. **That code has not been through the seven-step gate.** The annotated tag names both
commits and lists every file in the difference, generated at tag time — read it there rather than here,
because a list written into prose goes stale the moment anything else lands. Five steps passed. Two did not, for unrelated reasons, and **neither is a defect in what you are
installing** — one of them is the system refusing to do something dangerous.

**Steps 1, 2, 4, 5 — install and doctor, spawn an agent, agent-to-agent mail, a decision card that
resumes the agent — PASSED, clean.** A stranger can install this and run it: `doctor` exited 0, every
process came up, the dashboard served.

**Step 3 — push notification to a phone — was RUN AND UNMET. It was not skipped.** The runner paused
the notification router, opened the documented window and sent the ping. Nothing arrived, for two
independent reasons, and neither is the notification path's fault. First, the step depends on a person
being reachable and they were not. Second — and this one will affect you if you reproduce it —
**Telegram's Bot API permits exactly one `getUpdates` poller per bot token, and stopping the orchestra
telegram service is not sufficient to release it, because your own supervisor restarts the service
under you.** The documented five-minute paused-router window is therefore not performable as written.
**The workaround is to disable the plugin in `orchestra.toml` (`enabled = false`) and do a full
`orchestra down` / `orchestra up` without the token, rather than killing the process** — that is what
finally cleared it here. The path was never exercised, so we are not claiming it works and we are not
claiming it is broken. We know nothing about it either way, which is why it is reported as unmet
rather than passed or failed.

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

**Fixed after this release, on main (check your registry):** running the test suite in an installed checkout (one with `orchestra.toml`) could write test seats into your REAL data dir, because `scripts/orchestra-env.sh` overrode the data dir a test had chosen. If you ever ran the suite there, look for the seats `helper-a` and `probe-` followed by 8 hex characters:
`python3 -c "import json,os; d=os.path.expanduser('~/.orchestra'); print([a for a in json.load(open(d+'/registry.json'))['agents'] if a == 'helper-a' or a.startswith('probe-')])"` (use your `[data] dir` if it is not `~/.orchestra`).
To remove them: `orchestra down`, back up `registry.json`, delete those keys from its `agents`, and delete `<data>/memory/<seat>/` for each. Then `orchestra up`. A spawn in a checkout with no `[data] dir` also no longer falls back to the checkout itself: the data dir is `~/.orchestra`, as everywhere else.

**Breaking after this release, on main:** Post-call webhook now requires `ELEVENLABS_WEBHOOK_SECRET`; without it ElevenLabs transcript saving stops (401). Set it in ElevenLabs > Agents > Settings > webhook (HMAC) and in `.env.secrets` (or the environment). `orchestra doctor` shows a WARN row (`arturo:post-call-secret`) and Arturo logs one warning at startup until it is set. Escape hatch while you set it: `ARTURO_POSTCALL_AUTH=log` accepts unsigned pushes and counts what it would have refused. A signed push is accepted once: re-sending the same signed push inside its 30-minute window is refused as a replay.

**Added after this release, on main:** GPT-Live, a third Arturo voice vendor (OpenAI). Off until `OPENAI_API_KEY` is set; it is only the voice, and every substantive request still runs through Arturo with the same tool allowlist. Per-call and per-day minute caps bound its cost (docs/ARTURO.md, "GPT-Live").

**Added after this release, on main:** Voice selection for every server-side voice engine (Hume and GPT-Live). One pick per engine, shared by every device, so switching engine and back keeps each pick. The server renders the picker rows (title, group, recommended), and a pick must be one of that engine's voices. Rows keep the v1 `provider` field, and a request with no `?vendor=` still means Hume unless the live engine has its own list, so apps already shipped keep working.

**Renamed after this release, on main:** the Telegram chat id setting is now `ORCHESTRA_TELEGRAM_CHAT_ID` (env, or a line in `<data>/.env.telegram`). The old `SHAW_TELEGRAM_ID` (and `SHAW_TELEGRAM_CHAT_ID` for the sign-in code) is still read, so nothing breaks; new installs should use the new name. The chat id file is now the Telegram plugin's own `<data>/state/telegram/chat-id`; the old `.shaw_chat_id` is still read.

**Removed after this release, on main:** `POST /api/voice/sync-prompts` and the dashboard's **Sync Prompts** button. Nothing in the product shipped the `voice-agent.py` script they ran, so on every install the button failed. No shipped client calls the route.

**Removed after this release, on main:** `GET /api/project-status`. Nothing shipped its `project-status-api.py` script; Command Center now reads `/api/projects` directly, as it already did after every failed attempt. No shipped client calls the route.

**Fixed after this release, on main:** a Codex seat now starts on the first try. Codex asks "Do you trust the contents of this directory?" even with `--yolo`; the seat's first instruction landed on that question, Codex quit, and the seat was a bare shell. Spawn now marks the seat's own directory trusted in Codex's `config.toml` before launch (only that directory, never your home directory; your other settings are kept, and the original file is backed up as `config.toml.orchestra-backup` the first time). A new seat with no `--runtime` now gets the first enabled runtime that is installed and logged in on this machine, so a Codex-only machine gets a Codex seat; with Claude installed nothing changes. A spawn on a runtime that is not installed or not logged in is refused with the runtime's name and the reason. Codex seats also start with Codex's "Update available" question turned off (its default answer was "Update now", which the seat's first Enter would have chosen), and the first instruction now waits the few seconds Codex spends starting MCP servers instead of being dropped. The "mail it" line printed after a spawn now works when pasted from any directory. Fixed in PR #394; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** adaptive model selection now works on first spawn. `spawn_adopt` looked for the quota oracle under the data dir, where it never exists, so selection never ran. A seat spawned with no runtime and no model now gets the oracle's pick, and the pick is logged. A respawn whose model is unknown still refuses, and an oracle that cannot load refuses the spawn with a plain error. Fixed in PR #377; upgrade or apply that commit if you are on the tag.

**Security fix after this release, on main:** the voice service's post-call webhook now accepts only a plain conversation id and refuses anything else before using it. Fixed in `ec9456c` (PR #369); upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** every fresh install of v0.1.0 had an agent chat window that could not send — the inject path read the gateway token from a legacy path that `orchestra init` never writes. Fixed in `1ffb179` (PR #109); upgrade or apply that commit if you are on the tag.

## CI note for this release

GitHub Actions was refused on billing from 2026-09-19 09:52Z; PRs #38–#40 merged on bare-box + CI-identical local evidence (council PASS at `c6b278c`; local run rc 0).
