# Track 1 — Device pairing replaces the built-in app token

Size: S/M · Labels: `track`, `ios`, `gateway`

## Problem

The iOS app bakes the gateway base URL and a bearer token into the build at compile
time (`Config/Local.xcconfig`, gitignored, filled in per-developer). There is no
login flow: a new phone needs a maintainer to hand-edit and rebuild the app with a
fresh token. The file that proves it: `orchestraos-ios` app's `Config/Base.xcconfig`
(placeholders `GATEWAY_BASE_URL` / `GATEWAY_TOKEN`) and the gateway's single static
token in `scripts/watch_gateway.py` (`gateway_token()`, line ~60), which every
device shares — there is no per-device identity, so "revoke this phone" is not a
thing that exists.

## Design

Two new gateway routes, both unauthenticated by design (the code itself is the
credential, time-boxed):

- `POST /pair/start` — mints a 6-digit numeric code + a QR payload (the gateway's
  base URL + the code), 5-minute TTL, single use. `orchestra pair` (new CLI command)
  calls this and prints both the digits and a terminal QR.
- `POST /pair/claim {code, device_name}` — validates the code against the TTL
  store, mints a new per-device bearer token, records `{device_id, device_name,
  created_at, last_seen}` in a `paired_devices` table (new, in the gateway's sqlite
  state), and returns `{bearer, device_id, gateway_base_url}`. The app's first
  screen (camera scan or manual 6-digit entry) calls this and stores `bearer` +
  `device_id` in Keychain alongside `gateway_base_url`. `device_id` in the response
  is not optional: Track 8's push-token registration is
  `POST /pair/devices/<device_id>/push-token`, addressed by the app's own id, and
  `/pair/claim` is the only place the app ever learns it.

Every existing gateway route keeps its bearer check (`_authorized`,
`scripts/watch_gateway.py` line ~68 — `gateway_token()` at line ~60 reads the
static token) but the check widens from "matches the one static token" to "matches
the static token OR a live row in `paired_devices` whose `revoked_at` is null" —
constant-time comparison against each candidate, same as today's single-token
check. A `DELETE /pair/devices/<device_id>` route (bearer-gated, any paired device
can revoke any other — single-operator app, no per-device ACL yet) sets
`revoked_at` and the device's next request gets 401.

The watch keeps the existing single-token path until it gets its own pairing flow
(explicitly out of scope below) — `paired_devices` is additive, not a replacement,
until the watch migrates.

On the app side, the real integration point is narrower than "replace the
xcconfig": the app does not read `Config/Local.xcconfig` at runtime at all today —
`Sources/iOS/GatewayClient.swift:11-22` reads the Info.plist keys `GatewayBaseURL`
/ `GatewayToken` via `Bundle.main.object(forInfoDictionaryKey:)`, and the xcconfig
only feeds those plist keys at *build* time. So the pairing screen's job is to make
`GatewayConfig`'s token/base-URL lookup prefer a Keychain value (written by
`/pair/claim`) over the Info.plist read, and "no baked token" becomes "Keychain
empty" — that's the condition that routes to the pairing screen instead of
`Config/Local.xcconfig` being absent.

## Files you will touch

- `scripts/watch_gateway.py` — add `/pair/start`, `/pair/claim`,
  `/pair/devices` (list), `/pair/devices/<id>` (DELETE); extend `_authorized`
  (~line 68) to check `paired_devices`; new sqlite table + migration alongside the
  gateway's existing state tables (search `CREATE TABLE` in this file for the
  pattern).
- `orchestra_cli/__main__.py` — new `pair` subcommand (`orchestra pair`), calls
  `/pair/start`, prints the code and a QR (use a small terminal-QR library or ASCII
  fallback — no new heavy dependency).
- `orchestraos-ios` repo (separate checkout, see EXTRACTION note below) —
  `Sources/iOS/GatewayClient.swift:11-22` (`GatewayConfig`'s token/base-URL lookup:
  add a Keychain source that takes priority over the existing
  `Bundle.main.object(forInfoDictionaryKey:)` read of `GatewayBaseURL`/
  `GatewayToken`); new first-run screen: camera scan (QR → the claim payload) or
  manual code entry, calls `/pair/claim`, writes `bearer` + `device_id` +
  `gateway_base_url` to Keychain. Settings screen lists paired devices (`GET /pair/devices`) with a
  revoke button per row.
- `docs/GATEWAY_API.md` (in the iOS repo) — document the two new routes.

## Steps

1. `cd services && python3 -c "import scripts.watch_gateway"` — confirm the gateway
   module still imports cleanly before you start (baseline).
2. Add the `paired_devices` table and the two routes to `watch_gateway.py`. Run the
   gateway locally (`orchestra up` or `python3 scripts/watch_gateway.py` directly)
   and `curl -X POST localhost:<port>/pair/start` — expect
   `{"code": "123456", "expires_at": "..."}`.
3. `curl -X POST localhost:<port>/pair/claim -d '{"code":"123456","device_name":"test"}'`
   — expect `{"bearer": "...", "device_id": "...", "gateway_base_url": "..."}`. A
   second claim with the same code must 400 (single-use).
4. `curl -H "Authorization: Bearer <new bearer>" localhost:<port>/api/approvals` —
   expect the same 200 the static token gets today.
5. `curl -X DELETE -H "Authorization: Bearer <static token>" localhost:<port>/pair/devices/<device_id>`,
   then repeat step 4 with the revoked bearer — expect 401.
6. Wire the iOS first-run screen against the local gateway (or a Tailscale/LAN
   address), scan or hand-enter the code, confirm the approvals list loads.
7. `orchestra pair` from a terminal — confirm it prints a code and the app's manual-
   entry path accepts it.

## Acceptance test

Fresh app install (no baked token, `Config/Local.xcconfig` absent or empty) → the
app shows the pairing screen, not the approvals list → scan (or type) the code from
`orchestra pair` → the approvals list loads with live data. Then: revoke that
device from Settings (or `curl -X DELETE .../pair/devices/<id>`) → the app's next
API call returns 401 and the app returns to the pairing screen.

## Start prompt

```
I'm working Track 1 (device pairing) for the OrchestraOS hackathon.
Read docs/tracks/01-device-pairing.md in this repo for the full design.
Files to touch: scripts/watch_gateway.py (new /pair/start, /pair/claim,
/pair/devices routes + paired_devices table), orchestra_cli/__main__.py
(new `orchestra pair` command), and the orchestraos-ios repo's first-run
screen + Settings device list.
Start with scripts/watch_gateway.py: add the table and the two routes,
prove them with curl per the doc's Steps 1-5, then move to the CLI
command and the iOS screen (Sources/iOS/GatewayClient.swift:11-22 is the
real integration point — it reads Info.plist, not the xcconfig, at
runtime; add a Keychain source ahead of it). Existing bearer auth must
keep working unchanged for the watch app.
```

## Out of scope

- Migrating the watch app off the static token (stays on it until its own track).
- Per-device permission scoping (any paired device can act as the operator; no
  read-only or limited-scope devices yet).
- Multi-operator pairing (multiple humans, not just multiple devices for one
  operator) — the whole harness is still single-operator.
