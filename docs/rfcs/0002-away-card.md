# RFC 0002 — "While you were away": anchor on your last real action

**Status:** proposed, for agreement on the shape before any code
**Author:** orchestraos-builder (gen 33)
**Decision owner:** the operator (answered "Spec it first" on card `apr_8abf5935`), with gm
**Builds:** server half in this repo (orchestraos-builder); iOS card (ios-watch-dev); web card (webos-dev)

---

## 1. The ask

> Rebuild the "While you were away" card around your last real action (which device, what you said,
> what came of it), with a tap-to-expand summary of what moved since.

## 2. Why today's card misses

The card reads `GET /briefing` (`scripts/watch_gateway.py`, `handle_briefing`):

| Line on the card | Where it comes from | What is wrong |
|---|---|---|
| pending / answered counts | `ApprovalStore.pending_to_notify()`, `history(50)` | Repeats the approvals page. Nothing ties it to when you left. |
| "crashed" | every agent in the fleet cache with state `crashed` or `stopped` | Counts long-retired seats and service panes. It is not "what broke while you were gone". |
| "last spoke by voice" | `GET /voice-memory` -> `<data>/state/voice-memory.json` | **Wrong path.** Arturo writes `<data>/state/arturo/voice-memory.json` (`ARTURO_STATE`, `services/arturo/arturo-proxy.py`). The gateway reads the old un-namespaced file, which nothing writes any more. Measured on a live install: gateway file last written 2026-08-23, Arturo's file written today. |
| `since=` | a timestamp the client picks | The server has no idea what "away" means, so every client guesses. |

## 3. What already exists (this is mostly assembly)

| Your action | Recorded today | Device on the record? |
|---|---|---|
| Answered a card | `approval_requests.answered_at`, `answer_surface` (web, phone, watch, ...), `answer_device` (`<device_id>:<label>`) | **Yes** (for device tokens; NULL for the legacy fleet token) |
| A voice call | one journal per call under the voice-calls dir (`services/arturo/call_journal.py`): `started_at`, `ended_at`, `turns[]` (your words with `role: user`), `summary`, `page` (the calling channel), `origin` | **Channel only.** No device id. See §7 Q1. |
| A chat message to an agent | `messages` rows with `from_agent = 'operator'` (`/api/chat-history?view=operator`) | **No.** See §7 Q2. |
| Opened a screen | `device_tokens.last_seen` is touched on **every** authenticated call, background polling included | **Not usable as is.** It cannot tell "looked at the app" from "the app polled". See §5.3. |

## 4. The card

**Collapsed (one line).** It names the anchor and a count, with no number another page already shows:

> Since your call from **Quest** at **2:04 pm**: **7 things moved.**

**Tapped (expanded):**

1. **You asked:** "<your words, trimmed to ~140 chars>", to **<agent>**.
   **Outcome:** done / still working / waiting on you, with a link to the agent or the card.
2. **Everything else** (3 to 6 bullets, newest first, each linking to its page):
   - new cards waiting on you;
   - tasks an agent marked done;
   - real crashes: a seat that is in the registry, not retired, and went `crashed` **after** the anchor;
   - rotations (seat X moved to a new generation);
   - an agent message addressed to you.
3. A one-paragraph **summary**, written once per anchor and cached, so the card opens instantly (§5.4).

**Anchor toggle:** "since my last action" (default) or "since I last opened any screen" (§5.3).

Nothing on the card is a count of a whole table. If nothing moved, it says so: "Nothing moved since your call at 2:04 pm."

## 5. Server: one new endpoint

### 5.1 `GET /away?anchor=action|screen`

Read scope, the same as `/briefing`. Response:

```json
{
  "ok": true,
  "anchor": {
    "id": "a_<sha256 of kind+ref, 12 hex>",
    "kind": "call | answer | message | screen",
    "at": "2026-10-10T14:04:11Z",
    "device": "Quest",
    "surface": "voice | phone | watch | web | unknown",
    "said": "make the away card start from my last action",
    "target": "gm",
    "outcome": {"state": "done | working | waiting_on_you | unknown",
                "link": "/agents/gm", "card_id": null}
  },
  "moved": [
    {"kind": "card | task_done | crash | rotation | message",
     "at": "...", "text": "...", "link": "...", "agent": "..."}
  ],
  "moved_count": 7,
  "summary": {"text": "...", "anchor_id": "a_...", "generated_at": "...", "source": "brain | rules"}
}
```

