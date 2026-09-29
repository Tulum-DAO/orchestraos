# Independent Peer Review — "Replace OrchestraOS's Telegram Bridge with Hermes (+ optional Matrix)"

> Source: operator-supplied PDF, delivered via Telegram 2026-09-29. External reviewer,
> no access to the OrchestraOS repo/router.py/approval.py/local Hermes install — all
> claims about local code are reported as "stated by the brief" [B], not independently
> re-verified by this reviewer. All web sources consulted 2026-09-29. Transcribed
> verbatim by gm from the PDF for plan to reconcile into `docs/PLAN_hermes-matrix-telegram-bridge.md`.
>
> **[plan/gm annotation, added 2026-09-29 after transcription — the text below
> this point is unedited from the original PDF, this note is appended, not
> inserted into the reviewer's words]:** `build`, implementing the fix this
> review's F1/F2/Risk#1 correctly called for, found that this review's
> reliance on the brief's own "0/5 test coverage" claim (labeled [B] by the
> reviewer's own methodology above — inherited from the brief, not
> independently checked, exactly as their own evidence-labeling system
> says) was inaccurate: `plugins/telegram/tests/test_router.py` already
> existed with 17 tests. This does not reflect an error by this external
> reviewer, who correctly scoped their own confidence on this point — it
> reflects an error in the brief they were given to review. See
> `docs/PLAN_hermes-matrix-telegram-bridge.md` §7.5 for the full correction,
> including a related finding (the proposed "never advance offset on
> failure" fix was itself incomplete — build's actual implementation,
> commit `b834241`, is the corrected version).

Document reviewed: Product Brief: Replace OrchestraOS's Telegram Bridge with Hermes (+ optional
Matrix), 33 pages, dated 2026-09-29, status "REVIEWED (CEO + Eng)".

## Evidence labels

| Label | Meaning |
|---|---|
| [V] | Verified by this reviewer against a cited primary source (official docs, spec, maintainer release notes) |
| [B] | Stated by the brief from its own code/config reading; plausible, not independently re-checked here |
| [I] | Inference by this reviewer; reasoned, but not confirmed by a source |
| [R] | Recommendation |

## 1. Executive summary

