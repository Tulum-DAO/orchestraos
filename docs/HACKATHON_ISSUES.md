# Hackathon issues — ready to paste

Seed these as GitHub issues on Friday: one issue per `##` heading, the heading is the
title, the block under it is the body, labels are on the first line. Tracks are the
weekend's build lanes (Sat–Sun 2026-09-19/20); good-first-issues are small and real.
Paths are as of the public tree; re-grep before starting.

---

## T1 · Device pairing replaces the built-in app token
`labels: track, size:S/M, ios, gateway`

**Problem.** The iOS app bakes the gateway base URL and bearer token into the build; there
is no login.

**Design.** `POST /pair/start` on the gateway mints a 6-digit code + QR (`orchestra pair`
prints both in the terminal, 5-minute TTL). The app's first screen scans or enters it.
`POST /pair/claim {code, device_name}` returns a per-device bearer + the gateway base URL;
the app stores both in the Keychain; Settings lists paired devices with revoke. The
existing bearer path stays for the watch until it is migrated.

**Acceptance.** Fresh app install → scan → the approvals list loads; revoking the device
makes its next request 401.

## T2 · Zero-key Arturo brain on the CLI you already have
`labels: track, size:M, arturo`

**Problem.** Arturo's conversational turn is a Gemini API call (needs an API key).

