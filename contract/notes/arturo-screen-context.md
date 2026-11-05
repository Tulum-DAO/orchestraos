# Arturo's screen context: the cards on screen

Status: proposed with wave 2 (items 6 and 7), for review by the integrator. The web dashboard builds it; the native
apps (Phase 9) build the same shape.

## What it is

Arturo knows which cards are on the person's screen, not only which page. Cards show in several places at once
(the Approvals page, the drawer's History, a chat's inline cards, a card opened from a reference chip, later the
apps), so every card renderer reports into ONE context source on the client (web: `dashboard/src/lib/screenCards.ts`,
fed by `lib/threadKit/useScreenCard.ts` from the shared question card, the approval card and the History rows). The
client sends a small summary of it with each text turn, and with each context frame during a call.

## The shape: `context.cards`

It rides in the text turn's existing `context` object (`POST /api/arturo/text` and `/api/arturo/text/stream`), next
to `route`, `entityKind`, `entityId` and `hint`:

```json
{
  "route": "/approvals",
  "entityKind": "approvals",
  "cards": {
    "v": 1,
    "focus": "c1",
    "cards": [
      {
        "ref": "c1",
        "kind": "multipart",
        "from": "pm-demo",
        "state": "waiting",
        "surface": "popover",
        "question": "Which size?",
        "summary": "Two layouts are ready.",
        "part": {
          "n": 2, "of": 3,
          "question": "Which colour?",
          "select": "single",
          "options": ["A. Blue", "B. Green"],
          "picked": ["A. Blue"],
          "typed": "a darker one"
        }
      },
      { "ref": "c2", "line": "Approval from dev-demo, waiting: Deploy the drawer?" }
    ]
  }
}
```

- `v`: 1.
- `focus`: the ref of the card in focus, or `null` when no card is clearly in focus.
- `cards`: at most 12 (the web sends at most 8): the cards in view, the focused one first, then the newest in view.
  - The focused card is sent in full. Every other card is one line (`{ref, line}`).
  - `ref`: `c1`…`c99`, per turn. Only tools use it. **No card id is ever sent**: the client keeps which card each ref
    stands for until the reply comes back.
  - `kind`: `decision` (a question card an agent posted), `approval` (Approve / Deny), `multipart` (several parts on
    one card), `questionnaire`, `menu` (an agent's live terminal menu), `permission` (a permission prompt).
  - `from`: the asking agent's id. `state`: `waiting`, `answered`, `dismissed`, `expired`, `withdrawn`, `replaced`.
  - `surface`: `popover` (opened from a reference chip), `approvals`, `history` (the drawer), `chat`, `inbox`,
    `questionnaires`.
  - `question`, `summary` (the card's text, plain), `options` (a card that is not a question card: an approval offers
    `["A. Approve", "B. Deny (needs a reason)"]`), `answer` (an answered card's answer in one line).
  - `part`: a question card's part ON SHOW: `n` of `of`, its question, `select` (`single` or `multi`), its options
    lettered as drawn, what is `picked` so far on that part, and what is `typed`.
- Sizes: strings are clipped (question and part question 300, summary 400, answer and typed 300, each option 100,
  a line 200, kind/state/surface 30, from 60), lists capped at 12. Unknown fields are dropped. A `cards` value that is
  not an object with a `cards` list is refused: `400 {"error": "bad_context", "field": "context.cards"}`. The API
  (`api/src/routes/arturo.ts` `pickCards`) and Arturo (`services/arturo/screen_cards.py` `pick_cards`) apply the same
  rules; the shared fixture is `services/arturo/fixtures/screen_cards_cases.json`.
- The cards never go into the stored message: Arturo reads them in that turn's context only (the `[Context: …]` line
  stored with the message is unchanged).

## Focus and visibility (the client's rules)

- In view: at least a quarter of the card (or 160px of it) inside the viewport, re-read on scroll, resize, part paging,
  navigation and a popover opening or closing. A card drawn twice at once (the drawer and the chat) counts once: the
  popover copy, else the fuller copy, else the one touched or seen last.
- Focus, in order: an open popover (the newest); else the card touched last (a press, a key, focus inside it) that is
  still in view; else the only card in view. Several in view and none touched: `focus: null`.
- Kept current mid-call: each change sends a call context frame (below).

## How Arturo uses it

- One card in focus (or the only one): "what is this?", "what should I pick?", "answer B" are about it and its CURRENT
  part.
- Several and none in focus: infer from the conversation; if still unclear, ASK, naming the cards by their questions
  ("The 'Which size?' card or the 'Deploy the drawer?' card?").
- Cards are described in words (question, who asked, state, the options or the answer), never by id or ref. The same
  words helper exists for text-only surfaces: `describeCard` in `dashboard/src/lib/cardRefs.ts` (calls, Telegram,
  watch notifications).
- His context card names the focused card by its question ("Approvals · Which size?"), else "N cards on screen", else
  the page. A page focused on a card id shows only the page, never the id.

## Answering a card for the person: `answer_card`

Arturo has a tool, `answer_screen_card {card: ref, picks?: [letter or label], words?: string}`, offered on dashboard
(`fleet`) turns only and only for a ref in that turn's cards. It answers nothing on the server: the reply carries

```json
{ "answer_card": { "card": "c1", "picks": ["B"], "words": "a darker one" } }
```

(in the `/text` JSON and in the stream's `turn.end`), and the client hands it to the card on screen, which applies it
exactly as the same taps and typing would (on its current part; a multi-part card moves to its next part; the last
part sends) and sends it through the card's own route with the card's own send states. An approval takes `Approve`
or `Deny` (a deny's reason in `words`). If the card has left the screen, nothing is sent and the client says so.

## During a call

The call socket's context frame gains the same object, additively:
`{"event": "context", "route": …, "focusedEntity": …, "cards": {v, focus, cards}}`. Receivers that do not read it
ignore it. Open item: the gateway's `_record_web_call_surface` keeps only `route` and `focusedEntity`, so the voice
brain does not see `cards` yet; carrying it into `active-surface.json` and the screen readout is a gateway change.

## Card references in messages (item 6)

A card id (`apr_<8 hex>_<digits>`, `qnr_<8 hex>_<digits>`) in any text a person sees is drawn as a chip, never as the
id. The chip reads one card in any state from `GET /api/approvals/unified/:id/card` (new, read-only, scoped like the
other `/:id` routes): `{card: {…the decisions feed's row…, state, delivery, terminal, batch}}`, 404 when unknown; a
questionnaire from `GET /api/questionnaires/:id`. The apps should do the same: draw the reference, open the card in
place, never print the id.