**Bottom line:** the brief lands in the right place. Its recommendation (harden the existing
router.py now, defer Hermes-as-transport and Matrix behind explicit operator decisions) is
sound, and its self-corrections (§4a, and the Eng review's correction of the CEO review) are
the strongest part of the document. This review **endorses path (a)** and proposes a refined
version, **"path (a+)"**, that addresses five points the brief leaves under-specified:

1. **The real reliability invariant is commit ordering, not offset persistence.** Telegram
   keeps unconfirmed updates server-side for up to 24h and treats an update as confirmed once
   `getUpdates` is called with a higher offset [V]. So a restart *without* local offset state
   causes **redelivery (duplicates)**, not loss. Loss happens when the offset advances *before*
   the message's effect is durably committed to gm's inbox. The brief's "one real open
   verification item" (§Eng-3, `handle_update` behaviour on a mid-batch exception) is
   therefore the P0 item, and the fix is "commit idempotently keyed by `update_id`, then
   confirm" — not more persistence code.
2. **Callback (button) authorization should be treated as a security boundary.** The brief
   says the swap adds "no new trust boundary" (CEO §3). True of the transport — but approval
   taps are the fleet's most privileged input. Callback data comes from the client and is
   limited to 64 bytes [V]. The authorization check should be written down explicitly: numeric
   user id + chat id allowlist, a pending-state check, answer-once enforced server-side, and an
   audit record. Today the brief marks this "carried, not re-verified".
3. **Hermes's own documented defaults confirm the brief's caution about using Hermes
   directly.** Hermes's Telegram integration drops pending updates on cold boot by default
   (`drop_pending_updates=True`, configurable) [V]. It also documents a single gateway process
   per host serving several profiles under multiplexing [V]. Direct bearing on the "dedicated
   instance vs. keonda profile" isolation argument. Neither point is in the brief.
4. **Path (b) is ambiguous as written, and the ambiguity changes what gets built.** In Hermes
   Relay, the Hermes gateway runs the agent turn and the connector owns platform credentials
   [V]. "Keep gm as the brain and use Hermes purely as transport" therefore means gm would
   implement the *gateway* side of the contract against NousResearch's Node connector — Hermes
   the agent is not in the loop at all. The contract is EXPERIMENTAL, Telegram is not yet a
   validated Class-1 platform, and unsupported features "silently degrade to plain text" [V].
   An approval card silently degrading to text is exactly the wrong failure mode for this
   channel.
5. **Rate limits, observability and rollback mechanics need concrete acceptance criteria.**
   Examples: one message per second per chat [V], 429 handling, a heartbeat on the last
   successful poll, and the webhook/polling mutual exclusion during rollback [V].

**Decision the team should make now:** confirm path (a+) (§7) and schedule Phase 0
(verification, about half a day). Put Hermes relay and Matrix behind the explicit re-open
criteria in §8, rather than treating them as open-ended "maybe later".

## 2. Faithful summary of the plan (what the brief says, in its own terms)

- **The ask (§1):** route the operator↔fleet Telegram channel (currently
  `plugins/telegram/router.py`) through Hermes ("a separate, more mature agent runtime already
  running on this machine"). Optional goal: Matrix/mautrix bridges so many networks converge
  on one integration point. Rationale cites a conversation with "Shaw" and an "Elon's team
  orchestration layer" analogy.
- **Current state (§2) [B]:** router.py is 369 lines, stdlib only. Long-polls the Bot API,
  writes operator messages into gm's msg_store inbox, pushes inline-button approval cards
  whose taps land in `scripts/approval.py`. Dashboard and Apple Watch share the same approval
  core. Card mechanic = the invariant any replacement must preserve.
- **Hermes [B]:** 22 platform adapters incl. native Telegram (python-telegram-bot, Bot API) and
  native Matrix (mautrix SDK). Local Hermes instance already runs a different business persona
  ("keonda") with WhatsApp; its config records the operator's earlier decision to un-mix a
  separate deliverable (Jason CRE) from that gateway.
- **Matrix (§2-3) [B]:** dormant Synapse + mautrix-whatsapp stack for a different project, with
  dev tokens unsafe for public use. No mautrix-telegram deployment. Puppeting relies on MTProto
  user accounts; relay/bot mode "forfeits most of what the bridge is for".
- **Correction (§4a):** adversarial review found Hermes's Telegram adapter has no relay-only
  mode — pointing it at the bot token makes Hermes the agent answering the operator. Verified
  by grep (one `CallbackQueryHandler` hard-wired to Hermes-internal prefixes; no
  relay_only/passive mode) [B].
- **Recommendation (§4):** three separate decisions. (1) Native Hermes adapter direct is *not*
  low-risk. (2) Choose (a) harden router.py in place (recommended now) or (b) build a connector
  against Hermes's experimental relay contract. (3) Matrix is its own future brief, gated on a
  real second-network need.
- **Eng review:** router.py already persists offset atomically (tmp file + `os.replace`) [B] —
  withdraws the CEO review's "CRITICAL GAP". Router has 0/5 direct test coverage; 4 pytest
  tests proposed. One open question: whether `handle_update` advances past an update whose
  delivery raised.
- **Open operator questions (§5):** a vs. b; dedicated instance vs. profile (if b); source of
  the Elon/Shaw framing; whether a concrete second network exists.

## 3. What the plan already does well

1. **Verify-by-rederiving culture.** Both corrections (§4a, Eng scope challenge) were checked
   against code before acceptance. The brief records its own errors instead of hiding them.
2. **Names the true invariant** (approval-card parity), not just "text relay."
3. **Separates the decisions** (Telegram transport / who is the brain / Matrix) instead of
   bundling them.
4. **Preserves tenant separation** — found the operator's written de-enmeshment precedent in
   config and applied it to the fleet-command channel.
5. **Flags unverifiable claims** (Elon/Shaw) instead of asserting a match.
6. **Rollback posture** (disabled-not-deleted, single live token holder) is correct in
   principle.
7. **HOLD SCOPE discipline** — does not expand into a Matrix build.
8. **Spots the cost/autonomy risk** of routing every operator message through a second LLM
   loop.

## 4. Dialectic — thesis → antithesis → synthesis

### Round 1 — "Should Hermes (and Matrix) replace the bridge?"

**Thesis (operator's ask).** Consolidate onto Hermes: richer tool surface, 20+ adapters, one
integration point. Matrix would add a universal, self-hosted event model across networks.

**Antithesis (the brief, confirmed by this review).**
- Hermes is agent-first. Its gateway normalizes platform events into `MessageEvent`s and hands
  them to an `AIAgent` turn [V: Gateway Internals]. Pointing the operator's bot at it changes
  *who answers*, not just how messages travel.
- Hermes Telegram docs describe approvals as "Reply 'yes' to approve" prompts, and inline
  keyboards for the agent's own `clarify` tool [V]. No documented generic surface for "post an
  external system's card, return its callback" — matches the brief's grep finding [B].
- Matrix for a Telegram bot-to-operator channel adds a homeserver, an appservice registration,
  and a bridge with its own database. Relay/bot-mode bridging doesn't use the MTProto puppeting
  that makes mautrix-telegram distinctive (brief's argument; consistent with mautrix docs)
  [V/I].

**Synthesis 1.** Separate the brain (gm, which stays) from the transport (the thing that must
be reliable). For a single-operator Telegram channel, the smallest reliable transport is the
existing one. → Path (a).

### Round 2 — "Is path (a) really near-zero risk and near-zero work?"

**Thesis (brief's synthesis).** Harden router.py in place. Offset persistence is already
durable. Add four tests and verify one mid-batch behaviour. Zero cutover.

**Antithesis (this review).**
- *Durable offset ≠ no loss.* The Bot API confirms updates by offset [V]. If
  `offset = update_id + 1` is persisted *before* `deliver_to_gm` durably commits, a crash or
  exception in between **loses** the message. If persisted *after*, a crash **duplicates** it.
  Neither is handled by atomic file writes. The brief's statement that a crash "loses at most
  the update(s) not yet durably offset-advanced" [B] reads as if unconfirmed updates are lost —
  per the Bot API they're re-fetched, for up to 24h [V].
- *Authorization is asserted, not specified.* "Card button taps still need to land through
  approval.py's existing authorization" [B] — but the brief never states what that
  authorization checks. Callback data is attacker-influenceable client data [I], capped at 64
  bytes [V].
- *Rate limits are unaddressed.* A burst of pending approvals to one chat must respect ~1
  msg/s per chat. Exceeding it yields 429 errors [V].
- *Observability.* Brief notes nothing alerts on the router being down [B, unverified]. For a
  control channel, silent failure is the worst case, as the brief itself says.
- *Strategic cost.* Path (a) leaves the operator's actual goal (consolidation, optionality)
  unmet — risks relitigating the question every few months.

**Synthesis 2 → "path (a+)".** Keep path (a), but define the channel by explicit invariants
and a conformance test suite, not by the current implementation. Fix the commit ordering
(idempotent inbox write keyed by `update_id`, then confirm). Specify callback authorization.
Add rate-limit handling and a heartbeat. Any future transport (Hermes relay, Matrix, a second
network) then becomes "pass the same suite" — answers the consolidation goal without building
anything speculative.

### Round 3 — "Isn't a transport abstraction over-engineering?"

**Thesis (Synthesis 2).** Define a channel port and invariants so future transports can plug
in.

**Antithesis.** A code-level abstraction built for a hypothetical second network violates HOLD
SCOPE. Adds indirection to a 369-line file that's currently easy to reason about.

**Synthesis 3 (final).** Do **not** build an abstraction layer. Write the invariants as (i) a
one-page channel contract document and (ii) pytest tests against router.py's existing
injectable seams (`api`, `deliver`, `answer`, `pending`, `state`, `fetch` [B]). Costs roughly
what the brief already proposes, gives the future-proofing without the structure. Any future
adapter gets the tests; nobody gets an interface until a second implementation exists.

## 5. Findings, contrasted with current sources

| # | Topic | Finding | Basis | Severity |
|---|---|---|---|---|
| F1 | Offset semantics | Telegram stores unconfirmed updates ≤24h. An update is confirmed when `getUpdates` is called with a higher offset. Local offset state therefore controls duplicates vs. loss via **commit ordering**, not "whether messages survive a restart". Outages longer than 24h lose updates regardless. | [V] Bot API `getUpdates` | High (framing) |
| F2 | Mid-batch exception | The brief's open item (`handle_update`, line 202) decides whether silent loss is possible today. Should be promoted to P0, fixed with commit-then-confirm plus idempotency on `update_id`. | [B]+[I] | **High** |
| F3 | Hermes cold boot | Hermes Telegram defaults to `drop_pending_updates=True` on cold boot (`drop_pending_on_cold_boot: false` preserves backlog). Hermes also suppresses repeated `update_id`s. Directly substantiates the brief's CEO-§2 concern for any Hermes-native path. | [V] Hermes Telegram docs | Medium (path b/native only) |
| F4 | Hermes process isolation | Hermes docs: under multiplexing, "one gateway process per host" serves multiple profiles, with profile-scoped PID files. A "dedicated instance" must be verified as a separate process/user/HOME (or container), not a profile sharing the keonda process. | [V] Gateway Internals + [I] | Medium (path b) |
| F5 | Relay contract roles | The Hermes gateway dials the connector and runs the agent turn. The connector (Node/TS) holds all platform secrets. Contract v1 is EXPERIMENTAL, may change without deprecation until Discord and Telegram validate it. Ops: send, edit, typing, follow_up, send_media, prompt, react, thread_create, thread_rename. No `delete` op observed. | [V] Relay contract + Relay guide | Medium |
| F6 | Silent degradation | Relay docs: unsupported features (buttons, media, threads) "silently degrade to plain text." For approval cards this must be a hard failure, not a degradation. | [V] Relay guide | High (path b) |
| F7 | Path (b) wording | "Keep gm as brain, Hermes as transport" would mean gm implements the gateway side of the contract. The Hermes agent runtime is then not used at all, only NousResearch's connector. Brief should state this plainly — changes the value proposition ("richer tool surface" disappears). | [V] roles + [I] | Medium (clarity) |
| F8 | Callback auth | Callback data is ≤64 bytes and client-originated. Taps in a group can come from any member. Authorization must bind to numeric `from.id` (the operator's id) and the expected chat, and check the approval is still pending. Hermes itself documents numeric-id allowlists and warns never to allow all users on a bot with terminal access — a useful parallel. | [V] Bot API, Hermes docs + [I] | **High** |
| F9 | Rate limits | Avoid >1 msg/s per chat, 20 msg/min per group, ~30 msg/s bulk. Beyond that, 429 errors. A card backlog after an outage needs queuing and honouring `retry_after`. | [V] Bot FAQ | Medium |
| F10 | Webhook vs. polling | `getUpdates` doesn't work while a webhook is set. If any candidate (Hermes webhook mode, a connector) ever sets a webhook on the production token, rollback to router.py requires `deleteWebhook` first. If a webhook is used, `secret_token` (header `X-Telegram-Bot-Api-Secret-Token`) should be mandatory — Hermes marks it required. | [V] Bot API; Hermes Telegram docs | Medium |
| F11 | Concurrent pollers | Brief correctly requires a single token holder. Telegram returns a conflict error when two `getUpdates` consumers run concurrently — widely observed, but reviewer didn't find it stated on the official page. | [I] | Medium |
| F12 | "Operator's only channel" | Brief calls Telegram the operator's only channel, but also says dashboard/Apple Watch share the approval core. If those remain usable, Telegram loss is degraded service, not loss of fleet control. Team should state which is true — sets the severity of every failure mode. | [B] internal ambiguity | Medium (clarity) |
| F13 | Matrix bridge landscape | mautrix-telegram's Go rewrite (bridgev2) released 2026-04-16 (v26.04), with extended relay mode emulating old-style relaybots. Slightly updates the brief's picture of the bridge, doesn't change the conclusion for a single-operator bot channel. | [V] mau.fi release post | Low |
| F14 | Matrix delivery model | Appservice transactions pushed with an idempotent `txnId`; homeservers retry with exponential backoff and must not alter a retried transaction. Good *pattern* to copy for gm's inbox writes regardless of Matrix. | [V] Matrix AS API v1.19 | Low (idea) |
| F15 | Media parity | Bot API downloads limited to 20MB (Hermes docs cite 20MB via public API, 2GB with a local Bot API server). router.py's upload path and its limits should be written into the channel contract. | [V] Hermes docs; [B] | Low |
| F16 | Stale sections | CEO §1-10 were written for the superseded "native adapter" design. Brief says so in a note, but diagrams still show that design, and the completion summary reports "0 unresolved decisions" while listing four. Invites misreading when shared. | [B] | Low (editorial) |

## 6. Prioritized risk register

| Rank | Risk | Likelihood | Impact | Applies to | Mitigation |
|---|---|---|---|---|---|
| 1 | Operator message silently lost: offset confirmed before gm inbox commit, or exception mid-batch | Unknown (P0 check) | High | a, b | Commit-then-confirm; idempotent insert keyed by (bot_id, update_id); test with injected failure |
| 2 | Unauthorized or stale approval executed via callback (forwarded card, group member, replayed tap) | Low | Critical | a, b | `from.id` + chat allowlist; pending-state check; server-side answer-once; audit log; short card expiry |
| 3 | Duplicate action from redelivered update or double tap | Medium | High | a, b | Idempotency on `update_id` and `callback_query.id`; approval state machine transitions once |
| 4 | Silent channel death (no alert) | Medium | High | a, b | Heartbeat: last successful poll timestamp; alert when > N min; distinct from "gm idle" |
| 5 | Approval card degrades to plain text or button semantics diverge | Medium | High | b | Require capability check at handshake; refuse to run if buttons unsupported |
| 6 | Tenant bleed with keonda (shared process, credentials, logs) | Low-Med | High | b | Separate OS user/HOME/container; separate token; verify no shared PID/profile |
| 7 | Contract drift (experimental v1) | Medium | Medium | b | Pin `contract_version`; fail closed on mismatch; conformance tests in CI |
| 8 | 429 storm after outage when many cards are pending | Low | Medium | a, b | Per-chat queue ≤1 msg/s; honour `retry_after`; coalesce into a digest card |
| 9 | Token exposure during migration (two env locations) | Low | High | b | Single secret store; rotate via BotFather after cutover if copies existed |
| 10 | Autonomy/cost: second LLM loop answering the operator | High if native Hermes | Medium | native Hermes | Do not use the native adapter for this channel (brief's conclusion) |
| 11 | Rollback blocked by a leftover webhook | Low | Medium | b | Runbook step `deleteWebhook`, then restart the router |

## 7. Recommended refined architecture — "path (a+)"

```
operator ──Telegram Bot API (long-poll)──► router.py ──► gm msg_store inbox (system of record)
   ▲                                          │              ▲
   └──── inline cards / replies ◄─────────────┘              └── approval.py (authz + answer-once + audit)
                                               │
                                               └──► heartbeat/metrics (last_poll_ok, lag, 429s, pending_cards)
```

**Channel contract (one page, versioned).** Any present or future transport must satisfy it:

1. **Identity:** only numeric Telegram user ids on an explicit allowlist can speak or approve.
   Display names and usernames are never trusted. Direct chats only, unless a group is
   explicitly listed.
2. **Ingress durability:** an inbound update is confirmed to Telegram only after its effect is
   committed to the gm inbox. The inbox rejects duplicates by `(transport, bot_id, update_id)`.
3. **Callback authority:** a tap resolves an approval only if the sender is authorized, the
   approval exists and is pending, and the callback payload matches the issued card (an opaque
   short id; no semantics in `callback_data`). First resolution wins on the server; later taps
   get "already resolved". Every decision is audited (who, when, via which surface).
4. **Egress:** at most 1 msg/s per chat. 429 responses retried after `retry_after`. Cards edited
   to show final state when resolved elsewhere (dashboard/watch).
5. **Edits and deletes:** decide and document. Proposed: operator edits to an already-delivered
   message forwarded as a new "edited" event, not silently applied. Deletions logged, not
   propagated. Card edits come only from the bot.
6. **Loop prevention:** the bridge never re-ingests its own outgoing messages (bots can't see
   other bots' Telegram messages anyway [V], but matters for any future bridge — Matrix relays
   can echo).
7. **Media:** state the types, size limits, and storage path gm expects.
8. **Observability:** structured log per inbound, outbound, tap, error. Heartbeat metric.
   Runbook for "channel down".
9. **Single holder:** exactly one process holds the token. Startup checks no webhook is set
   (`getWebhookInfo`).

**What stays unchanged:** gm is the only persona. `approval.py` stays the system of record. No
new runtime, no Matrix, no Hermes in the loop.

## 8. Pending decisions (for the team/operator)

| Decision | Options | Reviewer's recommendation | Re-open criteria |
|---|---|---|---|
| D1 Telegram leg | (a) / (a+) / (b) | **(a+) now** | — |
| D2 Hermes relay (b) | build / defer | **Defer** | Contract validated for Telegram (non-experimental), **and** the operator names a concrete capability that needs Hermes infra, **and** a card capability is advertised |
| D3 Matrix | build / defer | **Defer** | A named second network must share the same operator channel, with an owner for homeserver operations and a security review of the dormant stack's tokens |
| D4 Severity model | Telegram is sole channel / one of three | Decide explicitly (F12) | — |
| D5 Edit/delete semantics | forward / ignore / apply | Forward edits as events; log deletes | — |
| D6 Isolation if (b) ever happens | separate process+user / profile | Separate process, user and token (F4) | — |

## 9. Phases, tests and acceptance criteria

**Phase 0 — Verify (~½ day, no behaviour change).**
- Trace `handle_update`/`poll_once`. Document whether the offset is persisted before or after
  `deliver_to_gm` commits, and what happens on an exception.
- Read `approval.py`'s tap path. Document the exact authorization checks (user id, chat id,
  pending state, answer-once) and where the audit record is written.
- Confirm what supervises router.py and whether anything alerts on its death.
- *Exit:* a one-page written answer to each point, attached to the brief.

**Phase 1 — Tests + minimal fixes.** Tests (pytest, injected seams). The brief's four, plus:
- `test_exception_mid_batch_does_not_confirm_failed_update`: deliver raises on update 2 of 3 →
  the offset does not pass update 2. After a retry, exactly one inbox row per update.
- `test_redelivered_update_is_idempotent`: the same `update_id` delivered twice → one inbox row.
- `test_callback_from_unauthorized_user_rejected`, `test_callback_for_resolved_approval_is_noop`,
  `test_double_tap_resolves_once`.
- `test_429_honours_retry_after` (fake API).
- `test_startup_fails_closed_if_webhook_set`.

*Acceptance:* all tests green in CI; fixes limited to commit ordering and idempotency, if Phase
0 shows they're needed.

**Phase 2 — Observability + runbook.**
- Heartbeat (`last_successful_poll_at`), alert after N minutes, distinct from gm idle.
- Counters: inbound, outbound, taps, 429s, errors.
- Runbook: restart, token rotation, webhook reset, how to approve via dashboard/watch during an
  outage.

*Acceptance, live drill on a non-production bot:* `kill -9` during a message burst → zero lost
and zero duplicated inbox rows. Unauthorized tap → rejected and audited. 20 queued cards →
delivered without 429 escalation. Alert fires within N minutes of the router stopping.

**Phase 3 — Decision gate** for D2 and D3, using the re-open criteria in §8.

## 10. Threat model (condensed)

| Asset | Threat | Control |
|---|---|---|
| Bot token | Leak via env files, git, logs, second copy | Single secret location; not in git; redact in logs; rotate via BotFather on suspicion |
| Approval authority | Tap by a non-operator; forwarded or stale card; crafted callback | Controls 1 and 3 of the channel contract; card expiry; audit |
| Operator identity | Username or display-name change, impersonation | Bind to the numeric id only |
| gm inbox integrity | Other agents also write to msg_store [B]; injected "operator" messages | Tag the source transport and verified sender on each row; gm treats only transport-verified operator rows as operator authority. Message content never grants authority |
| Tenant boundary | keonda and OrchestraOS sharing process, config or logs (path b) | Separate process, user and token |
| Webhook endpoint (if ever used) | Spoofed updates | `secret_token` header verification; TLS |
| Availability | Silent death, 24h update expiry during a long outage | Heartbeat alert; runbook; alternate approval surfaces |

## 11. Ambiguities in the PDF

1. **Offset timing.** "sets `self.state.offset = update_id + 1` (line 323) as it processes each
   update". Unclear whether before or after delivery commits — the crux of loss vs. duplication.
2. **"Operator's only channel"** vs. dashboard/Apple Watch sharing the approval core (F12).
3. **Path (b) actors.** Whose brain runs where, and whether "Hermes" means the agent runtime or
   only its connector (F7).
4. **Stale CEO sections and diagrams** describing the superseded design; "0 unresolved
   decisions" vs. four listed (F16).
5. **Effort estimates** ("human ~1h / CC ~10min") without stating assumptions.
6. **What "richer integration/tool surface" should concretely enable** for the operator. Without
   that, path (b) has no measurable success criterion.
7. **Status of the dormant Synapse stack's unsafe dev tokens.** Whether they're still present
   on disk deserves a separate hygiene ticket even if Matrix stays deferred.

## 12. Questions for the team

1. In `poll_once`/`handle_update`, is the offset persisted before or after `deliver_to_gm`
   returns successfully? What happens to the batch on an exception?
2. What exactly does `approval.py` check on a tap: numeric user id, chat id, pending state?
   Where is the audit record, and does it record the surface (Telegram/dashboard/watch)?
3. Is Telegram the operator's only approval surface in practice, or can the dashboard/watch be
   used during a Telegram outage?
4. Does the gm inbox have a uniqueness constraint that could hold `(transport, update_id)`?
5. What supervises router.py today, and has anyone been alerted to its death in the past?
6. Is the bot ever added to groups, or is it DM-only by design?
7. What concrete capability did the operator expect from Hermes that gm cannot provide today?
   This defines success for any future path (b).
8. Is there a named second network (WhatsApp, Discord...) that must share *this* operator
   channel, and who would operate a homeserver?
9. Are the unsafe dev tokens in the dormant Matrix stack still on disk, and should they be
   rotated or removed as hygiene?

## 13. Sources (consulted 2026-09-29)

- Telegram Bot API reference — `getUpdates` (24h retention, confirmation by offset,
  incompatible with webhooks), `setWebhook` `secret_token`, webhook retry behaviour; current
  version Bot API 10.3 (2026-08-24). https://core.telegram.org/bots/api
- Telegram Bots FAQ — rate limits (1 msg/s per chat, 20/min per group, ~30/s bulk, 429 errors);
  bots cannot see other bots' messages; privacy mode. https://core.telegram.org/bots/faq
- Telegram Bot API — `InlineKeyboardButton.callback_data` (1-64 bytes).
  https://core.telegram.org/bots/api#inlinekeyboardbutton
- Hermes Agent — Gateway Internals (MessageEvent → AIAgent; authorization order; relay mode;
  one gateway process per host under multiplexing).
  https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals
- Hermes Agent — Telegram (polling/webhook, webhook secret required, numeric-id allowlists,
  `drop_pending_updates=True` on cold boot, update_id dedup, 20MB media via public API).
  https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram
- Hermes Agent — Relay ↔ Connector Contract (v1, EXPERIMENTAL; roles; ops; acks/drain; HMAC
  auth). https://hermes-agent.nousresearch.com/docs/developer-guide/relay-connector-contract
- Hermes Agent — Hermes Relay user guide (experimental; tokens on connector; silent degradation
  to plain text). https://hermes-agent.nousresearch.com/docs/user-guide/messaging/relay
- Hermes Agent repository. https://github.com/NousResearch/hermes-agent
- Tulir Asokan, "April 2026 releases // Telegram Go release and more advanced relays"
  (mautrix-telegram Go/bridgev2 v26.04, 2026-04-16). https://mau.fi/blog/2026-04-mautrix-release/
- Matrix Specification v1.19 — Application Service API (transactions, `txnId` idempotency,
  backoff, `as_token`/`hs_token`, exclusive namespaces).
  https://spec.matrix.org/latest/application-service-api/
- python-telegram-bot v22.8 — `Application.run_polling` (`drop_pending_updates`).
  https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html

---

*Limits of this review: no access to the OrchestraOS code or the local Hermes install. All
line-number claims are the brief's. Hermes documentation is a moving target (an experimental
contract, no page dates). Re-check before implementation.*