**Design.** A `Brain` interface in `services/arturo` with two implementations: `ApiBrain`
(today's path, BYO key) and `RuntimeBrain`, which shells the authed CLI from the runtime
catalog (`claude -p`, `gemini`, `codex exec`) with the same system prompt + tool schema and
streams text back. Selection by config `arturo.brain = auto|api|runtime` (auto = api when a
key is present, else runtime). Manager-seat injection unchanged.

**Acceptance.** No API keys in config, `orchestra doctor` shows one authed CLI, a text
message on Arturo home gets a reply in under 5 s, and "commission an agent to X" creates a
seat and files a msg_store row.

## T3 · On-device voice tier (iOS)
`labels: track, size:M, ios, arturo`

**Problem.** Voice needs ElevenLabs / Cartesia / Gemini Live keys.

**Design.** `LocalVoiceEngine: VoiceEngine` using `SFSpeechRecognizer` (already in the app
for captions) for STT with on-device recognition when available, and `AVSpeechSynthesizer`
for TTS; text goes through the existing gateway text path (T2 brain); the reply text is
spoken; the voice pill/panel and partials render unchanged. Engine chosen by the gateway's
`/voice/vendor` answer (`local` when no vendor key).

**Acceptance.** A fresh install with no vendor keys holds the orb → speaks → hears the
reply; the transcript card posts like any other call.

## T4 · Arturo home + conversation-first onboarding (web + iOS)
`labels: track, size:L, ui, arturo`

**Design.** Reference set in `design/references/`. Shell = one thin header (sidebar glyph ·
"Arturo · <model> ⌄" · brain glyph), dark canvas with a provider-accent glow behind the
composer, empty state = mark + greeting, two-row composer (field; `+` upload · model chip ·
mic · adaptive voice/send circle), a model bottom sheet (provider logos → grouped models
with one-line descriptors, effort row), a Brain sheet (Facts / Second Brain /
Commitments). An always-available pill on every other page (iOS: tab-bar orb + voice
surface; web: floating pill) carrying the context record `{route, entityKind, entityId,
hint}`. Onboarding = Arturo's first thread: name → pair (T1) → runtime detect → optional
voice upgrade → "what should your first agent do?" → spawn. No forms.

**Acceptance.** A fresh install reaches a spawned first seat through the conversation only;
a phone-width capture matches the reference notes (no legacy chrome, one composer, no
inject buttons).

## T5 · Manager-seat-optional first run
`labels: track, size:M, core`

**Problem.** The front page and routing assume a manager (`gm`) seat exists.

**Design.** Arturo files commissions to msg_store under a `gm` lineage even when no gm
exists (rows park, visible in Inbox); `orchestra spawn gm` (or Arturo, when a second seat
appears) creates the manager, which drains the parked rows on first effect. The router
treats a missing target as "park", never dead-letter.

**Acceptance.** Fresh install, one worker seat, three commissions → all three visible as
parked; spawning gm delivers them in order.

## T6 · Telegram / WhatsApp as plugins
`labels: track, size:S, notify`

**Design.** The bridges move to `plugins/telegram` and `plugins/whatsapp` with their own
config section and a `notify` interface (`send_text`, `send_photo`, `send_card`); core
depends only on the interface; `orchestra doctor` lists enabled plugins.

**Acceptance.** Core tests pass with both plugins absent; enabling telegram with a bot
token delivers a test message.

## T7 · Tab restyle to the reference set (iOS + web)
`labels: track, size:M, ui`

Approvals, Agents, Projects, Pipeline rebuilt to the same visual language as T4: dark
canvas, one header, grouped cards, native sheets. Keep every existing card contract
(approval, menu, questionnaire, commitment, voice-call).

**Acceptance.** Headless captures at 390 px for each tab reviewed against the notes; no
card type regresses (fixture harness — `orchestra init --demo` seeds one of each).

## T8 · Push without ntfy
`labels: track, size:S/M, notify, ios`

**Design.** `notify` backends: `apns` (bundled key path in config, device tokens from T1
pairing), `ntfy` (today, see `docs/REFERENCE_INSTALL.md` §4), `none` (in-app polling).

**Acceptance.** An approval card raises an APNs push on a paired device with no ntfy
server configured.

## T9 · Installer + doctor + supervisor — DONE, hardening welcome
`labels: track, size:S, install`

Shipped: `orchestra init` / `doctor` / `up` / `down` / `status`, Dockerfile + dev
container, `init --demo`. Proven by an outsider on a clean container in under 4 minutes.
Remaining: `doctor` rows for plugins, a `make image` step for the VPS snapshot
(`docs/INSTALL.md` "Machine image"), and the templated systemd unit (issue 3 below).

**Acceptance.** Clean Ubuntu VPS → `orchestra init && orchestra doctor && orchestra up` →
dashboard reachable, one seat spawnable, in under 30 minutes by someone new (already
true), plus the three remaining items.

## T10 · Docs and tutorials
`labels: track, size:S, docs, good-first-issue`

`docs/ARCHITECTURE.md` front page (seat, generation, gateway, approvals, msg_store,
memory, Arturo, rotation), "your first agent in 15 minutes", "how a decision reaches your
phone", "how memory survives a rotation".

**Acceptance.** A newcomer follows the 15-minute tutorial without asking a maintainer.

## T11 · Autonomous blue-green rotation on every runtime (core, default ON)
`labels: track, size:L, core, rotation`

The driver ships default ON: cron beat, arming, prewarm, readiness (ingest + composer
probe), quota gate, swap, verify. Known portability gaps: a Claude-specific liveness gate,
a Codex-specific credit gate, the tmux-on-one-host assumption, capture-based probes. Track
= make one full lossless rotation run on a clean install for each runtime with fixture
transcripts, then a real one.

**Acceptance.** `orchestra rotate --auto <seat>` completes green → promote → verify on a
clean install for claude; gemini and codex documented or done.

## T12 · Gauntlet mode: a critic loop for creative work
`labels: track, size:M, core, skills, ui`

**Problem.** A builder reports done the moment it thinks it is; creative output (3D
scenes, video, UI, image sets) ships as programmer art because nothing forces a second
opinion before the operator sees it.

**Design.** A skill declares `gauntlet: required` in its front-matter. After each
attempt a separate critic seat (art-director persona, writes no code) takes its OWN
captures from several viewpoints/zooms, runs the checks (console errors, perf budget,
reference contract) and scores 0–10 against references (10 indistinguishable, 8.5 AAA
with nits, 7 good indie, 5 programmer art). Pass = ≥ 8.5 with zero errors; below that
the builder gets the ranked issue list and goes again. The operator sets the **loop
count** per skill in the Skills-page settings window, and **each loop is its own dive
with its own parameters** (viewpoints, references, checks, pass bar, critic model).
Levels `off/light/standard/brutal` are presets. Loops exhausted → card: accept best /
more loops / stop. Full design: `docs/tracks/12-gauntlet-mode.md`.

**Acceptance.** Demo creative skill at `standard` (3 loops): weak attempt scores ≤ 6
with a ranked list, second attempt ≥ 8.5 → completion released at loop 2/3; `brutal`
with loop 1 bar 9.5 fails the same attempt; exhausted loops raise the operator card.

## T13 · Memory: see it, prune it, then teach it to extract
`labels: track, size:S/M, core, ui, memory`

**Problem.** A seat's memory is `$ORCHESTRA_DIR/memory/<lineage-id>/` (index + one-fact
files) and nobody looks in it — there is no page. `api/src/routes/memory.ts` (mounted at
`/api/memory`) reads the wrong stores: the Claude CLI's private auto-memory dir (line 101)
and a private `OMNI_DIR` layout (lines 82, 196-229) that `orchestra init` never creates; no
endpoint touches the per-seat dir, nothing in `dashboard/src` calls it, and it has no test.
Because nobody looks, nothing prunes (the index has no budget and is silently truncated at
boot once it outgrows the prompt) and nothing extracts (a fact is remembered only if the
seat writes it the moment it learns it).

