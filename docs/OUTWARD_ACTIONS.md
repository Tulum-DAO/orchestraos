# Outward actions: approved once, run once

An outward action is anything an agent does that leaves the install: a deploy, a payment, a
message to a person, a push notification. The outward-action ledger (`scripts/outward_ledger.py`)
holds one rule for all of them:

> An approved outward action runs exactly what the card showed, once. An interrupted one becomes
> `uncertain` and is never re-run automatically. A changed material argument after approval is
> refused. A duplicate pending card for the same action is refused.

The ledger is its own SQLite file, `<data dir>/state/outward-actions.db` (`OUTWARD_LEDGER_PATH`
overrides it, the way `APPROVAL_DB_PATH` overrides the approvals DB). Nothing in the approvals
schema changes; the card only mirrors the action's state in its `evidence.action` object.

## What makes two actions "the same"

The action name plus its **material arguments**: the JSON object the agent puts on the card. The
ledger hashes `{"action": NAME, "args": ARGS}` after normalising it (sorted keys, no whitespace,
NFC strings, ASCII). So:

- key order and whitespace do not matter;
- list order does (a deploy to `[a, b]` is not a deploy to `[b, a]`);
- a float is refused (`material args must not carry floats; pass a string`), so an amount is
  `"12.50"`, never `12.5`;
- `null` and a missing key are different arguments on purpose.

The card id is **not** part of the hash: a new card for the same arguments is the same action.

## The states

| state | meaning |
| --- | --- |
| `asked` | the card is pending |
| `approved` | the card was answered with one of the `--run-on` answers (default `approve`) |
| `executing` | an executor claimed it; the row records the claiming pid, its start time and host |
| `done` | the executor finished; a later card for the same arguments is allowed |
| `not_run` | nothing happened: denied, a text-only answer, the card expired / was retired / discarded, or the executor finished without acting. `reason` says which. A later card is allowed |
| `uncertain` | the executor claimed and never finished (killed, or its claim is older than `OUTWARD_CLAIM_TTL_S`, default 6 h). **It may have run.** Nothing moves out of this state |

Every transition is one `UPDATE … WHERE state = <expected>`; a transition the row is not in is
refused with the row's current state named (exit 3 from the CLI, one stderr line).

## Filing a card for an action

```
approval.py request "Deploy build 42 to prod?" --from deploy-bot --worker-kind node \
    --action deploy --material-args '{"target":"prod","build":"42"}' [--run-on approve]
```

The ledger row is filed with the card and `evidence.action` is written onto the card. If the
same action and arguments already have a pending or approved card, `request` prints **that**
card's id and exits 0 (idempotent re-requesters keep working). If the action is `executing`, or
`uncertain` and not yet released, it exits 3: `<hash> is executing|uncertain: release it or wait`.

## How an executor claims

Claim with the **exact** arguments you are about to use, run, then finish:

```
H=$(outward_ledger.py claim --action deploy --material-args '{"target":"prod","build":"42"}' \
      --actor deploy-bot | jq -r .action_hash)        # exit 3: not approved / already running
... do the deploy ...
outward_ledger.py finish --hash "$H" --state done --reason "sent"
```

A changed argument hashes to a row that does not exist: `not approved`. A second claim while the
first is executing, done-and-waiting, or uncertain is refused. The claim records the **caller's**
pid (`--pid`, default the CLI's parent process), its `/proc` start time and the hostname, so a
reused pid number cannot look alive. From Python: `outward_ledger.claim(action, args, actor)`
records `os.getpid()`.

`outward_ledger.py sweep` (run at the top of every `approval.py` and `outward_ledger.py` call)
turns a lost claim into `uncertain`, and reconciles `asked` rows against their cards, so a crash
between the two databases heals on the next invocation.

## When something is uncertain

The operator must see it, because the approved card has already left the pending list. The
ledger files **one** "Waiting on you" card from the executing agent:

> Check: deploy may have run, will not retry

with the original card id and the reason as its summary, and the verbs done / cant / snooze.
"done" means "I checked". The action itself stays `uncertain` forever.

To try again, an operator (or gm) releases it, then files a new card:

```
outward_ledger.py release --hash H --by shaw --reason "checked the host: the deploy never started"
approval.py request ... --action deploy --material-args '...'
```

Release keeps the row `uncertain` (the fact that it may have run is never erased); it stamps who
released it and why, and lets a new row for the same arguments exist. The reason is required, one
line, at most 200 characters, and is shown verbatim.

## The notification exception

The first ntfy push and the first Telegram ping for a card go through the same ledger
(`notify.ntfy` / `notify.telegram`, claimed directly, no card of their own): a notifier killed
between claim and send leaves an `uncertain` row instead of a silent duplicate. A failure before
the network call is `not_run` ("RuntimeError: before send") and is retried as before; a failure
during or after it is `uncertain` ("OSError: during send").

Because a dropped push would break the delivery contract (held, never dropped) while a duplicate
push is the lesser harm, these rows carry `policy='at_least_once'`: an uncertain push gets **one**
more attempt (attempt 2, driven by the existing cron backstop), never a third. If attempt 2 is also
uncertain, the "Check:" card is filed. Every other action class is `policy='once'`.

Not wrapped: the "not acking" escalation push (its 30-minute repeat is designed) and the re-fire
when the phone returns to the tailnet.

## Where the state shows

`evidence.action = {hash, action, state, reason, updated_at}` on the card row. The gateway poll,
`approval.py get` and `approval.py pending --json` carry it; the web API passes it through as
`evidence`. Suggested labels: executing "working", done "done", not_run "not run: <reason>",
uncertain "may have run, won't retry".
