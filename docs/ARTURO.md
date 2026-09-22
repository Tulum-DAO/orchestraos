# Arturo — the front door

Arturo is the voice-and-text assistant that sits in front of your OrchestraOS: the
main page of the dashboard, a floating "Ask Arturo" pill on every other page, and
(with a vendor key) a voice on your phone and watch. It answers questions about the
fleet, reads what an agent is doing, files decisions, and — the part that matters
on day one — **commissions agents**: "commission an agent to X" spawns a seat and
files the task as a durable message.

It runs with **zero API keys**. Its brain is the agent CLI you are already logged in
to (`claude`, `codex`, or `agy`); an API key is an upgrade, not a requirement.

## 60-second tour

```bash
orchestra init && orchestra doctor && orchestra up      # nothing else to configure
open http://127.0.0.1:8891/                              # Arturo is the main page
```

`orchestra doctor` tells you what Arturo will think with:

```
runtime:claude          OK      claude installed + authed
arturo:brain            OK      runtime (claude via `claude`, model claude-cli-default) — brain=auto
```

The first thing you see is Arturo's first thread, not a settings form: it asks your
name, checks which runtimes are logged in, asks whether you want voice, then asks
what your first agent should do — and spawns it.

## The brain

`services/arturo/brain.py` — one `Brain` interface, three implementations:

| kind      | when                                             | what it does |
|-----------|--------------------------------------------------|--------------|
| `runtime` | no key, ≥1 CLI installed **and** logged in       | shells `claude -p` / `agy --print` / `codex exec` with Arturo's system prompt; tools ride in the prompt as a JSON envelope the CLI answers with |
| `api`     | `GEMINI_API_KEY` set                             | today's Gemini OpenAI-compatible path (streaming, native function calling) |
| `none`    | neither                                          | the service still boots; `/health` says so; every reply is the fix |

Selection is `[arturo] brain` in `orchestra.toml`:

```toml
[arturo]
enabled = true
port = 5071
brain = "auto"          # auto | api | runtime
runtime_model = ""      # optional model flag for the CLI ("" = that CLI's default)
```

`auto` = `api` if a key is present, else the **first authed CLI in `[runtimes]
enabled` order**, else `none`. The probe is the same one `orchestra doctor` runs
(`orchestra_cli/runtime_probe.py` over `config/providers.json`), so doctor and
Arturo never disagree about what is "available".

Env overrides (what `orchestra up` exports to the service): `ORCHESTRA_ARTURO_BRAIN`,
`ORCHESTRA_ARTURO_RUNTIME_MODEL`, `ORCHESTRA_RUNTIMES_ENABLED`.

### What the runtime brain actually runs

| runtime | command | notes |
|---------|---------|-------|
| claude  | `claude -p --no-session-persistence --tools "" --strict-mcp-config --setting-sources "" --system-prompt <sys> [--model M]`, prompt on stdin | `--tools ""` — the CLI must not run tools, Arturo runs its own. The two `--strict/--setting` flags skip MCP servers, hooks and plugins: cold start 5.1 s → 2.7 s measured. `CLAUDECODE` is unset so it works from inside a Claude session. |
| gemini  | `agy --print=<sys + prompt> [--model M]` | the prompt is attached with `=` — `agy --print` swallows the next bare argument. |
| codex   | `codex exec --skip-git-repo-check --ephemeral -s read-only -o <file> - [-m M]`, prompt on stdin | the last message is read from `<file>`; ~10 s per turn on the default model. |

Timings measured in a clean container (claude, default model): plain text turn
**2.9–3.4 s**; a turn that calls one tool and answers ~10 s; a commission (spawn +
verify) ~18 s.

### Tool calling without function calling

The CLIs have no tool-call API, so `RuntimeBrain` appends a `## Tools` block to the
system prompt: the same JSON schema `ApiBrain` sends, plus the rule *"to call tools,
reply with ONLY `{"tool_calls":[{"name":…,"arguments":{…}}]}`"*. The reply is parsed
back into the OpenAI response shape (`choices[0].message.tool_calls[i].function.name`
/ `.arguments`) so `arturo-proxy.py`'s tool loop, filler guards and journaling are
untouched. Fenced or prose-wrapped envelopes are tolerated; a reply with no envelope
is plain text.

