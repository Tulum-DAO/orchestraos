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

**Fixed after this release, on main (more test leftovers to check):** besides the seats above, a test run in an installed checkout could also leave state files in your data dir. Safe to delete if present (your real seats never use these names): `state/agent-state/` files named `a.json`, `b.json`, `c.json`, `root.json`, `worker-1.json`, `ctxguard-victim.json`, `second-brain-dev.json` (only if you have no such seat), `sid-arm-seat.json`, `no-bg-json-seat.json`, `no-such-gemini-seat-zzz9.json` and `live-prewarming.json` / `live-ready.json` / `live-solo.json` / `live-swapping.json`; and the `cv4_write_fence_shadow` table in `state/tasks.db` (a diagnostics table, safe to `DELETE FROM`). A test could also have rewritten `state/.approval-listener-cursor`, a real file: if a phone answer was ever ignored after a test run, delete it; the listener then re-reads the retained answers, and each card still takes one answer only. The test suite now runs against a temp data dir and the stores refuse to write a real one from a test, so this cannot recur. Fixed in PR #396; upgrade or apply that commit if you are on the tag. `scripts/registry-update.py` also used the checkout's `registry.json` when no `REGISTRY_PATH` was set; it now uses the data dir like everything else.

**Fixed after this release, on main:** scripts run directly (not through `orchestra`) with no `ORCHESTRA_DIR` set used a folder named `orchestra` in your home directory as the data dir, a folder nothing else uses: the message router, `msg_store.py`, the rotation and lineage tools, and two Claude Code hooks. They now use your data dir like everything else (`ORCHESTRA_DIR`, then `[data] dir` in `orchestra.toml`, then `~/.orchestra`). Normal launches already set `ORCHESTRA_DIR`, so they are unaffected. If that `orchestra` folder exists in your home directory and you never created it, a direct run made it; look inside before deleting it (messages sent that way are there, not in your data dir). Fixed in PR #399; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** the "While you were away" card on the phone showed a stale "last spoke by voice": the gateway read `state/voice-memory.json`, which nothing writes any more, while Arturo writes `state/arturo/voice-memory.json`. It now serves Arturo's file (the old one only if Arturo's is absent). The card's "crashed" list also counted service panes (a proxy, a dashboard, a bare shell); it now lists only registered seats that crashed. Fixed in PR #428; upgrade or apply that commit if you are on the tag.

**Breaking after this release, on main:** Post-call webhook now requires `ELEVENLABS_WEBHOOK_SECRET`; without it ElevenLabs transcript saving stops (401). Set it in ElevenLabs > Agents > Settings > webhook (HMAC) and in `.env.secrets` (or the environment). `orchestra doctor` shows a WARN row (`arturo:post-call-secret`) and Arturo logs one warning at startup until it is set. Escape hatch while you set it: `ARTURO_POSTCALL_AUTH=log` accepts unsigned pushes and counts what it would have refused. A signed push is accepted once: re-sending the same signed push inside its 30-minute window is refused as a replay.

**Added after this release, on main:** `GET /away` (and `/api/away` for the web dashboard), the data for the redesigned "While you were away" card: your last real action (a card you answered, a voice call, or a chat message you sent), which device it came from when the app said so, what came of it, and what moved since, with a short summary. To know what you sent, the gateway now keeps a small log of your chat sends, `state/operator-actions.jsonl` (the first 140 characters, secret-scanned, readable only by you, capped at 2 MB). `POST /presence {"foreground": true}` records when an app is on screen. A presence report without `viewing` no longer clears which agent you are watching. RFC 0002 has the design. Added in PR #432.

**Added after this release, on main:** GPT-Live, a third Arturo voice vendor (OpenAI). Off until `OPENAI_API_KEY` is set; it is only the voice, and every substantive request still runs through Arturo with the same tool allowlist. Per-call and per-day minute caps bound its cost (docs/ARTURO.md, "GPT-Live").

**Added after this release, on main:** Voice selection for every server-side voice engine (Hume and GPT-Live). One pick per engine, shared by every device, so switching engine and back keeps each pick. The server renders the picker rows (title, group, recommended), and a pick must be one of that engine's voices. Rows keep the v1 `provider` field, and a request with no `?vendor=` still means Hume unless the live engine has its own list, so apps already shipped keep working.

**Renamed after this release, on main:** the Telegram chat id setting is now `ORCHESTRA_TELEGRAM_CHAT_ID` (env, or a line in `<data>/.env.telegram`). The old `SHAW_TELEGRAM_ID` (and `SHAW_TELEGRAM_CHAT_ID` for the sign-in code) is still read, so nothing breaks; new installs should use the new name. The chat id file is now the Telegram plugin's own `<data>/state/telegram/chat-id`; the old `.shaw_chat_id` is still read.

