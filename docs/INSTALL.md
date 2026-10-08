# Install — the minimum path

> **Never rented a server, made an ssh key, or used Terminal?** Start with
> [docs/FROM_SCRATCH.md](FROM_SCRATCH.md). It takes you from your computer (Mac, Windows or Linux) to logged in on your own server
> in about 20 minutes, then sends you back here to §0.
>
> **Want an AI agent to guide you?** Sections of §0 have a **Hand this to your agent** box:
> copy it into the AI you already use. Steps marked **[PERSON ONLY]** (paying, signing in,
> passwords, approving a device) are yours to do; the agent stops there.

One machine, one CLI (claude OR gemini OR codex), no voice, no Telegram.
Starting from nothing, you end with: a VPS, Tailscale on it and on your own
laptop or phone, `orchestra up` running, the dashboard open in your browser over
https, an agent CLI logged in, and one seat spawned. Budget about an hour the
first time; a clean Ubuntu 22.04/24.04 VPS is assumed.

The path, in order:

| step | where | what you end with |
|---|---|---|
| Get a VPS | your provider's website | a server you can `ssh` into |
| 0. Prerequisites | the VPS | a normal sudo user, Tailscale, packages, a pinned agent CLI, logged in |
| 1. Clone, init, doctor | the VPS | `orchestra doctor` all OK |
| 2. Up | the VPS, then your browser | the supervisor running; the dashboard open at `https://<vps>.<tailnet>.ts.net` |
| 3. Your starter team | the VPS | three live seats: gm (T0) → a project manager (T1) → a worker (T2) |
| 4. See your team | your own computer | your computer on your Tailscale network; the dashboard open in your browser, showing the three agents |
| 5. Answer one card | the dashboard | the seat receives your answer |

Connecting the iOS app to your gateway (pairing) is [docs/ONBOARDING.md](ONBOARDING.md), a
separate short walkthrough after this one.

## Get a VPS

(First time? [docs/FROM_SCRATCH.md](FROM_SCRATCH.md) walks through this whole section step by step,
with one recommended provider.)

Any provider that sells a Linux virtual server works; nothing here is tied to one.
Choose:

- **Ubuntu 24.04 LTS** (22.04 also works).
- **At least 2 vCPU, 4 GB RAM and 40 GB disk.** The models run on the vendor's
  servers, not yours. What uses memory is the agent CLIs: each Claude Code seat
  takes roughly 400 MB (median of 27 seats on the reference install). 4 GB holds
  the services and a handful of seats; take 8 GB if you plan on more than five.
  On a 2 GB box, `orchestra init` and `orchestra up` have been seen to work (a clean
  run, 2026-10-08); seats were not tested there, so plan on 4 GB once you run seats.
- **ssh key login.** Most providers ask for your public key when you create the
  server. If you have none, run `ssh-keygen -t ed25519` on your own computer
  and paste the contents of `~/.ssh/id_ed25519.pub`.

[docs/COSTS.md](COSTS.md) has current prices for a few providers, and a no-cost path. When the
server is ready, the provider shows its public IP address. Log in from your own
computer:

```bash
ssh root@<server ip>        # the IP address your provider shows (FROM_SCRATCH.md step 3); some providers give a named user instead of root
```

You only need the public IP until Tailscale is set up below. The dashboard is never
opened on it.

## 0. Prerequisites

**How to run the command boxes in this guide:** paste **one line at a time**, press Enter, and
wait for it to finish before the next. Some commands stop to ask a question or a password; a
line pasted while they wait becomes the answer. Every line in a box is meant to be run, in
order. Optional commands are never mixed in; they sit in their own section.

