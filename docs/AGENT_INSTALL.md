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

Use their commands exactly. Don't rewrite them from memory. If you can run commands, fetch
pages raw rather than through a tool that summarises them, for example
`curl -s https://raw.githubusercontent.com/Tulum-DAO/orchestraos/main/docs/INSTALL.md`; a
summary loses the exact commands.

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
   - `ssh-keygen` (it asks where to save the key and for a passphrase)
   - `sudo tailscale up` (it prints a login link)
   - turning on HTTPS certificates / serve in the Tailscale admin page
   - the agent CLI's first login on the server

   Show them the exact command, wait for "done", and ask them to paste what it printed. For
   the logins (`sudo tailscale up`, the agent CLI login), ask only for "done" and then run the
   check yourself (a chat-only assistant has them run the check and paste its output instead):
   their login screen can contain a link or code, so don't ask them to paste that screen.
   Everything else, run yourself and show the output.

   This holds for an agent running ON the server too (for example Claude Code started over
   ssh): a `sudo` line still needs the person's password, so they open a second ssh window to
   the server and run it there (on Windows Terminal: a new tab with `Ctrl+Shift+T`, then `ssh`).
4. **Never delete, destroy, reset, overwrite, wipe or kill** anything you did not create in
   this install. If a command asks `Overwrite (y/n)?`, the answer is `n`. Never stop or kill a
   process you did not start, even if a message suggests it; bring it to the person.
5. **Tailscale serve:** run `tailscale serve status` before any `tailscale serve` command. Never
   replace or turn off an entry that is already there (it may belong to another app); use a
   free https port. Never use `--funnel`. Never change `[dashboard] host`. An entry the person
   didn't add in this install is not theirs, even if it proxies to the dashboard's port.
6. **Ports come from `orchestra status`** (the `dashboard` row, and so on), not from memory.
   The one exception: before the first `orchestra up`, `orchestra status` has no rows yet, so
   read the port from `orchestra.toml` instead (the `port` line under `[dashboard]`, for example
   `grep -A3 '^\[dashboard\]' ~/orchestraos/orchestra.toml`).
7. **Never run `orchestra down`** without the person's yes. Restarting is their decision.
8. **Run from the right folder.** Commands that read `orchestra.toml` or the `scripts/` folder
   need `cd ~/orchestraos` first, in the same command (for example
   `ssh user@host 'cd ~/orchestraos && ./bin/orchestra doctor'`). Over a plain `ssh` command,
   `~/.local/bin` may not be on the PATH, so use `~/orchestraos/bin/orchestra` or
   `./bin/orchestra`.
9. **Show, don't claim.** At the end of each phase, show the done-check output. Never just say
   it worked.
10. **Long commands run in the background.** Your tool may stop a command after a couple of
    minutes (Claude Code's default is 2). Anything that can take longer, such as
    `orchestra init --yes` (about five minutes), you start detached, with an exit marker, and
    then check on it every 30 to 60 seconds (phase 6 shows the exact form). Never start it a
    second time while the first is still running: "1. Where am I?" shows how to tell.

## 1. Where am I? (do this first, and again whenever you take over)

Work out where things stand before doing anything. These checks only read; they change nothing.

- Where are you running? If you can run commands, run `hostname` and `whoami`. If you can't,
  ask the person.
- Ask: which computer they use (Mac, Windows or Linux), and whether they already have a server.
- If a server exists: its address, and the user they log in as.
- On the server, if you can reach it (`ssh <user>@<address> '<command>'`, or you are running
  on it): `whoami`, `tailscale status`, `command -v claude codex agy`, the CLI's login check
  (for Claude Code, `claude auth status`), `ls ~/orchestraos/bin/orchestra`,
  `~/orchestraos/bin/orchestra status`, and `tailscale serve status`. A serve entry is this
  install's only if it proxies to the dashboard port that `orchestra status` shows AND the
  person added it in phase 7; anything else is not theirs.
- Is `orchestra init` running, done or failed? `orchestra status` can't tell (it says the
  supervisor is not running in all three cases), so check:
  `pgrep -af "[o]rchestra init"` and `tail -n 5 ~/init.log`.
  - A `./bin/orchestra init` process is listed: it is still running. Check again in 30 to 60
    seconds; NEVER start init again. (Only that line counts; ignore any other line that merely
    mentions those words.)
  - `~/init.log` ends with `INIT_EXIT=0`: init finished. Continue with `orchestra doctor`.
  - `INIT_EXIT=` followed by anything else, or a row that says `failed`: bring those lines to the
    person; don't retry by guessing.
  - No process and no `INIT_EXIT=` line (it was cut off): start it once more, as phase 6 shows.

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
- First check for an existing key yourself (`ls ~/.ssh/id_ed25519.pub`, or ask them to run it).
  If one exists, use it and skip `ssh-keygen`.
