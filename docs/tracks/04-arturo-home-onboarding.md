# Track 4 — Arturo home + conversation-first onboarding

Size: L · Labels: `track`, `ui`, `arturo` · web + iOS

> **Status: web half shipped on branch `arturo/oss` (oss-arturo-dev, 2026-09-17);
> iOS half open.** Corrections to the design below, from what landed:
>
> - The home is a **new page** `dashboard/src/pages/ArturoHome.tsx` mounted at `/`
>   *outside* `DashboardLayout` (own shell) — not a restyle of `AgentPage.tsx`,
>   which still exists at `/agent/:id` as the per-agent view with the card feed.
>   The old Overview moved to `/overview`.
> - The reference set lives at `.workspace/agent-page-v2/{references,mockup}` in the
>   operator's tree (the mockup HTML was the build target); `design/references/`
>   in this repo is still to be populated (screenshots + notes) — see Step 1.
> - The ambient pill is `dashboard/src/components/arturo/ArturoPill.tsx`, mounted
>   in `DashboardLayout` (it replaced the legacy `JarvisPanel`, whose
>   `/system/jarvis/message` route does not exist in this repo). The context
>   record type `ArturoContext {route, entityKind, entityId, hint}` and
>   `contextFromLocation()` live in `dashboard/src/lib/arturo.ts`; the record is
>   sent as the first line of the turn (`[Context: route=… entity=…]`).
> - Text turns go through `POST /api/arturo/text` → gateway `/arturo/text` →
>   `:5071/text` (Track 2); the header/chip read the live brain from
>   `GET /api/arturo/health`. There is no `orchestra spawn` dependency — the
>   first-agent step sends a commission and the brain's `spawn_agent` tool runs
>   `spawn-agent.sh` + files a `msg_store` row.
> - Onboarding order on the web today: name → runtime detect → voice-or-text card
>   → first agent → spawn. **Pairing (Track 1) is skipped** until it lands; the
>   iOS thread should insert it after the name step.
> - The glow accent is the Claude-warm `#d97757` from the mockup, not yet keyed to
>   `config/providers.json` per provider (open item).
> - Brain sheet = the existing `BrainModal` (Facts / Commitments; Second Brain
>   placeholder); model sheet = the existing `ModelSelectorSheet` on
>   `/api/runtimes/available`.
>
> Proven: fresh container (no keys, one authed claude) + headless Chrome 390×844
> drove the whole thread to a spawned seat; captures matched the notes' common
> denominators (one header, one composer, no inject buttons). Walkthrough in
> `docs/ARTURO.md`.


## Problem

Arturo's page exists (`dashboard/src/pages/AgentPage.tsx` at `/agent/:id`, defaulting
to `gm`) with the right component skeleton — `TopBar`, `Drawer`, `BrainModal`,
`Feed`, `Composer` — but it reads as an old dashboard with extra buttons bolted on,
not a home screen. There is no first-run flow at all: a fresh install drops a new
operator straight into an empty feed with no explanation of what to do, no pairing
step, no runtime detection, no "spawn your first agent" path. `design/references/`
(mockups this track is supposed to build against) does not exist in the tree yet.

## Design

