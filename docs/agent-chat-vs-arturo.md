# Agent chat vs Arturo: what each has

## Only agent chat has
- Reads each coding tool's own session log, with support for Claude Code, Codex, Gemini and Grok Build.
- Decision cards and live menu cards you can answer in the chat.
- Tool-call cards and thinking blocks, with Markdown rendering.
- A terminal view and a Stop button.
- Delivery status on sent messages: queued, held or unknown.
- Export and resume of a conversation.

## Only Arturo has
- Replies that stream in word by word.
- A header pill with the assistant's face, and a greeting.
- A list of past chats.
- A settings panel.
- Choice, pair and jobs cards.
- Call cards.
- Light and dark themes.

## Hard to port
- Word-by-word streaming for agent chat: agent replies are read from logs after they're written, so real streaming would need the coding tool to expose its output live.
- A terminal view and Stop for Arturo: Arturo doesn't run in a terminal session, so there's nothing equivalent to show or stop.
- Light theme for agent chat: its cards are built dark-only, so theming them means reworking each card.

## Easy wins
- Arturo's face pill on the agent chat header.
- Arturo's greeting on the agent chat page.
- Markdown rendering in Arturo.
