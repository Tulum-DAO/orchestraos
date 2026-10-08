# From scratch: from your computer to your own server

This page is for you if you have never rented a server, made an ssh key, or typed
commands into a server. You'll need a computer (Mac, Windows or Linux) with an internet
connection, an email address, and a way to pay for a server and an AI plan; step 3 shows
where. You need to be able to copy and paste. Nothing else is assumed.

By the end of this page you will have your own server on the internet, and you will be
logged in to it from your computer. Then [docs/INSTALL.md](INSTALL.md) takes over and
installs OrchestraOS on it. This page takes about 20 minutes.

Most steps are the same on every computer. Where they differ, you get **Mac**, **Windows**
and **Linux** lines: follow the one for your computer.

## Words you will meet

- **Terminal**: an app where you type commands instead of clicking. Each command is one
  line of text; you press Enter to run it, and the terminal prints the result. Every
  computer has one (step 1 shows where).
- **Server** or **VPS** (virtual private server): a computer you rent in a data
  centre. It stays on all the time, so your agents keep working when your own computer
  is asleep or off.
- **IP address**: the server's address on the internet, four numbers with dots, like
  `203.0.113.25`. (That one is an example; yours will be different.)
- **ssh**: how you log in to the server from your terminal. Once logged in, the commands
  you type run on the server, not on your computer.
- **ssh key**: a pair of files on your computer that let you log in without a password.
  The **public** half (ends in `.pub`) is safe to give to the server company. The
  **private** half never leaves your computer and is never shared with anyone.
- **root**: the all-powerful administrator account on the server.

## How to read the boxes on this page

Each grey box holds commands. Copy **one line at a time**, paste it into your terminal,
and press Enter. Text after a `#` is a note for you; the computer ignores it, so it is
fine to paste it too. Under each box, **"You should see"** tells you what success looks
like. If you see something else, stop and check the "If it goes wrong" notes before you
continue.

**Steps marked [PERSON ONLY]** are ones you do yourself: paying, signing in, choosing a
password, approving a device. If an AI agent is helping you, it stops there and lets you do
them.

**Want an AI agent to guide you?** Each step below has a **Hand this to your agent** box.
Copy it into the AI you already use (Claude, ChatGPT, Codex, Gemini or any other); it reads
that step and walks you through it, and stops at the [PERSON ONLY] steps.

**Copy and paste in a terminal:**

- **Mac** (Terminal): `Cmd+C` and `Cmd+V`.
- **Windows** (Windows Terminal or PowerShell): `Ctrl+C` copies selected text and `Ctrl+V`
  pastes. In the older PowerShell window, right-click pastes.
- **Linux**: `Ctrl+Shift+C` and `Ctrl+Shift+V`.

## 1. Open a terminal

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/FROM_SCRATCH.md#1-open-a-terminal
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when `whoami` in my terminal prints my username. Show me that output; don't just tell me it worked.
```

- **Mac**: press `Cmd+Space`, type `Terminal`, press Enter. The prompt ends in `%`, for
  example `yourname@Your-MacBook ~ %`.
- **Windows 10 or 11**: click **Start**, type `Terminal` (Windows 11) or `PowerShell`
  (Windows 10), press Enter. The prompt looks like `PS C:\Users\yourname>`.
- **Linux**: open your **Terminal** app. The prompt usually ends in `$`.

That line is the **prompt**: the terminal is waiting for you to type. Try your first
command:

```bash
whoami
```

You should see: your username on this computer (on Windows, `computername\yourname`), and
then the prompt again.

## 2. Make an ssh key

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/FROM_SCRATCH.md#2-make-an-ssh-key
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when showing my public key prints one line that starts with `ssh-ed25519`. Show me that output; don't just tell me it worked.
```

First check whether you already have one:

- **Mac or Linux**: `ls ~/.ssh/id_ed25519.pub`
- **Windows**: `dir $env:USERPROFILE\.ssh\id_ed25519.pub`