### 5.2 Picking the anchor (`anchor=action`)

The newest of the following:

- the newest answered card with `answered_by = 'operator'` (`answered_at`, `answer_surface`, `answer_device`);
- the newest voice-call journal with at least one `user` turn (`ended_at` or else `started_at`; `said` = the first substantive user turn);
- the newest `messages` row with `from_agent = 'operator'` (`said` = its body).

No anchor at all (a fresh install): `anchor: null`, and the card falls back to "Welcome back" with the moved list since the install started.

**Outcome rule** (deterministic, no brain):

- a card answered by you: `done` once the requesting agent has resumed (its state left `waiting` after `answered_at`), else `working`;
- a prompt or call to an agent: `waiting_on_you` if that agent has a pending card created after the anchor; else `working` if it is working now; else `done` if it went idle after the anchor; else `unknown`.

### 5.3 "Since I last opened any screen" (`anchor=screen`)

`last_seen` cannot answer this (§3). New, small: **`POST /presence {"foreground": true}`**, sent by a client when its app or tab comes to the foreground. The server stores `foreground_at` per device. `anchor=screen` uses the newest `foreground_at` across your devices. Until a client sends it, `anchor=screen` returns `anchor: null` with `reason: "no_presence_yet"`. It never falls back to `last_seen`.

### 5.4 The summary

- It is written once per `anchor.id` and cached at `<data>/state/away-summary/<anchor_id>.json`. The cache is invalidated only when `moved_count` changes.
- **Brain path:** a short prompt over the `moved` list and the anchor. Same provider seam as the rest of the product, so it is not Claude-only. Target under 80 words.
- **Rules path** (no brain configured, or the call failed): the first three `moved` items joined as a sentence. `source` says which one ran, so a client can show it honestly.
- Cost: at most one brain call per new anchor, so a handful a day.

### 5.5 `/briefing` and `/voice-memory` stay

Shipped apps read it. It keeps its shape and gets two honesty fixes in the same PR:

- "crashed" = registry seats that are not retired and are crashed, not every pane in a stopped state;
- `/voice-memory` reads Arturo's file (`state/arturo/voice-memory.json`), falling back to the old path only when the new one is absent; "last spoke by voice" can then also be checked against the newest call journal.

It is marked deprecated in its docstring, in favour of `/away`.

## 6. Tests (server, written first)

- Anchor selection: each source wins when it is newest; ties break deterministically; the legacy fleet token's answers show `device: null`, not a guess.
- Outcome rule: each of the four states from a fixture, plus `unknown` when the agent is gone.
- `moved` excludes: retired seats, service panes, events at or before the anchor, cards answered by agents (self-authored gates).
- `anchor=screen` with no presence returns `null` and never uses `last_seen`.
- The summary is cached per anchor, regenerated when `moved_count` changes, and falls back to rules with `source: "rules"` when the brain raises.
- `/briefing`: a retired crashed seat is not counted. `/voice-memory` serves Arturo's file when both exist, and the old one only when Arturo's is absent.

## 7. Open questions (for gm and the app owners)

1. **Device on voice calls.** The journal records the channel (`page`), not the device. Is the channel enough ("from Quest", "from the watch")? Or should the app pass its device label when it starts a call, so it can be stamped on the journal? (ios-watch-dev)
2. **Device on chat messages.** Operator messages carry no device. Proposal: stamp `"<device_id>:<label>"` on operator messages sent through the gateway, the same way `answer_device` is stamped on answers. One new column, read-only for everything else.
3. **Presence signal.** Are iOS and web happy to send `POST /presence` on foreground? (ios-watch-dev, webos-dev)
4. **Where the card lives.** The Arturo tab only (today), or also the dashboard home?

## 8. Order of work

1. Agree on this shape (gm, ios-watch-dev, webos-dev).
2. Server PR in this repo, test-first: `/away`, the `/briefing` honesty fixes, `/presence`, and the message device stamp if Q2 is yes.
3. Clients render `/away` (iOS: `ArturoView.swift`; web).
4. Live: orchestra-builder applies the reviewed server diff.
