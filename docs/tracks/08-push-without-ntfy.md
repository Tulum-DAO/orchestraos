# Track 8 — Push without ntfy

Size: S/M · Labels: `track`, `notify`, `ios`

## Problem

Push notifications for approval cards go through ntfy today:
`scripts/approval_notify.py`'s `_default_ntfy_publish()` (~line 395) posts to
`NTFY_BASE` with a bearer token from `~/.config/jarvis/ntfy-token`, and refuses
outright if that token is missing ("ntfy token missing ... refusing to push
unauthenticated"). That means push requires standing up and trusting a third
server (self-hosted or otherwise) even for a single-operator install with the app
already paired directly to the gateway.

The push is also one-way in practice. The process that consumes its action-button
taps (`scripts/approval_listener.py`) is not in the supervisor's process table and
is not running on the operator's reference fleet, so no card has ever been answered
through ntfy: of 628 recorded decisions the answer surfaces were watch 152, agent
CLI 37, phone app 15, web 2 — ntfy zero. Two consequences for this track: `none` is
the intended default for a single-operator install (the card is durable in the
ledger the instant it is filed, and the app polls), and an APNs push must deep-link
into the app rather than inherit ntfy's dead-end button pattern.

## Design

A `notify` backend abstraction for push specifically (distinct from the channel
plugins in Track 6, which are for chat-style notify — approval-card *push* is its
own concern): `apns`, `ntfy` (today's path, unchanged), and `none` (in-app polling
only — the app already has to poll for state anyway; `none` just means "don't also
push").

`apns` needs: a bundled Apple Push key (`.p8`) referenced from `orchestra.toml` by
path (never embedded in the file), the app's bundle id and team id (already in the
iOS app's config per the extraction notes — `Config/Base.xcconfig`), and device
tokens sourced from Track 1's pairing (`paired_devices` gains a `push_token`
column, set when the app registers for remote notifications and reports its token
back to the gateway via a new `POST /pair/devices/<id>/push-token` route — the app
already has a `/register-device` gateway call in `GatewayClient` per
`docs/GATEWAY_API.md`'s endpoint list; build the token round-trip on that existing
call shape rather than inventing a new client-side pattern). When a
card is created, the same code path that calls `_default_ntfy_publish` today calls
whichever backend `[notify] push_backend` selects, looked up per paired device (a
device with no push token registered just doesn't get a push — no error).

## Files you will touch

- `scripts/approval_notify.py` — generalize the push call site around
  `_default_ntfy_publish` (~line 395) into a backend-selectable function; keep the
  ntfy path byte-for-byte identical when selected.
- `scripts/watch_gateway.py` — new `POST /pair/devices/<id>/push-token` route
  (depends on Track 1's `paired_devices` table existing); add the `push_token`
  column. The `<id>` here is the `device_id` Track 1's `/pair/claim` now returns
  and the app keeps in Keychain — without that field in the claim response the
  app has no way to learn which row is its own.
- `scripts/notify_apns.py` (new) — APNs HTTP/2 provider-API client (token-based
  auth via the `.p8` key, not the older certificate-based auth); keep it small and
  dependency-light, matching the rest of `scripts/`'s style (plain `requests`/http
  calls, no heavy push SDK).
- `orchestra.example.toml` — `[notify] push_backend = "apns" | "ntfy" | "none"`,
  `[notify.apns] key_path`, `team_id`, `bundle_id`, `key_id`.
- `orchestra_cli/doctor.py` — a `notify:push` row reporting the active backend and
  whether its required config/credentials are present.
- iOS repo — register for remote notifications, report the device token to the new
  gateway route, handle a received push (deep-link to the card).

## Steps

1. `grep -n "_default_ntfy_publish\|ntfy_token" scripts/approval_notify.py` —
   confirm today's single call site before generalizing it.
2. Build `notify_apns.py` against Apple's provider API in isolation first (a
   standalone script that sends one test push to a known device token) — prove it
   works before wiring it into the approval flow.
3. Add the `push_token` column and the pairing-time registration route; confirm a
   paired device's token round-trips (`GET /pair/devices` shows it).
4. Wire `approval_notify.py`'s push call through the backend selector; with
   `push_backend = "apns"` and no ntfy token configured anywhere, create a card and
   confirm a push arrives on a paired device.
5. Confirm `push_backend = "none"` sends nothing and the app still sees the card
   via its normal poll.
6. `orchestra doctor` with each backend selected — confirm the `notify:push` row
   matches reality (OK when configured, a clear remedy when not).

## Acceptance test

An approval card raises an APNs push notification on a paired device (Track 1) with
no ntfy server configured anywhere (`NTFY_BASE` unset, no ntfy token file). The
push deep-links to the card in the app.

## Start prompt

```
I'm working Track 8 (push without ntfy) for the OrchestraOS hackathon.
This depends on Track 1 (device pairing) for the device-token registration
path — check whether paired_devices exists in scripts/watch_gateway.py
before starting; if not, coordinate with whoever has Track 1 or build a
minimal stand-in table and note the merge conflict for later.
Read docs/tracks/08-push-without-ntfy.md in this repo for the full design.
Files to touch: scripts/approval_notify.py (generalize the push call site),
scripts/notify_apns.py (new APNs client), scripts/watch_gateway.py (new
push-token registration route), orchestra_cli/doctor.py (notify:push row).
Start by proving the APNs client works standalone against one known device
token (Step 2) before wiring it into the approval flow — isolate vendor
integration risk from the harness plumbing.
```

## Out of scope

- Android push (no Android app in this repo yet).
- Rich push actions (approve/deny directly from the notification) — v1 deep-links
  into the app, it does not act from the lock screen.
- Multi-provider push fan-out to more than one paired device per card (send to all
  paired devices with a registered token; no per-device routing rules yet).
