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

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this page. Copy the whole box:

```text
Help me connect my phone or Mac app to my own OrchestraOS server. Read this page and do it
with me: https://github.com/Tulum-DAO/orchestraos/blob/main/docs/ONBOARDING.md
If you can't open that link, ask me to paste the page to you; don't guess commands.
The commands on this page run on my server; I log in to it with ssh (ask me for the
address and user if you need them). Some steps happen in the app on my phone or Mac.
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
  Exception: never ask me to paste what `orchestra pair` printed. I only tell you whether I
  saw `Minted device ... with scopes: ...`; the `orc1_` code stays with me, because it is
  a password for my server. If there was no `Minted device` line, I paste you the output
  instead: it holds no code.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- Before any `tailscale serve --https=...` command, run `tailscale serve status` and show me
  the output. Use an https port that is not in that list; never replace or turn off an
  entry that is already there, and never use `--funnel`.
- For `orchestra pair --scopes`, ask me what the device may do. A phone or Mac that answers
  cards needs `read,approve`; add `message` only if I want to message agents from it. Never
  add a scope I did not ask for.
- If you can run commands yourself, never show me the `orc1_` code in our chat or save it
  anywhere; let me run `orchestra pair` in my own terminal instead.
- Never revoke a device (`orchestra devices --revoke`) unless I ask you to.
- Skip step 3 (the web dashboard connect screen): it is not on `main` yet.
- If `orchestra up` says `supervisor already running`, run `orchestra status` on its own.
  Never run `orchestra down` on your own. The one exception is a restart the page marks as
  my decision ([PERSON ONLY]): ask me first, and run it only once I say yes.
- The device id (in `Minted device <id> ...` and in `orchestra devices`) is not secret; I may
  paste it, or the whole `Minted device ...` line, which holds no code.
- Before every `orchestra pair`, make sure I have the app installed, open and on its pairing
  screen, with Tailscale on that device: the code disappears from the screen after 60
  seconds.
- We are done when the app shows it is connected (I tell you what I see: on a Mac the
  window switches from "Connect this Mac" to the app's main screen; on an iPhone a green
  "Connected to ..." line appears), AND
  `orchestra devices` on the server lists my device with the scopes I chose AND a LAST
  SEEN time, not `never`. A row appears as soon as `orchestra pair` runs, so the row alone
  proves nothing. Show me that output; don't just tell me it worked.
```

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

If it says `supervisor already running` (your team is already up), run `orchestra status` on
its own; there is no need to run `orchestra down`. Confirm the `gateway` row has a live pid.
Note the host you'll reach it at —
a Tailscale hostname, a LAN IP, or `127.0.0.1` if the phone and the gateway are
on the same machine (rare outside a demo). The iOS app needs an **https**
address with a trusted certificate (see step 4), so if you'll pair a phone,
plan on the Tailscale hostname. This doc uses
`your-gateway.example.net` as a placeholder everywhere; substitute your real
host, never share it outside people you're actually pairing.

