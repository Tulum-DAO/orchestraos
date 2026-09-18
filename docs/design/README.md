# Design captures

Headless-Chrome captures at 390×844 (iPhone width) of the shipped web surfaces, taken
against a fresh container (no keys, one authed CLI). Compare against the mockup
notes' common denominators: one thin header, empty state = mark + greeting, one
composer, no legacy chrome, no inject buttons.

- `arturo-home-390x844.png` — left to right: empty state after onboarding
  (serif time-of-day greeting), then the onboarding thread: ask name → runtime
  detect + voice/text decision card → "what should your first agent do?" →
  seat spawned (`ran spawn_agent`) and the thread flips to ordinary chat.
- `arturo-pill-390x844.png` — the "Ask Arturo" pill on `/approvals`, closed and
  expanded (context chip `approvals`, "Open Arturo" jump).

Regenerate: run the dashboard (`npx vite`) against an install, then a puppeteer
script that clears `localStorage`, types through the thread and screenshots each
step — see `docs/ARTURO.md` → Onboarding for the localStorage keys.

## Mockups (approved 2026-09-17)

`mockups/` — the six phone frames the home was built against, approved as-is:
S1 home empty state · S2 typed, adaptive send · S3 onboarding thread + decision
card · S4 select-model sheet · S5 Approvals with the floating pill · S6 pill
expanded with the context chip. `mockups/mockup.html` is the source (plain
HTML/CSS, 390×844 frames); `render.mjs` re-renders the PNGs
(`CHROME_BIN=<chrome> node render.mjs`, needs `puppeteer-core`). Names and ids in
the frames are placeholders.
