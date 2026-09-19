# Kickoff — slide outline

Ten slides, plain English. Nothing here assumes a word `docs/BEGINNERS_GUIDE.md`
hasn't already introduced. Presenter notes are the indented text under each
slide's bullets — say those out loud, don't put them on the screen.

## 1. Who we are

- A small team that runs a fleet of AI coding agents every day, for real work —
  not a demo.
- We're opening the whole harness so people who want this to exist can build it
  with us, starting this weekend.

  *Keep this to thirty seconds. The software is the interesting part, not us.*

## 2. What this is

- An open harness for running a team of coding agents: they message each
  other, remember things across restarts, replace themselves before they run
  out of context, and put every real decision in front of a human on their
  phone.
- Two days: today you install it and understand it, tomorrow you build
  something on it.

## 3. The honest state

- This is not a finished product. It runs one operator's fleet today, every
  day — that's the reference install, and it's real.
- Some pieces you'll read about in the tracks are still stubs or in progress —
  every doc says so plainly where that's true. We'd rather you know a gap
  exists than discover it three hours in.

  *Name one or two concrete gaps out loud here (check the latest
  `docs/tracks/README.md` and `docs/HACKATHON_ISSUES.md` for what's current) —
  it builds trust faster than pretending everything's finished.*

## 4. The gate — everyone does this first

- Before anyone picks a track, everyone reaches the same seven-step
  checkpoint: install it, get one agent running, connect it to your phone,
  make two agents talk to each other, answer one decision from your phone,
  rotate an agent without losing anything, and recall a fact after a restart.
- This isn't busywork — by the end you've touched most of the files any track
  will send you to.
- Full steps: `docs/GATE.md`. Copy-paste prompts if you'd rather have your
  agent type the commands: `docs/PROMPTS.md`.

## 5. Target: everyone has an agent running by 13:30

- That's the finish line for slide 4 — the seven-step gate, done.
- If you're stuck, ask — that's what the room is for. Don't sit stuck alone
  past ten minutes.

## 6. The tracks

- Thirteen tracks, each a real gap in the harness with a design already written
  down: device pairing, a zero-key assistant brain (note: "gemini" here means
  Google's Antigravity `agy` CLI, not `@google/gemini-cli` — see `docs/COSTS.md`), on-device voice, the
  assistant's home screen, running without a manager agent, chat bridges as
  plugins, a visual restyle, push notifications without a third-party server,
  installer hardening, docs and tutorials, autonomous rotation proven on
  every agent runtime, a critic loop that scores creative work before it
  reaches you, and a memory page so you can finally see what a seat remembers
  and prune it before its index outgrows its boot prompt.
- Full list with sizes and dependencies: `docs/tracks/README.md`. Each track's
  own doc has the design, the exact files to touch, and a start prompt you can
  hand straight to your agent.
- Smaller and faster: `good-first-issue`s in `docs/HACKATHON_ISSUES.md` — land
  something in an hour instead of a weekend.

## 7. Picking a track

- Solo or pair, your call. Tracks 1, 2, 5, 6, 9, 10, 11, 12 don't depend on
  another track landing first — good solo starts. Tracks 3, 4, 7, 8 build on
  another track's contract — read that track's doc first if you pick one of
  these.
- Nothing stops you from switching tracks partway if you find something more
  interesting.

## 8. How to submit a PR

- Fork the repo, branch, make your change against `main`.
- Sign off your commits (DCO) — `docs/CONTRIBUTING.md` has the exact flag.
- Open the PR. CI runs the tests and a secrets scan; both need to pass.
- Maintainers answer PRs within the hour all weekend — don't wait for a
  perfect diff before opening one; open it early and let review happen in the
  open.

## 9. Where to ask for help

- The room, out loud, any time.
- If you're stuck on the harness itself (not your track's specific feature),
  `orchestra doctor` answers most "is something broken" questions before you
  need to ask a person.

## 10. Dinner

- [Time and location — fill in from the current event page before presenting;
  this slide is a placeholder so it isn't forgotten, not a scheduling
  decision made here.]
- Come whether or not you shipped anything — that's not the point of today.