**Design.** Leg 1 (S): repoint `memory.ts` at the real store — `GET /api/memory/seats`
(per-lineage counts, index bytes vs. a `[memory] index_budget_bytes` budget, orphans,
dangling lines), `/seats/:lineage` (parsed index + frontmatter), `/search` over the real
dirs — and a `dashboard/src/pages/Memory.tsx` with a seats table (budget bar), a seat
drawer (index → file body), cross-seat search, and a `memory` doctor row. Leg 2 (S/M):
`scripts/memory_prune.py` (dry-run default; regenerates the index from the files, moves
duplicates aside, never deletes) and an explicit over-budget warning in the boot prompt
instead of silent truncation. Leg 3 (M, stretch): read the seat's own CLI transcript, have
its own runtime propose candidate one-fact files into `.candidates/`, accept/discard from
the page — nothing reaches the index without an accept. Full design:
`docs/tracks/13-memory.md`.

**Acceptance.** Clean install, gate step 7 → `/memory` lists `hello` with 1 file, green
bar, the fact readable in the drawer. Pad the index past budget → red row, doctor WARN
naming `hello`, next generation's boot prompt names the prune command. `memory_prune.py
hello --apply` regenerates the index from the one real file; nothing deleted.

---

# Good first issues

## G1 · Operator-naming: rename the remaining operator-named identifiers
`labels: good-first-issue, size:S, scrub`

All string literals, prose and the tenant default are already generic. What remains are
~48 identifiers (variables, functions, constants) found via
`git grep -ciE '\bshaw\b'`. Known locations: `scripts/lineage_daemon/wal/checkpoint_producer.py`
(local var + `operator_attached_fn` parameter, and its supervised test double),
`api/src/services/state-reader.ts` (`getShawPresence`), `dashboard/src/components/JarvisPanel.tsx`
and `dashboard/src/pages/ChatHistory.tsx` (`isShaw`), and every reader of the
`SHAW_TELEGRAM_ID` env var name (`brief.py`, `scripts/approval_config.py`, `scripts/tg-notify.sh`)
— that one is a breaking rename for existing installs, so rename with a documented
migration note or keep the env var name and mark it historical. Do NOT touch the
`systemd User=` docstrings in `telemetryd.py` / `multiplexer.py` / `realtime/daemon.py`
(they describe a real install-time username; see G3).

**Acceptance.** `git grep -ciE '\bshaw\b'` returns 0 outside the README maintainer
mention; every CI job still green (pure rename, no behavior change).

## G2 · Test isolation: the order-dependent failures
`labels: good-first-issue, size:M, tests`

CI runs the python suites per package because a few tests fail only in one full-tree
process. Already fixed: the arturo bearer family (it was pytest configfile scoping — a
nested `pytest.ini`; now a root `pytest.ini`), `test_staleness_15m` (stale assertion).
Still order-dependent, all green alone: `scripts/test_approval_get_qnr.py` ×2 (CLI
subprocess against the default `tasks.db` path), `scripts/lineage_daemon/boundary_delivery_test.py::test_REAL_resolver_tuple_contract_no_mock`,
`services/arturo/test_msg_store_envelope.py::test_agent_targets_are_never_touched`,
`scripts/test_env_isolation_belt.py::test_b_sees_a_clean_environ` — the last one is the
tell: something session/module-scoped, or collection-time code, writes `HOME` /
`ORCHESTRA_DIR` after the per-test snapshot in the root `conftest.py`.

**Note on flakes.** `pytest-randomly`, if installed in your environment, shuffles order
every run and makes a few `scripts/lineage_daemon/wal` tests (green_liveness ×2,
test_green_quota_gate, completion_e2e_zero_override) appear and disappear between runs.
Run with `-p no:randomly` when sizing this list; those four are order flakes, not
regressions.

**How.** Baseline the empty set first (run the victims alone), then bisect collection
order; do not trust a bisect that never tried keep-none. Grep for `scope="session"` /
`scope="module"` fixtures touching `os.environ` or `Path.home`, and module-level code
under `scripts/lineage_daemon` and `services/arturo`.

**Acceptance.** `python3 -m pytest -q` over the whole tree in one process is green, and
the CI matrix can collapse to one job (optional).

## G3 · systemd unit templating for the telemetry daemon
`labels: good-first-issue, size:S, install`

`deploy/systemd/orchestra-telemetryd.service` was excluded from the public tree because
it hardcodes the install user and checkout path; `scripts/lineage_daemon/unit_file_test.py`
tests it and was excluded with it. Produce a `.service.template` with placeholders for the
user and `ORCHESTRA_DIR`, and an `orchestra install-unit` (or `make`) step that
substitutes them; then re-add the unit and its test.

**Acceptance.** A fresh install enables the unit without hand-editing it, and
`unit_file_test.py` passes against the substituted file.

## G4 · Generic example timezone in test fixtures
`labels: good-first-issue, size:XS, scrub`

Six real city/timezone mentions remain inside test fixture data and comments that
illustrate timezone conversion (the display code is config-driven already). Replace them
with a generic example city/zone for consistency with the rest of the scrub.

**Acceptance.** `git grep -ci 'tulum\|cancun'` returns 0.

## G5 · Data-dir defaults: 88 non-test files still default to a private checkout path
`labels: good-first-issue, size:S each, install`

Each of these resolves its DATA dir (or, worse, a code path) from
`os.environ.get("ORCHESTRA_DIR", os.path.expanduser("~/scripts/agent-orchestra"))` or
the shell/TS equivalent. They work under `orchestra up` and `scripts/orchestra-env.sh`
(both export `ORCHESTRA_DIR`) and silently point at a directory that does not exist when
run standalone. Fix shape, one file per PR: read the data dir through one helper —
`services/config.py` (`load_config().data_dir`) for Python, `api/src/lib/config.ts`
(`loadConfig().dataDir`) for TS, `scripts/orchestra-env.sh` for shell — and fail LOUD
(`ConfigError`) when neither the env nor `orchestra.toml` provides it. Code paths resolve
from the checkout (`ORCHESTRA_ROOT` / `__file__`; see `cron_beat.code_path`,
`watch_gateway.CODE_SCRIPTS_DIR`, `resolveDetectorPath`), never from the data dir.

**Files.** `git grep -nE 'scripts/agent-orchestra' -- ':!*_test.py' ':!*/tests/*' ':!*.test.ts'`
(88 hits at the time of writing: `api/src/routes/*.ts`, `api/src/services/*.ts`,
`msg_store.py`, `message_bus.py`, `scripts/*.py`, `scripts/continuity/*`,
`scripts/identity_store/*`, `scripts/lineage_daemon/*`, `services/arturo/*.py`,
`spawn-agent.sh`, `unified_log.py`).

**Acceptance.** That grep returns 0 hits and the touched script still runs under
`orchestra up` (doctor + one beat tick clean).

## G6 · `orchestra doctor`: plugin and push rows
`labels: good-first-issue, size:S, install`

Doctor knows CLIs, ports, config keys, builds, the rotation beat and foreign tmux sessions.
Add rows for the notify channel (`[notify] channel` + its credentials present), the active
push backend (`[notify] push_backend` + its credentials, T8), and enabled plugins (T6). Not
ntfy reachability — ntfy is legacy and T8 replaces it; a doctor row for it would point a new
installer at a server they do not need.

**Acceptance.** Each row OK / MISSING / INFO with a one-line remedy; tests in
`orchestra_cli/tests/test_doctor.py` with fake probes.

## G7 · Dashboard empty states
`labels: good-first-issue, size:S, ui`

With `orchestra init` (no `--demo`) every page is blank. Give Agents, Approvals,
Questionnaires and Inbox an empty state that says what fills it (the exact command from
`docs/INSTALL.md`).

**Acceptance.** Fresh install, each page shows the hint; `init --demo` replaces it with
the fixtures.

## G8 · A `/api/health` row in INSTALL's smoke check for every service
`labels: good-first-issue, size:XS, docs`

`/api/health` exists for the api. Add equivalent lightweight endpoints (or document the
existing ones) for the gateway and the dashboard proxy, and one `orchestra status --probe`
that hits all three.

**Acceptance.** `orchestra status --probe` prints one OK line per running service.

## G9 · `spawn-agent.sh` should seed the identity store (hand-registered seats cannot rotate)
`labels: good-first-issue, size:S, rotation`

`orchestra spawn <seat>` registers the seat **and** seeds its lineage (generation 1) in the
identity store, so `orchestra rotate <seat>` works. The lower-level path still documented in
INSTALL — `scripts/registry-update.py <seat> ...` then `./spawn-agent.sh <seat> --task ...` —
registers the seat but seeds no lineage, and rotation refuses it with
`no authoritative generation ... seed the seat via the identity store (adopt_identity/register)`.
Found by the docs-only gate run on 2026-09-17 (GATE.md now teaches `orchestra spawn` only).
A seat commissioned through Arturo home (`POST /api/arturo/text` → its `spawn_agent` tool) takes
that same legacy path without the operator typing any spawn command, so an Arturo-commissioned
seat cannot be rotated either.

Fix: make `spawn-agent.sh` (or `registry-update.py`) call the same identity seeding
`orchestra_cli/seats.py` does when the seat has no lineage yet — idempotent, never inventing a
generation number for a seat that already has one. Ruled to stay as-is until after the
hackathon so the fleet's own spawn path is untouched that week.

**Acceptance.** `registry-update.py hello ...` + `./spawn-agent.sh hello --task ...` then
`orchestra rotate hello --synthesize` proceeds to grading (no identity refusal); a seat that
already has a lineage is unchanged after a respawn.

## G10 · `state/agents/<seat>-gN.json` carries the wrong generation after a rotation
`labels: good-first-issue, size:XS, rotation`

After `orchestra rotate hello --synthesize` promoted gen 2, the successor itself noticed
`state/agents/hello-g2.json` still said generation 1 while `registry.json` and the identity
store said 2 (sandbox run, 2026-09-17). The flat per-seat state file is a projection; find
where the promotion writes it and carry the generation through.

**Acceptance.** After a promotion, `state/agents/<seat>-g<N>.json`, `registry.json` and the
identity store agree on the generation; a test rotates a seat in a tmp data dir and asserts it.

## G11 · Generic seats have no `prompts/<seat>.md`; the boot prompt says they should
`labels: good-first-issue, size:XS, docs`

`orchestra spawn hello` works with no `prompts/hello.md` (the seat's instructions come from
the generated `/tmp/agent-init-<seat>.md`), but the baton and boot text still name
`prompts/<seat>.md` as "its role prompt", and a fresh successor spends a turn discovering the
file does not exist. Either ship a generic `prompts/_seat-default.md` that `orchestra spawn`
copies to `prompts/<seat>.md` when none exists, or stop naming the file when it is absent.

**Acceptance.** A generic seat's baton and init text never point at a file that is not on disk.

## G12 · `rotate_agent.py` false-negative "promotion inject NOT verified" warning
`labels: good-first-issue, size:XS, rotation`

After a promotion `rotate_agent.py` prints `promotion inject to '<seat>' NOT verified
committed — the prompt may be sitting in the composer; press Enter in the pane` even when
the inject landed (a pane capture shows the prompt submitted and the seat working). Seen on
two consecutive gate runs on 2026-09-17; cosmetic — nothing is wrong with the rotation. The
verify step probably reads the pane before the CLI redraws (see the composer-probe note in
docs/ROTATION.md). Re-capture after a short delay, or check the seat's hook/state file for a
new turn, before warning.

**Acceptance.** A promotion whose inject visibly landed prints no warning; a real unsubmitted
composer still does.

## G13 · `/api/agents` keeps stale successor alias rows after a promotion
`labels: good-first-issue, size:XS, dashboard`

After `orchestra rotate hello` promotes `hello-g2` to `hello`, `/api/agents` still lists the
retired alias rows (`hello-g2`, later `hello-g3`) with `alive: false` next to the canonical
`hello` and the parked predecessor `hello-gen2`. Harmless, but the Agents page shows ghosts.
Either drop alias rows from the registry projection once promoted, or hide `alive: false`
alias rows whose canonical seat is live.

**Acceptance.** After a rotation the Agents list shows the canonical seat and its parked
predecessor only; a test rotates in a tmp data dir and asserts the projection.

## G15 · Arturo home during `orchestra up` boot says "HTTP 502" instead of "still starting" — FIXED
`labels: good-first-issue, size:XS, ui, arturo`

Open the home page while the supervisor is still bringing Arturo up and the first turn
fails with `I could not reach my brain: HTTP 502`. The proxy is simply not listening yet.
`dashboard/src/lib/arturo.ts` (`arturoHealth` / `arturoText`) should map a 502/503 or a
connection error during the first ~60s after page load to a friendly "still starting — I'll
retry in a few seconds" state and retry with backoff, instead of surfacing the status code.

**Acceptance.** Loading the page mid-boot shows the starting state, then the greeting once
`/api/arturo/health` answers `ok`; no raw HTTP code reaches the user.

Fixed on main (see git log for "G15"): `dashboard/src/lib/arturo.ts` `isStarting()` treats any
502/503/504 or a failed fetch as the boot window; `waitForArturo()` polls `/api/arturo/health`
with 1/2/3/5 s backoff (90 s cap); the home shows "Arturo · starting…" + the starting line, and a
turn sent mid-boot says "Still starting — I will retry in a few seconds." then retries itself
once health is ok. Proven by effect on a container with the stack down, then `orchestra up`.

## G14 · "Check again" after a CLI login kept saying "installed but not logged in" — FIXED
`labels: bug, size:XS, arturo, fixed`

Fixed on main (see git log for "G14"): the onboarding tap now POSTs
`/api/runtimes/available/refresh` (busting the 300s in-process cache) and Arturo's `/health`
re-selects a brain that was `none` at boot once a CLI is authed. Kept here so attendees who
hit it on an older clone know it is a one-line `orchestra upgrade` away.

## G16 · Terminal action bar: no key may suspend the agent (^Z → ^U, confirm ^C) — FIXED on web, OPEN on iOS/watch
`labels: bug, size:S, ui, ios, harness`

On 2026-09-17 the operator tapped the **^Z** button on the terminal action bar and the agent
CLI was suspended (`SIGTSTP`, process STAT `T`: alive, never scheduled). Nothing in the fleet
noticed; the seat looked "up" to every pid-based check. Operator's words: *"REMOVE ^Z from the
button set and replace it with ^U (clear input). Add an 'Are you sure?' modal for ^C."*

Fixed on main for the web harness: `api/src/lib/special-keys.ts` is the one key policy
(`ctrl-z` → HTTP 400 with the reason; `ctrl-u` → `C-u`), `dashboard/src/components/ActionBar.tsx`
shows **^U** where **^Z** was and asks "Are you sure?" before **^C**.

**Still open — the native clients.** The iOS/watch terminal view keeps its own key bar. Apply
the same three rules there (no ^Z, ^U present, ^C confirms), and never send a raw `0x1A`.

**Acceptance.** `POST /api/agents/:id/key {key:"ctrl-z"}` returns 400; no client shows a ^Z
key; ^C shows a confirmation; `npx tsx --test src/lib/special-keys.test.ts` green.

## G17 · Prepaid repair vault: let the self-heal agent buy its own API credits
`labels: help-wanted, size:L, self-healing, crypto, discussion`

The RED ALERT self-healing loop (`docs/RED_ALERT.md` in the private tree; watchdog →
report → card → 2-minute auto-repair → diagnosis seat on the strongest model) has one failure
it cannot repair: **the provider is out of credits.** Switching provider only helps while a
sibling has credits. When every runtime is dry, the diagnosis seat itself cannot boot.

Seed for a design + first slice: a **prepaid repair vault** — a wallet (stablecoin, or a
provider-prepaid balance where the provider offers one) the self-heal agent may draw from
ONLY to restore functionality, with a hard cap per incident, a daily cap, a signed audit
line per draw in the RED ALERT report (`repair_attempts[].funding`), and an operator card
before any draw above the per-incident cap. The vault is funded by the operator, never
auto-topped from a bank account.

Questions for the room: which providers accept crypto or card-on-file top-ups via API
today; how to keep the key that can spend out of the agent's own context (a signing sidecar
with a policy, not a key in `.env`); what the "repair-only" spend policy looks like as code.

**Acceptance (first slice).** `orchestra vault status` shows balance + caps; a simulated
out-of-usage RED ALERT on a sandbox seat draws once, logs the draw in the report, and
refuses a second draw over the cap with a card.

## G18 · The ticket button: report sheet on every surface (web done, iOS/watch open)
`labels: good-first-issue, size:S, ui, ios, self-healing`

The RED ALERT system (`docs/RED_ALERT.md`) has a back door (the watchdog) and now a front
door on the web agent page: the siren button in the top bar opens a report sheet
(crash / bug / improvement / suggestion + your words); the server attaches the evidence
(screen capture, process state, logs) via `scripts/red_alert.py report --surface`, files
`state/red-alert/<ts>-<seat>-<class>.json`, and surfaces it (crash/bug → approval card +
Telegram + Arturo; improvement/suggestion → Telegram + Arturo). API: `POST
/api/red-alert/report {seat, kind, words}`, `GET /api/red-alert/reports?status=open`.

**Open.** The iOS/watch agent view needs the same button next to (or above) the auth-key
control, calling the same endpoint; and a tiny "tickets" list view over `GET
/api/red-alert/reports`. Operator's framing: *"this will serve as the gateway to the
ticket system."*

**Acceptance.** Filing from the phone lands a report file with `channel: ios` and the
evidence attached; a crash/bug puts a card on the approvals surface within seconds.

## G19 · The operator's name survives in schema-bearing identifiers (columns, files, API fields)
`labels: good-first-issue, size:S, cleanup`

The scrub in PR #6 removed the original operator's name from prompts, comments, test
fixtures and in-tree identifiers (`git grep -iw <name>` = 0). Four compound identifiers were
left because they are contracts, not prose — renaming them touches a schema or a stored
file name:

- learning DB: columns `shaw_decision`, `shaw_timestamp` and the status value
  `pending_shaw` (`api/src/routes/learning.ts`, `unified-approvals.ts`, the SQL that creates
  and reads `proposals`).
- `api/src/routes/auth.ts`: the `.shaw_chat_id` file under the data dir.
- `api/src/routes/system.ts`: `getShawPresence()` and the `shaw_presence` field of
  `GET /api/system/...` (read by the dashboard and iOS).
- `api/src/routes/agents.ts`: the `${ts}_dashboard_shaw.json` upload filename.

Rename each to its `operator_*` form **with a migration**: an `ALTER TABLE ... RENAME
COLUMN` (SQLite ≥ 3.25) and a status-value UPDATE guarded to run once, a read-old-then-new
fallback for the file names, and the API field emitted under both names for one release.
Update every consumer in the same PR (grep the dashboard and the iOS repo for the field).

**Acceptance.** `git grep -i shaw` over the tree returns only git-author lines; an
existing data dir upgrades in place (`orchestra upgrade`) with no lost proposals/decisions;
`/api/system` still answers with the presence field the clients read.

## G20 · Arturo threads are one-way: "New thread" exists, old threads are unreachable — FIXED
`labels: arturo, ui, size:M, fixed`

**SHIPPED — do not pick this up as open work.** The design below landed before the release.
On the release tree `api/src/routes/arturo.ts` serves `GET /api/arturo/threads` (line 80) and
`GET /api/arturo/threads/:id` (line 91), and `dashboard/src/components/arturo/ArturoPill.tsx`
reads that list, so the thread list and every thread's turns come from the SERVER: "New thread"
leaves the old one IN the list instead of losing it, the home and the pill read the same list,
and `localStorage` holds only WHICH thread you were in, never the archive. Verified by effect
against the release sha, 2026-09-19. The rest of this section is kept as the design record.

The Ask-Arturo pill keeps one conversation and its history, and "New thread" starts a fresh
one — but the previous thread is then gone from the UI: there is no list, no switcher, no way
back. The home page has the same shape (one thread, no history of past ones). Whatever you
asked yesterday is unreachable even though the turns exist server-side.

**What exists today.** The pill persists its turns and its `conversation_id` in
`localStorage` (`orchestra.arturo.pill.thread` / `.conversation`), and the home keeps its own
(`orchestra.arturo.conversation`). Every turn already carries that `conversation_id` to
`POST /api/arturo/text`, and the Arturo service threads context per conversation — so the
server is already the durable side; only the client forgets.

**Design.** Make the conversation a first-class object instead of a localStorage string:
- Persist the thread list server-side, keyed by `conversation_id` (title, created/updated,
  turn count, last snippet). The Arturo service already sees every turn; the list belongs
  next to it rather than in the browser, so the phone and the web show the same threads.
- `GET /api/arturo/threads` (list, newest first, paged) and `GET /api/arturo/threads/:id`
  (its turns) — the pane and the home both read these; localStorage becomes a cache of
  "which thread was I in", not the archive.
- UI: a threads affordance in the pill header and on the home (the drawer is the obvious
  place) listing recent threads with their first line as the title; selecting one loads its
  turns and makes it current. "New thread" stays, and the thread it leaves behind is now in
  the list rather than lost.
- Titles: derive from the first user message; do not ask the operator to name a thread.
- Home and pill should share one thread space — the same conversation continued in either
  place, not two parallel archives.

**Acceptance.** Ask something in the pill, start a new thread, then reopen the previous one
from the list and see its turns; the same thread is visible on the home page and survives a
browser reload and a service restart (i.e. it is not localStorage-backed).

## G21 · `make test` can fail one wall-clock ratio test on a busy host
`labels: good-first-issue, size:XS, tests, harness`

`scripts/lineage_daemon/realtime/cpu_measure_test.py::test_steady_state_cpu_under_one_percent_on_real_proc`
asserts that the one-scan fanout tick is *materially cheaper* than a per-agent scan, as a ratio of
milliseconds. Its docstring calls that relative guard "load-invariant"; it is not — on a busy laptop or a
shared VPS the ratio collapses (measured 0.55 vs 0.80 ms/tick at load 31, 3 of 5 runs red in isolation)
while it passes on any quiet box. `_high_load()` gates only the absolute "<1% CPU" budget.

**What exists today.** The test is deselected from the default `make test` and lives in `make test-perf`
(Makefile comment says why). So a first run is green, but the guard protects nothing until it is fixed.

**Fix.** Gate the relative assertion on `_high_load()` too, or widen its tolerance, or turn it into a
benchmark that *reports* the ratio instead of asserting it. Then put it back in the default suite.

**Acceptance.** `make test` includes the test again; it passes 5/5 in isolation at load ≥ 2× cores AND on
a quiet box; the docstring no longer claims the relative guard is load-invariant.

