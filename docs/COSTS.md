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

**Subscription-limit risk: a 5-hour window AND a weekly cap, not just daily.**
Claude Code's paid plans meter usage two ways at once: a rolling 5-hour session
window (hit it and you're locked out of that plan until the window rolls over,
same day) and a separate weekly cap on top of it. Codex/ChatGPT plans work
similarly. A two-day hackathon run at hackathon intensity — multiple seats,
near-continuous use, rotations that spawn fresh sessions — can hit the 5-hour
window in an afternoon and the weekly cap well before Sunday, and a plan that
hits either has no fallback except switching runtimes or paying for a higher
tier on the spot. Gemini CLI's free-tier cap is daily only (resets every 24
hours, no weekly ceiling), which is why it is the recommended fallback if
you're worried about running out — see "The zero-key path" below. Bring a
second authed CLI as backup if you're planning to run the full weekend on one
paid plan; `orchestra doctor` shows every runtime you have authed and the
harness switches between them without reconfiguring anything else.

Sources: [Claude pricing](https://claude.com/pricing),
[ChatGPT Plus / Codex bundling](https://userjot.com/blog/openai-codex-pricing),
[Gemini CLI free tier](https://x.com/mhdfaran/status/2029567739216736544) — verify
current limits (daily AND weekly) on each vendor's own pricing page before
relying on these numbers; weekly caps in particular change without much notice.

## What a weekend costs, per attendee

Rough numbers for planning, not a quote — actual spend depends on which VPS
tier and CLI plan you pick above.

| Setup | VPS | CLI | Per-attendee weekend total |
|---|---|---|---|
| Solo, own VPS, zero-key | Hetzner CX22 (~$4.59/mo, prorated to a weekend is a few cents) | Gemini CLI free tier | **~$0** |
| Solo, own VPS, paid CLI | Hetzner CX22 | Claude Pro or ChatGPT Plus, already-owned monthly plan | **$0 incremental** (you're already paying monthly; the weekend doesn't add cost, only usage risk — see above) |
| Shared VPS, group of 4, zero-key | Hetzner CPX31 (~$20/mo) split 4 ways | Gemini CLI free tier each | **~$5/attendee** (VPS share only) |
| Shared VPS, group of 4, mixed CLI | Hetzner CPX31 split 4 ways | mix of free tier + already-owned plans | **~$5/attendee** + nothing incremental for anyone already subscribed |

The cheapest real path for a first-timer with no CLI subscription: the
zero-key path below, $0 committed. The dev container (`Dockerfile` /
`.devcontainer/`) needs no VPS spend at all if you're running the harness on
your own laptop for the weekend.

## How long the gate actually takes

The seven-step gate (`docs/GATE.md`) end to end, for someone following it
literally with no prior exposure to this repo: **45-60 minutes**, install
through step 7 (one fact recalled after a rotation). Budget more your first
time if you hit an unauthed CLI or a Docker/VPS setup snag — those are the
two places people actually get stuck, not the harness steps themselves.
`docs/KICKOFF.md`'s target of "everyone has an agent running by 13:30" assumes
a start around 12:30-12:45 plus this range.

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