**Removed after this release, on main:** `POST /api/voice/sync-prompts` and the dashboard's **Sync Prompts** button. Nothing in the product shipped the `voice-agent.py` script they ran, so on every install the button failed. No shipped client calls the route.

**Removed after this release, on main:** `GET /api/project-status`. Nothing shipped its `project-status-api.py` script; Command Center now reads `/api/projects` directly, as it already did after every failed attempt. No shipped client calls the route.

**Added after this release, on main:** `orchestra install-unit` installs systemd units so a server brings OrchestraOS back after a reboot: `orchestra.service` runs the supervisor (`orchestra up` in the foreground) and `orchestra-recovery.service` resumes the seats that were running, from their saved sessions. The units are rendered from `deploy/systemd/*.service.template` for your user, checkout and data dir, so nothing is edited by hand; they carry your `PATH` plus `~/.local/bin`, and `install-unit` warns when an enabled agent CLI is not on it. `--dry-run` shows every step and changes nothing. The telemetry daemon's own unit (`--unit telemetryd`) is back for installs that do not run `orchestra up`; installing it next to `orchestra.service` is refused while `[telemetry] enabled` is on, so the daemon never runs twice. Install guide: "Start on boot (systemd)". Added in PR #403; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** a new seat no longer lands on a signed-out Claude when another runtime is signed in. On a Linux machine signed in to Codex only, with the `claude` CLI installed but never logged in, the starter team put `gm` on Claude, which stopped at Claude Code's first-run screen, so the project lead and worker were never made. When Claude is first in `[runtimes] enabled`, a new seat with no `--runtime` now gets the first other enabled runtime whose login file shows it signed in, but only when Claude is definitely signed out: not macOS (the login is in the Keychain), no `~/.claude/.credentials.json`, no `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` or Bedrock/Vertex setting in the environment or in Claude's `settings.json` (including `apiKeyHelper`), and `claude auth status` reports `loggedIn: false` within 5 seconds. A timeout, an error or an unclear answer keeps Claude, and so does a machine where nothing else is signed in. The command runs only in that case, so a Claude machine starts seats exactly as before. Seats already in the registry keep their runtime. Fixed in PR #404; upgrade or apply that commit if you are on the tag.

**Added after this release, on main:** `orchestra doctor` has two more rows for Codex: the installed `codex` version against the one this release was proven on (0.153.4; a different version warns with the `npm install -g @openai/codex@0.153.4` pin and never blocks), and an honest `hooks:codex` note that Codex has no hook layer here yet, so mail, idle and menus are read from the seat's screen and there is no Stop event. The Claude-only `DISABLE_AUTOUPDATER` advice no longer appears on a box without claude. Added in PR #431.

