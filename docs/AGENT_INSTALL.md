# OrchestraOS install playbook (for the AI helping the reader)

You are an AI assistant. The person who gave you this link wants OrchestraOS installed on a
server of their own, and wants you to do as much of it as you can. This page is written for
you. It tells you the goal of each phase, where the exact commands are, how to check each
phase is done, and the few steps only the person can do.

The finish line is one thing: the person opens their OrchestraOS dashboard in their browser.
From there, Arturo (the dashboard's built-in guide) takes over.

The commands themselves live in two tested guides. Each phase below names the section to use:

- From scratch: https://github.com/Tulum-DAO/orchestraos/blob/main/docs/FROM_SCRATCH.md
- Install guide: https://github.com/Tulum-DAO/orchestraos/blob/main/docs/INSTALL.md

Use their commands exactly. Don't rewrite them from memory.

## 0. Rules (all of them, every phase)

1. **If you can't open a link, ask the person to paste that section to you.** Each phase names
   the exact section. Never guess a command.
2. **Steps marked [PERSON ONLY] are the person's.** That means paying, signing in, any password
   or passphrase, any `sudo` password prompt, approving a device, an admin or UAC prompt, and
   any decision about another program, port or Tailscale entry already on the server. Stop,
   tell them exactly what to do, and wait. Never ask for their passwords.
3. **Anything that asks a question in the terminal is the person's to run.** If you run
   commands over `ssh user@host '<command>'`, there is no terminal for prompts, so the
   following go to the person, typed in their own terminal:
   - every `sudo` line (each ssh call is a new session, so a `sudo -v` the person typed earlier
     does not carry over to yours)
   - `adduser` and anything else that asks for a password
   - the ssh key passphrase
   - the first `ssh` to a new server (it asks `Are you sure you want to continue
     connecting (yes/no...)?`; the answer is `yes`)
   - `sudo tailscale up` (it prints a login link)
   - the agent CLI's first login on the server

   Show them the exact command, wait for "done", and ask them to paste what it printed.
   Everything else, run yourself and show the output.
4. **Never delete, destroy, reset, overwrite, wipe or kill** anything you did not create in
   this install. If a command asks `Overwrite (y/n)?`, the answer is `n`. Never stop or kill a
   process you did not start, even if a message suggests it; bring it to the person.
5. **Tailscale serve:** run `tailscale serve status` before any `tailscale serve` command. Never
   replace or turn off an entry that is already there (it may belong to another app); use a
   free https port. Never use `--funnel`. Never change `[dashboard] host`. An entry the person
   didn't add in this install is not theirs, even if it proxies to the dashboard's port.
6. **Ports come from `orchestra status`** (the `dashboard` row, and so on), not from memory.
7. **Never run `orchestra down`** without the person's yes. Restarting is their decision.
8. **Run from the right folder.** Commands that read `orchestra.toml` or the `scripts/` folder
   need `cd ~/orchestraos` first, in the same command (for example
   `ssh user@host 'cd ~/orchestraos && ./bin/orchestra doctor'`). Over a plain `ssh` command,
   `~/.local/bin` may not be on the PATH, so use `~/orchestraos/bin/orchestra` or
   `./bin/orchestra`.
9. **Show, don't claim.** At the end of each phase, show the done-check output. Never just say
   it worked.

## 1. Where am I? (do this first, and again whenever you take over)

Work out where things stand before doing anything. These checks only read; they change nothing.

- Where are you running? If you can run commands, run `hostname` and `whoami`. If you can't,
  ask the person.
- Ask: which computer they use (Mac, Windows or Linux), and whether they already have a server.
- If a server exists: its address, and the user they log in as.
- On the server, if you can reach it (`ssh <user>@<address> '<command>'`, or you are running
  on it): `whoami`, `tailscale status`, `command -v claude codex agy`,
  `ls ~/orchestraos/bin/orchestra`, and `~/orchestraos/bin/orchestra status`.

Then say the facts back in one line, and repeat that line at the start of each later phase,
for example:

> Computer: Mac. Server: 203.0.113.5, user `orchestra`. Tailscale on both: yes. Agent CLI on the
> server: claude, logged in. OrchestraOS: installed, not started. Next: phase 7.

Start at the first phase that is not done yet.

## 2. Choose a path

- **Path A: you can run commands on the person's computer** (you are Claude Code, Codex, Gemini
  CLI or similar, running on their machine). You do phases 1 to 7 yourself: from phase 3 on,
  over `ssh`. The person only does the [PERSON ONLY] steps and the interactive commands in rule 3.
- **Path B: you can only chat.** Guide the person through phases 1 to 5 one command at a time:
  give one command, wait for them to paste what it printed, then the next. At the end of
  phase 5, hand over to the agent CLI on the server (see "Path B hand-over" below). That agent
  finishes phases 6 and 7 as Path A, from the server side.

## 3. Phases

### Phase 1: a terminal and an ssh key (on the person's computer)

- Section: From scratch, "1. Open a terminal" and "2. Make an ssh key".
- Path A: run the key commands yourself, except the passphrase prompt (rule 3). If a key already
  exists and the command asks `Overwrite (y/n)?`, the answer is `n`; use the existing key.
- Done when: the public key file exists (`ls ~/.ssh/id_ed25519.pub`, or the name of their
  existing key) and they have its contents ready to paste into the provider.

### Phase 2: rent a server

