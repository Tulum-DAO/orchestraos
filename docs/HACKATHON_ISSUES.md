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

---

# Good first issues

## G1 · Operator-naming: rename remaining `shaw_*` / `*Shaw*` identifiers
`labels: good-first-issue, size:S, scrub`

All string literals, prose and the tenant default are already generic. What remains are
~48 identifiers (variables, functions, constants) found via
`git grep -ciE '\bshaw\b'`. Known locations: `scripts/lineage_daemon/wal/checkpoint_producer.py`
(local var + `shaw_attached_fn` parameter, and its supervised test double),
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
Add rows for the notify channel (`[notify] channel` + its credentials present), the ntfy
server reachability when `NTFY_BASE` is set, and enabled plugins (T6).

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