### Run as a normal user, not root

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#run-as-a-normal-user-not-root
The commands in this section run on my server; I log in to it with ssh (ask me for the
address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when logged in as the new user, `whoami` prints its name and `sudo -v` succeeds. Show me that output; don't just tell me it worked.
```

On a fresh VPS you are often logged in as `root`. Do not install as root. Every seat launches
its agent CLI with the permission-skip flag (`claude --dangerously-skip-permissions`;
`spawn-agent.sh`), and Claude Code refuses that flag as root with
`--dangerously-skip-permissions cannot be used with root/sudo privileges for security reasons`.
Every seat would fail at launch.

If `whoami` prints `root`, create a normal user with sudo once, give it your ssh key, and log
back in as that user. Everything after this point runs as that user.

As root, once (the name "orchestra" is only an example). `adduser` stops to ask questions, so
run it on its own and answer them before pasting anything else:

```bash
adduser orchestra                       # choose a password; the other questions can be left blank
```

Then the rest, one line at a time:

```bash
usermod -aG sudo orchestra
mkdir -p /home/orchestra/.ssh
cp ~/.ssh/authorized_keys /home/orchestra/.ssh/
chown -R orchestra:orchestra /home/orchestra/.ssh
chmod 700 /home/orchestra/.ssh && chmod 600 /home/orchestra/.ssh/authorized_keys
exit
```

What `adduser` asks: **[PERSON ONLY]** `New password:` and `Retype new password:` (type a password for
the new account and remember it: `sudo` asks for it later; nothing shows while you
type), then `Full Name []:`, `Room Number []:` and a few more (press Enter for each),
then `Is the information correct? [Y/n]` (press Enter). The other lines print nothing
when they work. `exit` logs you out of the server.

Not sure whether you log in with a key or a password? As root, run `ls ~/.ssh/authorized_keys`:
if it prints that path, you use a key and the lines above work; if it says `No such file or
directory`, you use a password, so read on.

Logged in as root with a password rather than a key? Then `/root/.ssh/authorized_keys` does
not exist and the `cp` line fails. Run only `adduser` and `usermod`, `exit`, and then, from your
own computer, **[PERSON ONLY]** `ssh-copy-id orchestra@<server ip>` (it asks for the new user's password once). On
Windows there is no `ssh-copy-id`; in PowerShell use
`type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh orchestra@<server ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"`
(it also asks for the new user's password once; `>>` only adds the key).

Then, from your own computer: `ssh orchestra@<your server address>` (the same address you
used for `root@`).

You should see: `whoami` prints `orchestra`, and `sudo -v` asks for that user's password
(`[sudo] password for orchestra:`, **[PERSON ONLY]**: type it yourself) and then prints nothing, which
means it worked. From now on, `sudo` asks for this password the first time in a while; that
prompt is always yours to answer. If your provider already
logs you in as a normal user with sudo, skip this step.

### Tailscale on the VPS and on your own device

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#tailscale-on-the-vps-and-on-your-own-device
The commands in this section run on my server; I log in to it with ssh (ask me for the
address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when `tailscale status` on the server lists both the server and my own computer or phone. Show me that output; don't just tell me it worked.
```

Tailscale puts the VPS and your own laptop or phone on a private network (a
"tailnet") that only your devices can join. It is how you will open the dashboard
in your browser over https without putting it on the public internet. The free
personal plan is enough.

On the VPS, as your normal user:

```bash
curl -fsSL https://tailscale.com/install.sh | sh   # downloads and installs Tailscale; ends with "Installation complete!"
```

```bash
sudo tailscale up                       # [PERSON ONLY] prints a login URL: open it in your browser and sign in
```

Wait until you have signed in and this command has finished, then:

```bash
sudo tailscale set --operator=$USER     # lets your user run `tailscale serve` without sudo (step 2); prints nothing
tailscale status                        # the VPS is listed, with a 100.x.y.z address
```

`sudo tailscale up` prints `To authenticate, visit:` and a link. **[PERSON ONLY]** Select the link, copy it
(`Cmd+C` on a Mac, `Ctrl+C` in Windows Terminal, `Ctrl+Shift+C` on Linux), and open it in
your own computer's browser. Sign in or create a
Tailscale account (it signs you in with an existing Google, Microsoft, GitHub or Apple account) and approve the
device. The command on the server then finishes by itself. Remember which account you
used: your computer and phone must sign in with the same one.

**[PERSON ONLY]** On your own computer and/or phone: install Tailscale and sign in **with the same account**.
- **Mac:** **Tailscale** from the Mac App Store (or tailscale.com/download); open it and click
  its icon in the menu bar, top right of the screen, to log in.
- **Windows:** the installer from tailscale.com/download. Windows asks whether to allow it to
  make changes: that admin prompt is yours (**[PERSON ONLY]**). Then click the Tailscale icon in the
  taskbar's notification area, bottom right; if you don't see it, click the `^` arrow there
  to show hidden icons.
- **Linux:** the commands at tailscale.com/download, then `sudo tailscale up`.
- **Phone:** the **Tailscale** app from the App Store or Google Play.

You should see: `tailscale status` on the VPS now lists your device too, and `tailscale ip -4`
prints the VPS's tailnet address (it starts with `100.`). From your own computer,
`ping <that address>` answers (on a Mac or Linux, `Ctrl-C` stops it; on Windows it stops by
itself after four replies). From now on
you can `ssh <your user>@<that address>` instead of the public IP. (Step 4 checks your
computer is on the network again, and opens your dashboard on it.)

### Packages

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#packages
The commands in this section run on my server; I log in to it with ssh (ask me for the
address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when `node -v` prints a version starting with `v22`. Show me that output; don't just tell me it worked.
```

**[PERSON ONLY]** The first `sudo` in a while asks for your password (`[sudo] password for
<you>:`); type it yourself. That goes for every `sudo` line below, including the pinned CLI
install.

```bash
sudo apt update && sudo apt install -y git tmux python3 python3-venv build-essential curl iproute2   # iproute2 = `ss`, which `orchestra doctor` needs to attribute ports to its own supervisor
# build-essential + python3 are not optional: node-pty (the web terminal's native addon) compiles at `npm install`;
# without them the install used to finish green with the terminal dead. `orchestra doctor` now shows a red
# `terminal:node-pty` row in that state; remedy: `npm rebuild node-pty` after installing the toolchain.
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
node -v                      # checks that Node is installed
```

What these do: the first line installs the tools OrchestraOS needs (git, tmux, Python and
a compiler); the `curl` line installs Node.js 22, which runs the dashboard and Claude Code.

You should see: each of the two long lines prints hundreds of lines and takes a minute or
two. They end with lines like `No VM guests are running outdated hypervisor (qemu)
binaries on this host.` If a coloured box asks which services to restart, press Enter.
`node -v` then prints a version starting with `v22`, for example `v22.23.3`.

Then install ONE agent CLI, pinned (next section), and log in to it (the section
after).

### Pin the agent CLI version

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#pin-the-agent-cli-version
The commands in this section run on my server; I log in to it with ssh (ask me for the
address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when `claude --version` prints `2.1.276 (Claude Code)`. Show me that output; don't just tell me it worked.
```

The harness reads the CLI's screen, transcripts, and hook events. It does not heal itself yet when
the CLI changes shape under it, so pin the CLI to a version this release was proven on and turn the
auto-updater off. Two versions are known good, both measured on 2026-09-19: the release gate ran on
**Claude Code 2.1.276** in the Docker image, and the reference fleet runs **2.1.260**. An unpinned
native install moved from 2.1.263 to 2.1.278 in one morning with no action from the operator.

```bash
sudo npm install -g @anthropic-ai/claude-code@2.1.276
echo 'export DISABLE_AUTOUPDATER=1' >> ~/.bashrc && export DISABLE_AUTOUPDATER=1   # every seat's shell inherits it
claude --version                 # must print 2.1.276
```

You should see: the install prints `added 2 packages in 6s` (the time varies). It may also
print a few `npm notice` lines saying a newer npm is available; ignore them, and do not
update npm. The `echo` line prints nothing. `claude --version` prints `2.1.276 (Claude Code)`.

Why `sudo`: the Node above is a system-wide install, so global npm packages go to
`/usr/lib/node_modules`, which is owned by root. Without `sudo` the install fails with
`EACCES: permission denied` for a normal user. Do not work around that by pointing npm at a
prefix in your home directory: a prefix you own is one the CLI's auto-updater can write to,
and it can then move you off the pinned version. With the root-owned install the pin holds
even if `DISABLE_AUTOUPDATER` is ever missing from a shell; the updater just reports
`Auto-update failed: no write permission to npm prefix`, which is harmless. To change versions
later, run the same `sudo npm install -g` line with the new version.

If you installed Claude Code with the native installer instead of npm, it auto-updates; switch to the
npm install above for any machine that runs seats. For the Docker image, pass the pin as the build
argument: `--build-arg AGENT_CLIS="@anthropic-ai/claude-code@2.1.276"`. Without it the image pulls
whatever is current at build time, and inside the container auto-update is attempted every session
and fails with `Auto-update failed: no write permission to npm prefix` because the npm prefix is not
writable by the container user. That footer is not a fault in your setup; the pin and the export make
it go away. Gemini and Codex CLIs: pin the same way with their package managers (`sudo npm install -g` for
an npm package; the reference fleet runs agy 1.2.6 and codex-cli 0.153.4).

### Log in to the agent CLI (the one step only you can do)

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#log-in-to-the-agent-cli-the-one-step-only-you-can-do
The commands in this section run on my server; I log in to it with ssh (ask me for the
address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when `claude auth status` shows `"loggedIn": true`. Show me that output; don't just tell me it worked.
```

This is the only step that needs a person: you sign in with your own account. Use
your own login; never copy someone else's credentials onto the server.

**[PERSON ONLY] You need a paid plan.** For Claude Code that is a Claude **Pro** or **Max** subscription
(claude.com/pricing; [docs/COSTS.md](COSTS.md) compares the options). Buy it first, with the same
email you will sign in with. On Claude's website it takes only a credit or debit card; if you
subscribe in the Claude iPhone or Android app instead, the App Store or Google Play handles
payment ("Paid plan billing FAQs", support.claude.com); check which payment methods your store
accepts.

On the server, start Claude Code once:

```bash
claude
```

It shows a few screens. The server has no browser, so you sign in from your own computer's browser:

1. **`Choose the text style that looks best with your terminal`**: a list of themes, with
   one marked `❯`. Press Enter to keep it (you can change it later with `/theme`).
2. **`Select login method:`** The first option is selected:
   `1. Claude account with subscription · Pro, Max, Team, or Enterprise`. Press Enter.
3. **[PERSON ONLY]** **`Browser didn't open? Use the url below to sign in`**, then a very long link that wraps
   over several lines, then `Paste code here if prompted >`. With the mouse, select the
   whole link, from `https://` to its last character on the last line, copy it, and open it in
   your own computer's browser. Sign in, and click to authorize Claude Code. If the browser
says the link is invalid, it was copied broken across the line wraps: make the terminal
window wider and copy it again, or check that the pasted link has no spaces in it.
4. **[PERSON ONLY]** The browser then shows a **code**. Copy it, go back to your terminal, paste it after
   `Paste code here if prompted >`, and press Enter.
5. A few more screens follow (a login confirmation and some notes). Press Enter on each.
   If it asks whether you trust the files in this folder, choose the option that says yes.
6. You are now in Claude Code's own prompt. Type `/exit` and press Enter to leave it.
   Your login is saved.

Check that the login stuck:

```bash
claude auth status
```

You should see: a few lines of text that include `"loggedIn": true` and a
`"subscriptionType"` naming your plan. `"loggedIn": false` means the login did not
finish: run `claude` again.

For the other CLIs:

| runtime | binary | run once | check |
|---|---|---|---|
| claude | `claude` | `claude` | `claude auth status` reports `loggedIn: true` |
| gemini | `agy`    | `agy` | `~/.gemini/antigravity-cli/antigravity-oauth-token` exists |
| codex  | `codex`  | `codex login` | `~/.codex/auth.json` has a `tokens` key |

Do this before step 1. (You can log in later, but until you do, `orchestra doctor` reports
`runtime:login` as `MISSING`.) `orchestra spawn` (step 3) checks first: with no enabled CLI
installed and logged in, it refuses with `refusing to spawn: no enabled runtime is
installed AND logged in` and exits 2. The low-level `./spawn-agent.sh` does not check:
a seat it launches against a CLI you have not logged in to retries, prints
`Injection FAILED`, and exits, while the CLI's own sign-in screen waits unread in the
seat's terminal.

## 1. Clone, init, doctor

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#1-clone-init-doctor
If you can't open that link, ask me to paste the section to you; don't guess commands.
The commands in this section run on my server, as my normal user (not root); I log in to
it with ssh (ask me for the address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- Never stop or kill a process you did not start, even if a message suggests it. Bring it
  to me instead.
- In the `[runtimes]` `sed` line, use the agent CLI I logged in to in section 0 (claude, gemini or
  codex). Ask me which one if you don't know; don't guess.
- `orchestra init --yes` takes about five minutes. Wait for it to finish; don't stop it.
  Its `--yes` adds OrchestraOS's hook rows to `~/.claude/settings.json`; that is expected
  and is not an overwrite.
- If `orchestra doctor` shows a `port:<name> MISSING ... in use` row, change that port with
  the section's line for it, then run `orchestra doctor` again; never stop the other process.
- We are done when `orchestra doctor` ends with `doctor: all required checks OK`. Show me
  that output; don't just tell me it worked.
```

```bash
git clone https://github.com/Tulum-DAO/orchestraos.git orchestraos && cd orchestraos
make install                 # symlinks bin/orchestra into ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"
grep -q 'HOME/.local/bin' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
orchestra init --yes         # data dir (~/.orchestra), orchestra.toml, .venv + pip, npm install, builds.
                             #   Takes about 5 minutes on a small VPS (the dashboard build); it is not hung.
                             #   --yes writes the Claude Code hook rows into ~/.claude/settings.json (see below)
                             #   mic dictation works in every browser out of the box (~99 MB model, background);
                             #   --stt adds the better faster-whisper engine (+~500 MB, see docs/ARTURO.md)
sed -i '/^\[runtimes\]/,/^\[/ s/^enabled = .*/enabled = ["claude"]/' orchestra.toml   # the CLI you logged in to
orchestra doctor             # every row OK (WARN/INFO rows are advisory); exit code 0
```

What these do: `git clone` downloads OrchestraOS into a folder called `orchestraos` and
`cd` moves you into it; `make install` and the two PATH lines make the `orchestra` command
available; `orchestra init --yes` sets everything up; the `sed` line tells OrchestraOS which
agent CLI you use; `orchestra doctor` checks the result.

You should see:

- `git clone`: progress lines ending with `Resolving deltas: 100% (...), done.` If it says the
  folder `orchestraos` already exists instead (you ran it before), run `cd ~/orchestraos` and
  continue with the next line.
- `make install`: first the command it runs (a line starting `mkdir -p`), then
  `installed /home/<you>/.local/bin/orchestra`, then a `NOTE` that
  `orchestra` is not on your PATH yet. The two PATH lines right after it fix that; they
  print nothing.
- `orchestra init --yes`: about five minutes of output, ending with a table whose rows
  say `did` (or `skipped` on a re-run), then three lines: `data dir:` (where OrchestraOS keeps
  its state), `config:` (your `orchestra.toml`) and `next:     orchestra doctor && orchestra up`.
  A step that failed shows `skipped` and the word `failed` in its row (for example
  `failed rc=1`, or `hook install failed: ...`); bring that row to
  whoever is helping you.
- `sed`: nothing.
- `orchestra doctor`: a table with one row per check (`CHECK`, `STATUS`, `DETAIL`, `REMEDY`),
  ending with `doctor: all required checks OK`. `WARN` and `INFO` rows are advice, not
  failures. A failing row ends with `-> ...`, a suggested fix: read it, and if an agent is
  helping you, it brings the suggestion to you rather than acting on it alone.

**A port is already taken** (`port:<name> MISSING :<port> in use by pid N`, or `in use by an
unknown process`, where `<name>` is `api`, `gateway`, `dashboard` or `arturo`): another program
on this server already uses that port. Don't stop that program, even though the remedy
mentions it; you may need it. Instead, give OrchestraOS a different port. Run only the line for
the row that failed, inside the `orchestraos` folder:

```bash
sed -i '/^\[dashboard\]/,/^\[/ s/^port = .*/port = 18891/' orchestra.toml   # port:dashboard
```

```bash
sed -i '/^\[gateway\]/,/^\[/ s/^port = .*/port = 18890/' orchestra.toml     # port:gateway
```

```bash
sed -i '/^\[api\]/,/^\[/ s/^port = .*/port = 18888/' orchestra.toml         # port:api
```

```bash
sed -i '/^\[arturo\]/,/^\[/ s/^port = .*/port = 15071/' orchestra.toml      # port:arturo
```

Then run `orchestra doctor` again. If you changed the `[dashboard]` port, use 18891 in §2's
`tailscale serve` command instead of 8891.

Not logged in to the CLI yet? Then `runtime:login` is the one `MISSING` row (a few rows
marked `MISSING*` go away with it) and the last line is `doctor: 1 required check(s)
MISSING`. Log in (§0, "Log in to the agent CLI") and run `orchestra doctor` again.

The two PATH lines matter: `~/.local/bin` only joins your PATH at **login**, and only if it
already existed then. On a clean machine it did not, so without them a bare `orchestra` right after
`make install` answers `command not found`. With them, `orchestra` works in this shell and in every
later one; `./bin/orchestra` from the checkout always works too.

The `sed` line sets the `enabled = [...]` line **under `[runtimes]`** to the one CLI you logged in
to. It must run inside the `orchestraos` folder (if you reconnected, run `cd ~/orchestraos`
first). Use `["gemini"]` or `["codex"]` if that is your CLI. For Gemini the command you ran is
`agy`, but the value here is still `"gemini"`. `orchestra.toml` has other `enabled =`
lines (`[arturo]`, `[telemetry]`, `[plugins.*]`); leave those alone. To edit by hand instead:
`nano orchestra.toml`, find `[runtimes]`, and change the `enabled` line just below it (save with
`Ctrl-O` then Enter; exit with `Ctrl-X`).

**Your timezone (recommended).** Agents show times in UTC unless you tell them where you are.
In `orchestra.toml`, under `[operator]`, set `timezone` to your IANA zone name, for example
`timezone = "America/Cancun"` or `timezone = "Europe/Berlin"` (find yours by searching "IANA
time zone" plus your city). It is not detected from the server on purpose: a cloud server
usually runs on UTC, not where you are. Left empty, agents show UTC and label it "UTC".

`orchestra init` is idempotent: it never overwrites `orchestra.toml`, skips what
exists, and prints did/skipped per step. `--data-dir PATH` moves state elsewhere
(so does `ORCHESTRA_DIR=PATH` in the environment: flag > `ORCHESTRA_DIR` > `[data] dir`
in orchestra.toml > `~/.orchestra`, and the default `~/.orchestra` is never touched when
the env names another dir); `--no-npm` / `--no-venv` / `--no-build` skip the slow steps.

The last step writes hook rows into your Claude Code settings
(`$CLAUDE_CONFIG_DIR/settings.json`, default `~/.claude/settings.json`) — a file every
Claude session on the machine reads. init prints exactly the rows it will add and asks
`y/N`; `--yes` (or `ORCHESTRA_YES=1`) answers yes, and a non-interactive run without it
SKIPS the hooks and says so (unattended installs: `orchestra init --yes`;
`ORCHESTRA_SKIP_HOOKS=1` for a container that runs no Claude seats).

Want gm on your phone? `plugins/telegram/README.md` — a BotFather token in
`TELEGRAM_BOT_TOKEN`, `[plugins.telegram] enabled = true`, and `orchestra up` runs the
channel: texts land in gm's inbox, decision cards arrive with buttons.

If you don't want the voice brain, set `[arturo] enabled = false` — the flask/openai
rows in doctor become INFO and `orchestra up` skips it.

## 2. Up

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#2-up
If you can't open that link, ask me to paste the section to you; don't guess commands.
The commands in this section run on my server, as my normal user (not root); I log in to
it with ssh (ask me for the address and user if you need them). The last part happens in
the browser on my own computer.
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- Never stop or kill a process you did not start, even if a message suggests it. Bring it
  to me instead.
- Before any `tailscale serve --https=...` command, run `tailscale serve status` and show me
  the output. Use an https port that is not in that list; never replace or turn off an
  entry that is already there. Never use `--funnel`, and never change `[dashboard] host`.
- We are done when `orchestra status` prints `supervisor: running pid ...`, and my browser
  shows the dashboard at the https address whose entry proxies to
  `http://127.0.0.1:<my [dashboard] port, default 8891>` in `tailscale serve status`. Ask
  me to confirm what I see; don't just tell me it worked.
```

```bash
orchestra up --detach && orchestra status   # starts everything in the background, then shows what is running
```

You should see: `supervisor started in background (pid …)`, then `supervisor: running pid …`,
then one line per part (`gateway`, `api`, `dashboard`, the beats) with its status. If it
says `supervisor already running` instead (you ran it before), run `orchestra status` on its own.
If it says `supervisor did not come up (...); see <data>/logs/supervisor.log`, read the last
lines of that file with `tail -n 30 <the supervisor.log path it printed>` and bring them to whoever is helping
you; don't keep re-running `orchestra up`.
It keeps
running after you log out. `orchestra down` stops it. (`orchestra up` without `--detach` runs
in the foreground instead, and `Ctrl-C` stops it.)

One supervisor process runs, restarts (with backoff) and logs each child under
`<data>/logs/<name>.log`:

| name | what | port / cadence (orchestra.toml) |
|---|---|---|
| gateway | `scripts/watch_gateway.py` — approvals + verified inject | `[gateway] port` (8890) |
| api | `api/dist/server.js` | `[api] port` (8888) |
| dashboard | `dashboard-proxy.js` — static UI, `/api` proxy, web terminal | `[dashboard] port` (8891) |
| arturo | `services/arturo/run.sh` (optional) | `[arturo] port` (5071) |
| bus_beat | event-bus drain | every `bus_beat_interval_seconds` (60) |
| boundary_delivery | turn-boundary delivery, armed | every 60 s (`boundary_delivery_armed`) |
| cron_beat | autonomous blue-green rotation beat — ON by default | every `cron_beat_interval_seconds` (900) |
| router | `scripts/message-router.py --cron` delivery backstop | `[router] interval_seconds` (60) |
| approval_resume | `approval_resume.py` — delivers an answered card to its seat (pane inject + msg_store row) | every 60 s |
| telemetryd | `lineage_daemon.telemetryd` — works out which seats are working or idle, for the Agents page and the router. Local only: it reads the seats' terminal output and `/proc` and writes a status file under `<data>/realtime`; nothing is sent anywhere | `[telemetry] enabled` (on) |
| menu_bridge | `scripts/menu_bridge.py` — turns a question menu inside a seat into a decision card, and types your answer back | every 60 s (`[menus] bridge_enabled`) |
| telegram | `plugins/telegram/router.py` (optional) | off unless `[plugins.telegram] enabled` and `TELEGRAM_BOT_TOKEN` |

No crontab is installed. `orchestra up --dry-run` prints this table without
starting anything. `orchestra down` stops it; `orchestra status` shows pids.

Smoke check: run `orchestra doctor` and look for its `api:health` row (it probes the API on
your own `[api] port`). Or, on the default port 8888 only:
`curl -s http://127.0.0.1:8888/api/health` → `{"status":"ok", "db":{"open":true}, ...}`.

### Open the dashboard in your browser, over Tailscale https

The dashboard listens on `127.0.0.1:8891` on the VPS, so it is not reachable from
outside the machine. `tailscale serve` gives it an https address that only devices
on your tailnet can open.

**Keep it tailnet only.** The dashboard has no login of its own, and its web terminal
types into your seats' terminals on the server. Anyone who can open it can run
commands as your user. So: never `--funnel` (that publishes it to the whole internet),
never `[dashboard] host = "0.0.0.0"` on a VPS, and share your tailnet only with people
you would give a shell to. The web terminal also refuses on purpose to connect when the
dashboard is opened by a raw IP address (e.g. `http://203.0.113.5:8891`), so a
`0.0.0.0` bind gets you a dashboard whose terminal does not work.

First see what Tailscale already serves on this machine. `tailscale serve` on an https
port that is already taken silently replaces whatever was there:

```bash
tailscale serve status        # on a fresh VPS: "No serve config"
```

Each entry starts with an `https://` line. An entry shown with no `:port` after the name (for
example `https://x.ts.net (tailnet only)`) is using port 443, so if you see one, 443 is taken.

Pick an https port that is not in that list. On a fresh VPS nothing is, so use 443,
which gives an address with no port number in it. The target is the dashboard's plain
http address (`[dashboard] port`, default 8891; `orchestra status` prints it):

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8891
```

If 443 was taken, use another free port instead, for example 8446 (the address then ends
in `:8446`):

```bash
tailscale serve --bg --https=8446 http://127.0.0.1:8891
```

If you changed the dashboard's port in §1 (`orchestra status` then shows `dashboard` on
`:18891`), use `http://127.0.0.1:18891` instead of `http://127.0.0.1:8891` in these commands.

The first time, Tailscale may answer that serve or https certificates
are not enabled on your tailnet and print an admin link. **[PERSON ONLY]** Open it, sign in
if asked, and enable them. The command may wait at that point and carry on by itself once
they are enabled; if it stopped, or you pressed `Ctrl-C`, run it again.

Then find your dashboard's address:

```bash
tailscale serve status
```

Each entry is an `https://` line followed by a `|-- / proxy ...` line. Your dashboard's
address is the `https://` line just above `|-- / proxy http://127.0.0.1:8891` (or your own
`[dashboard] port`, if you changed it in §1), for example:

```text
https://<vps>.<tailnet>.ts.net (tailnet only)
|-- / proxy http://127.0.0.1:8891
```

Ignore every other entry; they belong to other things on this server.

Open that address in a browser on your laptop or phone (it must be signed in to
Tailscale). The dashboard opens on its chat page, with **Arturo** at the top. Arturo
greets you and may start asking you first-run questions; you don't need to answer them to
continue. To see your agents, tap the gear button at the top left, then **Agents** (or add `/agents` to the
address). That page's heading is **Agents**, and it stays empty until step 3. Step 4 walks you
through this again once your team is running. The first
visit can take a few seconds while the certificate is issued. Optional: put the
address in `orchestra.toml` as `[public] host` so links in the UI and notifications
point at it.

`tailscale serve` keeps this setting across reboots. `tailscale serve --https=<the port you
used> off` removes only your dashboard's entry. Never turn off a port you didn't add: it may
belong to another app on this server.

The web terminal only accepts connections from the address the dashboard was opened at,
and it knows three kinds: loopback (`127.0.0.1`), the `[dashboard] host` you set, and
Tailscale names (`*.ts.net`). If you put the dashboard behind your own reverse proxy or
domain instead, list that name in `ORCHESTRA_DASHBOARD_ALLOWED_HOSTS` (comma-separated)
in the shell that runs `orchestra up`, then restart it, e.g.
`export ORCHESTRA_DASHBOARD_ALLOWED_HOSTS=dash.example.org`. Otherwise the dashboard loads
but its terminal fails to connect, and `<data>/logs/dashboard.log` shows
`[ws] refused ... (host-not-served)`.

No Tailscale? From your laptop, `ssh -L 8891:127.0.0.1:8891 <user>@<server>` and then
open `http://127.0.0.1:8891` while that ssh session stays open.

## 3. Your starter team

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#3-your-starter-team
The commands in this section run on my server; I log in to it with ssh (ask me for the
address and user if you need them).
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when `orchestra starter` ends with `starter team up: gm (T0) -> pm-first-project (T1) -> dev-first-project (T2)`. Show me that output; don't just tell me it worked.
```

One command starts three agents ("seats"), one at each level, each reporting to the one above:

| seat | tier | what it does |
|---|---|---|
| `gm` | T0, the manager | the one you talk to; always on; hands work down |
| `pm-first-project` | T1, a project manager | runs one project for gm; reports to `gm` |
| `dev-first-project` | T2, a worker | does the hands-on work, in its own folder (`~/.orchestra/projects/first-project/`); reports to `pm-first-project` |

```bash
orchestra starter
```

You should see some output for each of the three seats as it starts, ending with
`starter team up: gm (T0) -> pm-first-project (T1) -> dev-first-project (T2)`. It takes a
minute or two. The three seats run on your AI plan. The project manager and the worker say they
are ready and then wait; they do almost nothing until you give them work.

If it stops instead: `refusing to spawn: no enabled runtime is installed AND logged in` means
the agent CLI login in §0 was skipped; do that, then run `orchestra starter` again.
`starter stopped at <name>: ...` names the seat that did not start; fix the error printed above
it and run `orchestra starter` again (seats already running are skipped).
Want a real name instead of `first-project`? `orchestra starter --project website` names them
`pm-website` and `dev-website`. Running `orchestra starter` again is safe: seats that are already
running are skipped, and one that stopped is started again.

Then talk to gm. Run this at your server's own prompt, in the ssh session from your
computer, never inside an agent's screen and never in the dashboard's agent page: typing in
either of those already goes to the agent, and a `tmux attach` typed there takes over that
agent's screen.

```bash
tmux attach -t gm
```

**Talking to a seat.** Each seat runs in its own terminal session on the server, kept alive by
tmux, so it keeps working when you close the terminal on your own computer. `tmux attach -t gm` shows you
the `gm` seat's screen: type to it like a chat and press Enter. To leave without stopping it,
**detach**: press `Ctrl-B`, let go, then press `D`. You are back at your own prompt, and the
seat keeps running. (Closing the Terminal window also leaves it running.) `tmux ls` lists the
sessions. You can also talk to seats from the dashboard in your browser.

**If an agent's screen shows another tmux inside it** (two status bars at the bottom, or a
different agent's screen where you expected this one): press `Ctrl-B`, then `Ctrl-B` again,
then `D`. That closes the inner one and leaves your own view open; a single `Ctrl-B` then `D`
only detaches your own view. If the screen then shows a plain `$` prompt instead of the agent,
the agent itself has stopped: detach (`Ctrl-B`, then `D`), don't type commands at that prompt,
and send a report with the dashboard's **Report** button (top bar).

**Scrolling back.** While you are looking at an agent's screen in your terminal, turn the mouse
wheel to scroll back through what it wrote. To get back to typing, press `q`.

**If an old message appears in the box where you type** (it shows `History 1/...`; this happens
if you press the up arrow), press the **down arrow** until the box is empty again. Never press
Enter on it: that sends the old message to the agent again.

(Without a mouse: press `Ctrl-B`, let go, then `[`, and use the arrow keys; `q` returns. If the
wheel does not scroll an agent that was already running before you upgraded, run
`tmux set -t <seat> mouse on` once, with the agent's name in place of `<seat>`.)

**Selecting text** on an agent's screen to copy it: a plain drag does not select. Instead:

- **Windows Terminal, or Linux:** hold `Shift` while you drag.
- **iTerm2 on a Mac:** hold `Option` while you drag.
- **The Mac's Terminal app:** it has no drag key for this. Untick **View → Allow Mouse
  Reporting**, select and copy, then tick it again.

The three seats appear in the dashboard's Agents list in your browser within about 15 seconds.

One seat at a time, later: `orchestra spawn <seat>` registers and launches a single seat.
Options: `--runtime claude|gemini|codex` (default: first of `[runtimes] enabled`), `--model`,
`--tier`, `--prompt path/relative/to/checkout`. An existing registry row is kept as-is.

Mail a seat and watch it act with no keypress (the shipped hooks + the router beat under
`orchestra up`; see docs/HOOKS.md):

```bash
source scripts/orchestra-env.sh      # once per shell: tells msg_store.py and the scripts where your data dir is
echo "hello" > note.txt
python3 msg_store.py send --from you --to gm --subject hi --body-file note.txt
```

The lower-level pieces are still there (but a seat made this way has no lineage and
cannot be rotated — use `orchestra spawn` for anything you will rotate): `scripts/registry-update.py` writes the row,
`./spawn-agent.sh <seat> --task ...` launches an already-registered seat, `--list` / `--running`
show registered and live seats.

- `machine` is a label. On a single-machine install (`[machines]` left blank in
  `orchestra.toml`) the spawner never dispatches elsewhere.
- The spawner pre-seeds Claude Code's workspace-trust bit and the bypass-permissions
  acceptance for `cwd` (`scripts/ensure_cwd_trusted.py`; honors `CLAUDE_CONFIG_DIR`), so the
  seat does not stop at a first-run dialog. If you see one anyway, answer it once in `tmux attach`, run at your server's prompt (see §3).

Verify through the dashboard proxy (the same list the UI shows):

```bash
curl -s http://127.0.0.1:8891/api/agents | python3 -m json.tool | grep -E '"id"|"alive"|"state"'
```

The `gm`, `pm-first-project` and `dev-first-project` rows appear immediately; `alive`/`state` follow within ~15 s from the
status detector. Only registered seats are listed — tmux is host-global, see "Sharing a
host" below.

### Later: more seats (skip on a first install)

`orchestra agent create` fills a role template (it refuses an unfilled `{TOKEN}`), records the
seat's parent, checks the runtime and model, spawns it and checks it is alive. The parent should
be a seat that already exists. For example, with an existing `gm`:

```bash
orchestra agent create dev-x --template dev --parent gm --set PROJECT=demo
```

## 4. See your team from your own computer

Your three agents are running on the server. Now look at them from your own computer, in
your browser: the dashboard shows each agent, its tier and what it is doing, and lets you
type to it. No terminal needed for that.

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md#4-see-your-team-from-your-own-computer
Some commands run on my server (I log in to it with ssh; ask me for the address and user if
you need them), and one part happens on my own computer.
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- Never stop or kill a process you did not start, even if a message suggests it. Bring it
  to me instead.
- Don't run `tailscale serve` in this section, never use `--funnel`, and never change
  `[dashboard] host`. If no serve entry points at my dashboard, use section 2's own box
  instead (or ask me to paste section 2).
- First run `orchestra status` on the server and read the port on the `dashboard` row (8891
  unless I changed it). The dashboard's address is the serve entry that proxies to THAT
  port; another app may also use 8891.
- We are done when my browser shows the dashboard's Agents page with gm, pm-first-project
  and dev-first-project. Ask me to confirm what I see; don't just tell me it worked.
```

### 1. Put your computer on your Tailscale network

Your server is already on your private Tailscale network (§0). Your own computer has to join
the same one, signed in to **the same Tailscale account** as the server. If you already did
this in §0, skip to the check below.

**[PERSON ONLY]** Install Tailscale on your computer and sign in:
- **Mac:** **Tailscale** from the Mac App Store (or tailscale.com/download). Open it, click its
  icon in the menu bar (top right of the screen) and log in.
- **Windows:** the installer from tailscale.com/download. Windows asks whether to allow it to
  make changes: that admin prompt is yours. Then click the Tailscale icon in the taskbar's
  notification area, bottom right (click the `^` arrow there if you don't see it), and log in.
- **Linux:** the commands at tailscale.com/download, then `sudo tailscale up` and open the
  link it prints.
- **Phone** (optional, to see your team on the go): the **Tailscale** app from the App Store or
  Google Play.

Check, on the server:

```bash
tailscale status
```

You should see: one line for the server and one for your computer, with its name (for
example `macbook-pro` or `desktop-1a2b3c`) and a `100.x.y.z` address. If your computer is not
listed, it is signed in to a different Tailscale account: sign out of Tailscale on your
computer and sign in again with the account you used on the server.

### 2. Find your dashboard's address

On the server, first check which port your dashboard uses:

```bash
orchestra status
```

Read the port on the `dashboard` row: `:8891`, unless you changed it in §1 (for example
`:18891`). Then:

```bash
tailscale serve status
```

Your dashboard is the `https://` line just above `|-- / proxy http://127.0.0.1:<that port>`.
Ignore every other entry, including one that proxies to a port your dashboard does not use:
another app on this server may use 8891 too. If no entry proxies to your dashboard's port,
you skipped that part of §2, or it stopped at the link to turn on HTTPS certificates. Use
§2's **Hand this to your agent** box (or §2, "Open the dashboard in your browser, over
Tailscale https", by hand). It checks which ports are already taken before you pick one, so
you don't replace another app's address.

### 3. Open it and see your team

On your own computer, open that address in your browser and go to **Agents**.

You should see three agents, each marked alive, with its tier on the card:

- `gm`, **T0**: the one you talk to.
- `pm-first-project`, **T1**: reports to gm.
- `dev-first-project`, **T2**: reports to the project manager.

Click an agent to open its page: its screen, what it is doing, and a box to type to it. Typing
there is the same as typing into `tmux attach` on the server, so type your messages there, never
`tmux` commands. The first visit can take a few seconds while
Tailscale issues the https certificate.

### If it does not load

- **The page never loads, or says the site can't be reached:** your computer is not on your
  Tailscale network, or is signed in to another account. Check `tailscale status` on the
  server (step 1).
- **The page still fails although your computer is on your Tailscale network** (it shows in
  `tailscale status`): the first visit waits for the https certificate. Wait a minute and
  reload. If it still fails, bring the exact error text your browser shows to whoever is
  helping you.
- **It works on your computer but not your phone:** the phone needs the Tailscale app, signed
  in to the same account.
- **The Agents page is empty or the agents show as not alive:** wait 15 seconds and reload. If
  they stay that way, run `orchestra starter` on the server again; agents that are running are
  skipped.

## 5. Answer one approval card from the dashboard

From a shell (or let the seat run it):

```bash
source scripts/orchestra-env.sh      # already done in §3 if you are in the same shell; harmless to repeat
python3 scripts/approval.py request "Ship the first change?" --from dev-first-project --worker-kind pane --options approve,deny
# -> prints the card id, e.g. apr_1a2b3c4d_567
```

On a minimum install it also prints two warnings: an `authorship-guard` note (`no caller
signal ... fail-open`, because you ran it from a plain shell, not a seat) and one about
ntfy push (no push is set up on the minimum path). Both are normal; the card is created.

The card appears under Approvals in the dashboard (`GET /api/approvals` through the
proxy lists it under `pending`); answer it there, or from a shell:

```bash
curl -s -X POST http://127.0.0.1:8891/api/approvals/<card id>/approve
```

What happens next, and how to see it:

1. The answer is recorded in `<data>/state/tasks.db` (`python3 scripts/approval.py get <card id>`
   shows `status: answered`).
2. Within a minute the `approval_resume` beat (see the `orchestra up` table) delivers it:
   because the card came `--from dev-first-project --worker-kind pane`, the decision is typed into the
   `dev-first-project` tmux pane as a message and a durable row is written for the seat
   (`python3 msg_store.py inbox --agent dev-first-project`). `approval.py get` then shows
   `status: resumed`; `tmux capture-pane -p -t dev-first-project | tail -20` shows the delivered
   decision; `<data>/logs/approval_resume.log` has the delivery line.
3. `GET /api/approvals` keeps the card out of `pending` from the moment it is answered.

A card requested from an ambient shell behaves exactly like one a seat requested for
itself: the seat named in `--from` is the one that receives the answer.

## 6. Rotate a seat (manual, lossless)

A seat near its context ceiling banks a handoff (its prompt knows the format:
`<data>/docs/HANDOFF_<seat>-next.md` with a `## canary_questions` block anchored in its own
state). First check, which changes nothing:

```bash
orchestra rotate gm --dry-run            # preconditions only
```

Then rotate:

```bash
orchestra rotate gm                      # spawn successor -> it authors a readback -> strict grade -> promote
```

Two other forms, each for one situation only. Run one only if its situation applies:

- `orchestra rotate <seat> --synthesize`: the seat never banked a handoff; a minimal baton is
  made for it (sid, ports, last mail ids).
- `orchestra rotate <seat> --resume`: an earlier attempt is held and its successor pane is
  still up.

What "lossless" means here: the successor answers the canary questions from the handoff and
the repo alone; a generic readback HOLDs (predecessor keeps the seat, nothing is renamed). On
PASS the readback is committed in the data-dir repo (`orchestra init` made it one), the
registry flips to the new generation, the old pane is kept as `<seat>-gen<N>` for one
generation, and the successor gets its promotion prompt. Verify by effect:

```bash
python3 -c "import json;print(json.load(open('$ORCHESTRA_DIR/registry.json'))['agents']['gm'])"
tmux ls | grep gm
```

## 7. Check the rotation beat is armed (default ON)

`orchestra doctor` rows `rotation:beat` (armed, cadence, e-brake) and
`rotation:seats` (which T2 claude seats are eligible; the
`<runtime_dir>/self_retire_armed` allowlist — one lineage root per line — enables
hard rotation for a seat). E-brake: `touch ~/runtime/FLEET_BEAT_DISABLED`.

## Dev container / Docker (no VPS)

The repo ships a `Dockerfile` and `.devcontainer/devcontainer.json` that reproduce the
clean-machine recipe the B1 walkthroughs were proven on (ubuntu 24.04, non-root user
with sudo, §0 prerequisites, node 22, the Claude CLI preinstalled). `orchestra init`
runs at image build time, so `doctor` is instant on first open.

```bash
docker build -t orchestraos .
docker run -it --rm -p 8891:8891 -p 8888:8888 -p 8890:8890 orchestraos
# NOTE: those -p publishes answer HTTP 000 until you add a relay — the services bind 127.0.0.1
# inside the container. See "Running inside Docker: the services bind loopback" below.
# inside:  claude            # log in ONCE — your login, never baked into the image
#          orchestra doctor  # all required rows OK
#          orchestra up      # then open http://127.0.0.1:8891 on the laptop
```

- The login is yours: the image contains no credentials. To keep it across containers,
  mount your CLI config: `-v ~/.claude:/home/orchestra/.claude`. The dev container does
  that mount for you and keeps the data dir in a named volume (`orchestraos-data`).
- VS Code / GitHub Codespaces: "Reopen in Container". The workspace is bind-mounted over
  the image's copy, so `postCreateCommand` re-runs `orchestra init` once (~1 min) to
  rebuild `.venv` and `node_modules` for the mounted tree.
- Other CLIs: `docker build --build-arg AGENT_CLIS="@anthropic-ai/claude-code@2.1.276 @openai/codex"` (pin the version; see "Pin the agent CLI version" above).
- The container is one instance on one host: tmux inside it is its own, so the
  registry-scoping rules below apply per container.

### Machine image (VPS snapshot) — 20-minute job once the provider is chosen

Same recipe, no Docker: on a fresh Ubuntu 24.04 VPS as a non-root sudo user, run the
`RUN` steps of the `Dockerfile` in order (§0 prerequisites, node 22, `sudo npm i -g
@anthropic-ai/claude-code@2.1.276`, clone, `make install`, `orchestra init`), leave the agent CLI
logged OUT, then snapshot. A team booting the snapshot logs in, edits `[runtimes]
enabled`, and runs `orchestra doctor && orchestra up --detach`. Pre-provision one snapshot
per team (P5).

## Sharing a host with other tmux sessions

tmux is host-global. The dashboard's agent list, `agent-status.py --all` and the
rotation beat are **registry-scoped**: they only see sessions that resolve to a seat in
`<data>/registry.json` (its id or its `tmux_session`). `orchestra doctor` warns
(`tmux:foreign-sessions`) about the sessions it is ignoring. Set `[dashboard]
show_unregistered_sessions = true` to list them anyway; `agent-status.py --all
--all-sessions` is the host-wide escape hatch. One instance per host is still the
simplest setup.

## Where things live

- config: `orchestra.toml` (or `$ORCHESTRA_CONFIG`) — every key documented in `orchestra.example.toml`; secrets only via env
- data: `[data] dir` → `registry.json`, `state/` (sqlite, sessions, gateway token), `logs/`, `queue/`
- code: the checkout; `ORCHESTRA_ROOT` / `PYTHONPATH` are exported to every child by the supervisor

## Running inside Docker: the services bind loopback

Every service (`gateway`, `api`, `dashboard`, `arturo`) binds `127.0.0.1` by default
(`orchestra.toml` `[gateway] host`, `dashboard-proxy.js` `ORCHESTRA_DASHBOARD_HOST`). A Docker
`-p` published port therefore answers **HTTP 000** even on a fully-up container — measured on the
release image 2026-09-19: inside the container the dashboard answered 200, the published host
port answered nothing. Until the bind is configurable (post-release), publish through a small
in-container relay that listens on `0.0.0.0` and forwards to `127.0.0.1:8891`, and point your
`ssh -L` / browser at the relay's port. Inside the container:

```bash
sudo apt install -y socat
socat TCP-LISTEN:18891,fork,reuseaddr TCP:127.0.0.1:8891 &
```

Then publish `-p 8891:18891` instead of `-p 8891:8891`; any TCP forwarder does the same job.

## Reference install (the operator's own setup: VPS + Mac over Tailscale, ntfy, Telegram, voice)

Not needed for the minimum path. See `orchestra.example.toml` `[machines]`,
`[notify]` and `services/arturo/run.sh` for the knobs; a step-by-step is tracked as
checklist item B2.