**The apps need OrchestraOS on your server at `main` `cdcd701` (#259, 2026-10-08) or newer.**
On an older checkout, a long or multi-line message you send from the app comes back in your
transcript as a one-line `[LONG-MSG chip-dodge] ...` note with a server file path, instead of
your own words and photos. To check, run this on the server, inside your OrchestraOS checkout
(`cd ~/orchestraos` first if you are not already there):

```bash
git merge-base --is-ancestor cdcd701 HEAD && echo "up to date"
```

It prints `up to date` if you are. If it prints nothing, or an error, run `orchestra upgrade`
and then the restart in [docs/UPGRADE.md](UPGRADE.md). If `orchestra upgrade` stops with a
message about uncommitted changes, don't discard anything: stop, and bring that message to
whoever is helping you (or open an issue at https://github.com/Tulum-DAO/orchestraos/issues).

**Pairing needs the restarted server.** Updating the files is not enough: the running gateway
keeps its old code until it restarts, and an old gateway can refuse the app's token or be
unable to take the app's answers to cards. The same goes if you updated earlier and haven't
restarted since. **[PERSON ONLY]** The restart is your decision: it briefly stops your
agents' services, so choose a moment that suits you. If you'd rather not restart now, stop
here and come back to this page when you have.

## 2. Run `orchestra pair`

**Before you run it**, because the code disappears from the screen after 60 seconds:

- **[PERSON ONLY]** Have the app installed, open and showing its pairing screen (on iPhone:
  the gear at the top right of the Arturo tab, then **Connect your gateway**; steps 4 and 5
  say how to get each app; there is no public download yet).
- **[PERSON ONLY]** Have Tailscale on that device, signed in to the same Tailscale account as
  the server. On an iPhone: the **Tailscale** app from the App Store, open it and sign in. On a
  Mac: see step 5.
- If you want a QR code to scan instead of copying the code, install `segno` first, once, on
  the server: `cd ~/orchestraos`, then `.venv/bin/pip install segno`.

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
  | `ptt` | push-to-talk to Arturo **with its tools**, which can act on your agents: as strong as `inject`. Not a mild "talk" scope |
  | `admin` | file red-alert reports, post telemetry |

  A phone or Mac that answers cards needs `read,approve`. Add `message` only if you want to
  message agents from it. Give it more only if you mean to.

```bash
orchestra pair --base-url https://<machine>.<tailnet>.ts.net:8445 --scopes read,approve --label my-phone
```

`orchestra devices` lists paired devices, with a LAST SEEN column. A row appears the moment
`orchestra pair` runs, before any app uses the code, so a new row says `never` until the app
has actually connected; check LAST SEEN, not just the row. Each `orchestra pair` adds a row, so
a retry after an expired code leaves an extra `never` row behind. The list ends with a note that
the gateway's own bearer (`legacy-fleet-token`) has every scope: that is the server's built-in
key, not a paired device, and it is not something to revoke here.
`orchestra devices --revoke <device id>` cuts one off (its token stops working on its next
request). Revoking is your decision: an agent should not revoke a device on its own.
A revoked device can't come back; pair again to get a new one.

This prints the pairing code: one long word starting with `orc1_`. Copy all of it and
paste it into the app's pairing box. It carries your server's address too, so there is
nothing else to type. (An older app that also shows an address field: type your
server's `https://` address there, then paste the code. That needs a gateway started after
you updated OrchestraOS, so after updating, restart once with
`orchestra down && orchestra up --detach`; **[PERSON ONLY]** when to restart is your
decision.) On a stock install the code is
all you get: the terminal says
`No QR encoder is installed on this machine`. With `segno` installed (see "Before you run
it" above), it also draws a QR code you can scan, which works over a plain ssh session.
The code is **short-lived and single-use**: it expires the moment it's exchanged, or after
about 10 minutes, whichever comes first. The command also waits 60 seconds and then clears the
screen (`Pairing code hidden`), so the code does not sit in your scrollback; if you weren't
fast enough, run it again. Running `orchestra pair` again mints a fresh code; it does NOT
cancel the old one, which stays usable until it is used or its 10 minutes run out.

**Do not screenshare this terminal while the code is visible.** The code is
effectively a password for your gateway for the few minutes it's live — anyone
who has it before you use it can pair their own device to your server. Running `orchestra pair`
again does **not** cancel it. If you think someone saw a code, cut off the device it belongs to:
its id is in the `Minted device <id> ...` line printed just above the code. If the screen has
already cleared, run `orchestra devices`: it lists the newest first, and the newest row with
your label and LAST SEEN `never` is the one. The device id is not secret. If your own app
already paired with that code (its row shows a LAST SEEN time), the code is spent and nobody
else can use it: don't revoke, or you'll cut off your own app. But if your app's first try
failed and you paired again, an older row with your label and a LAST SEEN time is NOT your
app's: that one is the code someone else used. (LAST SEEN is shown in UTC.) Otherwise,
revoking is your own
decision **[PERSON ONLY]**:

```bash
orchestra devices --revoke <device id>
```

That device's token stops working on its next request, even if someone uses the code first.
Then run `orchestra pair` again for your own device. Using a code never adds a row of its own,
so a stolen code would show up under YOUR label, with a LAST SEEN time your own app didn't
cause; that is what to look for in `orchestra devices` afterwards.

Leftover `never` rows from a retry can simply be left alone: they never connected. Removing
one with `orchestra devices --revoke <device id>` is your choice, not something to do for tidiness
alone.

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

**Getting the app:** it is not on the App Store yet and has no public download. If nobody gave
you a test build, you can't connect an iPhone yet: stop here. **[PERSON ONLY]** Installing and
opening it is yours.

**Finding the pairing screen.** The app does not open on it by itself: it opens on the
**Arturo** tab, which shows no pairing prompt. **[PERSON ONLY]** Tap the gear at the top right of the Arturo tab
(to the right of the brain icon), then, under **GATEWAY**, tap **Connect your gateway**. On an
iPad, use **Settings** in the sidebar instead. Have that screen open, and Tailscale on the
iPhone (see step 2, "Before you run it"), before you run `orchestra pair`.

If you switch to the **Approvals** tab before pairing, it shows *Gateway not configured on this
build. Approvals will appear once the token is set.* That only means the app is not paired yet.

**Getting the code onto the iPhone.** `orchestra pair` prints the code in your computer's
terminal, not on the phone. Either install `segno` first (step 2) and tap **Scan** on the QR, or,
if your computer is a Mac signed in to the same Apple ID as the iPhone, copy the code on the Mac
and paste on the iPhone (Universal Clipboard). Never message or email the code to yourself.

The **Connect your gateway** screen says *On the machine running your OrchestraOS gateway, run
`orchestra pair`, then paste the code it prints here — or scan its QR.*

1. **[PERSON ONLY]** Paste exactly what `orchestra pair` printed into the **PAIRING CODE** box
   (placeholder *paste the code from orchestra pair*), or tap **Scan** and point the camera at
   its QR.
2. **[PERSON ONLY]** Tap **Pair**. There is no address to type: the code carries it.

**When it works**, the screen stays open, the code box empties, and a green check line appears
under the fields: *Connected to <your server> · gateway v1 · no cards yet — they appear here
when an agent needs a decision.* (If cards are already waiting, it ends with *· 1 card waiting*
or similar instead.) A red **Forget this gateway** button appears at the bottom, and back in
Settings, under **GATEWAY**, Status shows **Connected**, Token shows **Configured**, and the row
now reads **Change gateway**. To check from the server, `orchestra devices`
lists your device with a LAST SEEN time.

If the line says *Paired, but this device couldn't save it. That code is now used up — run
`orchestra pair` again and retry.* or *That is an OrchestraOS gateway, but it didn't accept this
token. Run `orchestra pair` on the server and use the new code.*, it did not work: run
`orchestra pair` again and use the new code.

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

In that list, each entry is an `https://` line with a `|-- / proxy ...` line under it. An entry
whose proxy is `http://127.0.0.1:8890` is your gateway, already served: reuse its port and skip
the `tailscale serve` command below. The entry whose proxy is `http://127.0.0.1:8891` is the dashboard, not the gateway. The
host name in any entry is your `<machine>.<tailnet>.ts.net`.

Otherwise, pick an https port that is not in that list (8445 here) and serve the
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
Keychain. It does not ask again unless the device is revoked or its pairing genuinely
expires. **Forget this gateway** in the app only removes the pairing from the phone; the
device stays active on the server. To cut it off there too, revoking is your decision
**[PERSON ONLY]**: `orchestra devices --revoke <device id>`.

## 5. Connect the Mac app

The Mac app connects to your **gateway** (8890), the same way the iOS app does. It is not
the dashboard: the dashboard (8891) needs no app, just a browser ([docs/INSTALL.md](INSTALL.md) §2).

Before you start:

- **[PERSON ONLY]** **Tailscale on the Mac**, installed and signed in to the same account as
  the server ([docs/INSTALL.md](INSTALL.md) §0, "Tailscale on the VPS and on your own device").
- **The gateway on an https address.** The Mac app refuses plain `http://`. On the server,
  follow step 4 above: run `tailscale serve status` first, then serve the gateway on a free
  https port, for example `tailscale serve --bg --https=8445 http://127.0.0.1:8890`. That
  gives you `https://<machine>.<tailnet>.ts.net:8445`.

**Getting the app:** it has no public download yet. If nobody gave you a test build, you can't
connect a Mac yet: stop here. **[PERSON ONLY]** Installing and opening it is yours.

On the Mac, open the app. It has one window, titled **OrchestraOS**, which first shows **Connect
this Mac**, with the line *Run `orchestra pair` on the gateway machine and paste the code it
prints.* Leave it on that screen.

Then, on the server, make a pairing code for the Mac (it stays on screen for 60 seconds):

```bash
orchestra pair --base-url https://<machine>.<tailnet>.ts.net:8445 --scopes read,approve --label my-mac
```

`read,approve` lets the Mac see your cards and answer them (step 2 explains each scope). To
also message your agents from it, use `--scopes read,approve,message` instead. You should see
`Minted device … (my-mac) with scopes: read, approve`, and then the code to paste: one long
word starting with `orc1_`. (An older OrchestraOS prints a `{"code":…}` line instead; the Mac app takes that too.)

1. **[PERSON ONLY]** In Terminal, select the whole code and copy it (`Cmd+C`).
2. **[PERSON ONLY]** Paste it into **Pairing code** (the box that says *Paste the code orchestra pair
   printed*). The gateway address fills itself in, and appears under the box as
   `Gateway: <address>`.
3. **[PERSON ONLY]** Press **Pair** (or Return).

When it works, the same window switches from **Connect this Mac** straight to the app's main
screen. There is no success message; that switch is the sign it worked. To check from
the server: `orchestra devices` lists `my-mac` with the scopes you gave it and a LAST SEEN time
(not `never`).

**If it does not work**, a sentence appears under the button. *That isn’t a pairing code. Paste
exactly what `orchestra pair` printed.* means the paste was not a code: copy the whole code again.
*That pairing code didn't work. Codes are single-use and expire quickly — run orchestra pair
again for a fresh one.* means the code was used or expired: run `orchestra pair` again and paste
the new code. The same goes for *Paired, but this Mac couldn't save it (keychain status ...).
That code is now used up — run `orchestra pair` again and retry.* and for *That is an
OrchestraOS gateway, but it didn't accept this token. Run `orchestra pair` on the server and
use the new code.* Each code works once and expires after
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
