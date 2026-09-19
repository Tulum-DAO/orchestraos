# Deck 4 companion — Every real decision reaches a human

> Companion to slide deck 4 from the Tulum Build-a-thon, 2026-09-19. Everything the slides claim is here with the file that proves it, the effect observed, and the known gaps. Give this page to your agent; it is written to be read by one.


Written for the Tulum Build-a-thon, Sat 2026-09-19. Same rules as deck 1.
as doc 01.

## The one-sentence version
When an agent hits something only a human can decide, it files a card, stops,
and is resumed in its own live session by the answer.

## Mechanism
- Request: `scripts/approval.py request "<question>" --from <seat> --worker-kind
  pane|node [--options approve,deny,hold] [--summary ...]`. Kinds: go/no-go,
  multi-choice menu, and questionnaire (items of kind menu or free_text).
  Stored in state/tasks.db (the approvals.db files are empty, dead).
- Notify: approval_notify.py on a one-minute cron pushes the card to the
  phone and watch (APNs) and to Telegram. The watch gateway on :9091
  serves the decision surface and the fleet feed.
- Answer: approve, deny, hold, a menu option, or free text (`--text`, and
  questionnaire kind free_text). Nothing expires; the operator ruled it.
- Resume: approval_resume.py, one-minute cron. A request is DONE only at
  "resumed", never at "answered". For a pane seat the answer is
  VERIFIED-INJECTED into the seat's live tmux pane; for a menu it is a
  keypress into the source pane. It re-resolves the seat after a rotation so
  the answer lands on the live generation, and a watchdog stamps failed
  attempts.
- Enforcement, two Stop hooks:
  - turn_boundary_self_audit.py: if the seat just asked a two-plus-option
    question or an approval as plain terminal text with no native card, the
    stop is blocked once and the seat is told to convert it to a card. The
    seat converts; the hook never fabricates.
  - blocker_surface_watchdog.py (five-minute cron): finds seats blocked on the
    operator with no card and would-card them.
- Fleet norm: a seat blocked on the operator fires a card BEFORE ending its
  turn. Rotation checkpoints, uncommitted work gating a deploy, spend, and
  client go/no-go are all cards (prompts/infrastructure.md).

## Proof by effect
- Gate step 5, answer an approval card from the phone, passed on the release
  candidate in the unattended run of 2026-09-18 (gm report, 20:03 Tulum).
  [Told by gm; run log not re-read by this seat.]
- 2026-09-18 ~18:2x Tulum: card a card (approve a spawn-path change)
  answered APPROVE from the phone; the seat resumed and acted on it.
- 2026-09-18: gm's pairing refusal, "a QR carrying a long-lived bearer token
  does not ship", was a ruling that reached a seat as a message and is now
  held by that seat without returning to gm. On 2026-09-19 it was widened to
  the typed path after gm read the shipped iOS screen itself.

## Known gaps, measured
- Nothing expires, so the pending queue only grows. 46 cards were pending on
  2026-09-18 and are the operator's demo material by his order; corrections go
  as replies or new cards, never by retiring one.
- Cards written in jargon do not get answered. Rule: plain English, the
  decision first, context expandable.
- The pairing surface as shipped asks a stranger to paste a bearer token
  (GatewaySetupView.swift, read by gm 2026-09-19). Refused for Saturday. The
  fix, a short-lived code exchanged for a token, depends on the handshake PR
  that merges after the flip.
- A card and a chat message about the same decision are two sources of truth.
  The operator ruled: the decision belongs on the card.
- Push reaches the phone only while it is on the tailnet; a phone off the
  tailnet can miss a push silently. [Fleet memory; not re-measured.]
