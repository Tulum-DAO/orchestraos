# Track 2 — Zero-key Arturo brain on the CLI you already have

Size: M · Labels: `track`, `arturo`

> **Status: shipped on branch `arturo/oss` (oss-arturo-dev, 2026-09-17).** The design
> below is kept for the hackathon; the corrections in *What actually landed* are the
> truth where the two differ. Walkthrough: `docs/ARTURO.md`.

## What actually landed (read this first)

- `services/arturo/brain.py`: `Brain.complete(messages, tools=, tool_choice=, stream=, timeout=)`
  returning the **OpenAI response shape** the proxy already reads — not a
  `reply(context, history, user_text) -> str` method. That kept all six call sites
  in `arturo-proxy.py` (`_ptt_brain()` + the five in the chat tool loop) one-line
  swaps and left the tool loop / filler guards / journaling untouched. Streaming
  works for all three brains (`stream=True` yields delta chunks).
- Three implementations, not two: `ApiBrain`, `RuntimeBrain`, and `NullBrain` (no
  key + no authed CLI → the service **still boots**, `/health` says
  `brain.kind = none`, every turn answers with the fix). Before this the process
  died at import and `orchestra up` restart-looped it.
- `RuntimeBrain` tool calling: the tool schema is appended to the system prompt as
  a JSON-envelope protocol (`{"tool_calls":[{"name","arguments"}]}`), parsed back
  into `tool_calls`. The exact CLI invocations (flags that matter, measured
  timings) are in `docs/ARTURO.md` → *What the runtime brain actually runs*.
- Config: `[arturo] brain = auto|api|runtime` **and** `runtime_model`; exported as
  `ORCHESTRA_ARTURO_BRAIN` / `ORCHESTRA_ARTURO_RUNTIME_MODEL` /
  `ORCHESTRA_RUNTIMES_ENABLED` by `orchestra up` and `scripts/orchestra-env.sh`.
- `orchestra doctor` row `arturo:brain` (OK runtime/api, WARN none + remedy).
- The **text turn** the acceptance test needs did not exist on the web — added:
  `POST :5071/text` (loopback) → gateway `POST /arturo/text` (Bearer) → API
  `POST /api/arturo/text`; plus `/arturo/health` passthroughs.
- Commission: the `spawn_agent` tool now runs the repo's `spawn-agent.sh`
  (`AGENT_RUNTIME` = the brain's runtime) and files a `msg_store` row
  **`from=arturo to=<seat>` type=task** — the seat's inbox, not gm's
  (`msg_store.py inbox --agent <seat>`). Routing commissions through a `gm`
  lineage when no gm exists is Track 5's job.
- `services/arturo/dispatcher.py` needed no change.
- Measured in a fresh container, no keys, one authed claude: text turn 2.9–3.4 s;
  "commission an agent to …" → tmux seat + registry row + msg_store row in ~18 s.


## Problem

Arturo's conversational turn is one OpenAI-shaped client call against
`GEMINI_API_KEY`, hardcoded in one place: `services/arturo/arturo-proxy.py` reads
the key at import time (line ~839) and fails loud at startup if it's missing (line
~843-844: `log.error("No GEMINI_API_KEY found")`). The chat path (`build_context()` +
`client.chat.completions.create(...)`) and the push-to-talk path (`_ptt_brain()`,
line ~2794, same file) both go through this one client. There is no interface to
swap — a person running Arturo with no Gemini key gets nothing, even though they may
already have `claude`, `agy` (Gemini CLI), or `codex` installed and logged in for
their coding agents, and the harness already knows how to check that (see Design).

## Design

Introduce a `Brain` interface in `services/arturo/` with one method,
`reply(context, history, user_text) -> str` (streaming variant optional for v1),
and two implementations:

- **`ApiBrain`** — today's path, unchanged behavior: the `client.chat.completions
  .create(...)` call in `_ptt_brain()` / the main chat handler, gated on
  `GEMINI_API_KEY` being present.
- **`RuntimeBrain`** — shells the operator's own authed CLI instead of an API. It
  reuses the runtime catalog that `orchestra doctor` already probes
  (`orchestra_cli/runtime_probe.py`, driven by `config/providers.json` — each
  provider's `cli` field: `claude`, `agy`, `codex`) to pick the first authed runtime,
  then invokes it non-interactively with the same system prompt and tool schema
  Arturo uses today: `claude -p <prompt> --output-format json`,
  `agy --print <prompt>` (its non-interactive output has its own quirks — verify by
  running it directly before trusting the adapter's parsing),
  or `codex exec <prompt>`. Each has a different stdout shape — normalize to plain
  text in a small per-runtime adapter next to `RuntimeBrain`, mirroring how
  `runtime_probe.py`'s `ProbeDeps.run_cmd` already isolates subprocess calls for
  testability.

Selection is config-driven: `[arturo] brain = "auto" | "api" | "runtime"` in
`orchestra.toml` (new key next to the existing `[arturo]` block in
`orchestra.example.toml`, line ~32). `auto` (the default) picks `api` when
`GEMINI_API_KEY` is set, else `runtime`; `doctor` should gain a row
(`arturo:brain`) reporting which one is active and why, using the same verdict
strings `runtime_probe.py` already produces (`runtime:<id>` checks, line ~204).

The manager-seat commission path is unaffected either way: "commission an agent to
X" doesn't go through the brain at all — it's a tool call the brain's reply
triggers, which writes to the manager's (`gm`) inbox via `msg_store.py`. Whichever
Brain implementation is active, that tool-call contract stays the same; only the
text-generation step underneath changes.

## Files you will touch

- `services/arturo/arturo-proxy.py` — extract the two call sites (`_ptt_brain()`
  line ~2794 and the main chat completion path) behind the new `Brain` interface;
  `ApiBrain` wraps the existing `client.chat.completions.create(...)` call
  unchanged.
- `services/arturo/brain.py` (new) — `Brain` ABC, `ApiBrain`, `RuntimeBrain`, and
  the per-runtime CLI adapters (subprocess invocation + stdout normalization).
- `orchestra_cli/runtime_probe.py` — reuse `load_providers()` / `probe_all()` as-is
  to pick the runtime for `RuntimeBrain`; do not fork the probe logic.
- `orchestra_cli/doctor.py` — add the `arturo:brain` row (active brain +
  fallback reason), near the existing `runtime:*` checks (~line 200-232).
- `orchestra.example.toml` — document `[arturo] brain` next to `enabled`/`port`
  (~line 32-36).
- `services/arturo/dispatcher.py` — confirm (do not change unless a seam turns up)
  that the commission-to-`gm` tool call fires the same way regardless of which
  `Brain` produced the reply that triggered it.

## Steps

1. `grep -n "GEMINI_API_KEY\|chat.completions.create" services/arturo/arturo-proxy.py`
   — confirm the current call sites before touching anything (baseline).
2. Write `services/arturo/brain.py` with `Brain`, `ApiBrain` (thin wrapper around
   the existing client call — no behavior change), and a `select_brain(config) ->
   Brain` factory implementing `auto|api|runtime`.
3. Unit-test `select_brain` against all three config values with `GEMINI_API_KEY`
   present and absent (6 cases) — no live CLI calls in this test, fake the probe.
4. Implement `RuntimeBrain` for `claude -p` first (it's the reference runtime).
   `unset GEMINI_API_KEY`, set `[arturo] brain = "runtime"`, send a message to
   Arturo, confirm a reply comes back and `services/arturo/<log>` shows which CLI
   ran.
5. Add the `agy` and `codex` adapters; run the same manual check with each CLI as
   the only authed one (rename/hide the others' auth files temporarily, or run in a
   container with only one installed).
6. Wire `_ptt_brain()` through the same `Brain` interface so voice replies (Track 3)
   get zero-key coverage for free.
7. Add the `orchestra doctor` row; run `orchestra doctor` with no API key and one
   authed CLI — confirm `arturo:brain` reports `runtime (claude)` or similar, not a
   failure.
8. Timing check: `time curl <arturo>/chat -d '{"text":"hello"}'` — under 5 s end to
   end for the acceptance test below (a subprocess CLI call is slower than an API
   call; if it blows the budget, that's a finding to report, not a reason to fake
   the number).

## Acceptance test

`orchestra.toml` has no `GEMINI_API_KEY` in the environment and `[arturo] brain =
"auto"`. `orchestra doctor` reports exactly one authed runtime and `arturo:brain`
as `runtime`. A text message sent to Arturo's home returns a reply in under 5
seconds. Sending "commission an agent to say hello" creates a new seat (visible in
the Agents list) and a `msg_store` row from `arturo` to that seat
(`msg_store.py inbox --agent <seat>` shows it).

## Start prompt

```
I'm working Track 2 (zero-key Arturo brain) for the OrchestraOS hackathon,
owned by seat oss-arturo-dev — check in with it before diverging from this
design.
Read docs/tracks/02-zero-key-arturo-brain.md in this repo for the full design.
Files to touch: services/arturo/arturo-proxy.py (extract the two brain call
sites), services/arturo/brain.py (new: Brain / ApiBrain / RuntimeBrain),
orchestra_cli/doctor.py (arturo:brain row), orchestra.example.toml
(document [arturo] brain).
Start by extracting ApiBrain around the existing client.chat.completions.create
call with zero behavior change, prove Arturo still replies normally, then add
RuntimeBrain for `claude -p` first and prove it replies with GEMINI_API_KEY
unset per the doc's Steps 3-4.
```

## Out of scope

- Streaming replies from `RuntimeBrain` (v1 is a blocking call; today's `ApiBrain`
  path may already stream — don't regress that, but don't require the new path to
  match it).
- New runtimes beyond the three in `config/providers.json` (claude, gemini/agy,
  codex).
- Changing the manager-seat commission/tool-call contract itself — this track only
  changes what generates the text that can trigger it.
