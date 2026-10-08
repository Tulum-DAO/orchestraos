# Onboarding — connect your phone and the web dashboard to your own gateway

For someone who already has a gateway running and wants to reach it from a
phone and from a browser, on their own network, with no baked-in token. Two
client surfaces, one pairing flow.

Just want the web dashboard in your browser? That needs no pairing:
[docs/INSTALL.md](INSTALL.md) §2, "Open the dashboard in your browser, over Tailscale https".
This page is about the gateway (8890), which the phone app talks to.

> **What works today, step by step** (updated 2026-10-08):
>
> | Step | Status |
> |---|---|
> | 1. Run your gateway | Works on `main`. |
> | 2. `orchestra pair` | Works on `main`: prints the code and QR; the gateway serves `POST /pair/exchange`. |
> | 3. Connect the web dashboard | **Not on `main`.** The connect screen described below was proposed (PR #23) and closed unmerged; this section describes the intended flow. |
> | 4. Connect the iOS app | **Not released yet.** In testing and headed for the App Store; this section describes the build under test, with a fallback for older builds. |
> | 5. Connect the Mac app | **Not released yet.** It is in testing, with no public download; this section describes the build under test (it accepts the code `orchestra pair` prints, as is). |
>
> Run `orchestra pair --help` to confirm the command on your install.

## Before anything else: log in

Do this before you do anything below — install and log in to one agent CLI
(Claude Code, Gemini (Antigravity `agy` CLI), or Codex). [docs/INSTALL.md](INSTALL.md) §0 has the exact
commands. If you have no subscription to any of them, Google's free tier is the
likely zero-cost path, but note the harness uses the Antigravity `agy` CLI, and the
free-tier figures in [docs/COSTS.md](COSTS.md) are Gemini CLI's, **unverified against `agy`**.

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
on the same machine (rare outside a demo). The iOS app needs an **https**
address with a trusted certificate (see step 4), so if you'll pair a phone,
plan on the Tailscale hostname. This doc uses
`your-gateway.example.net` as a placeholder everywhere; substitute your real
host, never share it outside people you're actually pairing.

**The apps need OrchestraOS on your server at `main` `cdcd701` (#259, 2026-10-08) or newer.**
On an older checkout, a long or multi-line message you send from the app comes back in your
transcript as a one-line `[LONG-MSG chip-dodge] ...` note with a server file path, instead of
your own words and photos. To check, run this on the server, inside your OrchestraOS checkout:

```bash
git merge-base --is-ancestor cdcd701 HEAD && echo "up to date"
```

It prints `up to date` if you are. If it prints nothing, or an error, run `orchestra upgrade`
and then the restart in [docs/UPGRADE.md](UPGRADE.md).

## 2. Run `orchestra pair`

`orchestra pair` needs two things it will not guess:

- **The address the phone will use** (`--base-url`): the https gateway address from
  step 4 below, e.g. `https://<machine>.<tailnet>.ts.net:8445`. If you pair a phone, do
  step 4's `tailscale serve` first. Without it, pair stops with `I do not know this
  gateway's public address`. You can set `ORCHESTRA_PUBLIC_URL` in your shell instead of
  passing it each time; `[public] host` in `orchestra.toml` is **not** read here.
- **What the device may do** (`--scopes`): required, with no default, so nobody gets
  the power to answer on your behalf by accident. A comma-separated list of:

  | scope | the device can |
  |---|---|
  | `read` | see approvals, agents and transcripts |
  | `approve` | answer approvals and questionnaires: it acts as you |
  | `message` | send a message to an agent, upload a file |
  | `inject` | press keys in a live agent's terminal |
  | `voice` | talk to Arturo (every call spends provider credit) |
  | `admin` | file red-alert reports, post telemetry |

  A phone that answers cards needs `read,approve`. Give it more only if you mean to.

```bash
orchestra pair --base-url https://<machine>.<tailnet>.ts.net:8445 --scopes read,approve --label my-phone
```

`orchestra devices` lists paired devices; `orchestra devices --revoke <device id>`
cuts one off (its token stops working on its next request). Revoking is your decision: an
agent should not revoke a device on its own. Re-pairing with a new code undoes it.

This prints the pairing code: one long word starting with `orc1_`. Copy all of it and
paste it into the app's pairing box. It carries your server's address too, so there is
nothing else to type. (An older app that also shows an address field: type your
server's `https://` address there, then paste the code. That needs a gateway started after
you updated OrchestraOS, so run `orchestra down && orchestra up --detach` once after updating.) On a stock install the code is
all you get: the terminal says
`No QR encoder is installed on this machine`. To also get a scannable QR code drawn as
text (works over a bare ssh session), install the `segno` package into the install's
Python once, from your checkout: `.venv/bin/pip install segno`. The code is
**short-lived and single-use**: it expires the moment it's exchanged, or after
about 10 minutes, whichever comes first. Running `orchestra pair` again always
mints a fresh one; an old code left on screen goes stale on its own.

**Do not screenshare this terminal while the code is visible.** The code is
effectively a password for your gateway for the few minutes it's live — anyone
who has it before you use it can pair their own device to your server. Run
`orchestra pair` again to invalidate an old code if you think someone saw it.

## 3. Connect the web dashboard

The dashboard can't scan a QR code, so type the two values instead:

1. Open your own dashboard, at the address `orchestra up` printed when you
   started it.
2. It shows a connect screen asking for a gateway URL and a code — paste the
   URL you're running the gateway at and the code from step 2.
3. The dashboard exchanges the code for a token itself and stores it for that
   browser; you don't see or copy the token directly.

Plain `http://` is fine here if the gateway and your browser are on the same
LAN — the dashboard says so quietly, it isn't a warning you need to click
through.

## 4. Connect the iOS app

First launch shows a pairing screen, not the approvals list. It says *On the machine running
your OrchestraOS gateway, run `orchestra pair`, then paste the code it prints here — or scan
its QR.*

1. Paste exactly what `orchestra pair` printed into the **PAIRING CODE** box (placeholder
   *paste the code from orchestra pair*), or tap **Scan** and point the camera at its QR.
2. Tap **Pair**. There is no address to type: the code carries it.

If what you pasted is not a pairing code at all, the app says *That isn’t a pairing code. Paste
exactly what `orchestra pair` printed.* Copy the whole code again, from the first character
to the last, and paste it.

The address field is under **Advanced: gateway address and token**. You only need it for an
older OrchestraOS that prints a code with no address in it; the screen tells you, and opens
**Advanced** by itself, when that happens.

**On an older build of the iOS app** (one without that intro line): type your gateway's
`https://` address into the address field, then paste the whole `orc1_` code into the code
field. If your `orchestra pair` printed a `{"code":"…","base_url":"…"}` line instead (an
older OrchestraOS), paste only the code value, the text inside the quotes after `"code":`,
or run `orchestra upgrade` first.

**The app only connects over https, with a certificate the phone trusts.**
A plain `http://` address (a LAN IP, `localhost`) is refused on the pairing
screen, and the app tells you so. The simplest way to get a trusted https
address, whether the gateway runs on a VPS or on a Mac, is Tailscale on both
the gateway machine and the phone ([docs/INSTALL.md](INSTALL.md) §0 sets it up). On the gateway machine, first see what
Tailscale already serves, because `tailscale serve` on a port that is taken
silently REPLACES whatever was there:

```bash
tailscale serve status
```

Then pick an https port that is not in that list (8445 here) and serve the
gateway on it. The proxy target is the plain-HTTP address your gateway listens
on. `8890` below is the default (`[gateway] port` in `orchestra.toml`); if you
changed it, use the port that `orchestra status` prints on the `gateway` row:

```bash
tailscale serve --bg --https=8445 http://127.0.0.1:8890
```

Your gateway URL is then `https://<machine>.<tailnet>.ts.net:8445`. Use that
address in step 2 (`orchestra pair`) so the QR carries it. Keep it tailnet
only: do not add `--funnel` (or `tailscale funnel`). The phone reaches it
over Tailscale; the gateway does not need to be on the public internet.

A reverse proxy with a real certificate (Caddy, nginx + Let's Encrypt) works
too; a self-signed certificate does not.

Either way, the app exchanges the code for its own token and stores both in
Keychain. It does not ask again unless you revoke that device from Settings
or its pairing genuinely expires.

## 5. Connect the Mac app

The Mac app connects to your **gateway** (8890), the same way the iOS app does. It is not
the dashboard: the dashboard (8891) needs no app, just a browser ([docs/INSTALL.md](INSTALL.md) §2).

Before you start:

- **Tailscale on the Mac**, signed in to the same account as the server ([docs/INSTALL.md](INSTALL.md)
  §0, "Tailscale on the VPS and on your own device").
- **The gateway on an https address.** The Mac app refuses plain `http://`. On the server,
  follow step 4 above: run `tailscale serve status` first, then serve the gateway on a free
  https port, for example `tailscale serve --bg --https=8445 http://127.0.0.1:8890`. That
  gives you `https://<machine>.<tailnet>.ts.net:8445`.

On the server, make a pairing code for the Mac:

```bash
orchestra pair --base-url https://<machine>.<tailnet>.ts.net:8445 --scopes read,approve,message --label my-mac
```

`read,approve,message` lets the Mac see your cards, answer them, and message your agents
(step 2 explains each scope). You should see `Minted device … (my-mac) with scopes: read,
approve, message`, and then the code to paste: one long word starting with `orc1_`. (An older
OrchestraOS prints a `{"code":…}` line instead; the Mac app takes that too.)

On the Mac, open the app. Its first window is **Connect this Mac**, with the line *Run
`orchestra pair` on the gateway machine and paste the code it prints.*

1. In Terminal, select the whole code and copy it (`Cmd+C`).
2. Paste it into **Pairing code** (the box that says *Paste the code orchestra pair
   printed*). The gateway address fills itself in, and appears under the box as
   `Gateway: <address>`.
3. Press **Pair** (or Return).

When it works, the connect window goes away and the app's main window opens. To check from
the server: `orchestra devices` lists `my-mac` with the scopes you gave it.

**If it does not work**, a sentence appears under the button. *That isn’t a pairing code. Paste
exactly what `orchestra pair` printed.* means the paste was not a code: copy the whole code again.
*That pairing code didn't work. Codes are single-use and expire quickly — run orchestra pair
again for a fresh one.* means the code was used or expired: run `orchestra pair` again and paste
the new code. Each code works once and expires after
about 10 minutes. Also check that Tailscale on the Mac is connected, and that the address
under the box ends in the https port you served the gateway on (8445 above), not 8891.

You only need **Advanced** (click the row) for an old-style code that carries no address. It
holds a **Gateway address** field, and it opens by itself when it is needed: type the https
gateway address there.

Do not screenshare or post the `orchestra pair` output: until it is used, the code is a
password for your server.

## The handshake, if you're curious what "connected" actually checks

Neither client trusts a plain 200 OK. Two calls, in order:

- `GET /gateway/identity` — **unauthenticated**, frozen forever:
  `{"service":"orchestraos-gateway","protocol":1}`. This just confirms you're
  talking to an OrchestraOS gateway at all, before any credential is on the
  table.
- `GET /gateway/capabilities` — **behind your paired token**, tells the client
  what this gateway actually offers: `{"providers":[...],"surfaces":[...],
  "pending":N,"features":[...]}`. The client renders whatever's in the list — it
  never assumes a fixed set, so a gateway can add a provider or a surface later
  without an app update. `features` is a flat list of strings naming optional
  routes. The app answers approval cards through `POST /menu-submit`; a
  gateway whose `features` lacks `"menu_submit"` (or has no `features` at all)
  predates that route and can't take answers from the app — update it, or
  answer in the dashboard. Answers are delivered by default; every answer must
  name the card it was drafted for, so it can never land in a different
  menu. To turn delivery off, run the gateway with
  `MENU_MULTIPART_SUBMIT_ARMED=0`: the route then reports what it would press
  and presses nothing, and `menu_submit.armed: false` in the same response
  tells the app so. Answers typed in your own words stay off unless
  `MENU_MULTIPART_TEXT_ARMED=1`.

This is a deliberately different pair of endpoints from `/health` (that one's
for `orchestra doctor` and the supervisor — don't confuse the two if you're
scripting against either).

## What can go wrong, and what it means

Every client (web, iOS, this doc) shows the exact same words, verbatim,
for each of these five states:

- **Can't resolve the address.** Can't find `<host>`. Check the spelling —
  and if that's a tailnet name, make sure this device is on the same
  tailnet.
- **Nothing listening.** Found `<host>`, but nothing is answering on port
  `<port>`. Is `orchestra up` running on that machine?
- **Answered, but not a gateway.** Something is running at `<host>:<port>`,
  but it isn't an OrchestraOS gateway. Check the port — the gateway is
  usually 8890, and 8891 is the dashboard.
- **Refused.** That is an OrchestraOS gateway, but it didn't accept this
  token. Run `orchestra pair` on the server and use the new code.
- **Found a gateway, but it predates pairing.** Found an OrchestraOS gateway
  at `<host>:<port>`. This version predates device pairing, so there's
  nothing to connect to yet.

The first two are both "nothing valid answered" — a typo/DNS problem versus
a "nothing's listening on that port" problem, and they need opposite
fixes — the third is "found something, but not a gateway," the fourth is
"found a gateway, but the token's no good; re-pair," and the fifth is
"found a real gateway, but it's from before pairing existed."

## What success looks like

Once both calls succeed, every client (web, iOS, this doc) shows the same
line, word for word except the host and version:

```
Connected to your-gateway.example.net · gateway v1 · no cards yet — they appear here when an agent needs a decision.
```

That whole line is the success state on a fresh pairing with zero agents and
zero cards — it is not a placeholder or an error, even though nothing else on
the screen has happened yet. Fire one approval card ([docs/GATE.md](GATE.md) step 5) to
see the surface actually render something.

## Notes for anyone building against this

- The pairing exchange (`orchestra pair` → scan/paste the `orc1_` token → the app decodes it → `POST /pair/exchange
  {code}` → `{base_url, token}`) is the primary path. If that route isn't
  live yet on your checkout, `orchestra pair`'s own output will say so —
  don't assume the shape above without checking.
- `/gateway/identity`'s fields are frozen: `protocol` is an integer, never a
  semver string, and no field is ever renamed or removed once shipped.
  `/gateway/capabilities` is additive-only — treat any key your client
  doesn't recognize as "ignore it," never as an error, and treat an absent
  block (e.g. no `providers`) as "unknown," never as "none available."
- See [docs/tracks/01-device-pairing.md](tracks/01-device-pairing.md) for the fuller device-pairing design
  this onboarding flow is built on; if the two documents disagree on a route
  name or a response shape, this page (written against the frozen contract)
  is the one to trust, and the track doc needs an update.
