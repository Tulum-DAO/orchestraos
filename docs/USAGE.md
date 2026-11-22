# Plan usage

OrchestraOS shows how much of each AI plan's limit is used, and when each limit resets. You find it in two places:

- **Settings > Usage.** One card per provider (Claude, ChatGPT/Codex). Each card shows the provider's mark with a ring, then one bar per window: the 5-hour session, the week, and the per-model weeks (Opus, Sonnet) when the provider reports them. The bars are green, turn amber at 70% and red at 90%. Each bar says when its window resets.
- **The model picker.** Each provider's mark on the left rail has the same ring. Hover over the mark (or focus it) to see the provider, whether it is signed in, and its usage.

Only an admin sees usage. The data comes from `GET /api/usage`, and any other user gets that route's 404, so the page shows nothing for them.

## What you see on a fresh install

Nothing is guessed. Until a provider has reported a reading, its line says **No reading yet** and gives the reason, for example "Claude Code has not reported usage yet". You never see an empty bar at 0%.

A reading older than 30 minutes is greyed out and says when it was taken ("as of 3:05 PM"). A reading stamped more than 5 minutes in the future is treated the same way, since that means a clock is wrong.

Gemini and Grok report no usage that OrchestraOS can read yet. Each gets a line that says so.

## Where the numbers come from

By default OrchestraOS only reads local files. It makes no network call.

| Provider | Source | Written to |
|---|---|---|
| Claude | Claude Code passes its plan-usage block (`rate_limits`) to the OrchestraOS status line (`hooks/statusline.py`) after each reply. The status line copies it to disk. Install the status line with `orchestra init`, or see [HOOKS.md](HOOKS.md). | `<data dir>/state/usage/claude-<account>.json` |
| Codex | The newest rate-limit event in Codex's own session logs (`~/.codex/sessions`). | read in place |
| Claude, Codex (optional) | `scripts/usage_poll.py`, see below. | `<data dir>/state/usage/api-<provider>-<account>.json` |

`scripts/usage_meter.py` merges these sources. For each account and window, the newest reading wins. Any reading can fill in a window the others lack: for example, the status line may only have the 5-hour window while the poller has the weekly one.

Within one window (the same reset time), usage only goes up. A lower reading for the same window is an older one arriving late, so the status line does not write it.

The Claude account is named after its config dir: `~/.claude` is "default" (shown as "Main login"), and `CLAUDE_CONFIG_DIR=~/.claude-work` is "work". With one login per provider, you see one card per provider with one set of bars. If readings exist for more than one Claude login, the card lists each login separately. The ring follows the login the agents run on, so another login's full week does not fill it.

## Optional: polling the providers' usage endpoints

The status line only reports while Claude Code is running. A Codex reading only appears after a Codex session. If you want readings that stay fresh while nothing is running, turn on the poller. It is **off by default**.

1. In `orchestra.toml`:

   ```toml
   [usage]
   poll = true
   poll_minutes = 5      # at most one call per login per this many minutes
   ```

2. Run it from cron, on the same period or a longer one:

   ```cron
   */5 * * * * cd /path/to/orchestraos && python3 scripts/usage_poll.py
   ```

   Each run prints one JSON line. With `poll = false` it prints `{"ran": false, ...}` and makes no call.

What it does, per login (`$CLAUDE_CONFIG_DIR` or `~/.claude`, and `$CODEX_HOME` or `~/.codex`):

- It reads the access token the CLI already stored, sends it in one request to that provider's usage endpoint, and drops it. The token is never logged or written to disk. Error records keep only the error class and the HTTP status.
- It never refreshes or renews a login. An expired token is not sent at all. The CLI refreshes its own token the next time it runs.
- It makes at most one call per login per interval, across the whole host. A lock file and the backoff state live in `~/.cache/orchestraos/usage-poll/` (or `$ORCHESTRA_USAGE_LOCK_DIR`), named by a hash of the login file's path.
- It makes one attempt per run, never a retry loop. It backs off on 429 or 503 (it obeys `Retry-After`), on 401 or 403 (30 minutes), and on timeouts and server errors (exponential, up to an hour).
- It never follows a redirect, so the token cannot be forwarded to another host.
- Snapshots hold labels and numbers only: no token and no e-mail address.

### Terms

The poller reads usage endpoints that the Claude Code and Codex CLIs use themselves, with the login those CLIs already hold. Neither provider documents these endpoints as a public API. Provider terms can restrict automated access to a subscription plan. Read the terms that apply to your plan (for Anthropic, the Consumer or Commercial Terms; for OpenAI, the Terms of Use or your workspace's business terms) before you set `poll = true`. If you are unsure, leave it off: the view still works from the local sources.

## Paired devices

Phones and watches read the same rows from the gateway's `GET /usage`. That needs the `usage` scope; `read` alone does not show usage.