**codex is the one exception.** Measured 2026-09-19: codex never honoured the prose
envelope (0/3), and twice fabricated a completed action with no tool call in the
transcript. `codex exec` alone among the three CLIs accepts `--output-schema <FILE>`,
which constrains its final response to a JSON Schema — paired with the same `## Tools`
prompt block this is reliable (5/5, then 3/3 + a negative control through the real
`RuntimeBrain` code path). Only the codex branch of `runtime_command` sets this flag;
claude and gemini are untouched. Two schema files ship in `services/arturo/`:
`codex_tool_schema.json` (general — `tool_calls` may be empty) is used for every turn
with tools, and `codex_tool_schema_required.json` (`tool_calls.minItems: 1`) replaces
it only when `tool_choice="required"` — the prompt's own "you must call a tool" wording
is a request the model can still ignore (measured 1/3 clean); `minItems` is an API-
enforced structural guarantee. OpenAI strict mode requires `additionalProperties:false`
on every object, which makes a free-form `arguments` object illegal, so the codex
envelope carries `arguments_json` (a JSON-encoded string) instead of `arguments`;
`parse_cli_reply` decodes it into the same shape the other two runtimes produce. A
turn with no tools never gets `--output-schema` at all, so a plain conversational
reply stays prose instead of becoming a forced `{"tool_calls":[],"text":"..."}`
envelope. See `services/arturo/probe_codex_parity.py` for the by-effect proof — run it
against a `CODEX_HOME` that holds only `auth.json` (+ a minimal `config.toml`), not an
interactive fleet install whose own hooks intercept spawn-shaped turns before codex
ever sees them.

## Text turn — the path every UI uses

```
browser / iOS
  POST /api/arturo/text  {text, conversation_id}          api/src/routes/arturo.ts (holds the gateway bearer)
    → POST /arturo/text                                    scripts/watch_gateway.py (Bearer)
      → POST http://127.0.0.1:5071/text                    services/arturo/arturo-proxy.py (loopback-only)
        → chat_completions(channel="text", stream=False)   full tool loop on brain.complete(...)
  ← {ok, reply_text, conversation_id, brain:{kind,runtime,model}, tools_called:[...]}
```

- `conversation_id` threads a bounded history per conversation (same store PTT uses).
- The pill prefixes one line, `[Context: route=/approvals entity=approvals:apr_x]`,
  so Arturo knows where you were.
- `GET /api/arturo/health` → `/arturo/health` → `:5071/health`:

```json
{"status":"ok","brain":{"kind":"runtime","runtime":"claude","cli":"claude","model":"claude-cli-default"},
 "brain_mode":"auto","mode":"text-only","voice":false,"model":"claude-cli-default","tools":[...]}
```

`mode` is `voice` when any of `ELEVENLABS_API_KEY` / `CARTESIA_API_KEY` /
`HUME_API_KEY` / `GEMINI_API_KEY` is present, else `text-only`. A text-only install
disables the voice-call circle on the home (the Mic button still DICTATES — see
§ Dictation below); the `/ptt*` voice routes still exist
and refuse vendor-less calls visibly.

## Commissioning an agent

"Commission an agent to write the README" → the brain calls `spawn_agent` →
`services/arturo/arturo-proxy.py::commission_plan()`:

1. `bash spawn-agent.sh <name> --task "<task>"` with `AGENT_RUNTIME=<the brain's
   runtime>` and `AGENT_MODEL=<default for that runtime>` — a real, registered seat
   (registry row, tmux session, adopt gate), the same path every seat uses.
2. `tmux has-session` verifies it by effect.
3. A `msg_store` row `from=arturo to=<name> type=task` with the task as body — the
   commission survives a restart and shows in the Inbox:
   `python3 msg_store.py inbox --agent <name>`.

Mac targets keep the legacy raw-tmux spawn (spawn-agent.sh is a server-side script).

## The home page and the pill

- `dashboard/src/pages/ArturoHome.tsx` at `/` — its own shell (no sidebar, no
  banners): header `≡ · Arturo · <model> ⌄ · brain`, glow, greeting, thread, one
  composer. Header and chip read the live brain from `/api/arturo/health`.
- Drawer (≡) = the rest of the OS. Brain glyph = Facts / Commitments. Model chip /
  title = the runtime catalog sheet (`/api/runtimes/available`).
- `dashboard/src/components/arturo/ArturoPill.tsx` — the "Ask Arturo" pill on every
  dashboard page; expands over the page with a context chip; "Open Arturo" jumps home.
- The old dashboard Overview lives at `/overview`.