- **[PERSON ONLY]** `ssh-keygen` asks where to save the key and for a passphrase, so the person
  runs the whole command in their own terminal (rule 3). If it asks `Overwrite (y/n)?`, the
  answer is `n`: they already have a key.
- **[PERSON ONLY]** If they set a passphrase, every ssh you run would stop to ask for it. So they
  load the key once, in their own terminal:
  - Mac: `ssh-add --apple-use-keychain ~/.ssh/id_ed25519`
  - Linux: `ssh-add ~/.ssh/id_ed25519`
  - Windows (PowerShell as administrator, once): `Get-Service ssh-agent | Set-Service
    -StartupType Automatic`, then `Start-Service ssh-agent`; then, in a normal PowerShell,
    `ssh-add $env:USERPROFILE\.ssh\id_ed25519`

  Use their key's own name if it isn't `id_ed25519`.
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
- Then the person types `exit` to leave the root session, and logs in again as the new user:
  `ssh <user>@<address>`. From here on, every command and every ssh window is the new user,
  never root. (As root, the next phases would set things up for root instead.)
- Done when: `ssh <user>@<address> whoami` prints the new user's name. For Path A, also check
  that it works without a password prompt:
  `ssh -o BatchMode=yes <user>@<address> whoami`. If that fails with `Permission denied` or a
  passphrase question, the key isn't loaded: go back to the `ssh-add` step in phase 1. On a Mac
  this also happens after a restart; running `ssh-add --apple-use-keychain` again fixes it (or
  they can add `AddKeysToAgent yes` and `UseKeychain yes` under `Host *` in `~/.ssh/config`).
- Keep using the server's public IP address for ssh, even after Tailscale is on. A new name or
  a `100.x.y.z` address makes ssh ask the `yes/no` key question again, which you can't answer.

### Phase 4: Tailscale on the server AND on the person's own device

- Section: Install guide, "Tailscale on the VPS and on your own device".
- On the server: the install line, `sudo tailscale up` and `sudo tailscale set --operator=$USER`
  all use `sudo`, so the person runs them in their own ssh session, logged in as the new user,
  not root (`whoami` must print the new user; otherwise `$USER` is root) (rule 3). **[PERSON ONLY]**
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
  in their own ssh session, as the new user, not root (rule 3). Run the checks yourself afterwards (`git --version`,
  `node --version`, `claude --version` or the CLI they chose).
- **[PERSON ONLY]** The agent CLI login: the person runs `claude` (or their CLI) in their own ssh
  session and signs in through their own browser, as that section describes. It needs a paid
  plan, which they buy themselves.
- Done when: the CLI's login check passes (for Claude Code, `claude auth status` shows
  `"loggedIn": true`).

#### Path B hand-over (chat-only assistants stop here)

You have taken the person as far as a chat can. Now the agent on their server carries on. Tell
them, word for word:

1. In your ssh session to the server (as the new user), start a tmux session, so that a dropped
   connection or a sleeping laptop doesn't stop the agent: `tmux new -s install`. (If it says
   `duplicate session`, one is already there: run `tmux attach -t install` instead.)
2. In it, run `claude` (or the agent CLI you installed).
3. Paste this into it, as one message, with your one-line summary at the end:

   > Read https://raw.githubusercontent.com/Tulum-DAO/orchestraos/main/docs/AGENT_INSTALL.md
   > and continue the install. You are running ON the server now. Start with "1. Where am I?".
   > What's done so far: <the one-line summary from "1. Where am I?">

