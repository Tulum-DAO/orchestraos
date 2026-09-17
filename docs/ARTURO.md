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
disables the mic and voice circle on the home; the `/ptt*` voice routes still exist
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

Driven by `localStorage` keys `orchestra.arturo.name` and
`orchestra.arturo.onboarded`; clear them to run it again.

1. **name** — "What should I call you?" (used in the greeting).
2. **runtime detect** — reads `/api/runtimes/available` + `/api/arturo/health`;
   if nothing is logged in you get the exact commands and a *Check again* card.
3. **voice** — *Text is fine* / *I will add a voice key* decision card (free-text row
   included), only shown in text-only mode.
4. **first agent** — "What should your first agent do?" → your answer is sent as a
   commission; when `spawn_agent` ran, the thread flips to ordinary chat.

Pairing (Track 1) is not in the web thread yet; the iOS thread adds it between
steps 1 and 2 when it lands.

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
