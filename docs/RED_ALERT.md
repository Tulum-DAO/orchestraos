# RED ALERT — the crash-report standard the system upholds itself to

Commissioned by the operator 2026-09-17, after the harness bottom-bar **^Z** button
suspended the gm seat (process STAT `T`, alive but not running) and nothing in the fleet
noticed. the operator's words: *"LOG EVERYTHING IN A RED ALERT CRASH REPORT … a list of errors
that it reports when a user detects and reports a problem. The list can be logged, added
to, and acted on immediately either by an always-on agent or a script … Errors, bugs,
crashes (lower priority: improvements) resolved in a self-healing framework."*

## 1. The report

One incident = one JSON file: `state/red-alert/<UTC ts>-<seat>-<class-or-slug>.json`.
Written only by `scripts/red_alert.py` (atomic replace). Raw pane snapshots go to
`logs/red-alert/<seat>-pane-<ts>.txt`.

| field | meaning |
|---|---|
| `id` | `ra_<8 hex>` — quote it everywhere (card, Telegram, msg_store subject) |
| `created_at` | UTC ISO |
| `reported_by` | `user` \| `watchdog` \| `agent` |
| `channel` | where it came from: `telegram`, `arturo`, `dashboard`, `harness`, `watchdog`, `msg_store` … |
| `severity` | `crash` (seat cannot work) \| `error` (seat alive, cannot progress) \| `bug` \| `improvement` |
| `seats` | the seat ids involved |
| `symptom` | the user's words, verbatim — never paraphrased |
| `class` | a catalogue key (§3) or null when unclassified |
| `evidence` | `pane_snapshot{seat:path}`, `process_state{seat:[{pid,stat,tty,cmd}]}`, `pane_dead`, `attached`, `screen` (last screenful), `log_excerpt`, `sids`, `registry_rows` (identity store, DB-first), plus anything the reporter adds |
| `diagnosis` | root cause, written by the repairing agent or the diagnosis seat |
| `immediate_fix` | `{action, how, performed_by, verified, steps[]}` — what restores function NOW |
| `permanent_fix` | `{items:[{n, what, where[], status}]}` — what makes the class impossible |
| `status` | `open` → `repairing` → `awaiting-approval` → `resolved` |
| `card_id` | the approval card (`apr_…`) that surfaced it |
| `repair_attempts[]` | `{at, action, ok, detail, by}` — every attempt, by effect |
| `escalations[]` | `{at, reason, by}` |
| `timeline[]` | append-only `{at, event, by, note}` |

## 2. The CLI — `scripts/red_alert.py`

```
report   --reported-by user|watchdog|agent --channel X --severity S --seat gm [--seat …]
         --symptom "<verbatim>" [--snapshot <file>] [--class <key>] [--no-capture]
list     [--status S] [--json]        show <id>
update   <id> [--status] [--diagnosis] [--immediate-fix JSON] [--permanent-fix JSON] [--card] [--note]
resolve  <id> --note "<verified how>"  escalate <id> --reason "<why>"
classify --seat <seat>                 # what class would the watchdog see right now
```
`report` auto-captures the evidence (tmux `capture-pane -S -3000`, `ps` STAT of the pane's
process tree, log tail, registry row + sid) and classifies it. Reports never touch a pane.

**Every user report lands here.** A problem said on Telegram, to Arturo, on the dashboard
or in a pane is filed with `--reported-by user --channel <that>` before anyone starts
fixing it. Fixing without a report is the bug.

## 3. The catalogue (the list of errors)

Source of truth: `CATALOGUE` in `scripts/red_alert.py` (tests assert every row here has a
severity, a detect rule, an immediate fix and a doc line). Add a class = add a row + a line.

