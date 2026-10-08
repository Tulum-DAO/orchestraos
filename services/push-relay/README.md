# Push relay

A Cloudflare Worker that lets self-hosted OrchestraOS gateways send push notifications to the
published iPhone, iPad and Watch apps.

Only the team that owns an app can hold its APNs key, so a stranger's gateway can't push to the
App Store app directly. The app owner runs this relay with the key; gateways ask it to push.
An install that ships its own app build doesn't need it: set `ORCHESTRA_PUSH_DELIVERY=direct`
and send with your own key.

No dependencies. It runs on the Workers free plan with one D1 database.

## What it will and won't do

- **Accepts device tokens only from the genuine app.** The app registers through App Attest;
  the relay verifies Apple's attestation (certificate chain to the pinned Apple App Attestation
  Root CA, nonce, app id, environment, key id) and returns an opaque handle. A gateway only
  ever sees the handle, never the APNs token.
- **Pushes only to devices that chose that gateway.** The app binds its handle to a gateway
  install with an App Attest assertion; a gateway can push to a handle only if it's bound to it.
- **Writes every alert itself.** A gateway sends a kind, a count and an opaque card id. The
  alert reads "Approval waiting" or "N approvals waiting". No gateway text reaches APNs, so the
  relay never carries card content and can't be used to send arbitrary messages.
- **Rate-limits everything** (per device, per install, per IP) and keeps no logs of tokens,
  handles or card ids.
- **If it's down, nothing is lost but speed:** the apps keep polling their gateway.

## Protocol (v1)

App ↔ relay:

| Request | Body | Auth |
|---|---|---|
| `GET /v1/challenge` | | → `{challenge, expires_in}` (single use, 5 min) |
| `POST /v1/devices` | `{key_id, attestation, challenge, apns_token, env, bundle_id, platform}` | App Attest attestation over `SHA256(challenge)` → `{handle}` |
| `PUT /v1/devices/{handle}` | `{apns_token?, install_id?}` (`install_id: null` unbinds) | `X-Assertion` over `SHA256(raw body)`; counter must grow |
| `DELETE /v1/devices/{handle}` | `{"forget": true, "at": <ms>}` | `X-Assertion` as above |

`env` is `sandbox` (Xcode builds) or `production` (TestFlight and App Store) and must match the
attestation. `platform` is `iphone`, `ipad` or `watch`; the watch attests its own key (App Attest
is on watchOS 9+). A device whose `DCAppAttestService.isSupported` is false doesn't register; it
polls.

Gateway ↔ relay:

| Request | Body | Auth |
|---|---|---|
| `POST /v1/installs` | `{}` | → `{install_id, install_secret}` (the secret is shown once) |
| `POST /v1/push` | `{install_id, handle, kind: "approval", count, card_id, category, collapse}` | `X-Install-Timestamp` (±300 s) and `X-Install-Signature` = hex HMAC-SHA256(secret, `"<ts>.<raw body>"`) |

`category` is `approval.single`, `approval.multipart` or `approval.summary`; `collapse` is
`card:<card_id>` or `summary`. Responses: `{ok: true}`; `{ok: false, gone: true}` when APNs says
the token is gone (the relay deletes the device; the gateway should drop the handle); `429` when
rate-limited; `502` with APNs' reason otherwise.

Limits: 30 pushes per device per hour, 300 per install per hour; per IP, 30 challenges an hour,
20 registrations and 10 installs a day.

## Configuration

| Name | Kind | Value |
|---|---|---|
| `DB` | D1 binding | a database created from `schema.sql` |
| `TEAM_ID` | var | the Apple team id that owns the app |
| `BUNDLE_IDS` | var | comma-separated app bundle ids this relay serves |
| `APNS_KEY_ID` | var | the APNs key id |
| `APNS_KEY` | **secret** | the `.p8` key, PEM. Upload it as a Worker secret; never put it in code, config or logs. |

## Tests

```
cd services/push-relay && npm test
```

Needs Node 22.5+ (for `node:sqlite`, which stands in for D1) and `openssl` (the tests build a
synthetic App Attest chain with the same shape as Apple's). APNs is mocked: a deployed Worker
negotiates HTTP/2 with APNs, but local `wrangler` can't, so the real proof is one push to a real
device after deploying.
