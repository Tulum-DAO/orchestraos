# Costs

What it actually costs to run the minimum path (`docs/INSTALL.md`), plus the
zero-key path (no paid API keys, no paid CLI plan). Prices below are what the
providers listed as of this week (2026-09-17) — they change; verify against the
source link before budgeting.

## VPS: the minimum path needs one small box

`orchestra up` runs five always-on processes (gateway, api, dashboard, arturo,
plus the cron beats) and one tmux seat per agent. Nothing here is GPU or
memory-heavy — the model runs on the vendor's servers, not yours. A 2-4 GB / 2
vCPU box is enough for one operator with a handful of seats.

| Provider | Plan | Specs | Price |
|---|---|---|---|
| Hetzner | CX22 | 2 vCPU, 4 GB RAM, 40 GB disk | €3.79/mo (~$4.59/mo) |
| Hetzner | CPX31 | 4 vCPU, 8 GB RAM | ~$18-25/mo (post-April-2026 increase) |
| DigitalOcean | Basic (smallest) | 1 vCPU, 1 GB | $4/mo |
| DigitalOcean | Basic 2 vCPU / 4 GB | 2 vCPU, 4 GB RAM | $24/mo |

Hetzner is the cheaper of the two at every comparable tier; DigitalOcean's
per-second billing (since Jan 2026) means a box you tear down after the
hackathon costs only the hours it ran. Either is fine for the minimum path — the
`Dockerfile` / dev container in this repo reproduces the same recipe locally
with no VPS at all if you just want to try it.

Sources: [Hetzner Cloud pricing](https://www.hetzner.com/cloud/regular-performance/),
[Hetzner 2026 price adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/),
[DigitalOcean Droplet pricing](https://www.digitalocean.com/pricing/droplets).

## CLI plans: what each runtime costs

The harness runs on whatever agent CLI you already have a subscription for
(`orchestra doctor` probes for one). You do not need all three — pick one.

| Runtime | Free tier | Paid entry | Paid higher tier |
|---|---|---|---|
| Claude Code | none (Pro required for meaningful usage) | Pro: $17-20/mo | Max 5x: $100/mo · Max 20x: $200/mo |
| Codex (OpenAI) | none standalone | Bundled with ChatGPT Plus: $20/mo | ChatGPT Pro: $100/mo |
| Gemini CLI | **1,000 requests/day, no card, no expiration** | Google AI Pro (higher limits): varies by region | Google AI Ultra: higher still |

Gemini CLI's free tier is the zero-key path's backbone: it is enough for a
solo developer's full workday of moderate use with no subscription and no
credit card. `orchestra doctor` reports which runtimes you have authed; the
harness does not care which one, or how many. One nuance if you're relying on
the free tier specifically for the assistant brain: the runtime brain picks
the *first authed CLI* in `orchestra.toml`'s `[runtimes] enabled` order
(default `claude, gemini, codex`) — put `gemini` first in that list, or set
the brain explicitly, if you want the free tier to actually be what answers
(see `docs/ARTURO.md`).

Sources: [Claude pricing](https://claude.com/pricing),
[ChatGPT Plus / Codex bundling](https://userjot.com/blog/openai-codex-pricing),
[Gemini CLI free tier](https://x.com/mhdfaran/status/2029567739216736544) — verify
current limits on Google's own Gemini CLI page before relying on the number.

## The zero-key path

Minimum path + Gemini CLI's free tier + a free-tier VPS trial (both Hetzner and
DigitalOcean offer new-account credit) gets you to a running harness — one
seat, one answered card, one rotation — for $0 committed spend. `orchestra
doctor` and the [zero-key Arturo brain track](tracks/02-zero-key-arturo-brain.md)
are what make this possible: no `GEMINI_API_KEY`, no ElevenLabs/Cartesia key,
just an authed CLI. Past the free trial, budget the VPS line above; the CLI
stays free at Gemini's tier unless you outgrow 1,000 requests/day.

## What is not covered here

Voice (ElevenLabs/Cartesia/Gemini Live API keys), push notifications beyond
`ntfy` (self-hosted, free), and multi-machine reference installs (a Mac plus a
VPS over Tailscale) are reference-install extras, not part of the minimum
path's cost. See `docs/REFERENCE_INSTALL.md`.
