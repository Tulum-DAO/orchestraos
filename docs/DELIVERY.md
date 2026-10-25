# Message delivery: two paths into an agent's terminal

> Some gates described here land in separate pull requests that port them to main; until they merge, the router on main applies the subset it has.

## Why

OrchestraOS puts text into a running agent's terminal in two ways, and they deliberately behave differently.

- **Agent-to-agent mail is queued and gated.** Nobody is watching when one agent mails another. A message typed at the wrong moment can land on top of a person's half-typed sentence, answer a permission prompt by accident, or be pasted twice. So mail waits in the store until the router can prove the target's input box is empty and the agent is between turns.
- **A person's own chat goes straight in.** When the operator types to an agent from the dashboard, phone or desktop app, they are the one present, and waiting would only add lag. Chat-mode sends skip the store and land in the pane immediately. They keep only the checks that protect the terminal itself.

This document writes that model down, and the router, message store and gateway code point here, so future changes don't blur the two paths. For example, it stops someone from adding router-style waits to chat, or letting mail skip the gates.

## Path 1: agent mail (queued, gated)

1. **Send.** `MessageStore.send()` (`msg_store.py`) checks the sender and recipient, then writes one `pending` row with up to 5 retries and commits.
2. **Kick.** Only after the commit, `kick_router_after_send()` starts one router pass in the background, under the same non-blocking `flock` the 2-minute cron uses. It is best-effort: if a router is already running, the kick is dropped and cron is the next look. It is skipped under pytest, with `MSG_STORE_NO_KICK`, and for any store that isn't the installed data dir, so a test or scratch database can never type into real panes.
3. **Route.** `main()` in `scripts/message-router.py` loads pending rows, urgent first. `resolve_delivery_target()` maps each addressee to its live tmux session. The router makes at most one injection per session per pass, with a 60-second cooldown per session.
4. **Gates, in order. Any one of them holds the row as pending.**
   - **Stop-hook grace:** `hook_surfaced_defer()` waits 30 s if the agent's Stop hook already showed the row (`surfaced_by` and `hook_surfaced_at`), unless it is critical, so the agent doesn't see it twice. After the grace, `hook_surfaced_ready()` lets it skip the breathe window, so an agent that keeps ending turns can't hold mail forever.
   - **Presence hold:** `client_presence()` reads `tmux list-clients`. `attached_presence_hold()` holds only if an attached viewer typed in the last 120 s. Merely having a terminal attached does not hold mail.
   - **Breathe window:** 120 s of quiet in the session. It is skipped for critical, high-priority and held rows, for Stop-hook notices past their grace, for `codex_idle_release()` (a Codex agent whose idle prompt is back, which is its end of turn), and for agents `parked_idle_exempt()` marks as parked and idle.
   - **Idle check:** `agent_is_idle()` trusts a fresh hook file first; "working" or "waiting for permission" means busy. Otherwise it reads the screen: no busy markers, a prompt visible, nothing typed, and no change for 2 s. Anything it doesn't recognise counts as busy.
   - **Down agent:** if the agent isn't idle, `seat_is_down()` asks the state detector whether the agent process is gone while its pane is still alive. If so, `page_seat_down()` pages the escalation contacts, repeating every 30 minutes, with the owner's copy de-duplicated for 6 hours. Nothing is typed into a bare shell, and rows stay pending. A detector error counts as "not down", so an outage can't cause a paging storm.
5. **Inject.** `store.claim()` takes the row, then `inject()` takes the shared per-pane lock (`pane_lock.py`) and writes a typed-but-not-entered journal row (`inject_journal.py`). `_inject_unlocked()` then refuses typed text or a stranded paste, pastes, presses Enter, and verifies the submit. `composer_is_ghost()` treats Claude's dim ghost suggestion as an empty box, not as typed text. Gemini and Grok have no ghost suggestions, so any visible character counts as typed there.
6. **Outcome.**
   - `submitted`: the row is marked delivered, then `confirm_processing()` waits up to 20 s for the agent to start working and acknowledges it.
   - `held` or `cleared`: the row goes back to pending without spending a retry.
   - Failed paste: counts a retry, and the row is dead-lettered after 5.
   - Swallowed Enter (`stuck`): the row is marked delivered with an alert, so it isn't pasted twice.
   - Pending rows expire after 48 h.

## Path 2: a person's chat (direct, immediate)

- `POST /agent-message` (`handle_agent_message()` in `scripts/watch_gateway.py`) does **not** write to the store first. `verified_inject()` types straight into the pane under the same per-pane lock and journal, so it can never interleave with a router paste.
- **It still refuses to:**
  - type over unsent text in the box (unless the person sends with `force`)
  - answer a pending menu or permission prompt
  - type into an agent that started less than 10 s ago, or one whose composer isn't on screen
  - send `/exit` or `/quit`
- It presses Enter and only reports success when the box has cleared.
- **No** cooldown, breathe window, presence hold or Stop-hook grace applies. Those exist to protect a person from mail; here the person *is* the sender.
- If the agent is mid-turn and the client says it accepts a hold, the message is stored as a held row and the router delivers it at the next turn boundary, raw and at high priority.

## What a future agent needs to know

- **Choose the path by who is sending, not by urgency.** An agent that needs something faster sends it at high or critical priority, which skips the breathe window and the Stop-hook grace but never the idle or typed-text checks. It does not switch to the chat path.
- **Never add router-style waits to the chat path**, and never let mail skip the store.
- **Every writer into a pane** (router, gateway, nudges, boot injects) must take `pane_lock` and journal before typing.
- **Failure direction is deliberate.** The idle check fails toward holding, because a wrong delivery clobbers a person's input. The down-agent check fails toward not paging, because a wrong page trains people to ignore pages.
- **A held message is never lost.** It stays pending until it is delivered, expires, or is dead-lettered with an alert.