### Onboarding (the first thread)

The operator's name is **server state**: `services/arturo/operator_store.py` writes
`<ARTURO_STATE>/operator.json`, `/health` and every `/text` reply carry
`operator: {name, …}`, and every surface reads it there. `localStorage`
`orchestra.arturo.name` is only a cache for the first paint;
`orchestra.arturo.onboarded` marks a browser that finished the thread. Clear both
and delete `operator.json` to run onboarding again.

1. **runtime detect** — reads `/api/runtimes/available` + `/api/arturo/health`; if
   nothing is logged in you get a terminal here and a *Check again* card. A brain must
   exist before it is asked to listen, so this runs first.
2. **name** — "What should I call you?" is a **brain turn**. The page sends the reply
   with a first-line marker `[Onboarding: step=name]`; the proxy strips it and adds the
   step's directive (`services/arturo/onboarding.py`) to the system context for that turn.
   The brain understands the reply — typed or dictated, any phrasing, any language —
   and records it with the `set_operator_fact` tool, or asks again in its own words.
   The page advances when `tools_called` includes `set_operator_fact` (the same rule
   the first-agent step uses for `spawn_agent`). Nothing parses a name on the client,
   and a browser the server already knows is never asked twice.
3. **voice** — the mic already dictates with no key; the card asks whether Arturo
   should talk *back* (vendor key) or stay text-only. Only shown in text-only mode.
4. **first agent** — "What should your first agent do?" → your answer is sent as a
   commission; the brain picks the seat name and runs `spawn_agent`; when it ran, the
   thread flips to ordinary chat.

Pairing (Track 1) is not in the web thread yet; the iOS thread adds it between
steps 1 and 2 when it lands, and reads the name from `/health` like the web does.

## Paths, config keys, env — the index

| thing | where |
|-------|-------|
| brain seam | `services/arturo/brain.py` (`Brain`, `ApiBrain`, `RuntimeBrain`, `NullBrain`, `select_brain`, `runtime_command`) |
| service | `services/arturo/arturo-proxy.py` (`/text`, `/health`, `/v1/chat/completions`, `/ptt*`); launcher `services/arturo/run.sh` |
| gateway routes | `scripts/watch_gateway.py`: `POST /arturo/text`, `GET /arturo/health`, `/arturo/ptt*` |
| API routes | `api/src/routes/arturo.ts`: `POST /api/arturo/text`, `GET /api/arturo/health` |
| web | `dashboard/src/pages/ArturoHome.tsx`, `dashboard/src/components/arturo/{ArturoPill.tsx,arturo.css}`, `dashboard/src/lib/arturo.ts` |
| config | `orchestra.toml` `[arturo] enabled / port / brain / runtime_model`; `[runtimes] enabled` (order = brain preference) |
| secrets | env only (`GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`, `HUME_*`), or `<data.dir>/.env.secrets` (`KEY=value` lines, never in the toml) |
| logs | `<data.dir>/logs/arturo.log` — `brain: {...}` and `mode: ...` on boot, `COMMISSION:` / `RESULT:` per spawn |
| tests | `services/arturo/test_brain.py`, `test_commission.py`; `orchestra_cli/tests/test_doctor.py` (`arturo:brain`), `test_settings.py` |

## Troubleshooting

- **`arturo:brain none`** — log in to one CLI (`claude`, `codex login`, `agy`) or
  export `GEMINI_API_KEY`, then restart (`orchestra down && orchestra up`). Arturo
  keeps running meanwhile and says exactly this in every reply.
- **Reply says "My claude brain hit a snag"** — the CLI exited non-zero; the
  message carries its stderr head. Run the command from the table above by hand.
- **`orchestra doctor` OK but `/health` brain none** — the service probes with the
  env `orchestra up` gave it; a login done *after* `up` needs a restart.
- **Slow first reply** — a CLI cold start is 2–4 s (claude) to ~10 s (codex); set
  `runtime_model` to a small model for snappier turns.
- **`FAILED to spawn`** — the reply carries `spawn-agent.sh`'s tail; the usual cause
  is a registry writer refusal (a new row needs `AGENT_RUNTIME` + `AGENT_MODEL`,
  which `commission_plan` always sets) or tmux missing.

## A paste-in prompt for a seat that will work on Arturo