**Shell.** One thin header: sidebar glyph · "Arturo · `<model>` ⌄" · brain glyph.
Dark canvas, a provider-accent radial glow behind the composer (accent keyed to the
active model's provider — reuse `config/providers.json`'s per-provider metadata,
don't hardcode colors per provider in the component). Empty state: mark + a serif
greeting ("Afternoon, `<name>`" — time-of-day + the operator's configured name, not
a hardcoded string). Two-row composer: text field; second row `+` (upload) · model
chip · mic · an adaptive circle that shows send when there's text and voice
otherwise.

**Model sheet.** Bottom sheet: provider logos (`config/providers.json`
`logo_svg`) → grouped models with one-line descriptors, an effort row, a "more
models" push-through. Driven entirely by the runtime catalog
(`orchestra_cli/runtime_probe.py` / `GET /api/runtimes/available`) — no client-side
provider list to keep in sync.

**Brain sheet.** Facts / Second Brain / Commitments tabs. Facts and Commitments
already have API surfaces (`api/src/routes/*` — grep for `facts` and
`commitments`); wire the sheet to those rather than inventing new endpoints. Second
Brain is out of scope (see below) — the tab can exist as a placeholder.

**Ambient pill.** Available on every other page: iOS tab-bar orb + the existing
VoiceSurface; web a floating pill. Carries a context record `{route, entityKind,
entityId, hint}` so Arturo, when opened from elsewhere, knows what the operator was
looking at. Define this record shape once (a small shared type, `dashboard/src/lib/`
+ the iOS equivalent) and pass it through whatever opens the Arturo sheet/page — do
not let each page invent its own shape.

**Onboarding.** Arturo's *first thread*, not a wizard UI: name → pair (Track 1) →
runtime detect (the same catalog probe as the model sheet) → optional voice upgrade
(Track 3's local engine is the zero-setup default; vendor keys are an upgrade, not
a requirement) → "what should your first agent do?" → spawn. Every step is a
message in the conversation and a response to it, no separate forms screen.

## Files you will touch

- `dashboard/src/pages/AgentPage.tsx` and `dashboard/src/components/agent/*`
  (`TopBar.tsx`, `Drawer.tsx`, `BrainModal.tsx`, `Feed.tsx`, `Composer.tsx`) —
  restyle to the shell above; keep the existing card-rendering contract inside
  `Feed` (approval/menu/questionnaire/commitment cards must keep rendering, see
  Track 7).
- `dashboard/src/stores/agentSettings.ts` — extend for the ambient-pill context
  record and model-sheet selection if not already shaped for it.
- iOS repo — the app's home/tab-bar equivalent; new onboarding thread flow; the
  ambient orb + VoiceSurface wiring to the same context record.
- `design/references/` (new) — the mockup set this track builds against (screenshots
  + short notes); commit what you actually design against, so the acceptance
  reviewer can compare.
- `api/src/routes/runtimes-available.ts` — confirm the model-sheet data shape
  matches what `runtime_probe.py` serves for `orchestra doctor` (Track 2's
  `arturo:brain` row and this sheet must agree on which runtimes are "available").

## Steps

1. Capture or source the reference set into `design/references/` first (mobile
   assistant home screens — what the design intends to match) with one paragraph
   per screen noting the common denominators: no legacy chrome, one composer, no
   "inject" buttons.
2. Rebuild the shell in `AgentPage.tsx`/`TopBar.tsx` against those references;
   verify against a real phone-width capture (see `screenshot-verify` workflow) at
   each milestone, not just desktop devtools.
3. Wire the model sheet to `GET /api/runtimes/available`; confirm it lists exactly
   what `orchestra doctor` reports as authed.
4. Wire the Brain sheet's Facts and Commitments tabs to their existing API routes;
   leave Second Brain as a visible-but-disabled placeholder.
5. Build the ambient pill + context record; confirm opening Arturo from another
   page (e.g. an Agents list row) carries `{route, entityKind, entityId, hint}` into
   the conversation.
6. Build the onboarding thread: name prompt → pairing (Track 1's `/pair/start`
   flow) → runtime detect → voice upgrade offer → first-agent spawn (calls
   `orchestra spawn`, lands in PR: orchestra-builder if not yet merged — confirm
   current status with `orchestra spawn --help` before writing the doc's Start
   prompt for whoever picks this up next).
7. Fresh install end to end: no accounts, no config beyond `orchestra init` →
   reach a spawned first seat through conversation only.

## Acceptance test

A fresh install reaches a spawned first seat through the onboarding conversation
only (no form, no CLI command typed by the operator outside the terminal steps
`orchestra init`/`up` already require). A phone-width capture of the home screen
matches the reference notes' common denominators: no legacy chrome, one composer,
no inject buttons.

## Start prompt

```
I'm working Track 4 (Arturo home + onboarding) for the OrchestraOS
hackathon, owned by seat oss-arturo-dev.
Read docs/tracks/04-arturo-home-onboarding.md in this repo for the full
design. This is the largest track — start by populating design/references/
with the mockup set and a one-paragraph read of the common denominators,
then rebuild dashboard/src/pages/AgentPage.tsx and its components
(TopBar/Drawer/BrainModal/Feed/Composer) against it, verified with real
phone-width screenshots at each milestone (not devtools resize).
The onboarding thread (name -> pair -> runtime detect -> voice upgrade ->
spawn) is the last piece and depends on Track 1 (pairing) and the
`orchestra spawn` command — check whether that command has landed in main
yet before writing code against it.
```

## Out of scope

- Second Brain (the tab exists as a placeholder; the feature is not in the public
  tree — see `docs/ARCHITECTURE.md`'s Memory section).
- The tab restyle for Approvals/Agents/Projects/Pipeline (Track 7 — this track only
  covers Arturo's own home/onboarding surface and the ambient pill contract those
  tabs will consume).
- Multi-model conversations in one thread (one active model per conversation,
  switched via the model sheet, not blended).