**Fixed after this release, on main:** messages now reach Codex and Gemini seats. The router waited for Claude's `❯` prompt before delivering, so mail to a Codex (`›`) or Gemini/agy (`>`) seat stayed queued forever. A seat whose registry row has a `runtime` field (every seat `orchestra spawn` or `orchestra agent create` makes) is now judged by its own CLI's prompt. Rows without a `runtime` field are unchanged; add `"runtime": "codex"` (or `"gemini"`) to such a row to fix it. `ROUTER_DECLARED_RUNTIME_DISABLED=1` restores the old behaviour. For Gemini/agy seats the router still never clears a stuck line of its own from the composer (it logs `CLEANUP skipped` and redelivers once a person clears it); `ROUTER_AGY_CLEANUP_ARMED=1` turns that on. Fixed in PR #406; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** a stock install now runs on the identity database, the path every seat writer, boot recovery and the reconciler are built on. Before, `orchestra init` left the identity store unarmed, so seats stayed `provisioning`, their session ids were never saved, the reconciler refused to run and a reboot brought back no seats. `orchestra init` now arms a new data dir; an existing one is armed once with `orchestra identity arm` (backup, freeze writers, import, a zero-drift check, arm, start the regenerator; `--dry-run` to preview), and `orchestra identity status` shows the state. `orchestra up` now keeps the regenerator running (it was started only once, at boot). The starter wrote `machine: "local"` for seats on this host, which boot recovery and the reconcilers skip as remote; it now writes `vps` like `spawn-agent.sh`, and `identity arm` rewrites existing `local` seats. Migration also keeps a seat's own `runtime` instead of guessing `claude` when it has no session yet. Fixed in PR #407; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** boot recovery could never resume a seat on a stock install. `scripts/agent-recovery.sh` checks every seat's runtime with `scripts/recovery_runtime.py` before resuming it, and that file was never published, so every resume was refused with `can't open file .../recovery_runtime.py`. It is now in the repo with its tests: Claude and Gemini seats resume automatically; Codex seats still need the full C-R7 gate and are otherwise left for a manual resume, as before. Fixed in PR #408; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** with the identity store armed (#407), a seat that `orchestra starter`, `orchestra agent` or `orchestra init --demo` wrote to `registry.json` vanished at the next projection, within about 30 seconds, often before `spawn-agent.sh` had registered it. A fresh `init --demo` showed no agents at all. The CLI now records each seat it writes in the identity database too: just that one row, never the whole file. Its registry entry keeps the status the CLI wrote, and the database stores it as `parked` until its pane is live. Fixed in PR #410; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** the dashboard's notification bell now sits in line with the other top-bar icons on an agent's page (it was drawn about 16px lower), and its red count no longer moves it; on phones it sits inside the top bar in both themes. In Chat mode, on an agent's page and in the Agents-page panel, you can scroll back through the whole loaded conversation with the mouse wheel, trackpad or touch; before, anything above the first screen could not be reached. New messages still keep the view at the bottom when you are already there, and leave it alone when you have scrolled up to read. Dev mode is unchanged. Fixed in PR #405; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** seats now come back after a reboot. Nothing on a stock install ever recorded the seats' sessions in `state/agent-sessions.json` or moved them off `provisioning`, so `orchestra-recovery.service` found nothing to resume and the box came back with no seats. `orchestra install-unit` (the default `--unit stack`) now also installs `orchestra-reconciler.timer`, which runs the identity reconciler every 15 minutes, the same job the reference install runs from cron: a seat with a live pane is marked `online`, and the session list is rewritten. The reconciler also no longer retires a seat that has never been `online`; a still-`provisioning` seat with no pane is left alone. Re-run `orchestra install-unit` to add the timer. Fixed in PR #406; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** a menu on a Codex seat's screen now shows on the dashboard and phone, marked "answer it in the terminal". Before, it showed nowhere (the parser only knew Claude's and Gemini's menu cursor, and a fallback for Codex imported a module that was never shipped and hid the error). Codex menus cannot be answered from a device yet: no card is made for them and every answer path refuses with no keys sent, until a Codex screen has a real capture and a tested answer. Fixed in PR #397; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** a Codex seat now starts on the first try. Codex asks "Do you trust the contents of this directory?" even with `--yolo`; the seat's first instruction landed on that question, Codex quit, and the seat was a bare shell. Spawn now marks the seat's own directory trusted in Codex's `config.toml` before launch (only that directory, never your home directory; your other settings are kept, and the original file is backed up as `config.toml.orchestra-backup` the first time). A new seat with no `--runtime` now gets the first enabled runtime that is installed and logged in on this machine, so a Codex-only machine gets a Codex seat; with Claude installed nothing changes. A spawn on a runtime that is not installed or not logged in is refused with the runtime's name and the reason. Codex seats also start with Codex's "Update available" question turned off (its default answer was "Update now", which the seat's first Enter would have chosen): a seat's `codex` command line now carries `-c check_for_update_on_startup=false`. Nothing is written to `~/.codex/config.toml` for this, and `codex` you start yourself still checks for updates; set `ORCHESTRA_CODEX_UPDATE_CHECK=1` to keep the check on for seats too, and the first instruction now waits the few seconds Codex spends starting MCP servers instead of being dropped. The "mail it" line printed after a spawn now works when pasted from any directory. Fixed in PR #394; upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** adaptive model selection now works on first spawn. `spawn_adopt` looked for the quota oracle under the data dir, where it never exists, so selection never ran. A seat spawned with no runtime and no model now gets the oracle's pick, and the pick is logged. A respawn whose model is unknown still refuses, and an oracle that cannot load refuses the spawn with a plain error. Fixed in PR #377; upgrade or apply that commit if you are on the tag.

**Security fix after this release, on main:** the voice service's post-call webhook now accepts only a plain conversation id and refuses anything else before using it. Fixed in `ec9456c` (PR #369); upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** every fresh install of v0.1.0 had an agent chat window that could not send — the inject path read the gateway token from a legacy path that `orchestra init` never writes. Fixed in `1ffb179` (PR #109); upgrade or apply that commit if you are on the tag.

**Fixed after this release, on main:** a Codex seat's context % is now its own. Codex keeps every subagent's session file open in the seat's process, and the reader took whichever file it listed first, so a seat with subagents could show a subagent's context %. The reader now counts only a session whose first record proves it is the seat's main thread (Codex marks subagent sessions there; a session whose first record cannot be read is never counted), and shows `--` rather than guess when a process holds more than one; the identity resolver applies the same rule.

## CI note for this release

GitHub Actions was refused on billing from 2026-09-19 09:52Z; PRs #38–#40 merged on bare-box + CI-identical local evidence (council PASS at `c6b278c`; local run rc 0).