```
You are working on Arturo, the front door of OrchestraOS. Read docs/ARTURO.md.
The brain seam is services/arturo/brain.py (Brain / ApiBrain / RuntimeBrain /
NullBrain; select_brain; runtime_command per CLI). The text path is
POST /api/arturo/text -> gateway /arturo/text -> :5071/text -> chat_completions
(channel=text) -> brain.complete(...). The home is
dashboard/src/pages/ArturoHome.tsx; the pill is
dashboard/src/components/arturo/ArturoPill.tsx.
Rules: RED-first (services/arturo/test_brain.py, test_commission.py show the
style); prove by effect in a fresh container (`docker build -t orchestraos . &&
docker run ... orchestra up`), never against the live :5071; secrets only from
env; no host paths, operator names or business names in the tree; keep every
response shape arturo-proxy already reads (choices[0].message.content /
.tool_calls[i].function.name / .arguments; stream chunks .choices[0].delta.content).
Before you start: `orchestra doctor` must show arturo:brain, and
`curl -s localhost:5071/health | jq .brain` must match it.
```


## Dictation — the Mic button works with no vendor key, in every browser

The Mic button in every Arturo composer (the home and the *Ask Arturo* pill) turns speech
into text in the box. You read it, then tap send. It never needs ElevenLabs, Hume or any
key, and there is nothing to decide or install: transcription is on straight out of the box.
Three tiers, chosen per tap:

1. **On-device speech (Chrome, Edge, Safari, Samsung Internet).** The browser's own
   recognizer; words appear live while you talk. Chrome sends the audio to Google for
   this, so it needs internet even though it needs no key.
2. **Record, then transcribe on the box (everything else — Firefox, Chrome on iPhone,
   Brave, keyless Chromium).** The browser records a clip, decodes it itself to 16 kHz WAV,
   and the box transcribes it with a local Whisper model (`whisper tiny.en`, int8, via
   sherpa-onnx) on its own CPU. About a third of a second per sentence; punctuation and
   capitals come out right. What it costs: the sherpa-onnx wheel (~15 MB, part of
   `orchestra init`) and a one-time ~99 MB model download into `<data dir>/models/sherpa`,
   fetched in the background at `orchestra init` and retried at every `orchestra up`. Until it
   is on disk the button says so in plain words; nothing is ever downloaded inside a request.
   Clips longer than 28 s are split at the quietest point and joined (Whisper decodes 30 s at
   a time). Options:
   - `ARTURO_LOCAL_STT_MODEL=zipformer-small-en` — a 27 MB model for constrained boxes,
     3x faster, same accuracy on natural speech, **but it outputs ALL CAPS with no punctuation
     and is weak on synthetic or unusual voices.** Not the default anywhere.
   - `./bin/orchestra init --stt` — adds **faster-whisper** (`base.en` by default,
     `ARTURO_LOCAL_STT_MODEL=small.en` for the best quality), the better engine: +~365 MB in
     `.venv` plus its own model. Once installed it is chosen automatically
     (`ARTURO_LOCAL_STT_ENGINE=auto|faster-whisper|sherpa` overrides).
   - `ARTURO_LOCAL_STT=0` switches server dictation off.
   `orchestra doctor` shows `arturo:local-stt` = ready (engine + model) / warming / not
   installed (the wheel had no build for this platform — Chrome/Edge/Safari still dictate).
3. **Neither.** The button stays tappable and tells you exactly which of the above to
   fix. It is never silently dead.

**HTTPS is required for the microphone** in every browser, except on `localhost`. If
your dashboard is bound to a LAN or VPN address over plain `http://`, no browser will
open the mic. Put it behind `tailscale serve` or a TLS reverse proxy; `orchestra doctor`
adds a `dashboard:https` note when the bind address is not loopback.

Endpoint (browser → api → gateway → :5071, all bearer-guarded like `/text`):

```
POST /api/arturo/transcribe   multipart audio=<clip>   (wav from the browser; webm/mp4/ogg accepted; 10 MB cap)
  200 {ok:true, text, backend:"local-whisper", engine:"sherpa"|"faster-whisper", model, ms}
  422 no_speech · 415 wav_required (default engine, undecodable clip) · 503 stt_unavailable
      {reason: warming|not-installed|off|error, install} · 400/413 bad or oversized clip
  504 timeout (25 s on the box, 35 s gateway, 40 s api; queue wait is not charged to a clip)
```

The watch push-to-talk path (`/ptt`) is separate and unchanged: it still uses the vendor
speech-to-text order and needs a voice key for the spoken reply.