| class | severity | detect | immediate fix (watchdog may do alone) |
|---|---|---|---|
| `process_suspended` | crash | pane process STAT contains `T`; screen "Claude Code has been suspended" | `card_only` — the process is still present, so under the NO KILLS rule the watchdog never touches it; a human runs `tmux respawn-pane -k` + `claude --resume <sid>` (SIGCONT/`fg` do not stick under a dash pane shell — gm 2026-09-18 04:31Z) |
| `pane_dead` | crash | `pane_dead=1` or no process on the tty | `respawn_resume`: `tmux respawn-pane` (never `-k`) with the registry `resume_command`, only when no process remains |
| `out_of_usage` | error | "usage limit" / "out of usage credits" / "credits depleted" | `switch_provider`: `/model` in-pane to the next `[runtimes] enabled` (Opus → Sonnet → gemini → codex); record it |
| `api_error` | error | "API Error" in the last screenful | `wait_then_retry` (90s, bare Enter); 3× → `switch_provider` |
| `login_screen` | crash | "Select login method" / "not logged in" | `card_only` — auth is the operator's |
| `bypass_permissions_dialog` | error | the "Bypass Permissions mode" accept dialog | `accept_dialog` (fleet policy is skip-permissions) |
| `composer_stuck` | bug | typed text at ❯ unsubmitted > 2 scans | `bare_enter` (nudge_pane semantics) |
| `tmux_server_dead` | crash | `tmux list-sessions` says no server while online seats are registered | `card_only` — one fleet report + card + Telegram; gm resumes each seat (`claude --resume <sid>`). Never kill a pid whose argv starts with `tmux`: the server keeps its first client's argv |
| `gateway_unreachable` | error | user report from phone/watch | `probe_health` on :9091 + funnel, report the failing hop |

## 4. Who acts, and when

1. **Watchdog** `scripts/red_alert_watch.py` (cron, every 60s) scans every live registered
   seat, files a report per finding (dedup per seat+class while open), posts the card (§5),
   and after the 2-minute window performs the immediate fix itself — only the fixes in §3,
   each verified by effect, never on a pane a human client is attached to (that is card-only).
2. **Repair fails twice** → `escalate` → a diagnosis seat on the highest model
   (`red-alert-diag-<id>`, Opus 5) fans the dirty work to Haiku subagents, writes
   `diagnosis` + `permanent_fix`, and opens a dialogue with the operator through Arturo
   (msg_store row to `gm`, type `red_alert`, subject `RED ALERT <id>: …`).
3. **Improvements** are filed with the same schema, never carded, reviewed by gm weekly.

## 5. Card + all channels

Every `crash`/`error` report → `scripts/approval.py request --from red-alert-builder
--worker-kind node --options "Repair now,Wait,Show me"` (op-key `red-alert:<id>`), mirrored to
Telegram (`tg-notify.sh`) and to Arturo (msg_store → gm, type `red_alert`). No answer in
120s → the watchdog repairs (safe fixes only), then updates the card and the report.
"Wait" holds 30 minutes. "Show me" replies with the snapshot path + screen tail.

## 6. Rules the standard enforces

- Symptom is verbatim. Evidence is captured, not described. Fixes are verified by effect
  (`classify --seat` returns null) before `status=resolved`.
- Never `fg`, never SIGCONT-and-hope: a suspended CLI is respawned with `--resume`.
- Never send text+Enter in one `send-keys`; wakes go through `scripts/nudge_pane.py`.
- **NO KILLS, EVER (the operator, 2026-09-18).** The self-heal loop never sends
  kill/TERM/KILL/STOP/pkill/kill-session/kill-server to anything and never runs a
  history-rewriting or tree-discarding git command on the live tree. Allowed repairs:
  `claude --resume` in a new or dead pane, `tmux respawn-pane` only when the process is
  already gone, in-pane `/model`, cards, mail. Anything else = card to the operator, and wait.
  Enforced by `scripts/test_red_alert_no_kills.py`.
- Never touch an attached pane except through the card.
- Before killing ANY pid, compare it with `tmux display -p '#{pid}'` and read its argv: a
  `tmux new-session …` argv with ppid 1 is the tmux SERVER (it keeps its first client's
  argv). Killing it drops the whole fleet (seen once, 2026-09-18).
- Liveness checks anchor the pattern (`pgrep -f '^python3 x.py'`): an unanchored fragment
  matches the tmux server / `sh -c` wrappers and the service is never restarted.
- The permanent fix for a user-reachable crash is removing the way to cause it
  (^Z button → gone; ^C → confirm). Re-report the same class twice = the permanent fix is late.

## 7. The first report

The standard was written from a real incident: a terminal key bar exposed **^Z**, the manager
seat's CLI was suspended (`STAT T`) and nothing noticed. The report captured the pane snapshot,
the stopped process tree and the identity row; recovery was `tmux respawn-pane -k` +
`claude --resume <sid>`; the permanent fix removed ^Z from every key bar and made the API
refuse `ctrl-z`. Incident ids and captures stay in the operator's own `state/red-alert/`.
