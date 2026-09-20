# gstack Skills → Sprint Stages & Agent-vs-Skill Recommendation

Read all 54 `SKILL.md` files under `~/.claude/skills/gstack/`. `gstack` itself is the
router (dispatches to the others) and sits outside the Think→Plan→Build→Review→Test→Ship→Reflect
loop. A chunk of the rest are session/environment infrastructure (setup, safety hooks, browser
plumbing) rather than sprint work — labeled **Infra/Meta** instead of force-fit into a stage.

## 1. Full skill table

| Skill | Purpose | Stage(s) | Recommendation |
|---|---|---|---|
| gstack | Router — dispatches any gstack request to the right skill | Infra/Meta (router) | SKILL |
| office-hours | YC-style startup validation / builder-mode brainstorming, saves a design doc | Think | SKILL |
| spec | Turns vague intent into a precise spec, files an issue, can spawn an agent in a fresh worktree | Think/Plan | AGENT |
| diagram | English/mermaid → diagram triplet (source, .excalidraw, SVG/PNG) | Think/Plan | SKILL |
| design-consultation | Proposes a full design system (DESIGN.md) for a new product | Plan | SKILL |
| plan-ceo-review | Founder-mode scope/ambition review of a plan | Plan | SKILL |
| plan-design-review | Designer's-eye review of a plan pre-implementation | Plan | SKILL |
| plan-devex-review | Interactive DX review of a plan (personas, competitive benchmark) | Plan | SKILL |
| plan-eng-review | Eng-manager review locking in architecture/execution plan | Plan | SKILL |
| autoplan | Runs CEO+design+eng+DX plan reviews sequentially with auto-decisions | Plan | AGENT |
| plan-tune | Self-tuning question sensitivity + developer psychographic, observational | Reflect | SKILL |
| design-html | Turns approved mockup/plan into production HTML/CSS | Build | SKILL |
| design-shotgun | Spawns parallel agents to generate design variants, comparison board, iterates on feedback | Build | AGENT |
| document-generate | Generates missing docs (Diataxis) for a feature/module/project | Build | SKILL |
| investigate | 4-phase systematic root-cause debugging | Build | SKILL |
| ios-sync | Regenerates the iOS debug-bridge instrumentation against upstream templates | Build/Infra | SKILL |
| ios-clean | Removes the iOS DebugBridge/#if DEBUG wiring | Build/Infra | SKILL |
| ios-fix | Closes bug→fix→rebuild→redeploy→verify loop on a real device, zero human intervention | Build/Test | AGENT |
| skillify | Codifies a successful /scrape flow into a permanent, fast browser-skill | Build | SKILL |
| codex | OpenAI Codex CLI wrapper: independent code review, adversarial challenge, or consult | Build/Review | SKILL |
| review | Pre-landing PR/diff review (SQL safety, trust boundaries, side effects) | Review | SKILL |
| qa | Iterative test→fix→verify loop on a live app, atomic commits, 3 tiers | Test | AGENT |
| qa-only | Same testing pass as /qa but report-only, never fixes | Test | SKILL |
| design-review | Live visual QA: finds issues, fixes them in source, commits atomically, re-verifies with before/after screenshots | Test | AGENT |
| ios-qa | Vision-driven agent loop on a real iPhone via daemon/StateServer; can expose over Tailscale to remote agents | Test | AGENT |
| ios-design-review | Visual/HIG design audit on real iOS hardware | Test | SKILL |
| devex-review | Live browser-driven DX test: navigates docs, times onboarding, screenshots errors, scorecard | Test | SKILL |
| benchmark | Performance baselines, before/after comparison, trend tracking | Test | SKILL |
| benchmark-models | Cross-model (Claude/GPT/Gemini) latency/cost/quality comparison for a skill | Infra/Meta | SKILL |
| health | Wraps linter/typechecker/tests/dead-code into a weighted 0-10 quality score with trends | Test | SKILL |
| cso | Security audit: static findings daily; qualified profiles add reproduction/repair; resumable, replayable, own persistent run state, watchdog cleanup | Test | AGENT |
| ship | Merge base, tests, diff review, VERSION bump, CHANGELOG, commit, push, PR | Ship | SKILL |
| land-and-deploy | Merges PR, waits on CI/deploy, verifies prod health via canary | Ship | AGENT |
| landing-report | Read-only dashboard of claimed VERSION slots / open PR queue | Ship | SKILL |
| canary | Post-deploy background monitoring: periodic screenshots, error/perf regression alerts vs baseline | Ship | AGENT |
| setup-deploy | Detects deploy platform, writes config to CLAUDE.md for /land-and-deploy | Ship/Infra | SKILL |
| document-release | Post-ship doc sync: README/ARCHITECTURE/CHANGELOG update, coverage gaps, diagram drift | Ship/Reflect | SKILL |
| retro | Weekly retrospective: commit history, per-person breakdown, persistent trend history | Reflect | SKILL |
| learn | Review/search/prune/export cross-session learnings | Reflect | SKILL |
| context-save | Captures git state, decisions, remaining work for a future session | Infra/Meta | SKILL |
| context-restore | Loads the most recent /context-save snapshot | Infra/Meta | SKILL |
| gstack-upgrade | Detects install type, upgrades gstack, shows changelog | Infra/Meta | SKILL |
| setup-gbrain | Installs/initializes gbrain CLI + local brain, registers MCP | Infra/Meta | SKILL |
| sync-gbrain | Re-indexes repo into gbrain, refreshes CLAUDE.md search guidance | Infra/Meta | SKILL |
| careful | PreToolUse hook warning before destructive Bash commands | Infra/Meta | SKILL |
| freeze | PreToolUse hook blocking Edit/Write outside a directory | Infra/Meta | SKILL |
| guard | careful + freeze combined | Infra/Meta | SKILL |
| unfreeze | Clears the /freeze boundary | Infra/Meta | SKILL |
| browse | Drives a real browser (Aside): open, click, screenshot, console-check | Test/Infra | SKILL |
| scrape | Read-only page-data extraction via signed-in browser session | Infra/Meta | SKILL |
| setup-browser-cookies | Imports real Chromium cookies into the headless browse session | Infra/Meta | SKILL |
| open-gstack-browser | Launches visible AI-controlled Chromium with sidebar extension | Infra/Meta | SKILL |
| pair-agent | Generates a pairing key so a remote agent can share your browser | Infra/Meta | SKILL |
| make-pdf | Markdown → publication-quality PDF | Infra/Meta | SKILL |

