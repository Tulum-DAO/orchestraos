# Hackathon tracks

Thirteen tracks, one doc each, same shape: Problem (with the file that proves it) /
Design / Files you will touch / Steps / Acceptance test / Start prompt / Out of
scope. Paste a track's Start prompt into your own agent, pointed at this repo, to
begin — no other setup needed beyond the seven-step gate everyone completes first
(see `docs/BEGINNERS_GUIDE.md`).

Paths inside each doc are as of this tree; re-grep before starting if it has moved
since. A doc that names a command which has not landed on `main` yet says so and
tells you where it lives (a branch, a PR) so you can build on it or work around it.

| # | Track | Size | Depends on |
|---|---|---|---|
| [1](01-device-pairing.md) | Device pairing replaces the built-in app token | S/M | — |
| [2](02-zero-key-arturo-brain.md) | Zero-key Arturo brain on the CLI you already have | M | — |
| [3](03-on-device-voice.md) | On-device voice tier (iOS) | M | Track 2 |
| [4](04-arturo-home-onboarding.md) | Arturo home + conversation-first onboarding | L | Tracks 1, 2, 3 |
| [5](05-gm-optional-first-run.md) | Manager-seat-optional first run | M | — |
| [6](06-telegram-whatsapp-plugins.md) | Telegram / WhatsApp as plugins | S | — |
| [7](07-tab-restyle.md) | Tab restyle to the reference set | M | Track 4 |
| [8](08-push-without-ntfy.md) | Push without ntfy | S/M | Track 1, Track 6's notify interface |
| [9](09-installer-doctor.md) | Installer and doctor hardening | S | Track 6 (plugin rows) |
| [10](10-docs-tutorials.md) | Docs and tutorials | S | — |
| [11](11-autonomous-rotation-all-runtimes.md) | Autonomous blue-green rotation on every runtime | L | — |
| [12](12-gauntlet-mode.md) | Gauntlet mode: a critic loop for creative work | M | — |
| [13](13-memory.md) | Memory: see it, prune it, then teach it to extract | S/M | — |

`good-first-issue` is a separate, smaller list — see `docs/HACKATHON_ISSUES.md`'s
second half (G1-G8). Start there if you want to land something in an hour instead
of a weekend.

## Picking a track

Complete the seven-step gate first (`docs/BEGINNERS_GUIDE.md`): install, one
always-on agent, Telegram connected, two seats exchange a message, one card
answered, one rotation, one fact recalled after a restart. That path touches
most of the surfaces these tracks build on, so you will already recognize the
files each doc names.

Tracks 1, 2, 5, 6, 9, 10, 11, 12, 13 have no dependency on another track landing first —
any of those is a reasonable solo or pair start. Tracks 3, 4, 7, 8 build on
another track's contract; read that track's doc (or its author's latest state)
before diverging from the design.
