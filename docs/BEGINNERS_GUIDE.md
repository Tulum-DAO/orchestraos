# Beginner's guide

This is for you if you have never opened a terminal and are not sure whether the
"command line" is a website, an app, or something else. It is not. By the end of
this page you will have typed real commands, talked to a real AI agent, and saved
something it remembers later. Nothing here requires you to already know what any
of these words mean.

## What a terminal actually is

A terminal is a window where you type text commands instead of clicking buttons.
It is not connected to the internet by default and it is not a website — it is a
program on your own computer (or on a small rented computer, more on that below)
that runs whatever you type. Every "command" is just a short line of text, Enter
runs it, and the terminal prints back what happened.

- **On a Mac:** press `Cmd+Space`, type `Terminal`, press Enter.
- **On Windows:** press the Windows key, type `PowerShell` or `Windows Terminal`,
  press Enter.
- **On Linux:** you likely already know where it is; if not, look for
  "Terminal" in your applications menu.

A blank terminal shows a short line ending in `$` or `%` — that is waiting for
you to type. Try it now: type `date` and press Enter. It should print today's
date. That is the whole loop: type, Enter, read the result.

## Install one agent CLI

An "agent CLI" is a program that lets you talk to an AI assistant from the
terminal instead of a chat website, and — the part that matters here — lets that
assistant actually run commands on your behalf instead of just describing what
you should type. You only need one. Pick whichever company you already have an
account with, or Claude if you have none:

- **Claude Code:** `npm install -g @anthropic-ai/claude-code` (needs Node.js
  installed first — if `npm` prints "command not found", install Node.js from
  nodejs.org, then retry).
- **Gemini CLI:** follow Google's install instructions for `gemini`/`agy` — it
  has a free tier with no credit card, see `docs/COSTS.md`.
- **Codex (OpenAI):** `npm install -g @openai/codex`, or see OpenAI's install
  docs — bundled with a ChatGPT Plus subscription.

## Log in

Run the CLI's name by itself (`claude`, or `gemini`, or `codex login`). It opens
a login page in your browser, you sign in with the account for whichever
company you picked, and the terminal shows you are logged in. You do this once;
after that the CLI remembers you.

## Run one command

Type `claude` (or your CLI's name) and press Enter. You are now inside the agent
— it is waiting for you to type a request in plain English, not a command. Type:

```
what files are in this folder?
```

It will look, then tell you. That is the whole shape of everything else in this
guide: you type a plain-English request, the agent does real work (reads files,
runs commands, writes things) and tells you what it did.

## Talk to one agent

This repo's harness runs agents that stay alive continuously — not just for one
question, but as a standing presence you message like a coworker. Follow
`docs/INSTALL.md` up through spawning one seat (§§1-3): it walks the exact
commands to get the harness running and one always-on agent live in its own
terminal window. If a command's output does not match what the doc says it
should, stop and read the error rather than guessing — every command in that
doc prints something specific so you know it worked.

## Save one command

Once your agent is running, you can teach it something and have it remember —
not just for this conversation, but the next time it starts. Tell it, in plain
English inside its terminal:

```
Remember that my favorite color is blue. Write it to your memory files.
```

Later (even after restarting it), ask "what's my favorite color?" — it should
answer correctly by reading back what it wrote, not by guessing. This is the
same mechanism the full harness uses to survive `docs/ARCHITECTURE.md`'s
rotations — an agent replacing itself without forgetting anything. The exact
files and paths this writes to are in `docs/MEMORY.md`, once it lands (it's
not the same file as the handoff document a retiring generation writes — that
one carries where it stopped, not what it remembers).

## The seven-step gate

Everyone doing anything at the hackathon — picking a track, adding a feature —
completes these seven steps first. They are cumulative: each one builds on the
last, and together they touch most of the files any track's doc will send you
to, so you will recognize them when you get there. `docs/GATE.md` has the full
version of each step below — the exact command, what it should print, and
where to look if it doesn't.

1. **Install, doctor green, dashboard open.** `docs/INSTALL.md` §§0-2:
   `orchestra init`, `orchestra doctor` (every row OK), `orchestra up`, open the
   dashboard in a browser.
2. **Always-on agent spawned, answers questions in terminal.** `docs/INSTALL.md`
   §3: spawn one seat, confirm it is alive, ask it something in its own tmux
   pane and get a real answer.
3. **Telegram bot connected, agent answers from phone.** See `docs/PROMPTS.md`'s
   "Connect Telegram" prompt — set up the bot token as an environment variable
   (never paste it into a file or a chat), send yourself a message from your
   phone, get a reply.
4. **Two seats exchange a message, both visible in Inbox.** `docs/PROMPTS.md`'s
   "Two-seat message" prompt: spawn a second seat, send one message between
   them, see it land in both the dashboard's Inbox and the command line.
5. **One approval card answered from Telegram or dashboard.**
   `docs/INSTALL.md` §4 / `docs/PROMPTS.md`'s "Answer a card" prompt: fire a
   card, answer it from your phone or the dashboard, watch the decision land
   back in the agent's terminal.
6. **Manual rotation of the always-on agent completed, nothing lost.**
   `docs/PROMPTS.md`'s "Rotate" prompt: trigger one rotation by hand, read the
   handoff document the old version wrote and the new version's proof it
   understood it.
7. **One fact written, restart, agent recalls it.** The "Save one command"
   section above, but through a restart or rotation rather than the same
   conversation — confirm the memory survives.

Once you have done all seven, you understand the whole loop well enough to pick
a track. See `docs/tracks/README.md` for the list, or `docs/HACKATHON_ISSUES.md`
for something smaller (`good-first-issue`) if you would rather land something in
an hour than a weekend.

## If something breaks

`orchestra doctor` is always the first thing to run — it checks every piece the
harness depends on and tells you, in one line each, what is missing and how to
fix it. Read its output before asking anyone; it is built to answer the
question you are about to ask.

## If you're still stuck

Three places, in order: the in-app **Report** button (dashboard top bar) files
a structured report of exactly what broke, so whoever helps you starts from
real state instead of a description; the GitHub Discussions "Start here" post
pins the same path you're reading now for anyone else who lands there with the
same question; and — if you're at the event in person — the room, out loud,
any time. Nobody expects you to debug this alone.
