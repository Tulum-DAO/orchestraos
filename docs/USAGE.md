# Plan usage

OrchestraOS shows how much of each AI plan's limit is used, and when each limit resets. You find it in two places:

- **Settings > Usage.** One card per provider (Claude, ChatGPT/Codex). Each card shows the provider's mark with a ring, then one bar per window: the 5-hour session, the week, and the per-model weeks (Opus, Sonnet) when the provider reports them. The bars are green, turn amber at 70% and red at 90%. Each bar says when its window resets.
- **The model picker.** Each provider's mark on the left rail has the same ring. Hover over the mark (or focus it) to see the provider, whether it is signed in, and its usage.

Only an admin sees usage. The data comes from `GET /api/usage`, and anyone else gets that route's 404, so for them the page only says that an admin can see plan usage.

> **Turn polling off with one line** in `orchestra.toml`:
>
> ```toml
> [usage]
> poll = false
> ```
>
> Polling is on by default. Read [Terms](#terms) below before you leave it on.

## What you see on a fresh install

Until a provider has a reading, its line says **First reading in a moment**, with the off switch (`[usage] poll = false`) right next to it. The first reading usually arrives within a minute of opening Settings > Usage. With polling off, the line says **No reading yet** and gives the reason, for example "Claude Code has not reported usage yet". Either way, there is never an empty bar at 0%.

A reading older than 30 minutes is greyed out and says when it was taken ("as of 3:05 PM"). A reading stamped more than 5 minutes in the future is greyed out the same way, since that means a clock is wrong, and it never hides a fresh reading.

A login with no reading for 14 days leaves the view. Its file stays on disk, and it comes back with its next reading.

Gemini and Grok report no usage that OrchestraOS can read yet. Each gets a line that says so.

## Where the numbers come from

| Provider | Source | Written to |
|---|---|---|
| Claude | Claude Code passes its plan-usage block (`rate_limits`) to the OrchestraOS status line (`hooks/statusline.py`) after each reply. The status line copies it to disk. Install the status line with `orchestra init`, or see [HOOKS.md](HOOKS.md). | `<data dir>/state/usage/claude-<account>.json` |
| Codex | The newest rate-limit event in Codex's own session logs (`~/.codex/sessions`). | read in place |
| Claude, Codex | The poller, `scripts/usage_poll.py` (on by default, see below). | `<data dir>/state/usage/api-<provider>-<account>.json` |

`scripts/usage_meter.py` merges these sources. For each account and window, the newest reading that is not stale wins. Any reading can fill in a window the others lack: for example, the status line may only have the 5-hour window while the poller has the weekly one.

Usage within one window (the same reset time) normally only goes up. A lower status-line reading for the same window is usually an older block arriving late from an idle session, so for 15 minutes the higher reading is kept, along with its own timestamp. After that, the lower reading is accepted, because the drop is real (for example, a plan upgrade mid-week). The poller's reading is always taken as it comes.

The Claude account is named after its config dir: `~/.claude` is "default" (shown as "Main login"), and `CLAUDE_CONFIG_DIR=~/.claude-work` is "work". With one login per provider, you see one card per provider with one set of bars. If readings exist for more than one Claude login, each login gets its own card. The login this install runs Claude with (the server's `$CLAUDE_CONFIG_DIR`, else `~/.claude`) is marked "Agents use this", and the ring follows that login, so another login's full week does not fill it.

## The poller

The status line only reports while Claude Code is running, and a Codex reading only appears after a Codex session. The poller keeps readings fresh in between.

**It is on by default.** The gateway starts a run in the background when the dashboard asks for usage (Settings > Usage, or the model picker), so nothing polls while nobody is looking. To keep readings fresh even then, also run it from cron. Set the data dir explicitly in the cron line, so the snapshots land where the gateway reads them:

```cron
*/5 * * * * cd /path/to/orchestraos && ORCHESTRA_DIR=/path/to/your/data/dir python3 scripts/usage_poll.py
```

Each run prints one JSON line. With `poll = false` it prints `{"ran": false, ...}` and makes no call. `poll_minutes` (default 5) sets how often a login can be polled.

What it does, per login (`$CLAUDE_CONFIG_DIR` or `~/.claude`, and `$CODEX_HOME` or `~/.codex`):

- It reads the access token the CLI already stored, sends it in one request to that provider's usage endpoint, and drops it. The token is never logged or written to disk. Error records keep only the error class and the HTTP status.
- It never refreshes or renews a login. An expired token is not sent at all. The CLI refreshes its own token the next time it runs.
- It makes at most one call per login per interval, across the whole host. A lock file and the backoff state live in `~/.cache/orchestraos/usage-poll/` (or `$ORCHESTRA_USAGE_LOCK_DIR`), named by a hash of the login file's path.
- It makes one attempt per run, never a retry loop. It backs off on 429 or 503 (it obeys `Retry-After`), on 401 or 403 (30 minutes), and on timeouts and server errors (exponential, up to an hour).
- It never follows a redirect, so the token cannot be forwarded to another host.
- Snapshots hold labels and numbers only: no token and no e-mail address.

### Terms

Read this before you leave polling on.

- **There is no documented API for subscription usage.** Claude Pro and Max, and ChatGPT plans, publish no usage API. For Claude, the poller calls the same endpoint (`/api/oauth/usage`) that Claude Code's own `/usage` command calls, with the login Claude Code stores. For Codex, it calls the endpoint the Codex CLI uses to show its limits, with the Codex login.
- **Anthropic's [Consumer Terms](https://www.anthropic.com/legal/consumer-terms)** (Free, Pro, Max) forbid accessing the Services "through automated or non-human means, whether through a bot, script, or otherwise", except "via an Anthropic API Key or where we otherwise explicitly permit it". Claude Code's [legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance) says OAuth sign-in "is designed to support ordinary use of Claude Code and other native Anthropic applications", and that Anthropic may enforce its restrictions "without prior notice". The poller is a script using that OAuth login outside the Claude Code app.
- **OpenAI's [Terms of Use](https://openai.com/policies/row-terms-of-use/)** forbid users to "Automatically or programmatically extract data or Output". A ChatGPT Team or Business workspace may fall under OpenAI's business terms instead.
- **The official usage API does not cover subscriptions.** Anthropic's [Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) is an Admin API for Claude Console (API-key) organizations. It does not report Pro or Max plan limits.

To turn polling off, set `poll = false` under `[usage]` in `orchestra.toml`. The view then works from the status line and Codex's session logs alone. OrchestraOS cannot give legal advice. If you are unsure, turn it off.

## Paired devices

Phones and watches read the same rows from the gateway's `GET /usage`. That needs the `usage` scope; `read` alone does not show usage.
