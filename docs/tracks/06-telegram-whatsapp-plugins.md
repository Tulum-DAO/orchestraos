# Track 6 — Telegram / WhatsApp as plugins

Size: S · Labels: `track`, `notify`

## Problem

Telegram delivery is woven directly into core: `scripts/approval_notify.py` has
Telegram-specific functions at the top level (`_tg_send`, `_tg_text_for_approval`,
`_tg_text_for_qnr`, `_tg_full_card_text`, `_tg_digest_text` — grep that file, ~15
functions with a `_tg_` or `tg_` prefix) mixed in with the vendor-agnostic
escalation/digest logic (`cron_backstop`, `refire_on_return`,
`escalate_offtailnet`). There is no `notify` interface to swap against, so running
core with Telegram absent means dead code paths, not a clean "channel disabled"
state, and WhatsApp has no home at all.

## Design

**Sequencing note:** Tier 0 item 6 (orchestra-builder, landing this week) ships
the `plugins/telegram/` layout first — the INBOUND router extracted from the
private `telegram-router.py` (BYO bot token from `TELEGRAM_BOT_TOKEN` env,
routed to the `gm` seat, a `plugin:telegram` doctor row, and a TELEGRAM CHANNEL
section already in `prompts/gm.md` keyed on that row). The shape is
`plugins/telegram/{__init__.py, router.py (inbound long-poll), tg_send.py
(outbound), README.md}`, config `[plugins.telegram]` with `enabled` + a chat
allowlist. **This track builds on that layout — it does not create a second
`plugins/telegram/`.** If item 6 hasn't landed on your checkout yet, check
`orchestra doctor` for the `plugin:telegram` row and `git log -- plugins/`
before assuming the directory doesn't exist.

A `notify` interface with three methods — `send_text(text)`, `send_photo(path,
caption=None)`, `send_card(card)` (the approval/questionnaire card shape
`approval_notify.py`'s `_tg_full_card_text` already builds text for) — implemented
by the already-landed `plugins/telegram/tg_send.py` (outbound; this track's job
is moving `approval_notify.py`'s `_tg_*` functions into it, not creating a new
file) and a new `plugins/whatsapp/` (interface + stub, same shape). Each plugin
owns its own config section, bot token from env only, never the config file,
matching the existing secrets convention, and is entirely absent from core's
import graph when disabled — `scripts/approval_notify.py` calls through the
`notify` interface, never `import`s a plugin module directly; plugin discovery is
a small registry (`plugins/__init__.py`, already landed with item 6) that core
asks "which channel is configured" and gets back an object or `None`.

`orchestra doctor` already has the `plugin:telegram` row from item 6; this track
adds `plugin:whatsapp` alongside it (coordinate with whoever picks up
`good-first-issue` G6 in `docs/HACKATHON_ISSUES.md` on the row format so they
match).

## Files you will touch

- `plugins/telegram/tg_send.py` — already exists (item 6, outbound long-poll
  send); move the `_tg_*` functions out of `scripts/approval_notify.py` into
  here behind the `notify` interface; keep the actual HTTP call shape
  (`_http_post`, bot API URL) unchanged, just relocate and wrap it. Do not
  touch `plugins/telegram/router.py` (inbound) — that's item 6's, out of this
  track's scope.
- `plugins/whatsapp/` (new) — same interface, new implementation (Business API or
  whatever vendor is chosen; if no WhatsApp credentials exist to test against,
  ship the interface + a stub that reports `not configured` cleanly rather than a
  half-working integration).
- `scripts/approval_notify.py` — replace direct `_tg_*` calls in
  `cron_backstop`/`escalate_offtailnet`/`refire_on_return` with calls through the
  `notify` interface; the vendor-agnostic digest/escalation logic in this file
  stays in core.
- `orchestra.example.toml` — `[plugins.telegram]` already exists (item 6); add
  `[plugins.whatsapp]` alongside it (enabled flag; token via env, documented not
  stored).
- `orchestra_cli/doctor.py` — `plugin:telegram` row already exists (item 6); add
  `plugin:whatsapp`.
- Existing tests: `scripts/test_approval_notify_telegram.py` — move/adapt to test
  the plugin in isolation; core's tests must pass with both plugins absent
  (uninstalled / unconfigured), which is the track's acceptance bar.

## Steps

1. `grep -n "^def _tg_\|^def tg_" scripts/approval_notify.py` — full inventory of
   what moves, before moving anything.
2. Define the `notify` interface (a small ABC or Protocol) somewhere core can
   import without importing any plugin — e.g. `scripts/notify_interface.py`.
3. Move the Telegram functions into `plugins/telegram/`, implementing the
   interface; keep behavior identical (same message text, same bot API calls).
4. Update `approval_notify.py`'s call sites to go through the interface; run
   `scripts/test_approval_notify_telegram.py` (adapted) — green.
5. Run the full core test suite with `plugins/telegram` and `plugins/whatsapp`
   either uninstalled or `enabled = false` — confirm no import errors, no
   Telegram-specific test failures blocking unrelated suites.
6. Add doctor rows; `orchestra doctor` with telegram enabled + a real bot token
   shows `plugin:telegram OK`; disabled shows `INFO`, not a failure.
7. Send a real test message end to end with a bot token configured — confirm
   delivery, matching today's behavior exactly.

## Acceptance test

Core test suite (`python3 -m pytest`) passes with both plugins absent from the
environment (no bot token, plugin directories can even be removed from
`sys.path` for this check). Enabling `plugins.telegram` with a real bot token and
running the existing notify path delivers a test message to Telegram, unchanged
from today's behavior.

## Start prompt

```
I'm working Track 6 (Telegram/WhatsApp as plugins) for the OrchestraOS
hackathon.
Read docs/tracks/06-telegram-whatsapp-plugins.md in this repo for the full
design. plugins/telegram/ already exists (Tier 0 item 6, orchestra-builder
-- router.py inbound, tg_send.py outbound, plugin:telegram doctor row,
[plugins.telegram] config) -- check `orchestra doctor` and `git log --
plugins/` before assuming it doesn't. This track moves
scripts/approval_notify.py's _tg_* functions into the EXISTING
tg_send.py behind a shared notify interface, and adds a new
plugins/whatsapp/ (interface + stub) alongside it, plus the
plugin:whatsapp doctor row.
Start with the inventory in Step 1 of the doc, then define the notify
interface before moving any code, so the extraction is mechanical rather
than a rewrite. Behavior must not change for existing Telegram users —
same message text, same delivery path, just relocated behind the
interface. Do not touch plugins/telegram/router.py (inbound) -- that's
item 6's, not this track's.
```

## Out of scope

- A real WhatsApp Business API integration if no test credentials are available —
  ship the interface and a clearly-marked stub rather than an unverified
  integration.
- New notify channels beyond these two (Slack, Discord, etc. are future tracks).
- Changing the card/digest text format — this track relocates code, it does not
  redesign the message content.
