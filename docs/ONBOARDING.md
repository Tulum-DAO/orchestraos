# Onboarding — connect your phone and the web dashboard to your own gateway

For someone who already has a gateway running and wants to reach it from a
phone and from a browser, on their own network, with no baked-in token. Two
client surfaces, one pairing flow.

> **Landing tonight.** `orchestra pair`, `/gateway/identity`, `/gateway/capabilities`,
> and `/pair/exchange` are new — check `orchestra pair --help` and
> `git log -- scripts/watch_gateway.py` before relying on the exact shape below;
> this doc is written against the frozen contract, not a guess.

## Before anything else: log in

Do this before you do anything below — install and log in to one agent CLI
(Claude Code, Gemini CLI, or Codex). `docs/INSTALL.md` §0 has the exact
commands. If you have no subscription to any of them, Gemini CLI's free tier
needs no credit card — that's the zero-cost path onto this whole doc.

Two things people get wrong here, stated up front so you don't have to guess
mid-flow:

- **The gateway and the dashboard are different ports.** `8890` is the
  gateway (what you're pairing to); `8891` is the web dashboard (what you
  open in a browser). Typing the dashboard's port where the gateway's goes
  is the single most common mistake on this page.
- **The pairing code is a credential.** Anyone who has it before you use it
  can pair their own device to your server. Never screenshare a terminal
  while a live code is on screen.

## 1. Run your gateway

```bash
orchestra up --detach && orchestra status
```

Confirm the `gateway` row has a live pid. Note the host you'll reach it at —
a Tailscale hostname, a LAN IP, or `127.0.0.1` if the phone and the gateway are
on the same machine (rare outside a demo). This doc uses
`your-gateway.example.net` as a placeholder everywhere; substitute your real
host, never share it outside people you're actually pairing.

## 2. Run `orchestra pair`

```bash
orchestra pair
```

This prints a QR code **as text** in the terminal — no image viewer needed,
so it works over a bare SSH session on a headless VPS — plus the raw pairing
code underneath it, in case scanning isn't convenient. The code is
**short-lived and single-use**: it expires the moment it's exchanged, or after
about 10 minutes, whichever comes first. Running `orchestra pair` again always
mints a fresh one; an old code left on screen goes stale on its own.

**Do not screenshare this terminal while the code is visible.** The code is
effectively a password for your gateway for the few minutes it's live — anyone
who has it before you use it can pair their own device to your server. Run
`orchestra pair` again to invalidate an old code if you think someone saw it.

## 3. Connect the web dashboard

The dashboard can't scan a QR code, so type the two values instead:

1. Open the dashboard (`http://your-gateway.example.net:8891` or wherever
   `[dashboard]` is configured).
2. It shows a connect screen asking for a gateway URL and a code — paste the
   URL you're running the gateway at and the code from step 2.
3. The dashboard exchanges the code for a token itself and stores it for that
   browser; you don't see or copy the token directly.

## 4. Connect the iOS app

First launch shows a pairing screen, not the approvals list:

- **Scan** — point the camera at the terminal QR from step 2.
- **Type it in** — enter the gateway URL and the code by hand (the same
  values the dashboard used), if scanning isn't practical.

Either way, the app exchanges the code for its own token and stores both in
Keychain. It does not ask again unless you revoke that device from Settings
or its pairing genuinely expires.

## The handshake, if you're curious what "connected" actually checks

Neither client trusts a plain 200 OK. Two calls, in order:

- `GET /gateway/identity` — **unauthenticated**, frozen forever:
  `{"service":"orchestraos-gateway","protocol":1}`. This just confirms you're
  talking to an OrchestraOS gateway at all, before any credential is on the
  table.
- `GET /gateway/capabilities` — **behind your paired token**, tells the client
  what this gateway actually offers: `{"providers":[...],"surfaces":[...],
  "pending":N}`. The client renders whatever's in the list — it never assumes
  a fixed set, so a gateway can add a provider or a surface later without an
  app update.

This is a deliberately different pair of endpoints from `/health` (that one's
for `orchestra doctor` and the supervisor — don't confuse the two if you're
scripting against either).

## What can go wrong, and what it means

The client maps exactly four outcomes from that handshake, and every client
(web, iOS, this doc) uses the same four sentences for them:

1. **Can't find a gateway at that address.** — nothing answered at all: the
   URL is wrong, the gateway isn't running, or something between you and it
   (firewall, VPN, Tailscale) is blocking the connection. Check the URL
   first, then `orchestra status` on the gateway side.
2. **That's not an OrchestraOS gateway.** — something answered, but not with
   the identity shape: a typo'd port (see "8890 vs 8891" above), a different
   service, a stale reverse proxy.
3. **Found it, but your pairing isn't valid.** — identity looks right, but
   capabilities came back unauthorized: your token is expired, revoked, or
   the pairing never actually completed. Re-run `orchestra pair` and pair
   again.
4. **Connected.** — both calls came back clean. See "success" below.

## What success looks like

Once both calls succeed, every client (web, iOS, this doc) shows the same
line, word for word except the host and version:

```
connected to your-gateway.example.net · gateway v1 · no cards yet — they appear here when an agent needs a decision
```

That whole line is the success state on a fresh pairing with zero agents and
zero cards — it is not a placeholder or an error, even though nothing else on
the screen has happened yet. Fire one approval card (`docs/GATE.md` step 5) to
see the surface actually render something.

## Notes for anyone building against this

- The pairing exchange (`orchestra pair` → scan/type → `POST /pair/exchange
  {code}` → `{base_url, token}`) is the primary path. If that route isn't
  live yet on your checkout, `orchestra pair`'s own output will say so —
  don't assume the shape above without checking.
- `/gateway/identity`'s fields are frozen: `protocol` is an integer, never a
  semver string, and no field is ever renamed or removed once shipped.
  `/gateway/capabilities` is additive-only — treat any key your client
  doesn't recognize as "ignore it," never as an error, and treat an absent
  block (e.g. no `providers`) as "unknown," never as "none available."
- See `docs/tracks/01-device-pairing.md` for the fuller device-pairing design
  this onboarding flow is built on; if the two documents disagree on a route
  name or a response shape, this page (written against the frozen contract)
  is the one to trust, and the track doc needs an update.
