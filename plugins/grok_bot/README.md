# Grok Bot webhook

Tells an outside bot app (your "Grok Bot") that its OrchestraOS mailbox has new mail, so the bot
can come and read it. It is a notifier, not a chat channel: it never sends or reads message text.

## What it sends

When the agent (default `grok-bot`) has pending messages it has not reported yet, one HTTPS POST:

```json
{"agent": "grok-bot", "pending": 2, "new_ids": ["msg_...", "msg_..."]}
```

with `Authorization: Bearer <key>`. No subject, no body: someone who learns the URL learns only
that mail is waiting. A failed POST is retried (3 tries, backoff) and the ids stay unreported
until one is accepted. Each id is reported once.

## Turn it on

1. In `orchestra.toml`:

   ```toml
   [plugins.grok_bot]
   enabled = true
   agent = "grok-bot"      # the seat / mailbox your bot reads
   ```

2. Run `orchestra up` (or restart it). It creates `<data dir>/state/grok-bot-webhook.env`
   (mode 600) with placeholders.
3. Put the webhook URL and key your bot app gives you in that file:

   ```
   GROKBOT_WEBHOOK_URL=https://...
   GROKBOT_WEBHOOK_KEY=...
   ```

   The URL must be `https://`. The key is never logged and never goes in `orchestra.toml`.
   While either value is a placeholder, nothing is posted.
4. `orchestra doctor` shows `plugin:grok_bot`: off, on but not configured, or ready.

Pause it without editing config: `touch <data dir>/state/GROK_BOT_WEBHOOK_DISABLED`.
Sent ids are kept in `<data dir>/state/grok-bot-webhook.json`.

Where inotify is not available (macOS), it re-counts every 10 seconds instead.
