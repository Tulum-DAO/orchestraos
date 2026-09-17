# Track 7 — Tab restyle to the reference set

Size: M · Labels: `track`, `ui` · web + iOS

## Problem

The dashboard's non-Arturo pages (`dashboard/src/pages/Approvals.tsx`,
`Agents.tsx`, `Projects.tsx`, and whichever page is this install's task/pipeline
view — verify the actual route name in `dashboard/src/App.tsx` before starting,
`Tasks.tsx` and `Workflows.tsx` are both candidates) predate the Track 4 visual
language and look like a generic admin dashboard: dense tables, no consistent
header, no card grouping. Every one of them renders real card types (approval,
menu, questionnaire, commitment, voice-call) that other components depend on —
`api/src/routes/unified-approvals.ts` is the shared contract — so this is a
reskin, not a rewrite of what the pages show.

## Design

Rebuild these pages to the same visual language Track 4 establishes: dark canvas,
one thin header (reuse the `TopBar` component or its pattern, not a second
implementation), grouped cards instead of dense tables, native bottom sheets for
detail/expand instead of inline row expansion. Every existing card *contract* is
preserved exactly — the renderer that turns a `unified-approvals` row into a visual
card (approval / menu / questionnaire / commitment / voice-call) is shared with
Arturo's `Feed` component from Track 4 wherever practical, so a card type added
there does not need a second implementation here.

Order of attack: Approvals first (it's the highest-traffic page and the card
renderer work overlaps most with Track 4's `Feed`), then Agents, then
Projects/Pipeline last (lowest card-density, most table-like — biggest design
judgment call on how far to take "grouped cards" for a list that's naturally
tabular).

## Files you will touch

- `dashboard/src/pages/Approvals.tsx`, `Agents.tsx`, `Projects.tsx`, and the
  pipeline/task page (confirm exact filename first).
- `dashboard/src/components/agent/Feed.tsx` (Track 4) — factor the card-type
  renderer out so both Arturo's feed and these tab pages consume the same
  component, if Track 4 hasn't already made it reusable.
- iOS repo — `Sources/iOS/RootView.swift`, a **custom** tab bar (`enum Tab: Int,
  CaseIterable`, 5 items; a native `TabView` can't raise a center button, per the
  code comment there) — a restyle must keep the custom center-raised tab bar, not
  swap in a native one. Tab screens: `ApprovalsView.swift`, `ArturoView.swift`
  (Arturo home, Track 4's surface), `HistoryView.swift`, and the remaining two.
- `orchestra_cli/init_cmd.py` (`seed_demo_registry`, ~line 129-161, and the
  `--demo` card seeding at ~line 253) — this is the fixture harness the acceptance
  test uses (`orchestra init --demo` seeds one of each card kind); confirm it still
  covers every card type after the restyle, add any missing kind.

## Steps

1. `orchestra init --demo` on a scratch data dir; open each of the four pages
   today, screenshot them (desktop and 390px width) as the "before" baseline.
2. Restyle Approvals first; verify every demo-seeded card type still renders with
   its full contract (approve/deny works, menu options work, questionnaire fields
   submit, commitment countdown shows, voice-call transcript link works).
3. Factor the card renderer into a shared component if Track 4's `Feed` doesn't
   already expose one; wire Approvals to it.
4. Repeat for Agents, then Projects/Pipeline.
5. Headless capture each tab at 390px (the reference notes' target width); compare
   side by side against `design/references/` from Track 4.
6. Full regression pass: `orchestra init --demo`, click through every card type on
   every restyled page, confirm none regress (this is the fixture harness — treat
   a missing render as a blocking bug, not a follow-up).

## Acceptance test

Headless 390px captures of Approvals, Agents, Projects, and the pipeline/task page
reviewed against `design/references/`. `orchestra init --demo`'s fixture set (one
of each card kind) renders correctly on every restyled page — no card type
regresses.

## Start prompt

```
I'm working Track 7 (tab restyle) for the OrchestraOS hackathon.
Read docs/tracks/07-tab-restyle.md in this repo for the full design, and
docs/tracks/04-arturo-home-onboarding.md for the visual language and
design/references/ this track matches (Track 4 should land first or in
parallel — coordinate with oss-arturo-dev on the shared card-renderer
component before duplicating it). On iOS, Sources/iOS/RootView.swift is a
custom tab bar (not native TabView) — keep the center-raised button, don't
replace it with a system TabView.
Files to touch: dashboard/src/pages/Approvals.tsx, Agents.tsx, Projects.tsx,
and the pipeline/task page (confirm the real filename in App.tsx first).
Start with `orchestra init --demo` to seed one of every card kind, screenshot
the current pages as a baseline, then restyle Approvals first per the doc's
Steps.
```

## Out of scope

- Changing what data any of these pages show (this is visual, not a feature
  change).
- The Arturo home page itself (Track 4 owns that surface).
- New card types (this track restyles the existing five kinds; a new kind is a
  separate change).
