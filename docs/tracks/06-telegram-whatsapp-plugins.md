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

A `notify` interface with three methods — `send_text(text)`, `send_photo(path,
caption=None)`, `send_card(card)` (the approval/questionnaire card shape
`approval_notify.py`'s `_tg_full_card_text` already builds text for) — implemented
by `plugins/telegram/` and `plugins/whatsapp/` (new top-level `plugins/` directory;
none exists yet). Each plugin owns its own config section
(`[plugins.telegram]` in `orchestra.toml`, bot token from env only, never the
config file, matching the existing secrets convention) and is entirely absent from
core's import graph when disabled — `scripts/approval_notify.py` calls through the
`notify` interface, never `import` a plugin module directly; plugin discovery is a
small registry (`plugins/__init__.py` or similar) that core asks "which channel is
configured" and gets back an object or `None`.

`orchestra doctor` lists enabled plugins (new rows, `plugin:telegram`,
`plugin:whatsapp`) alongside the channel check work already tracked in
`good-first-issue` G6 in `docs/HACKATHON_ISSUES.md` — coordinate with whoever picks
that one up rather than duplicating the doctor rows.

## Files you will touch

- `plugins/telegram/` (new) — move the `_tg_*` functions out of
  `scripts/approval_notify.py` into here behind the `notify` interface; keep the
  actual HTTP call shape (`_http_post`, bot API URL) unchanged, just relocate and
  wrap it.
- `plugins/whatsapp/` (new) — same interface, new implementation (Business API or
  whatever vendor is chosen; if no WhatsApp credentials exist to test against,
  ship the interface + a stub that reports `not configured` cleanly rather than a
  half-working integration).
- `scripts/approval_notify.py` — replace direct `_tg_*` calls in
  `cron_backstop`/`escalate_offtailnet`/`refire_on_return` with calls through the
  `notify` interface; the vendor-agnostic digest/escalation logic in this file
  stays in core.
- `orchestra.example.toml` — add `[plugins.telegram]` / `[plugins.whatsapp]`
  sections (enabled flag; token via env, documented not stored).
- `orchestra_cli/doctor.py` — plugin rows.
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
design. Files to touch: scripts/approval_notify.py (extract the _tg_*
functions), new plugins/telegram/ and plugins/whatsapp/ directories behind
a shared notify interface, orchestra.example.toml (plugin config
sections), orchestra_cli/doctor.py (plugin rows).
Start with the inventory in Step 1 of the doc, then define the notify
interface before moving any code, so the extraction is mechanical rather
than a rewrite. Behavior must not change for existing Telegram users —
same message text, same delivery path, just relocated behind the
interface.
```

## Out of scope

- A real WhatsApp Business API integration if no test credentials are available —
  ship the interface and a clearly-marked stub rather than an unverified
  integration.
- New notify channels beyond these two (Slack, Discord, etc. are future tracks).
- Changing the card/digest text format — this track relocates code, it does not
  redesign the message content.
