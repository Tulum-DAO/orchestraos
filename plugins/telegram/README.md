# plugins/telegram — your phone as the gm seat's chat channel

Text the bot from your phone; the message lands in the `gm` seat's inbox and gm answers you
on Telegram. Decision cards (approvals, menus, "waiting on you" tasks) arrive with inline
buttons; a tap is landed through the same ONE core the dashboard and the watch use.

## Setup (2 minutes)

1. Create a bot: message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token.
2. Give the token to OrchestraOS — environment only, never `orchestra.toml`:
   ```bash
   export TELEGRAM_BOT_TOKEN=123456:ABC...          # in the shell that runs `orchestra up`
   # or, persistent: echo 'TELEGRAM_BOT_TOKEN=123456:ABC...' >> <data dir>/.env.telegram
   ```
3. Enable the plugin in `orchestra.toml`:
   ```toml
   [plugins.telegram]
   enabled = true
   allowed_chat_ids = []     # [] = the first chat that messages the bot becomes the operator
   ```
4. `orchestra doctor` → `plugin:telegram  OK`. `orchestra up` starts the `telegram` service.
5. Open the bot on your phone and send `/start`. It replies with your chat id and remembers
   it (`<data dir>/state/telegram/chat-id`). Put that id in `allowed_chat_ids` to lock the
   channel to you.

## What happens

| You do | OrchestraOS does |
|---|---|
| send a text (or a photo / document / voice note) | a `task_request` row in gm's msg_store inbox, `from_agent=telegram`, chat id + attachment paths in the metadata; media saved under `<data>/state/uploads/telegram/` |
| an agent creates a card (`scripts/approval.py request`, a menu, a human task) | the router pushes it to your chat with buttons (Approve/Deny, the menu options, Done/Can't/Snooze) |
| tap a button | `scripts/approval.py answer --surface phone --answered-by operator` runs; the message is edited with `✔ answered: …` and the answering seat is resumed as usual |
| gm wants to reply | `python3 plugins/telegram/tg_send.py "<text>"` (what prompts/gm.md tells it to run) |

Demo cards (`orchestra init --demo`, `feature = demo`) are never sent anywhere.

## Files

- `router.py` — inbound long-poll + card push; runs under `orchestra up` as service `telegram`. Stdlib only.
- `tg_send.py` — outbound CLI + the `send_text / send_photo / send_card` interface (track 6 moves `scripts/approval_notify.py`'s `_tg_*` helpers behind it).
- `__init__.py` — config/secret/status helpers; `plugins/__init__.py` is the registry core asks "which channel is configured".
- `tests/` — hermetic (fake Bot API, no token, tmp data dir).

## Troubleshooting

- `plugin:telegram MISSING — no TELEGRAM_BOT_TOKEN`: the shell running `orchestra up` doesn't have the variable; export it or use `<data>/.env.telegram`.
- `tg_send: no chat id`: nobody has messaged the bot yet — send `/start` from your phone, or pass `--chat <id>`.
- Nothing arrives: `orchestra status` shows the `telegram` service; its log (`<data>/logs/telegram.log`) prints every inbound row id and every card push.
