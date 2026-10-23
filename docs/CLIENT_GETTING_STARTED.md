# Your first day with OrchestraOS

Setup is done: your server says **Setup complete**, and you approved the welcome card. This page is
what to do next. Like the setup page, it assumes you have never used a terminal.

Your agents live on your server, not on your laptop. They keep working when your laptop is closed
or your phone is in your pocket. You talk to them, and answer their questions, from your dashboard.

## 1. Open your dashboard

On your phone or computer, make sure **Tailscale is on**, then open the address the setup showed you
at the end (it looks like `https://your-server.your-tailnet.ts.net/`). It is the same page where you
approved the welcome card.

**Bookmark it**, or on a phone choose **Share → Add to Home Screen**, so it opens like an app.

If the page does not load, Tailscale is off on that device: turn it on and try again.

## 2. Meet your General Manager

Your first agent is the **General Manager** (`gm`). It is the one you talk to most: it plans the
work, starts other agents when they are needed, and tells you when something needs your decision.

1. Choose **Agents**, find **gm**, and choose **Open chat**.
2. Tell it, in your own words, who you are and what you want done. For example:
   *"I run a small dental clinic. I want help answering patient emails and posting on Instagram
   twice a week."*
3. Let it ask questions. It will propose a plan before it starts anything big.

You can close the page at any time. The agent keeps working, and its replies are there when you
come back.

## 3. When an agent needs you

Agents ask before they do anything you should decide: spending money, sending something in your
name, or choosing between options. Each question is a **card** in **Approvals**, like the welcome
card you answered during setup.

- **Approve** or **Deny** to answer. Deny lets you say why, and the agent reads it.
- A card that offers choices shows them as buttons, plus **Respond** to answer in your own words
  instead (for example "yes, but only on weekdays").
- Anything else, just tell the agent in its chat.

The agent waits for your answer and carries on as soon as you give it.

## 4. Reading the agent list

Agents are grouped into **Needs you**, **Working** and **Idle**; agents that are not running sit
under a collapsed **Not running**. Each one shows its **name** and a **dot**: orange and pulsing =
working; amber with a ring = needs you; green = waiting at its prompt for something to do; red =
stopped or crashed. The number beside it is how much of its working memory (context) it has used.

The line at the top shows how much of your AI plan's limit you have used, for example
`Claude 40% 5 h`. Plans reset on their own (every 5 hours and every week); hover a number to see
when.

## 5. More agents

Ask the General Manager first ("I need someone to handle bookkeeping"): it knows what is running
and will suggest the right one. To add one yourself, choose **New agent** at the top of the page,
give it a name and say what it is for.

Each agent uses your same AI plan, so more agents use up the plan's limit faster. The usage line
shows where you stand.

## 6. Day to day

- **Leave the server running.** That is the point: it works while you do not.
- **Check Approvals** once or twice a day, or whenever you get a notification.
- **You do not need the console again** for normal use. Everything is in the dashboard.

## 7. Keeping it up to date

About once a month, or when someone helping you asks: open your server's console (as in step 2 of
the setup page), and type:

```bash
cd ~/orchestraos && orchestra upgrade
```

It updates OrchestraOS and restarts it. Your agents and their work are kept.

## 8. If something looks wrong

- **An agent shows red:** open it and look at its last message; ask the General Manager what
  happened. If the whole dashboard is unreachable, check that Tailscale is on.
- **Someone is helping you:** in the console, type `cd ~/orchestraos && orchestra doctor --share`
  and send them the block it prints. It holds no passwords, addresses or names, so nobody needs to
  log in to your server.

## 9. Stopping

To stop paying, delete the server (the Droplet) in DigitalOcean. That ends everything on it,
including your agents and their work, so copy anything you want to keep first (the General Manager
can tell you where it saved things).
