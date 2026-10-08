# OrchestraOS

![OrchestraOS](docs/design/brand/readme-header.svg)

OrchestraOS, published by Tulum DAO: an open harness for running a fleet of coding agents as a team — agents that message each other, remember across restarts, rotate themselves before they run out of context, and put every real decision in front of the human on their phone.

**This is not a finished product.** It runs one operator's fleet today, every day, and that setup is the reference install. We are opening it so people who want this to exist can build it with us. The first hackathon, Build-a-thon, ran on 2026-09-19 and 20; its tracks are still open ([docs/tracks/README.md](docs/tracks/README.md)).

## Install: pick your path

| You are... | Start here | First thing you do |
|---|---|---|
| **New to servers and Terminal** (you have a Mac and a card) | [From scratch](docs/FROM_SCRATCH.md) | Open Terminal and make an ssh key; it then walks you through renting a server and logging in, and hands you to the install guide. About 20 minutes. |
| **Have a fresh Ubuntu server (VPS)** | [Install guide](docs/INSTALL.md) | Log in, create a normal user (seats refuse to run as root), set up Tailscale, then `git clone https://github.com/Tulum-DAO/orchestraos.git`. About an hour. |
| **Want to try it on your laptop, no server** | [Docker / dev container](docs/INSTALL.md#dev-container--docker-no-vps) | `docker build -t orchestraos .` from a clone of this repo, then log in to your agent CLI inside the container. |
| **Want the full operator setup** (VPS + Mac, push, Telegram, voice) | [Reference install](docs/REFERENCE_INSTALL.md) | Do the install guide first; this adds to it. |
| **Already running it, want the phone or Mac app** (both in testing, not yet public) | [Onboarding](docs/ONBOARDING.md) | On the server: `orchestra pair --base-url <your https gateway address> --scopes read,approve,message`, then paste the `orc1_` code it prints into the app. |
| **Deciding what it costs** | [Costs](docs/COSTS.md) | A small VPS is about $24 a month; you also need one agent CLI plan (Claude Pro, or ChatGPT Plus for Codex; Google's free tier is unverified with `agy`). |

Every path needs one agent CLI (Claude Code, Codex, or Gemini through Google's Antigravity
`agy` CLI) installed and **logged in** before you start a seat. The install guide shows how,
at the point you need it.

Already installed? [Upgrading](docs/UPGRADE.md): `orchestra upgrade`, then
`orchestra down && orchestra up --detach`.

## What is in the box

- **Seats and generations.** An agent is a seat with a lineage. Each generation is one CLI session (Claude Code, Gemini (Antigravity `agy` CLI), or Codex). When a generation nears its context ceiling it writes a handoff, a successor boots, proves it understood the handoff by answering questions anchored in the predecessor's own state, and is promoted. Rotation is autonomous and on by default for Claude seats; Gemini and Codex are experimental and are not armed unless you set `[rotation] experimental_runtimes = ["gemini", "codex"]`.
- **Inter-agent messaging.** A durable message store with inboxes, acks, threads, and a router that parks mail for busy seats instead of losing it.
- **Memory.** A shared facts store with freshness, per-seat memory directories that survive rotation, and a recall hook that feeds the assistant.
- **Approvals surface.** Every decision an agent needs from the human is a card: approve or deny, a menu, a questionnaire, or a "waiting on you" block. Cards render on a web dashboard and an iOS and watch app, and the human's answer resumes the agent that asked.
- **Arturo.** The assistant. The main page, the new-chat page, and a button that is available on every other page and knows what you are looking at. Text today; voice through your own keys.
- **Runtime catalog.** Probes which CLIs are installed and authenticated and offers their models, so the harness runs on whatever subscription you already have.

## More docs

- [docs/BEGINNERS_GUIDE.md](docs/BEGINNERS_GUIDE.md) — what a terminal and an agent CLI are, and the seven-step gate in plain words.
- [docs/GATE.md](docs/GATE.md) — the seven-step gate everyone completes first: command, expected output, and what to check if it fails, for each step.
- [docs/ONBOARDING.md](docs/ONBOARDING.md) — connecting your own phone and browser to your gateway (no baked-in token): pairing, the handshake, what each failure means.
- [docs/PROMPTS.md](docs/PROMPTS.md) — copy-paste prompts for the same seven steps, plus the tracks.
- [docs/UPGRADE.md](docs/UPGRADE.md) — **updating:** `orchestra upgrade` pulls the newest release, re-runs `init`, re-checks `doctor` (it rebuilds what changed); then `orchestra down && orchestra up --detach`; running seats keep their code until their next spawn or rotation. `orchestra doctor` and `orchestra up` tell you when a newer release exists.
- [docs/COSTS.md](docs/COSTS.md) — what a VPS and a CLI plan actually cost, and the zero-key path.
- [docs/tracks/README.md](docs/tracks/README.md) — the thirteen hackathon tracks, one doc each.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the map. Read before touching rotation or approvals.
- [docs/MEMORY.md](docs/MEMORY.md) — per-seat memory (index + one-fact files + baton) and the facts store Arturo recalls from; the copy-paste prompt for gate step 7.

## Configuration

`orchestra init` writes `orchestra.toml` from [orchestra.example.toml](orchestra.example.toml) (every key is documented there). Edit it for the data directory, hosts, ports, your operator id, and the runtimes you have. Secrets are never in the file: bot tokens and API keys are read from the environment only. `orchestra doctor` tells you what is missing.

## Hackathon tracks

Thirteen tracks, each with its own doc: Problem, Design, files to touch, numbered
steps, an acceptance test, and a start prompt you can paste straight into your own
agent. See [docs/tracks/README.md](docs/tracks/README.md) for the full index. The big ones:

1. Device pairing, so the phone app logs in by scanning a code instead of a token baked into the build.
2. Zero-key assistant brain on your own CLI runtime.
3. On-device speech for a voice tier with no vendor keys.
4. Arturo home and conversation-first onboarding, from the reference mockups in [docs/design/](docs/design/).
5. First run without a general manager seat.
6. Chat bridges as plugins.
7. Tab restyle to the reference set.
8. Push without ntfy.
9. Installer and doctor hardening.
10. Docs and tutorials.
11. Autonomous rotation proven on all three runtimes on a clean install.
12. Gauntlet mode: a critic loop that scores creative work against real references before it reaches you.
13. Memory: a page that shows what each seat remembers, a budget on the index, and a pruner — the store exists today; nobody can see it.

`good-first-issue` is real and small (see [docs/HACKATHON_ISSUES.md](docs/HACKATHON_ISSUES.md)). Start there
if you want to land something in an hour. New to the command line entirely? Start
with [docs/BEGINNERS_GUIDE.md](docs/BEGINNERS_GUIDE.md) instead — it walks the seven-step gate everyone
completes before picking a track.

## How to contribute

Repo: https://github.com/Tulum-DAO/orchestraos. Fork, branch, open a pull request
against `main`. CI runs the tests per package and a secrets scan, and both must pass. Sign off your commits (DCO). Read [CONTRIBUTING.md](CONTRIBUTING.md) for the review rules and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the map before you touch the rotation lane or the approvals contract, which other components depend on.

## License

Apache 2.0. See [LICENSE](LICENSE).
