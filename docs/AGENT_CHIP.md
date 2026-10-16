# The agent chip

Every surface that lists agents (the dashboard rail, iOS, iPad, the Watch, the Mac grid) draws the
same small chip for each one: **our seat name**, a **provider logo**, a **status dot**, and how much
**context is left**. The gateway computes the chip once (`scripts/agent_chip.py`), so no client
works it out on its own and two screens never disagree.

## Where it is

- **Gateway `GET /agents`** (scope `read`): every row has a `chip` object. Older clients ignore it;
  every field that was there before is unchanged.
- **API `GET /api/agents`** (the dashboard): each visible row carries the same `chip`, copied from
  the gateway by agent id. If the gateway cannot be reached, rows have no `chip` and the dashboard
  falls back to its own fields.

## Shape

```json
{
  "name": "gm",
  "provider": "claude",
  "model_label": "Opus 5.5",
  "status": "working",
  "context_left_pct": 92
}
```

| field | meaning |
|---|---|
| `name` | Our seat name (`gm`, `ios-watch-dev`, ...). The provider is the logo, never the name. |
| `provider` | `claude`, `codex`, `gemini`, `grok`, or `null`. Taken from the live process, else the registry, so a stopped seat keeps its logo. `null` means no logo. |
| `model_label` | A short model name for a tooltip or a tap (`Opus 5.5`, `GPT-5.6 Terra`, `Grok 4.7`). From the model id and the labels in `config/providers.json`; an id with no known label is shown as it is. `null` when the model is unknown. |
| `status` | `needs_you`: a permission prompt, a pending menu, a pending card this seat filed, or an unsent draft. `working`. `idle`: at the prompt. `down`: crashed, stalled, stopped, not running, or a state the gateway does not know (never drawn as alive). |
| `context_left_pct` | 0-100, the share of the model's context window still free. `null` when there is no reading: draw nothing, never `0%` or `100%`. |

## Drawing it

- **Logo:** the provider's `logo_svg` from `config/providers.json` (also served by the API as
  `GET /api/runtimes/available` -> `providers[].logo_svg`). This is the one mark set for every
  surface: draw it small (about 14 px), as identification only. Do not recolour or redraw it.
- **Status dot:** one colour per `status`, the same on every surface (the dashboard's existing
  colours, `STATE_STYLE` in `dashboard/src/lib/agentStatus.ts`): `needs_you` amber
  with a ring, pulsing; `working` orange, pulsing; `idle` green; `down` red (the dashboard keeps a
  seat that is simply not running grey, in its collapsed "Not running" list). Working and
  needs-you must never look alike.
- **Context:** `92% left`. Amber at 20% left or less.
- **Model:** on hover or tap, `Runs on Claude · Opus 5.5`.

## Test against the fixture

`contract/fixtures/agent-chips.json` holds `/agents`-shaped rows and the chip the gateway returns
for each one: working, waiting on a prompt, waiting on a card, no context reading, a stopped seat
with its registry provider, and a crashed seat with no provider. The gateway's tests and the
dashboard's tests both read it, so it always matches the code. Native clients should decode it in
their own tests.
