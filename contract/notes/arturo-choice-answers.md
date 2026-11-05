# Arturo's choice cards answered on the server

Status: proposed with thread kit step 4e.4, for review by the integrator. The web dashboard builds it; the native apps
(Phase 9) should send and read the same fields.

## What changed

Arturo's choice cards (`ask_choices`) were answered only by the operator's next message: a tap sent the picked labels
as words, and every surface guessed the card's answer from whatever message followed it. Now the turn that answers a
card says so, and Arturo keeps the answer on the card itself, in the same write as the turn. What Arturo reads is
unchanged: the message's words, once.

## Sending: `card_answer` on a text turn

`POST /api/arturo/text` and `/api/arturo/text/stream` take one more optional field, next to `text`, `conversation_id`,
`turn_id`, `brain` and `context`:

```json
{ "text": "Square", "conversation_id": "web_…", "turn_id": "t_…",
  "card_answer": { "card_id": "0a1b2c3d4e5f6a7b", "picks": ["Square"], "words": "" } }
```

- `card_id`: the card's id (8–32 lowercase hex), as the reply that showed it gave it (`choices.card_id`).
- `picks`: the picked option labels, exactly as the card lists them (at most 8; one on a pick-one card).
- `words`: the person's own words with the tap (at most 1000 characters; may be empty when there are picks).
- `text` stays what Arturo reads: the picks joined by ", ", then " — " and the words (or the words alone), the same as
  before. The dashboard attaches `card_answer` only to a TAP on the card; a message typed in the chat box answers no
  card on the server (it is the person's own message, and a closed card must never block it).
- Every attempt of one send carries the same `card_answer` with its `turn_id`. A retry of a send that already landed
  is replayed by its turn id before any card check, so it is never refused as "already answered".

## Refusals (nothing runs, nothing is stored)

| Status | `error` | When |
|---|---|---|
| 400 | `bad_card_answer` (`field`: `card_answer` or `card_answer.picks`) | malformed, or a pick the card does not offer, or several picks on a pick-one card |
| 409 | `card_answered` (+ `answer`: the stored answer) | the card was already answered (another tab, another device) |
| 409 | `card_dismissed` | the card was closed unanswered |
| 409 | `card_unknown` | this conversation holds no such card |

The dashboard shows the card as it is (with the stored answer) and says the message was not sent.

## Reading: the card's stored answer

`GET /api/arturo/threads/:id` already returns each reply's `meta`. A choice card's meta (`kind: "card"`) now also
carries, once answered:

```json
{ "kind": "card", "card_id": "…", "choices": { "options": ["Circle", "Square"], "multi": false, … },
  "answer": { "picks": ["Square"], "words": "a big one", "at": 1792900000.5 } }
```

`at` is epoch seconds. An answered card can no longer be dismissed (the dismiss route answers 404 "no such card"), and
a dismissed card cannot be answered. A card from before this change has no `answer`; clients keep reading its answer
from the message that followed it.

## Where it shows

The card on Arturo's page (live and after a reload), and the drawer's History on his page, which now lists the open
conversation's choice cards (waiting, answered with when, dismissed) with "Go to card".