If it prints the file's name (Windows lists it with a date and size), you already have a
key: skip to "Show the public half" below. If it says `No such file or directory` (Mac or
Linux) or `Cannot find path` (Windows), make one. The command is the same on every
computer:

```bash
ssh-keygen -t ed25519 -C "my-computer"
```

It asks three questions. Answer them like this:

1. `Enter file in which to save the key`: press Enter (keep the suggested place). If it then
   asks `Overwrite (y/n)?`, you already have a key: type `n`, press Enter, and skip to "Show
   the public half" below. Never answer `y`: that replaces your existing key for good.
2. **[PERSON ONLY]** `Enter passphrase`: press Enter for no passphrase, or type one you will remember.
   Nothing appears on screen while you type it; that is normal.
3. **[PERSON ONLY]** `Enter same passphrase again`: the same again.

You should see: `Your identification has been saved in ...id_ed25519`, then
`Your public key has been saved in ...id_ed25519.pub`, then a small box of random
characters. That is your key pair.

**On Windows, if it says `ssh-keygen` is not recognized:** ssh is built into Windows 10
(version 1809 or later) and Windows 11, but it can be switched off. Click **Start**, type
`Optional features`, open it, and check that **OpenSSH Client** is in the list. If it is
not, add it, then close and reopen your terminal.

**Show the public half**, so you can copy it:

- **Mac or Linux**: `cat ~/.ssh/id_ed25519.pub`
- **Windows**: `type $env:USERPROFILE\.ssh\id_ed25519.pub`

You should see: one long line that starts with `ssh-ed25519 AAAA` and ends with
`my-computer` (or whatever name your existing key has). Select the whole line and copy it. (On a Mac, `pbcopy < ~/.ssh/id_ed25519.pub`
copies it for you; on Windows, `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard`
does the same.)

## 3. Rent a server

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/FROM_SCRATCH.md#3-rent-a-server
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when I tell you my new server's IP address (four numbers with dots), which
  I read off the provider's website.
```

This is the step where you pay. A server is rented by the hour from a hosting company,
and you pay that company directly, not OrchestraOS. The size these docs use costs about
$24 a month at DigitalOcean, billed by the hour while the server exists. You will also need a paid plan for one AI tool to run your agents (Claude
Pro is about $20 a month; Claude's website takes only a credit or debit card, while buying it
in the Claude iPhone or Android app uses your App Store or Google Play payment; check which
payment methods your store accepts);
[docs/INSTALL.md](INSTALL.md) covers it when you get there.
[docs/COSTS.md](COSTS.md) compares providers and prices.

We suggest **DigitalOcean** for a first server: its screens are simple, and it takes a
card, PayPal, Google Pay or Apple Pay. (With PayPal, it asks you to allow a $5
authorization charge to verify the account; see DigitalOcean's payment-methods docs.) The install works on any Ubuntu 24.04 server; other providers are at the
end of this step.

1. **[PERSON ONLY]** **Sign up** at digitalocean.com and add a payment method (card, PayPal, Google Pay or
   Apple Pay). Confirm your email, and complete any identity check, if it asks.
2. **[PERSON ONLY]** **Give it your public key.** Go to **Settings**, then the **Security** tab, and click
   **Add SSH Key**. Paste the line you copied in step 2 into **Public Key**. Type
   `my-computer` as the **Key Name**. Click **Add SSH Key**.
3. **[PERSON ONLY]** **Create the server.** Click the green **Create** button at the top, then
   **Droplets** ("Droplet" is DigitalOcean's word for a server). Fill in the page from
   top to bottom:
   - **Choose Region**: the one nearest to you.
   - **Choose an image**: the **OS** tab, **Ubuntu**, version **24.04 (LTS) x64**.
   - **Choose Size**: **Shared CPU**, **Basic**, then the plan with **4 GB** memory and
     **2 CPUs**. Smaller plans are not enough for agents.
   - **Choose Authentication Method**: **SSH key**, and tick `my-computer`.
   - **Hostname**: `orchestra` (lowercase letters only, no spaces).
   - Click **Create Droplet**.
4. **[PERSON ONLY]** **Find its address.** After about a minute the server appears in your
   **Droplets** list. Its row shows the **IP address**: four numbers with dots, like
   `203.0.113.25`. Copy it; you need it in the next step. Wherever the docs say
   "your server address", this is what they mean (until Tailscale gives the server a
   second, private address in [docs/INSTALL.md](INSTALL.md)).

You should see: your new droplet in the **Droplets** list, with an IP address in its row.

**Other providers.** Any company that rents Ubuntu 24.04 servers and lets you add an
ssh key works: Hetzner (cheaper; may ask for identity verification), Vultr, Linode,
and others. Pick Ubuntu 24.04, at least 2 CPUs and 4 GB of memory, and choose your
`my-computer` key when you create the server.

## 4. Log in to your server for the first time

**Hand this to your agent** (Claude, ChatGPT, Codex, Gemini or any other), if you'd rather
have it guide you through this section. Copy the whole box:

```text
Help me with one step of installing OrchestraOS. Read this section and do it with me:
https://github.com/Tulum-DAO/orchestraos/blob/main/docs/FROM_SCRATCH.md#4-log-in-to-your-server-for-the-first-time
Rules:
- If you can run commands on my computer, run them yourself and show me every output.
  If you can't, give me one command at a time and wait for me to paste back what it printed.