4. Answer its questions there. It may also ask your permission before it runs a command; that is
   the agent CLI's own safety check. It will tell you when your dashboard is ready. If the
   connection drops, or the window stops responding (a sleeping Windows Terminal freezes rather
   than showing a drop), close it, open a new one, ssh in again and run `tmux attach -t install`.

Fill in the placeholder yourself, and give them the whole block above, already filled in, as ONE
message they can paste as it is.

### Phase 6: clone, init, doctor (on the server)

- Section: Install guide, "1. Clone, init, doctor".
- Nothing here asks a question, so in Path A you run it all yourself. `orchestra init --yes`
  takes about five minutes, longer than your tool's command limit (rule 10). Run it detached
  from the `orchestraos` folder with an exit marker, exactly like this:

  ```bash
  ssh <user>@<address> 'cd ~/orchestraos && nohup sh -c "./bin/orchestra init --yes; echo INIT_EXIT=\$?" > ~/init.log 2>&1 &'
  ```

  (An agent running on the server itself runs the part inside the single quotes.) Then check
  every 30 to 60 seconds with `tail -n 5 ~/init.log`, and with "1. Where am I?"'s init check if
  you lose track. It is done when the log has an `INIT_EXIT=` line: `INIT_EXIT=0` means success
  (still scan the table for a row that says `failed`); any other number, or a `failed` row, goes
  to the person. The `next:` line alone proves nothing: init prints it even when a step failed. Its `--yes` adding hook rows to
  `~/.claude/settings.json` is expected, not an overwrite.
- In the `[runtimes]` `sed` line, use the CLI from phase 5. Ask if you don't know.
- If `orchestra doctor` shows a `port:<name> MISSING ... in use` row, another program owns that
  port. Never stop it (rule 4). Use that section's line for the port, then run doctor again.
- Done when: `orchestra doctor` ends with `doctor: all required checks OK`.

### Phase 7: start it, and the dashboard link (on the server)

- Section: Install guide, "2. Up", including "Open the dashboard in your browser, over Tailscale
  https".
- First the leftover-entry check that section describes: `tailscale serve status` before
  `orchestra up`. The dashboard's port comes from `orchestra.toml` here, not from `orchestra
  status`, since nothing is running yet (rule 6). If an entry the person didn't add already
  proxies to that port,
  move the dashboard to another port with that section's line, and confirm afterwards that the
  `dashboard` row of `orchestra status` shows the new port. Never touch that other entry.
- `orchestra up --detach`, then `orchestra status`. If it says `supervisor already running`,
  run `orchestra status` on its own; never `orchestra down` (rule 7).
- Before adding the entry, check that HTTPS certificates are on for their tailnet:
  `tailscale status --json` lists the server's name under `CertDomains` when they are. If it is
  empty or missing, **[PERSON ONLY]** have the person turn on HTTPS (and MagicDNS, if asked) in
  their Tailscale admin page first (rule 3): at login.tailscale.com, the **DNS** page, the
  **HTTPS Certificates** setting (the page's wording may differ). Otherwise `tailscale serve`
  stops and waits for it. After they say "done", check `CertDomains` again before going on.
- Then `tailscale serve status` again, pick a free https port, and add the entry pointing at the
  dashboard port from `orchestra status` (rules 5 and 6). Wrap it so it can't hang, for example
  `timeout 90 tailscale serve --bg --https=<port> http://127.0.0.1:<dashboard port>`, and show
  its output. **[PERSON ONLY]** If it prints an admin link, hand it to the person, wait for
  "done", and run the same command again. If it stops after 90 seconds (exit code 124) without
  printing a link, run it once more; if that also stops, bring it to the person.
- Done when: `tailscale serve status` shows the entry you just added, proxying to the dashboard's
  port.

## 4. The end

Give the person exactly one thing:

> Open your dashboard: https://<the address of the entry you just added>

Ask them to open it on their own computer (any device on their Tailscale network works) and tell
you that the dashboard loads. The first visit can take a few seconds while the certificate is
issued. Once they confirm, add one line:

> Arturo takes it from there.

Don't set up the team yourself. In the dashboard, Arturo offers to start it, and it can explain
the rest.
