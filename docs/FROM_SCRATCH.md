# From scratch: from your Mac to your own server

This page is for you if you have never rented a server, made an ssh key, or typed
commands into a server. You need a Mac, a credit card and an email address, and you
need to be able to copy and paste. Nothing else is assumed.

By the end of this page you will have your own server on the internet and you will
be logged in to it from your Mac. Then [docs/INSTALL.md](INSTALL.md) takes over and installs
OrchestraOS on it. This page takes about 20 minutes.

**What it costs.** The server is about $24 a month, charged by the hour while it
exists; delete it and the charges stop (step 3 says how). To run agents you also need
a paid plan for one AI command-line tool; Claude Pro is about $20 a month.
[docs/COSTS.md](COSTS.md) has the details and the cheaper options.

## Words you will meet

- **Terminal**: a Mac app where you type commands instead of clicking. Each command
  is one line of text; you press Enter to run it, and the Terminal prints the result.
- **Server** or **VPS** (virtual private server): a computer you rent in a data
  centre. It stays on all the time, so your agents keep working when your Mac is asleep.
- **IP address**: the server's address on the internet, four numbers with dots, like
  `203.0.113.25`. (That one is an example; yours will be different.)
- **ssh**: how you log in to the server from your Mac's Terminal. Once logged in, the
  commands you type run on the server, not on your Mac.
- **ssh key**: a pair of files on your Mac that let you log in without a password.
  The **public** half (ends in `.pub`) is safe to give to the server company. The
  **private** half never leaves your Mac and is never shared with anyone.
- **root**: the all-powerful administrator account on the server.

## How to read the boxes on this page

Each grey box holds commands. Copy **one line at a time**, paste it into Terminal
(`Cmd+V`), and press Enter. Text after a `#` is a note for you; the computer ignores
it, so it is fine to paste it too. Under each box, **"You should see"** tells you what
success looks like. If you see something else, stop and check the "If it goes wrong"
notes before you continue.

## 1. Open Terminal

Press `Cmd+Space`, type `Terminal`, and press Enter. A window opens with a line that
ends in `%`, for example `yourname@Your-MacBook ~ %`. That line is the **prompt**: the
Terminal is waiting for you to type.

Try your first command:

```bash
whoami
```

You should see: your Mac username, on one line, and then the prompt again.

## 2. Make an ssh key on your Mac

First check whether you already have one:

```bash
ls ~/.ssh/id_ed25519.pub
```

- If you see `/Users/yourname/.ssh/id_ed25519.pub`, you already have a key. Skip to
  "Copy the public half" below.
- If you see `No such file or directory`, make one:

```bash
ssh-keygen -t ed25519 -C "my-mac"
```

It asks three questions. Answer them like this:

1. `Enter file in which to save the key`: press Enter (keep the suggested place).
2. `Enter passphrase`: press Enter for no passphrase, or type one you will remember.
   Nothing appears on screen while you type it; that is normal.
3. `Enter same passphrase again`: the same again.

You should see: `Your identification has been saved in /Users/yourname/.ssh/id_ed25519`,
then `Your public key has been saved in /Users/yourname/.ssh/id_ed25519.pub`, then a
small box of random characters. That is your key pair.

**Copy the public half** to your clipboard:

```bash
pbcopy < ~/.ssh/id_ed25519.pub
```

You should see: nothing, just the prompt again. The key is now on your clipboard.
If you paste it somewhere (a note, an email draft) it is one long line that starts
with `ssh-ed25519 AAAA` and ends with `my-mac`.

## 3. Rent a server

We recommend **DigitalOcean** for a first server: sign-up needs only a card and an
email, and its screens are simple. The install itself works on any Ubuntu 24.04
server; other providers are at the end of this step.

1. **Sign up** at digitalocean.com and add your card. Confirm your email if asked.
2. **Give it your public key.** Go to **Settings**, then the **Security** tab, and
   click **Add SSH Key**. Paste (`Cmd+V`) into **Public Key**. Type `my-mac` as the
   **Key Name**. Click **Add SSH Key**.
3. **Create the server.** Click the green **Create** button at the top, then
   **Droplets** ("Droplet" is DigitalOcean's word for a server). Fill in the page from
   top to bottom:
   - **Choose Region**: the one nearest to you.
   - **Choose an image**: the **OS** tab, **Ubuntu**, version **24.04 (LTS) x64**.
   - **Choose Size**: **Shared CPU**, **Basic**, then the plan with **4 GB** memory and
     **2 CPUs** (about $24/month). Smaller plans are not enough for agents.
   - **Choose Authentication Method**: **SSH key**, and tick `my-mac`.
   - **Hostname**: `orchestra` (lowercase letters only, no spaces).
   - Click **Create Droplet**.
4. **Find its address.** After about a minute the server appears in your
   **Droplets** list. Its row shows the **IP address**: four numbers with dots, like
   `203.0.113.25`. Copy it; you need it in the next step. Wherever the docs say
   "your server address", this is what they mean (until Tailscale gives the server a
   second, private address in [docs/INSTALL.md](INSTALL.md)).

**To stop paying later:** open the droplet and choose **Destroy**. Turning it off is
not enough; a droplet is charged while it exists.

**Other providers.** Any company that rents Ubuntu 24.04 servers and lets you add an
ssh key works: Hetzner (cheaper; may ask for identity verification), Vultr, Linode,
and others. Pick Ubuntu 24.04, at least 2 CPUs and 4 GB of memory, and choose your
`my-mac` key when you create the server. [docs/COSTS.md](COSTS.md) compares prices.

## 4. Log in to your server for the first time

In Terminal, type `ssh root@` followed by your server's address:

```bash
ssh root@203.0.113.25        # use YOUR address from step 3
```

The first time, it asks you to confirm that this is a server you mean to talk to:

```
The authenticity of host '203.0.113.25 (203.0.113.25)' can't be established.
ED25519 key fingerprint is SHA256:...
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

Type `yes` and press Enter. This happens once per server and is normal. If you set a
passphrase in step 2, type it when asked.

You should see: a welcome text that starts with `Welcome to Ubuntu 24.04`, and then a
new prompt that ends in `#`, like `root@orchestra:~#`. You are now on the server:

- the `#` at the end means you are **root**;
- every command you type in this window now runs on the server, not on your Mac;
- `exit` logs you out and brings back your Mac's `%` prompt;
- `Cmd+T` opens a new Terminal tab that is on your Mac again.

**If it goes wrong:**

- `Permission denied (publickey)`: the server does not have your key. In step 3 you
  must tick `my-mac` under **Choose Authentication Method** when creating the server.
  The simplest fix is to destroy that droplet and create a new one with the key ticked.
- `Connection timed out` or `Operation timed out`: check the address, and give a brand
  new server another minute to start.
- `Could not resolve hostname`: there is a typo in the address.

## 5. Next: install OrchestraOS

Stay logged in, and continue with [docs/INSTALL.md](INSTALL.md) at **§0, "Run as a normal user,
not root"**. You are root right now, so that step applies to you: it creates your own
account on the server, which is what OrchestraOS runs as.
