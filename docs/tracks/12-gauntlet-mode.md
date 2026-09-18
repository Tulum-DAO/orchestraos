# Track 12 — Gauntlet mode: a critic loop for creative work

Size: M · Labels: `track`, `core`, `skills`, `ui` · web (+ iOS settings later)

## Problem

A builder agent returns its first "done" the moment *it* thinks the work is
finished. For creative output — a 3D scene, a video, a landing page, a
generated image set, a UI — that first pass is usually programmer art: it runs,
so it ships. Nothing in the harness today forces a second opinion before the
result reaches the operator. `scripts/approval.py` can put a card in front of
a human, but there is no built-in *machine* critic between "the builder stopped"
and "the card fires".

The pattern that works (credit: a YouTube commenter, @mavor101, on a frontier-
model demo, 2026-09) is a **gauntlet**: after every completed attempt, a
separate critic agent — a brutal AAA art director who writes no code — takes its
own screenshots from several viewpoints and zoom levels, checks the reference
contracts, console errors and performance budgets, and scores the attempt 0–10
against real references: 10 = indistinguishable, 8.5 = AAA with nits, 7 = good
indie, 5 = programmer art. Pass = ≥ 8.5 with zero errors. Below that the builder
gets the ranked issue list and goes again, up to N rounds. Today anyone who wants
this has to hand-write it into every prompt.

## Design

**Gauntlet mode** makes that loop a first-class harness feature: a skill can
*declare* that it needs a gauntlet, the operator sets how many loops it gets and
what each loop looks like, and the harness runs builder → critic → builder until
the score clears the bar or the loops run out.

### Vocabulary

- **Gauntlet** — the whole run: a builder seat, a critic seat, N loops, one
  verdict.
- **Loop** (round) — one builder attempt followed by one critic **dive**.
- **Dive** — the critic's inspection of one attempt. Each dive has its own
  parameters: which viewpoints/zooms to capture, which references to compare
  against, which checks to run (console errors, perf budget, contract file),
  the rubric, the pass bar, and the model/effort the critic runs on. Later dives
  can be stricter or look at different things than earlier ones (loop 1 checks
  silhouette and proportions; loop 3 checks materials and animation).
- **Gauntlet level** — a preset bundle of loop count + dive parameters: `off`,
  `light` (1 loop, pass ≥ 7), `standard` (3 loops, pass ≥ 8.5), `brutal`
  (5 loops, pass ≥ 9, zero warnings). Levels are just named defaults; the
  operator can override any loop.

### Skill contract

A skill opts in through its front-matter:

```yaml
gauntlet:
  required: true            # the skill will not report done without a gauntlet
  default_level: standard
  critic_prompt: prompts/critic-art-director.md   # persona, writes no code
  capture: screenshots      # screenshots | render | pdf | audio | none
  references: design/references/                   # what "10" looks like
  checks: [console_errors, perf_budget, reference_contract]
```

`required: true` is a hard gate: a builder seat running that skill cannot emit
its completion (msg_store `type=result`, or the approval card) until the
gauntlet writes a verdict. The Stop hook enforces it the same way it enforces
"decisions go on a card".

### Settings window

Skills page (`dashboard/src/pages/Skills.tsx`) gets a **Gauntlet** section per
skill that declares one:

- Level picker (`off / light / standard / brutal / custom`).
- **Loops: N** stepper (1–10).
- One expandable row **per loop**, each with its own dive parameters:
  viewpoints/zooms (or capture spec), references, checks, rubric, pass bar,
  critic model + effort, time box. Loop rows inherit from the level and show
  which fields were overridden.
- "Run gauntlet now" against the skill's last output, and a history of
  verdicts (score per loop, issue list, capture thumbnails).

Persisted as `[gauntlet.<skill>]` in `orchestra.toml` plus a `loops = [...]`
array of tables; the same block is what `orchestra init` seeds from the level
defaults, so the whole thing is editable by hand and versionable.

### Runtime

`scripts/gauntlet.py run --skill <name> --attempt <path-or-url>`:

1. Read the skill's gauntlet block and the operator's loop settings.
2. Spawn (or reuse) a **critic seat** — a normal seat with the critic prompt,
   `worker_kind=pane`, tagged `role=critic`, no write access to the builder's
   files. It captures its own evidence (the harness gives it a headless capture
   helper: Playwright for web, `xcrun simctl io` for iOS, ffmpeg frames for
   video) — it never trusts screenshots the builder hands over.
3. The critic writes `state/gauntlet/<run>/loop-<k>.json`: score, per-check
   results, ranked issue list, capture paths. Nothing else.
4. Pass (score ≥ bar and required checks green) → verdict `PASS`, the builder's
   completion is released. Fail → the ranked issue list is delivered to the
   builder as a msg_store `type=task` row ("gauntlet loop k/N: fix these, in
   this order") and the next loop starts when the builder reports again.
5. Loops exhausted → verdict `FAIL` with the best attempt marked; the operator
   gets a card: accept best / grant more loops / stop.

Every loop is one row in the `Feed`/Activity surfaces so the operator can watch
the score climb. Verdict cards use the existing approval contract
(`api/src/routes/unified-approvals.ts`), no new card type.

## Files you will touch

- `scripts/gauntlet.py` (new) — run/verdict/loop state machine; state under
  `<data>/state/gauntlet/`.
- `prompts/critic-art-director.md` (new) — the default critic persona; skills
  may ship their own.
- `hooks/` — Stop-hook rule: a `gauntlet.required` skill cannot complete
  without a verdict (`hooks/install.py` row, tagged `#orchestraos-hook`).
- `orchestra.example.toml` — `[gauntlet]` levels + per-skill blocks;
  `orchestra_cli/settings.py` tolerant read; `orchestra_cli/init_cmd.py` seeds.
- `api/src/routes/gauntlet.ts` (new) — GET/PUT settings per skill, GET runs,
  POST run; `dashboard/src/pages/Skills.tsx` — the Settings window above.
- `orchestra_cli/doctor` — a `gauntlet:capture` row (headless capture helper
  present for at least one medium).
- `docs/PROMPTS.md` — the critic-persona section; `docs/GATE.md` — gauntlet
  is an optional eighth step for creative tracks.

## Steps

1. Land the config shape + `gauntlet.py` state machine with a fake critic
   (fixture scores) — prove PASS at loop 2 of 3 releases completion and FAIL at
   3/3 fires the operator card, all on a scratch data dir.
2. Real critic seat + web capture helper; run it against a demo creative skill
   (a single-file WebGL/canvas scene is the reference case — no external libs,
   sliders for parameters, freeze-frame + orbit so the critic can look from any
   angle).
3. Skill front-matter parsing + the Stop-hook gate. Prove a `required: true`
   skill cannot report done without a verdict.
4. Skills-page Settings window: level, loops stepper, per-loop dive rows,
   history. Persist to `orchestra.toml`, round-trip through the API.
5. iOS: read-only gauntlet history on the agent page (settings editing stays
   web-only this weekend).

## Acceptance test

On a clean install: a demo creative skill set to `standard` (3 loops). Builder
ships a deliberately weak first attempt → critic scores it ≤ 6 with a ranked
list → builder's second attempt scores ≥ 8.5 → completion released, verdict
`PASS` at loop 2/3 visible in Activity and on the skill's history. Then set the
level to `brutal` with loop 1's dive bar raised to 9.5 → same attempt now fails
loop 1 and the issue list names what changed. Loops exhausted → operator card
with accept-best / more-loops / stop, answerable from the phone.

## Start prompt

```
I'm working Track 12 (Gauntlet mode) for the OrchestraOS hackathon.
Read docs/tracks/12-gauntlet-mode.md for the full design. Start with step 1:
the [gauntlet] config shape, scripts/gauntlet.py's loop state machine, and a
fixture critic — prove PASS-at-loop-2 and FAIL-at-loops-exhausted by effect on
a scratch data dir (ORCHESTRA_DIR set, never the live one) before touching a
real critic seat or the dashboard.
```

## Out of scope

- Human-in-the-loop scoring (the operator can still get a card; the critic is a
  model). Multi-critic panels / consensus scoring — a follow-up once one critic
  works. Non-visual media beyond audio frames. Editing loop settings from the
  watch.