## 2. Recommended agents (10)

Genuinely autonomous, multi-step, non-trivial-duration work; maintain their own run state
across steps (often literally on disk with resume/replay semantics); or actively
spawn/coordinate other agents — not just format a prompt and return.

1. **cso** — Already architecturally its own system: persistent run IDs, `resume`/`replay`/`recheck`,
   a background watchdog for cleanup, and up to 3 harness-repair attempts per finding within a
   bounded budget. The strongest single case in the whole suite — barely a "skill" today, more a
   thin shim over an actual audit agent/binary.
2. **ios-qa** — Runs a live screenshot→analyze→decide→act→verify loop against a real device over a
   USB/CoreDevice tunnel, backed by a daemon (StateServer), and can be exposed remotely over
   Tailscale for other agents to drive. Textbook "benefits from running independently."
3. **ios-fix** — Chains off ios-qa's findings into a fully autonomous find→fix→rebuild→redeploy→verify
   loop with zero human intervention and its own regression-fixture capture.
4. **qa** — The canonical "long QA run + autonomous fix loop": iterative test/fix/commit/re-verify
   across three depth tiers, producing before/after health scores.
5. **design-review** — The visual-design analog of /qa: finds issues, fixes source, commits
   atomically, re-screenshots to verify — same iterative-loop shape, same duration profile.
6. **canary** — Explicit background post-deploy monitoring over time (periodic screenshots, anomaly
   detection against a baseline) — the "background monitoring" example from the criteria almost
   verbatim.
7. **land-and-deploy** — Waits on CI and deploy completion (open-ended duration), then hands off to
   canary for verification; a natural background/async job rather than an in-session prompt.
8. **spec** — Can spawn a Claude Code agent in a fresh worktree to actually execute the spec'd issue,
   with /ship later closing the loop — direct agent coordination/dispatch, not just a template.
9. **autoplan** — Orchestrates four full review workflows (CEO, design, eng, DX) sequentially with
   its own auto-decision policy and a final approval gate — coordinates other skills' entire
   workflows, not a single prompt.
10. **design-shotgun** — Spawns multiple parallel generation agents for design variants, opens a
    comparison board, and iterates on structured feedback — same "coordinates other agents" shape
    as design-review's cousins.

**Next tier if rounding up to 12:** retro (persistent cross-session trend history, but invoked
point-in-time and exits with one report), devex-review, benchmark, health (all do real multi-step
live testing, but bounded to a single scorecard/output rather than an open-ended loop).

## 3. Stays as skills, by stage

- **Think:** office-hours, diagram (also Plan)
- **Plan:** design-consultation, plan-ceo-review, plan-design-review, plan-devex-review, plan-eng-review
- **Build:** design-html, document-generate, investigate, ios-sync, ios-clean, skillify, codex (also Review)
- **Review:** review
- **Test:** qa-only, ios-design-review, devex-review, benchmark, health, browse (also Infra)
- **Ship:** ship, landing-report, setup-deploy
- **Reflect:** plan-tune, document-release (also Ship), retro, learn
- **Infra/Meta (cross-cutting, not a sprint stage):** context-save, context-restore, gstack-upgrade,
  setup-gbrain, sync-gbrain, careful, freeze, guard, unfreeze, scrape, setup-browser-cookies,
  open-gstack-browser, pair-agent, make-pdf, benchmark-models, gstack (router)

**Judgment calls, not facts from the files:** `document-release` straddles Ship and Reflect,
`codex` straddles Build and Review, and the whole Infra/Meta bucket doesn't cleanly belong to
Think→Ship→Reflect at all (they're always-available session tooling).