- Stop at every step marked [PERSON ONLY] (paying, signing in, any password or
  passphrase prompt including sudo's, approving a device or an admin prompt) and let me
  do it myself. Never do those for me, and never ask for my passwords.
- Never delete, destroy, reset, overwrite or wipe anything. If a command asks
  `Overwrite (y/n)?`, the answer is n.
- We are done when, at my server's prompt, `whoami` prints `root`. Show me that output;
  don't just tell me it worked.
```

In your terminal, type `ssh root@` followed by your server's address. It is the same on
every computer:

```bash
ssh root@203.0.113.25        # use YOUR address from step 3
```

The first time, it asks you to confirm that this is a server you mean to talk to:

```
The authenticity of host '203.0.113.25 (203.0.113.25)' can't be established.
ED25519 key fingerprint is SHA256:...
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

Type `yes` and press Enter. This happens once per server and is normal. **[PERSON ONLY]** If you
set a passphrase in step 2, type it when asked.

You should see: a welcome text that starts with `Welcome to Ubuntu 24.04`, and then a
new prompt that ends in `#`, like `root@orchestra:~#`. You are now on the server. Check it
with `whoami`: it prints `root`.

- the `#` at the end means you are **root**;
- every command you type in this window now runs on the server, not on your computer;
- from here on, every command in the docs is the same whatever computer you started on;
- `exit` logs you out and brings back your own computer's prompt;
- a new terminal tab or window is on your own computer again.

**If it goes wrong:**

- `Permission denied (publickey)`: the server does not have your key. In step 3 you
  must tick `my-computer` under **Choose Authentication Method** when creating the server.
  To add it now, without starting over: in DigitalOcean, click your droplet's name, then
  **Web Console** at the top of its page. A terminal on the server opens in your browser.
  There, type `mkdir -p ~/.ssh && echo '`, paste your public key line from step 2, type
  `' >> ~/.ssh/authorized_keys` and press Enter. (`>>` adds the key; it does not remove anything.) Then try `ssh` again
  from your own terminal.
- `Connection timed out` or `Operation timed out`: check the address, and give a brand
  new server another minute to start.
- `Could not resolve hostname`: there is a typo in the address.
- **Windows:** `ssh` is not recognized: see the OpenSSH Client note in step 2.

## 5. Next: install OrchestraOS

You should see, before you go on: the server's prompt, ending in `#`, still open in your
terminal (step 4).

Stay logged in, and continue with [docs/INSTALL.md](INSTALL.md) at **§0, "Run as a normal
user, not root"**. You are root right now, so that step applies to you: it creates your
own account on the server, which is what OrchestraOS runs as.