- Section: From scratch, "3. Rent a server".
- **[PERSON ONLY]** Signing up, any email or identity check, and paying are theirs. Walk them
  through the provider's pages; never type their payment details or passwords.
- Done when: they tell you the server's IP address, shown in the provider's panel.

### Phase 3: first login, and a normal user

- Sections: From scratch, "4. Log in to your server for the first time", then Install guide,
  "Run as a normal user, not root".
- The first `ssh root@<address>` asks `yes/no` about the server's key: the person types `yes` in
  their own terminal (rule 3).
- `adduser` asks for a new password: the person runs it in their own ssh session. The other lines
  of that section (`usermod`, `mkdir`, `cp`, `chown`, `chmod`) run as root and ask nothing; in
  Path A you may run them over `ssh root@<address> '<line>'`.
- Done when: `ssh <user>@<address> whoami` prints the new user's name. For Path A, also check
  that it works without a password prompt:
  `ssh -o BatchMode=yes <user>@<address> whoami`.

### Phase 4: Tailscale on the server AND on the person's own device

- Section: Install guide, "Tailscale on the VPS and on your own device".
- On the server: the install line, `sudo tailscale up` and `sudo tailscale set --operator=$USER`
  all use `sudo`, so the person runs them in their own ssh session (rule 3). **[PERSON ONLY]**
  `sudo tailscale up` prints a login link: they open it and sign in.
- **[PERSON ONLY]** On their own computer (and phone, if they want): Tailscale, signed in to the
  SAME account. On Windows the installer asks for admin approval; that is theirs too.
- Done when, checked from the server side: `tailscale status` lists both the server and the
  person's device, and `tailscale ping <their device name>` answers. If it doesn't, loop: the
  usual cause is that the device is signed in to a different Tailscale account. Have them sign
  out on the device and sign in with the account they used on the server, then check again.
- If their only other device is a phone: it must be listed in `tailscale status`. A phone may not
  answer `tailscale ping` while the Tailscale app is in the background; ask them to open the app
  and try again. Then they will open the dashboard on that phone.

### Phase 5: packages and an agent CLI on the server

- Sections: Install guide, "Packages", "Pin the agent CLI version", and "Log in to the agent CLI
  (the one step only you can do)".
- Every line in "Packages" and "Pin the agent CLI version" uses `sudo`, so the person runs them
  in their own ssh session (rule 3). Run the checks yourself afterwards (`git --version`,
  `node --version`, `claude --version` or the CLI they chose).
- **[PERSON ONLY]** The agent CLI login: the person runs `claude` (or their CLI) in their own ssh
  session and signs in through their own browser, as that section describes. It needs a paid
  plan, which they buy themselves.
- Done when: the CLI's login check passes (for Claude Code, `claude auth status` shows
  `"loggedIn": true`).

#### Path B hand-over (chat-only assistants stop here)

You have taken the person as far as a chat can. Now the agent on their server carries on. Tell
them, word for word:

1. In your ssh session to the server, run `claude` (or the agent CLI you installed).
2. Paste this into it:

   > Read https://raw.githubusercontent.com/Tulum-DAO/orchestraos/main/docs/AGENT_INSTALL.md
   > and continue the install. You are running ON the server now. Start with "1. Where am I?".

3. Answer its questions there. It will tell you when your dashboard is ready.

Then give them the one-line summary from "1. Where am I?" to paste in as well, so the server's
agent knows what is already done.

### Phase 6: clone, init, doctor (on the server)

- Section: Install guide, "1. Clone, init, doctor".
- Nothing here asks a question, so in Path A you run it all yourself. `orchestra init --yes`
  takes about five minutes; wait for it, don't stop it. Its `--yes` adding hook rows to
  `~/.claude/settings.json` is expected, not an overwrite.
- In the `[runtimes]` `sed` line, use the CLI from phase 5. Ask if you don't know.
- If `orchestra doctor` shows a `port:<name> MISSING ... in use` row, another program owns that
  port. Never stop it (rule 4). Use that section's line for the port, then run doctor again.
- Done when: `orchestra doctor` ends with `doctor: all required checks OK`.

### Phase 7: start it, and the dashboard link (on the server)

- Section: Install guide, "2. Up", including "Open the dashboard in your browser, over Tailscale
  https".
- First the leftover-entry check that section describes: `tailscale serve status` before
  `orchestra up`. If an entry the person didn't add already proxies to the dashboard's port,
  move the dashboard to another port with that section's line, and confirm afterwards that the
  `dashboard` row of `orchestra status` shows the new port. Never touch that other entry.
- `orchestra up --detach`, then `orchestra status`. If it says `supervisor already running`,
  run `orchestra status` on its own; never `orchestra down` (rule 7).
- Then `tailscale serve status` again, pick a free https port, and add the entry pointing at the
  dashboard port from `orchestra status` (rules 5 and 6). **[PERSON ONLY]** If Tailscale prints
  an admin link to turn on HTTPS certificates, the person opens it and turns them on.
- Done when: `tailscale serve status` shows the entry you just added, proxying to the dashboard's
  port.

## 4. The end

Give the person exactly one thing:

> Open your dashboard: https://<the address of the entry you just added>

It works in a browser on any of their devices that is on their Tailscale network. The first visit
can take a few seconds while the certificate is issued. Then add one line:

> Arturo takes it from there.

Don't set up the team yourself. In the dashboard, Arturo offers to start it, and it can explain
the rest.
