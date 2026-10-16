# Set up OrchestraOS on your own server

Your agents will run on a server you rent, not on your laptop. They keep working when your laptop
is closed, and you follow them and answer their questions from your phone.

This page is for you if you have never used a terminal. It takes about 30 minutes. One line does
most of the work; you do the few things only you can do: pay, sign in, and tap Approve once on
your dashboard at the end.

If something goes wrong at any point, you lose nothing: run the same line again and it carries on
from where it stopped.

## Before you start

You need:

- **A card** to rent the server: about **$24 a month** at DigitalOcean, billed by the hour. You can
  delete the server at any time to stop paying.
- **A paid plan for one AI tool**: Claude Pro or Max (the default), or ChatGPT Plus or Pro for Codex.
  Your agents use your plan. You sign in to it yourself; no password ever goes into our setup.
- **Your phone or computer.** You will install the free Tailscale app on it, and open your dashboard
  in its browser. (The OrchestraOS phone app is not in the App Store yet; the dashboard does everything
  meanwhile.)

## 1. Rent the server

1. Create an account at [DigitalOcean](https://www.digitalocean.com) and add your card.
2. Choose **Create → Droplets** ("Droplet" is DigitalOcean's word for a server), then:
   - **Region:** the one nearest you.
   - **Image:** **Ubuntu 24.04 (LTS) x64**.
   - **Size:** **Basic, Regular, 4 GB RAM / 2 CPUs** (about $24 a month).
   - **Authentication:** **Password**. Choose a strong one and keep it.
3. Choose **Create Droplet** and wait until it shows an address like `203.0.113.7`.

**Checkpoint:** your Droplet is listed with a green dot.

Other providers work too (Hetzner, Vultr, Linode); pick Ubuntu 24.04 and at least 4 GB.
[docs/COSTS.md](COSTS.md) compares prices.

## 2. Open its console and paste one line

1. Open your Droplet and choose **Console** (top right). A black window opens in your browser.
   You are now typing on the server.
2. Paste this line and press Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Tulum-DAO/orchestraos/main/scripts/install-client.sh -o install-client.sh && bash install-client.sh
   ```

3. It asks you to choose a password for a new user called `orchestra` (your agents run as this
   user, never as the server's administrator). Nothing shows while you type: that is normal. Keep
   this password; the setup asks for it again (`[sudo] password for orchestra:`).

From here the setup tells you what it is doing, one numbered step at a time, for example
`Step 3 of 10 done: Node.js v22.23.3 installed`.

**If the console closes or you come back later:** open the Console again and paste the same line.
It says which steps are already done and continues.

## 3. The steps that need you

The setup stops at each of these. It says why in one sentence and shows exactly what to do. When
you have done it, run `bash install-client.sh` again (or paste the line from step 2 again).

- **Step 5, join your private network.** A link appears. Open it on your phone or computer and
  sign in to Tailscale (with Google, Microsoft, Apple or GitHub). Then install the **Tailscale**
  app on your phone and sign in with the **same** account. This private network is how only your
  devices reach your agents; nothing is put on the public internet.
- **Step 7, sign in to your AI plan.** Type `claude`, follow the link it shows, sign in, then type
  `/exit`. (For Codex: `codex login`.)
- **Step 10, say hello.** The setup shows your dashboard's address (it ends in `.ts.net`). Open it in
  your phone's or computer's browser, with Tailscale on. Go to **Approvals**, find
  **"Welcome to OrchestraOS!"** and tap **Approve**. That one tap proves everything works: your
  device reaches your server over your private network, and your answer reaches it.

## 4. Done

The last screen says **Setup complete** and shows a table of checks (every row OK). Your first agent,
the General Manager, is running: open the dashboard to see it and talk to it.

**Next: [Your first day with OrchestraOS](CLIENT_GETTING_STARTED.md)**: opening your dashboard,
talking to your General Manager, answering its questions, adding agents, and keeping it up to date.

**Later, when you have the OrchestraOS phone app:** run
`ORCH_INSTALL_PAIR_PHONE=1 bash install-client.sh`. A code appears for one minute; in the app,
choose **Pair** and scan it.

## If someone is helping you

At the end, the setup prints a short block that starts with `--- orchestra doctor --share ---`. It
lists the checks and whether each passed, and nothing else: no passwords, no addresses, no names.
Copy it and send it to whoever is helping you. You can print it again any time:

```bash
cd ~/orchestraos && orchestra doctor --share
```

Nobody needs to log in to your server to help you.

## If a step fails

Each failure says what went wrong and the one thing to do, then "run the same line again". The
most common ones:

- **"needs Ubuntu 24.04"**: the server was created with another system. Delete it and create it again
  with Ubuntu 24.04.
- **"could not be downloaded"**: the server lost its internet for a moment. Wait a minute and run
  it again.
- **"less than 4 GB of memory"**: a warning, not a stop. Resize the Droplet to 4 GB when you start
  running agents.

The full manual guide, for anything the setup cannot fix, is [docs/INSTALL.md](INSTALL.md).
