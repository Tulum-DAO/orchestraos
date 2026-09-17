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
