# Product Brief: Replace OrchestraOS's Telegram Bridge with Hermes (+ optional Matrix)

- **Requested by:** operator, via gm (msg_1f3acb80_74023124, 2026-09-29; Elon's-
  team open question resolved via msg_a45fe321_75270887; Shaw's identity
  resolved via the Silicon Jungle brief's addendum 4, msg_ffd21b6f_76509819;
  external adversarial review reconciled via msg_8b696347_76706490)
- **Authored by:** plan (BSHR research + brief, non-interactive)
- **Status:** CEO + ENG + EXTERNAL ADVERSARIAL REVIEW + BUILD IMPLEMENTATION
  COMPLETE, reconciled into **"path (a+)"** (§7) — awaiting the operator's
  one load-bearing decision (§5) before any implementation of path (b); the
  confirmed P0 bug in path (a) is already fixed and verified (build, commit
  `b834241`). **§7 is a real, eng-review-depth reconciliation, not a
  citation edit, and §7.5 records a genuine correction, not just an
  addition**: build, actually implementing the confirmed P0 fix, caught
  that this brief's own eng review and the external adversarial review were
  both wrong that `router.py` had zero test coverage (17 existing tests
  found, a real research gap on this seat's part), and that the proposed
  fix ("never advance offset on failure") directly contradicted an
  existing, correct test — the real fix splits transient vs. permanently-
  malformed failures, which no review layer before build surfaced. The
  strategic recommendation (harden the existing bridge, defer Hermes/Matrix)
  is unchanged across all four passes — this is convergent validation with
  an honest asterisk about what review catches vs. what only an implementer
  checking against real tests catches (§7.5's closing note). Other post-
  review updates (Elon's team, Grok Bot comparison, Shaw's identity) remain
  folded in as before.
- **Scope:** brief only. No implementation authorized.

## 1. The ask, as parsed from the operator's transcript

Replace OrchestraOS's built-in Telegram bridge (`plugins/telegram/router.py`) so the
operator's channel(s) to the fleet run through **Hermes** (a separate, more mature agent
runtime already running on this machine) instead of a bespoke bridge. The operator's
stated rationale, per gm: Hermes has a richer integration/tool surface, and routing chat
through **Matrix** (the open messaging protocol) via mautrix-style bridges would let many
chat networks — Telegram, WhatsApp, Discord, etc. — flow through one integration point.
The operator referenced a conversation with "Shaw" and an analogy to an orchestration
"layer" Elon Musk's team is reportedly building.

## 2. What's actually true today (verified this session)

**Current bridge.** `plugins/telegram/router.py` (369 lines, stdlib only) long-polls the
Telegram Bot API with a dedicated bot token, writes each operator message into gm's
msg_store inbox, and — critically — pushes **inline-button approval cards** for every
pending decision, landing button taps through `scripts/approval.py`. This is the *same*
core the dashboard and Apple Watch use. This card mechanic, not just text relay, is what
any replacement has to preserve — it's the primary way the operator approves/declines
fleet actions today, not a side feature.

**Hermes already has native adapters for both ends of this, with no Matrix required.**
`/Users/flybyflow/.hermes/hermes-agent/plugins/platforms/` ships 22 platform adapters,
including a **native Telegram adapter** (`telegram/adapter.py`, Bot-API-based via
`python-telegram-bot`, same dedicated-bot-account pattern as the current OrchestraOS
bridge — verified, no MTProto/Telethon imports) and a **native Matrix adapter**
(`matrix/adapter.py`, connects as a Matrix *client* to any homeserver via the mautrix
Python SDK, optional E2EE, production-featured: threads, mention-gating, allowlists,
reactions). Hermes also natively adapts WhatsApp, Discord, Slack, Signal, SMS, Teams,
Line, IRC, Mattermost, and more — **the "many networks through one surface" win the
operator wants is largely already delivered by Hermes's own adapter architecture**,
independent of whether Matrix is in the picture at all.

**The "existing Matrix homeserver" is dormant, and built for a different project.**
`/Users/flybyflow/substrate/infra/matrix/` has a Synapse + `mautrix-whatsapp` Docker
Compose stack, but: Docker isn't running (verified — daemon unreachable), the stack's own
README describes it as infra for **"Primrose Agentic OS"** (a different project, not
OrchestraOS), and its config ships **dev/localhost tokens explicitly marked unsafe for a
public-facing server**. There is **no `mautrix-telegram` bridge** present anywhere on
this machine today — it would need to be stood up from scratch.

**`mautrix-telegram`'s real value proposition doesn't fit this use case well.** Its
puppeting mode — the reason to pick it over a direct Bot API integration — is built on
Telegram's MTProto **user-account** API (full history sync, joining channels as a real
person), not the Bot API. OrchestraOS's bridge and Hermes's own Telegram adapter are both
dedicated-bot-account designs. Running `mautrix-telegram` in Bot-API/relay mode is
possible but forfeits most of what the bridge is for — at that point it's strictly more
infrastructure (Synapse + bridge container + appservice registration + DB) for the same
capability Hermes's native Telegram adapter already provides directly.

**Operational-boundary risk, found in Hermes's own config, not inferred.** The live
Hermes instance on this machine (`~/.hermes/config.yaml`) is already running as a
different persona/business ("keonda"), with WhatsApp enabled and Telegram/Matrix *not*
currently configured. The config contains the operator's own prior decision, in writing:

> `# jason-cre routes removed 2026-09-20 — Jason CRE is a separate deliverable... NOT a
> profile on this shared keonda gateway. Re-adding it here re-enmeshes the two — don't.`

OrchestraOS's fleet-command channel is, by the same logic, a separate deliverable from
keonda. Bolting it onto the shared keonda Hermes instance repeats the exact pattern the
operator already reversed once. This argues for a **dedicated Hermes instance** for
OrchestraOS, not a new profile on the existing one.

**"Elon's team" reference — RESOLVED (operator-sourced, gm relayed, independently
re-fetched and confirmed by this seat: https://x.ai/news/introducing-grok-bot).**
It's xAI's **Grok Bot**: always-on autonomous agents, each on its own dedicated
cloud machine, that sign into apps/tools directly (including ones with no
API/MCP), message each other and share context within threads, coordinate in
groups on parallel workstreams, and only surface back to a human when
something needs approval — beta on SuperGrok/Cursor/Enterprise waitlist as of
this writing. This is xAI's own product, unrelated to Hermes/NousResearch —
**does not change** the Shaw/NousResearch mismatch below, still worth the
operator clarifying separately. It does validate the operator's underlying
instinct (agent-to-agent messaging, approval-gated autonomy, coordinated
groups) — but that instinct is already implemented in this fleet today via
`msg_store` + `scripts/approval.py`, not something the Hermes/Matrix bridge
question was ever blocking on.

**Shaw's identity — RESOLVED (2026-09-29, gm's Silicon Jungle addendum 4,
independently corroborated there via a direct Luma-page fetch — see
`docs/PLAN_silicon-jungle-agentic-platform.md` §0c), and this original
brief's own speculative guess below was wrong, worth correcting plainly
rather than quietly dropping.** The original draft below guessed "Shaw" might
be Shaw Walters of ElizaOS/ai16z, based on his being a well-known public
figure in the agent-framework space, and flagged a mismatch against Hermes's
NousResearch authorship credit. **That guess was never verified and turned
out to be the wrong Shaw.** The real Shaw, per the Silicon Jungle brief's
direct verification: **Shaw Cole**, a real co-founder, guest-hosting Silicon
Jungle's BUILD-A-THON events, and — confirmed on the event's own page —
**the actual author of OrchestraOS itself** (this fleet's own codebase).
Shaw Cole's own project is **ListMagic** (stealth, intent-data + AI voice
outreach), not Hermes.

**What this does and doesn't resolve, precisely — two separate facts, not
one contradiction:** (1) *Who is Shaw* — resolved, real, confirmed. (2) *Is
Hermes Shaw's project* — still no, unchanged, and now on firmer footing:
Hermes's own plugin metadata credits **NousResearch**, and Shaw Cole's own
confirmed project is ListMagic, a different thing entirely. The original
transcript's framing ("discussed with Shaw... orchestra should release as a
layer") makes more sense now that Shaw's identity is known: he's the person
who *wrote OrchestraOS*, which is a materially different and more load-
bearing fact for this brief than a vague reference to an agent-framework
influencer would have been — worth the operator's attention for that reason,
independent of whether it changes anything about the Hermes/Telegram
decision itself (it doesn't; see §4/§5, unchanged).

## 3. What Matrix would actually add, if pursued

Once a real network is bridged in (e.g., WhatsApp today via `mautrix-whatsapp`), Matrix
gives: one client-side event model across bridged networks, self-hosted message data
(bridge traffic terminates on your own Synapse, not a third party), and E2EE on the
client↔homeserver leg (Megolm) — though the bridge↔platform leg is only as encrypted as
the platform allows (Telegram secret chats specifically can't be bridged via the Bot API
either way). The cost: one more moving part per platform (bridge container + DB +
appservice reg), and bridges that track unofficial/internal platform APIs are inherently
more fragile than official Bot APIs. For Telegram specifically, per §2, this cost buys
close to nothing beyond what Hermes's native Telegram adapter already does alone.

## 4. Recommendation (revised after outside-voice review — see §4a)

**Three separable decisions, don't conflate them:**

1. **Telegram → Hermes's native adapter, direct: NOT low-risk as originally framed —
   corrected below in §4a.** The original version of this brief called this the
   low-risk move. An adversarial outside-voice pass caught a load-bearing error in
   that framing (verified, not just asserted — see §4a): Hermes is agent-first by
   design, and its Telegram adapter has no passive/relay-only mode. Pointing it
   directly at the operator's bot token makes **Hermes itself** the agent answering
   the operator, not a transport carrying messages to **gm**. That is a different,
   bigger decision than "swap the bridge," and it was never surfaced as such in the
   original draft.
2. **Two corrected paths for the Telegram leg (pick one, don't default into either):**
   - **(a) Harden `router.py` in place.** Close the one real gap this brief actually
     found (offset/missed-message persistence, if it proves to be a real gap — Phase 1
     of the original plan already required verifying this). Zero new runtimes, zero
     new agent-autonomy risk, keeps gm as the sole persona the operator talks to.
     Does not touch Hermes at all — does not deliver "richer integration/tool surface,"
     because this use case doesn't need Hermes's tool surface; `gm` already has one.
   - **(b) Build a minimal connector against Hermes's own `gateway/relay/` contract**
     (`docs/relay-connector-contract.md`, **EXPERIMENTAL**, v1, "MAY CHANGE without a
     deprecation cycle until at least two real Class-1 platforms have validated it").
     This is a real platform-agnostic `MessageEvent`/`prompt`-op integration that
     *could* keep gm as the brain and use Hermes purely as transport plumbing — but it
     requires standing up a separate Node/TypeScript connector service
     (`NousResearch/gateway-gateway`), not a config flip. This is materially more
     engineering than the original brief scoped, and it's building against an
     explicitly experimental, pre-stable contract.
   **Recommend (a) now.** (b) is only worth doing if the operator confirms, after
   reading §4a, that they specifically want fleet-command traffic running through
   Hermes's own infrastructure — not just "a better bridge," which (a) already gives
   them at near-zero cost and risk.
3. **Matrix as a multi-protocol layer is unchanged: a separate, larger decision**
   that only pays off once there's a second and third network to bridge (e.g.,
   WhatsApp, Discord) that the operator actually wants unified with fleet-command
   traffic. Its own future brief, gated on a confirmed need — and now doubly so,
   since Matrix doesn't resolve the "who answers the operator" question either.

## 4a. Correction — what the outside-voice review caught

A dispatched adversarial review (native Claude subagent, Plan-mode, read-only) of
the original draft flagged that Recommendation 1 rested on an unverified — and
wrong — assumption: that Hermes's native Telegram adapter can act as a dumb relay.
Verified directly against the code before accepting this correction (per this
seat's own verify-by-re-deriving standard, not taking the review at its word):

- `/Users/flybyflow/.hermes/hermes-agent/README.md:19,26` — Hermes describes
  itself as "the self-improving AI agent... only agent with a built-in learning
  loop — it creates skills from experience, improves them during use..." This is
  the vendor's own framing, not an inference.
- `/Users/flybyflow/.hermes/hermes-agent/plugins/platforms/telegram/adapter.py:4361`
  registers exactly one `CallbackQueryHandler`, whose dispatch (lines 6271-7518)
  is hardcoded to Hermes's own internal concerns: `ea:` (execution approval),
  `sc:` (safety confirm), `cp:`/menu paging, `update_prompt:`. There is no generic
  "post an arbitrary card, get an arbitrary callback" surface here — grepped for
  one, found none.
- No `relay_only` / `passive` / `forward_only` / `no_agent` mode exists anywhere
  in the adapter (grepped, found none) — confirming every inbound Telegram
  message drives Hermes's normal agent turn, there is no toggle to suppress that
  and use the adapter as pure plumbing.
- **Independent corroboration, vendor docs (not just code):** Hermes's own
  published documentation, https://hermesbible.com/docs/user-guide/messaging/matrix
  (operator-sourced, re-fetched and confirmed directly) — describes Hermes as
  integrating with Matrix "as a client-based bot," not a bridge, with
  explicitly agent-driven interaction ("chat with your agent from any
  device"); no relay-only, passive, or non-agentic mode is mentioned anywhere
  in the docs. Same conclusion as the code-level finding above, from an
  independent source (vendor docs vs. adapter source) — this correction now
  rests on two independent lines of evidence, not one.
- `/Users/flybyflow/.hermes/hermes-agent/docs/relay-connector-contract.md` is
  real (50KB, exists) and is explicitly labeled `# Relay ↔ Connector Contract
  (v1, EXPERIMENTAL)` — confirming §4.2(b) above is accurately scoped as bigger
  and less stable than the original "native adapter" pitch implied.

The review also flagged, correctly: no simpler alternative (hardening `router.py`)
was named and rejected in the original draft — it should have been, given the
thing being replaced is 369 lines and already does the one job needed. And:
routing every operator→fleet message through a second LLM-calling agent loop
(Hermes's own) is a cost/autonomy risk the original draft's security section
never considered, distinct from the persona/enmeshment risk it did catch.

**Instance topology and migration shape are now conditional on which path (§4.2)
the operator picks — they were previously written for the no-longer-recommended
"native adapter, direct" design and are corrected here:**

**If (a) harden `router.py` (recommended now):** no new instance, no topology
question. Migration shape is trivial: fix the offset/missed-message gap in place
(verify first whether it's a real gap — `router.py`'s existing `state/telegram/offset`
file may already cover it; confirm before writing any new persistence code), add
the regression test from Section 6 below, ship on the existing bridge. Zero
downtime, zero cutover, the operator's channel never moves.

**If (b) build the relay-connector (only if the operator confirms they want
traffic on Hermes's own infra specifically):** a dedicated Hermes instance for
OrchestraOS is still the right call if this path is taken — not a profile on the
existing keonda gateway, matching the operator's own already-stated de-enmeshment
principle (§2) — but the phased shape gets bigger, not smaller, than originally
scoped:
- **Phase 1 — build & shadow.** Build the Node/TS connector against
  `relay-connector-contract.md`'s `MessageEvent`/`prompt` ops (real new service,
  not a config flip). Run it against a *second* bot/chat, `router.py` still
  serving production. Prove message-in/out parity and that gm — not Hermes's own
  persona — is who the operator ends up talking to.
- **Phase 2 — parallel cutover with instant rollback.** Point the real bot token
  at the connector; keep `router.py` and its state intact and disabled, not
  deleted, for an explicit burn-in window.
- **Phase 3 — retire the old bridge** only after burn-in shows no regression in
  card delivery, latency, or missed-message recovery, *and* no drift in the
  experimental contract (`docs/relay-connector-contract.md` may change without a
  deprecation cycle pre-1.0 — pin/monitor the connector's `contract_version`).
- **Matrix (if ever pursued):** still its own Phase 4+, gated on a real
  second-network need, independent of whether (a) or (b) is chosen for Telegram.

## 5. Open questions for the operator (surfaced, not decided here)

- **The load-bearing one, raised by outside review (§4a): does the operator want
  fleet-command traffic running through Hermes's own agent/infra specifically
  (→ path (b), real new engineering, experimental contract), or did they just
  want a more maintainable bridge (→ path (a), ships now, near-zero risk)?**
  The original transcript's rationale ("richer integration/tool surface") reads
  like it was answering a different question than the one this brief now knows
  it's actually asking.
- ~~Which specific claim/conversation is "the Elon orchestration layer"~~ —
  **RESOLVED**: xAI's Grok Bot (§2, operator-sourced, re-verified).
- ~~Who is "Shaw"~~ — **RESOLVED**: Shaw Cole, real co-founder, confirmed
  author of OrchestraOS itself (§2, verified via Silicon Jungle brief §0c).
  **The Hermes/NousResearch mismatch is still open, on firmer footing now**:
  confirmed Shaw Cole's own project is ListMagic, not Hermes — Hermes remains
  NousResearch's, unrelated to Shaw — still worth the operator having this
  precisely, given Shaw wrote this very fleet's codebase.
- If (b) is chosen: is a *dedicated* Hermes instance for OrchestraOS acceptable (extra
  process/infra to run), or is a cleanly-isolated profile on the existing gateway
  preferred despite the de-enmeshment precedent?
- Does the operator have a concrete second network in mind for Matrix (WhatsApp already
  has infra; Discord does not), or was Telegram-via-Hermes the whole near-term ask and
  Matrix was framed more as long-term direction?
- **(Added per §7.2, external review F12)** Is Telegram genuinely the
  operator's *only* channel to the fleet, or one of three (alongside the
  dashboard and Apple Watch, which this brief documents as sharing the same
  `approval.py` core)? This brief uses "only channel" language in places that
  implies total loss of fleet control on failure — if the dashboard/watch
  remain usable during a Telegram outage, the actual severity of every
  failure mode in Section 2/Section 8 is lower than stated. Worth an explicit
  answer before Phase 2's rollback urgency is calibrated.
- **(Added per §7.2, external review F7)** If path (b) is chosen: understood
  precisely that "Hermes as transport, gm as brain" means gm implements the
  *gateway* side of Hermes's relay contract against NousResearch's connector
  — the Hermes *agent runtime* itself (the "richer tool surface" originally
  cited as rationale) is not actually used in that design. Worth confirming
  the operator still wants (b) with this precise understanding, not the
  vaguer "use Hermes" framing from the original transcript.

## 6. Inspiration: xAI Grok Bot — where this fleet already matches, where it doesn't

The operator asked for this explicitly, as direction-validation, not as input to
the Telegram bridge decision itself: read Grok Bot's shape (xAI, announced,
https://x.ai/news/introducing-grok-bot, re-verified directly this session)
against what OrchestraOS's fleet already does, and say plainly where it
matches and where it doesn't — not a footnote, a real comparison.

Grok Bot's shape, as described: always-on agents, each with its own dedicated
cloud machine; sign into apps/tools directly, including ones with no clean
API/MCP; message each other and share context within threads; coordinate in
groups on parallel workstreams; surface to a human only when something needs
approval.

| Grok Bot capability | OrchestraOS today | Match? |
|---|---|---|
| Always-on agents, each on dedicated infra | Each seat runs in its own tmux pane/process (`spawn-agent.sh`), registered in `registry.json` with a tier and parent — not literally a dedicated cloud machine per agent, but the same "one durable, independently-addressable agent process per role" shape | **Close match** — infra granularity differs (shared host, separate processes vs. separate machines), the architectural pattern doesn't |
| Agent-to-agent messaging with shared thread context | `msg_store.py` — verified directly, has real `thread` and `conversations` subcommands (not just point-to-point sends); this session's own work (bridge brief ↔ Silicon Jungle brief cross-references, `re:` reply chains with gm) exercised exactly this | **Real match, not aspirational** — this is what this fleet already runs on, used throughout this very session |
| Coordinated groups on parallel workstreams | The T0→T1→T2 hierarchy (gm → plan/build/review/ea → bshr/think/test/ship/reflect/brain/builder-N) plus this session's own use of parallel dispatched research agents (Agent tool, background) | **Match** — arguably more structured than Grok Bot's flat "groups," since OrchestraOS has an explicit tiered ownership model, not just ad hoc coordination |
| Surfaces to human only when approval needed | `scripts/approval.py` + the inline-button card mechanic (the bridge brief's own subject) — this is the literal existing implementation of "approval-gated autonomy" in this fleet, verified in detail across both this brief and its reviews | **Real match, already the load-bearing mechanic this whole brief is about preserving** |
| Signs into apps/tools directly, including ones with no clean API/MCP | **Investigated directly this session, not assumed:** grepped for browser-automation libraries (playwright/selenium/puppeteer) fleet-wide — none found wired into OrchestraOS's own architecture. No MCP server configuration exists at the repo/fleet level either. Each agent seat does have raw shell/Bash access, which can drive CLI tools (`gh`, `git`, curl-based APIs) the same way a human would from a terminal — but that's per-session capability inherited from whichever underlying CLI runtime spawned the seat (e.g. a `gstack`-equipped Claude Code session's `/browse` skill), not a first-class OrchestraOS mechanism any seat gets by default. `arturo` (`docs/ARTURO.md`) is the closest thing to a "signs into things for you" front door today, but it's a voice/text assistant that commissions agents and answers questions — it doesn't itself drive arbitrary third-party apps. | **Real gap, not yet built** — this is the one Grok Bot capability this fleet does not have a first-class equivalent for today |

**Net read:** four of five capabilities are already real, working patterns in
this fleet — not inspiration to chase, validation that the existing
architecture (tiered seats, `msg_store` threading, `approval.py`'s human gate)
already independently converged on the same shape a well-funded external team
shipped as a named product. The one genuine gap — arbitrary direct tool/app
sign-in without a clean API — is real and worth naming plainly rather than
implying it's covered: today, an OrchestraOS agent's reach into a third-party
tool is bounded by (a) `msg_store`-mediated coordination with other seats, or
(b) whatever CLI/shell tools happen to be available in that seat's own
process, not a general "drive any app's UI" capability. Closing that gap, if
ever wanted, is its own separate scope decision — not something this brief
is recommending, and explicitly not something the Telegram bridge work
touches either way.

**Does this change the Telegram bridge recommendation? No — and here's why
explicitly, not just asserted:** the "sign into arbitrary apps directly"
gap is actually the same fundamental capability Hermes's relay-connector
architecture (§4a) would provide if path (b) is ever chosen — a
platform-agnostic way to reach a third-party surface. That's a real, useful
connection to note, but it doesn't change §4's recommendation to ship path
(a) now: this brief's core finding was that Hermes's *native Telegram
adapter specifically* is the wrong integration surface for *this specific
job* (transport for gm, not a new agent persona) — that finding is about
Telegram, not about whether "direct tool sign-in" is a capability worth
having in general. The Grok Bot comparison sharpens *why* the fleet's
tiered/threaded/approval-gated architecture is worth preserving as-is
(§10's ecosystem-fit finding, now doubly supported), it doesn't argue for a
different Telegram path.

## 7. External adversarial review, reconciled — "path (a+)"

The operator's own outside adversarial review landed (`docs/REVIEW_hermes-matrix-telegram-bridge-external.md`,
transcribed by gm from a PDF — **read in full before this section, it is the
primary source, not this reconciliation alone**). It is genuinely rigorous:
real citations against the Telegram Bot API reference, Hermes's own docs,
Matrix spec, and mautrix release notes — not a vibes-based critique. It
**endorses path (a)** and refines it into **"path (a+)."** This section is a
real reconciliation, not a citation bolt-on: what's accepted, what this seat
independently verified before accepting, and where this seat pushed back.

### 7.1 The headline finding — independently verified, and it's worse than
either document assumed

The review's most important point (F1/F2, Risk #1): the brief's Eng Review
left one open question — does a mid-batch exception in `handle_update` let
the offset advance past a failed update? **This seat re-read `poll_once`
directly (lines 310-324) to answer it, rather than leaving it open or taking
the review's inference at face value:**

```python
for upd in updates:
    try:
        self.handle_update(upd)
    except Exception as e:
        log(f"update {upd.get('update_id')} failed: {e!r}")
    self.state.offset = int(upd["update_id"]) + 1   # <-- OUTSIDE the try/except,
                                                       #     runs unconditionally
```

**Confirmed: this is a real, current bug, not a hypothetical risk.** The
offset advance is outside the `try`/`except` and executes regardless of
whether `handle_update` succeeded or raised. `deliver_to_gm` (the function
`handle_message` calls) has no internal retry or durability of its own — a
transient `msg_store` write failure, or any other exception during
processing, is caught, logged, and then **the offset silently advances past
that update anyway.** Per the Bot API's own confirmation semantics (the
review's F1, independently plausible and consistent with how long-poll
`getUpdates` is documented to work), once the offset advances past an
`update_id`, Telegram does not redeliver it. **Net effect: a transient
failure processing any single operator message causes that message to be
silently and permanently lost, with no retry, no redelivery, and (per the
brief's own Section 8 finding) no alert that anything went wrong.** This is
live in production code today, independent of anything about Hermes or
Matrix — the Telegram bridge decision doesn't cause this bug, but path (a+)
is the natural place to fix it since it's the same file already in scope.

**This resolves — with an answer, not just a citation — Eng Review's
Section 3 "one real open verification item," and elevates it from a proposed
test to a confirmed P0 fix**, matching Risk #1 in the review's own risk
register. Superseding the earlier "recommend as the first concrete task"
framing: it's not a recommendation anymore, it's a defect with an exact
line number.

**The fix is not "add more persistence"** (the CEO review's original framing,
already partly corrected by Eng review) — atomic offset writes don't help if
the write happens at the wrong time relative to delivery. Per the review's
correct framing: the offset must only advance *after* the update's effect is
durably committed, and because Telegram can then redeliver an
already-processed update within its retention window, delivery must be
**idempotent, keyed by `update_id`** — both halves are required together, not
either alone (advancing late without idempotency reprocesses; idempotency
without correct ordering doesn't stop the loss in the first place).

### 7.2 Other findings accepted into path (a+), independently assessed

Accepting these because the underlying technical claims are correctly
sourced (Bot API docs, Hermes's own docs) and consistent with what this
seat already independently verified about the codebase — not accepted
merely because they're external:

- **Callback/approval-tap authorization must be written down explicitly**
  (F8, Risk #2). The brief said this was "carried, not re-verified" —
  correct that it needed re-verification, and this review supplies the
  concrete spec (numeric `from.id` + chat allowlist + pending-state check +
  server-side answer-once + audit) rather than leaving it vague. Reading
  `handle_callback` (lines 238-258) directly: it does call `self.authorized`
  and `self.answer` (which routes through `approval.py`), but this brief
  never verified `approval.py`'s own internal checks match the review's
  spec — that verification is now Phase 0 work (§7.4), not assumed done.
- **Hermes's documented cold-boot default (`drop_pending_updates=True`) and
  one-gateway-process-per-host under multiplexing** (F3, F4) — real,
  vendor-documented, and directly relevant to the still-open path (b)
  dedicated-instance question. Strengthens (doesn't create) the brief's
  existing caution about the native-adapter path.
- **Path (b)'s actual actor model** (F7) — if gm implements the gateway side
  of Hermes's relay contract, "Hermes" in that design means NousResearch's
  connector only; the Hermes *agent runtime* (the "richer tool surface" the
  operator originally wanted) is not actually in the loop. This is a real
  clarification this brief should have stated more plainly in §4.2 — noted
  here, and worth restating to the operator alongside the path (a)/(b)
  question in §5.
- **Rate limits, 429 handling, webhook/polling mutual exclusion for
  rollback** (F9, F10) — concrete, sourced, and directly actionable; folded
  into the channel contract below.
- **F16 (completion summary says "0 unresolved decisions" while the doc
  lists several) — real inconsistency, fixed directly.** That line
  described the CEO review's own internal process (no new AskUserQuestion
  gate it needed to raise), not the brief's operator-facing open questions
  in §5 — a real distinction, but confusingly stated. Corrected in the
  Completion Summary below rather than left to confuse a future reader.
- **F12 (this brief calls Telegram "the operator's only channel" in two
  places while also documenting that the dashboard/Apple Watch share the
  same approval core) — real ambiguity, worth the operator's explicit
  answer, added to §5.** Whether Telegram is the sole channel or one of
  three changes the actual severity of every failure mode discussed in
  Section 2/Section 8.

### 7.3 Where this seat did not simply defer to the external review

Per gm's explicit instruction not to accept everything reflexively:

- **F11 (concurrent-poller conflict errors)** — the review itself flags this
  as [I] (inference, not confirmed against the official page). Treating it
  the same way here: plausible, consistent with how Telegram's long-poll API
  is generally understood to behave, but not independently verified this
  session either. Carried as an assumption, not asserted as fact.
- **The proposed channel-contract scope (9 invariants) and 6 additional
  tests could look like scope creep against a "harden a 369-line file"
  recommendation — considered this directly, decided it isn't.** The
  review's own Round 3 dialectic already rejects building a code-level
  abstraction layer for a hypothetical second transport (correctly, that
  would violate HOLD SCOPE) and settles on "a one-page contract doc + tests
  against the existing injectable seams" — no new classes, no new files
  beyond tests. Given §7.1 confirms a real bug exists, the additional rigor
  is now justified by a live defect, not speculative future-proofing.
  Accepting this scope as appropriate, not excessive.
- **Nothing else in the review reads as wrong or overstated on inspection** —
  its citations are specific (Bot API version numbers, exact byte limits,
  named release notes) in a way that's easy to spot-check and hard to fake
  convincingly; where it wasn't sure, it labeled itself [I] rather than [V].

### 7.4 Revised Implementation Tasks (supersedes the equivalent tasks in the
Eng Review section above — same numbering convention, marked superseded, not
duplicated)

- [ ] **T1-REVISED (P0, was P1)** — fix the confirmed offset/commit-ordering
  bug: only advance `self.state.offset` after an update's effect is durably
  committed; make delivery idempotent keyed by `update_id` so a redelivered-
  but-already-processed update is a safe no-op.
  - Surfaced by: §7.1 (this seat's direct code read), external review F1/F2/Risk#1
  - Files: `plugins/telegram/router.py` (`poll_once`, `handle_update`, `deliver_to_gm`)
  - Verify: `test_exception_mid_batch_does_not_confirm_failed_update` (deliver
    raises on update 2 of 3 → offset does not pass update 2; after retry,
    exactly one inbox row per update) and `test_redelivered_update_is_idempotent`
    (same `update_id` delivered twice → one inbox row) — both from the
    external review's Phase 1 plan
- [ ] **T3-REVISED (P0, was P1)** — callback authorization, made concrete:
  verify (Phase 0) then test that `approval.py`'s tap path actually enforces
  numeric `from.id` + chat allowlist + pending-state check + server-side
  answer-once + audit record — not just that a tap "lands," per the
  original T3's weaker framing.
  - Surfaced by: §7.2, external review F8/Risk#2
  - Files: `scripts/approval.py`
  - Verify: `test_callback_from_unauthorized_user_rejected`,
    `test_callback_for_resolved_approval_is_noop`, `test_double_tap_resolves_once`
- [ ] **T5 (P1, new)** — rate-limit handling: honor `retry_after` on 429,
  queue rather than drop a burst of pending cards after an outage.
  - Surfaced by: external review F9, Risk #8
  - Files: `plugins/telegram/router.py` (`push_pending_cards`)
  - Verify: `test_429_honours_retry_after` (fake API)
- [ ] **T6 (P1, new)** — startup safety: fail closed if a webhook is set on
  the token (prevents the rollback-blocked-by-leftover-webhook failure mode).
  - Surfaced by: external review F10, Risk #11
  - Files: `plugins/telegram/router.py` (startup/`run`)
  - Verify: `test_startup_fails_closed_if_webhook_set`
- [ ] **T7 (P2, new)** — heartbeat + observability: `last_successful_poll_at`
  metric, alert distinct from "gm idle" (supersedes the earlier, vaguer T4).
  - Surfaced by: external review §7 item 8, Risk #4
  - Files: to be determined
  - Verify: live drill (kill -9 mid-poll, confirm alert fires within N minutes)
- [ ] **T8 (P3, new)** — write the one-page channel contract document (§7's
  9 invariants, adapted) — not a code abstraction, a versioned doc any
  future transport must satisfy, per the review's own Round 3 synthesis.
  - Surfaced by: external review §7
  - Files: new, small — a doc, not code

**Phased rollout (adopted from the external review's §9, appropriate for
this file's size):**
```
Phase 0 (~half day, no behavior change): trace poll_once/handle_update's
  actual before/after ordering [DONE — §7.1, confirmed the bug]; document
  approval.py's exact authorization checks; confirm what supervises
  router.py and whether anything alerts on its death.
Phase 1: T1-REVISED, T3-REVISED, T5, T6, plus the brief's original 4 tests.
Phase 2: T7 (heartbeat/observability) + runbook.
Phase 3: decision gate for path (b)/Matrix, using §5's re-open criteria
  (unchanged from this brief's existing framing — the external review
  agrees defer is correct for both).
```

### 7.5 Correction — this brief's own test-coverage claim was wrong, and
the "never advance on failure" fix was incomplete (found by build,
implementing T1-REVISED, not caught by this seat or the external review)

**Two real errors, owned plainly, not glossed over — this section exists
because build checked build's own work against reality rather than
implementing this brief's fix literally, and caught what both this seat's
original eng review and the operator's external adversarial review missed:**

1. **"router.py has zero dedicated test coverage" (Eng Review §Test Review,
   external review's own framing) was wrong.** Verified directly, not taken
   on build's word: `plugins/telegram/tests/test_router.py` is real, 237
   lines, 17 existing tests — `git log --follow` confirms it shipped in the
   *original* commit that added the Telegram plugin (`cf323f2`), not
   something added recently. **This seat's original eng review searched
   `scripts/` (finding only `test_approval_notify_telegram.py`) and never
   checked for a `tests/` subdirectory inside `plugins/telegram/` itself —
   a real research gap, not a defensible judgment call.** The external
   review, working without repo access, correctly inherited this brief's
   own wrong claim [B] rather than a new error of its own.
2. **"Only advance the offset after an update's effect is durably
   committed" (§7.1/§7.4's T1-REVISED, this brief's own proposed fix,
   independently endorsed by the external review's F1/F2) is directly
   contradicted by an existing, deliberately-written test:**
   `test_a_bad_update_is_skipped_and_offset_still_advances` (verified, read
   directly, line 142) asserts a malformed update is skipped *and the
   offset still advances past it*. Implementing T1-REVISED's fix literally
   — never advance on any failure — breaks this test, and build's own
   analysis (relayed, this seat did not independently re-derive the
   distributed-systems argument but the conclusion is sound and the test
   itself is directly verifiable) explains why the test is *correct*, not
   stale: **retrying a permanently malformed update forever would
   head-of-line-block every real operator message behind it in the queue.**
   The correct fix splits two cases this brief's original framing
   collapsed into one — **transient failures** (network blip, a momentary
   `msg_store` write failure — never confirm, let Telegram redeliver) vs.
   **permanently malformed updates** (structurally broken, will never
   succeed on retry — confirm/step over immediately, log loudly, don't
   block the queue behind it). Both this seat's original P0 framing and the
   external review's F1/F2 treated this as one failure mode; it's two, with
   opposite correct handling.

**Resolution: build has already implemented and verified the corrected fix**
(commit `b834241`, `fix(telegram): stop silently dropping operator messages
on transient failures`) — split transient/permanent handling, per build's
own report: mutation-tested, plus a live `kill -9` drill confirming a
message survives a transient failure without duplication. **This
supersedes T1-REVISED above as written** — the confirmed P0 bug from §7.1
(the underlying finding: offset advances unconditionally, including on
failures that should retry) was real and is now fixed; the *specific fix
description* in T1-REVISED (undifferentiated "never advance on failure")
was incomplete and should not be implemented literally by anyone reading
this brief after this correction. See `docs/HANDOFF_build-next.md` for
build's own detailed account.

**What this means for T3-REVISED, T5, T6, T7, T8 above:** unaffected by
this specific correction — those address callback authorization, rate
limits, webhook safety, and observability, none of which depend on the
offset-advance framing that was wrong. Still open, still recommended as
written.

**Why this correction matters beyond the one bug — rewritten a second time,
because the first rewrite ("four layers, no single one catches everything")
was itself still understated, and counting layers turned out to be the
wrong frame entirely.** Two more commits landed after this section was
first written, each verified directly (commit + diff read in full, not
taken on build's word):

- `a062d57` (`fix(telegram): a failed attachment download no longer
  vanishes the message`) — `download_file()` swallows its own exception and
  returns `None`, so a photo-only message whose download failed hit
  `if not text: return` and was confirmed as a clean success. Found by the
  `review` seat auditing build's own implementation, not by build and not
  by this brief — build's commit message says so plainly.
- `649cb44` (`fix(telegram): an unrenderable message type no longer
  vanishes silently`, "F5") — `handle_message`'s attachment loop only
  enumerates photo/document/voice/video/audio; a message whose sole content
  is any other kind (sticker, GIF, round video, location, poll...) never
  even attempts a download, so there's no failure for the *previous* fix to
  record either — same `if not text: return`, same silent loss, a *third*
  distinct route to the same outcome in the same function. Found by
  `review` auditing the *previous* fix, not the original bug.

**The honest version of this lesson is not a number of layers — it's that
the defect class survived a declared "this is fixed" four separate times,
and what actually exhausted it was re-reviewing each fix, not re-reviewing
the original bug.** External review found the offset bug. This seat
confirmed it. Build's implementation caught an existing-test contradiction
the first two passes missed. Review, auditing that implementation, found a
second silent-loss route. Review, auditing *that* fix, found a third. Each
pass found something real by checking the pass before it, not by re-deriving
from the original finding — which is a different and more useful discipline
than "more review layers eventually catch everything."

**The transient-vs-permanent axis is not just generalizing across call
sites — it's the actual decision rule selecting between three different
correct answers to the same defect class:** the P0 fix retries transient
failures and never confirms until they succeed. The attachment-download fix
deliberately does *not* retry (a file over the Bot API's 20MB limit will
never succeed no matter how many attempts) — it confirms immediately and
delivers a flagged placeholder instead. The unrenderable-type fix has
*nothing to retry at all* (no download was ever attempted) — it's a pure
catch-all that names what arrived and confirms. Three routes, three
different right answers, all selected by one question: **can retrying this
specific failure ever succeed?** That's a stronger claim than "the axis
generalizes" — it's the axis doing real selection work across genuinely
different failure shapes, not just recurring.

**Implementation Tasks note:** both `a062d57` and `649cb44` are real,
review-cleared fixes (verified: `docs/HANDOFF_review-next.md` records both
clearances) — `649cb44` is not yet merged to `main` as of this writing
(confirmed via `git merge-base --is-ancestor`); cite the commit directly,
not a merge commit, if this brief is read before that lands. Neither is new
open scope for this brief; recorded as ground truth this brief's own
recommendations should be read against. **This brief will not chase further
instances of this defect class in real time** — build has said this is the
last one currently known, and this seat's own stated policy stands: flag
any future instance explicitly, don't expect it noticed unprompted.

### 7.6 What stays exactly as recommended before this review landed

Per the review's own bottom line: gm remains the only persona, `approval.py`
remains the system of record, no new runtime, no Hermes in the loop, no
Matrix. **Path (a+) is path (a), specified precisely and with one confirmed
bug fixed — not a different recommendation.** The open (a)/(b) decision in
§5 is unchanged; the review independently reaches the same "defer (b), defer
Matrix" conclusion this brief already had, for the same underlying reasons
(experimental contract, silent-degradation failure mode for approval cards,
autonomy/cost risk), which is real corroboration of the strategic call, not
just the technical details.

## Note on the review sections below

Sections 1-10 below were drafted before the outside-voice correction in §4a and
analyze the architecture/error-map/security implications of "a new relay
codepath in front of gm's msg_store inbox" — that analysis (approval-card
parity, the offset/missed-message CRITICAL GAP, dedicated-instance isolation,
audit-trail continuity) applies unchanged to **path (b)** if the operator
chooses it, since (b) is still fundamentally a new relay in front of gm. It does
**not** apply to path (a) (hardening `router.py` in place), which has no new
relay codepath at all — for (a), only the Section 2/6 offset-persistence finding
and its regression test are relevant; the rest of Sections 1-10 collapse to "no
material change." This review's mode is HOLD SCOPE either way, so nothing here
prescribes doing more than whichever path is picked, requires.

## CEO REVIEW — Step 0

**Review depth:** strategy-only (brief only, no implementation authorized per the
operator's task).

**Mode: HOLD SCOPE.** Auto-decided by plan (non-interactive T2 seat, no human
present to answer AskUserQuestion, per `prompts/plan.md`'s instruction to decide
toward the narrowest scope). Rationale: this is a transport-layer infra swap for
an existing capability (operator↔fleet messaging), not a new user-facing
feature — the mode heuristic's "fix/refactor → HOLD SCOPE" applies, and §4 of
the brief already recommends the conservative, non-destructive path (native
adapter first, Matrix deferred) rather than the more ambitious multi-protocol
build the operator's framing gestured at. Expanding scope here (e.g., prescribing
the Matrix build now) would be auto-expanding scope against plan.md's own
instruction not to.

**0A. Premise Challenge.** Real problem: the current bridge works today but is a
bespoke, single-purpose integration the operator wants folded into a more capable
runtime (Hermes) they're already operating elsewhere. Do-nothing cost: none
functionally — `router.py` is small, stable, and already carries the approval-card
mechanic. The actual driver is *consolidation* (fewer bespoke integrations to
maintain) and *optionality* (Hermes's broader tool/adapter surface), not a defect
in the current bridge. The brief solves this directly for the Telegram leg; it
does not solve it for "many networks," which was the other half of the operator's
framing — flagged explicitly in §5 as a separate, unresolved question.

**0B. Existing Code Leverage.** Reusable: Hermes's native `telegram` and `matrix`
platform adapters (verified, production-featured — see brief §2), so zero new
bridge code needs to be written for the direct-Telegram path. Not reusable
as-is: `scripts/approval.py`'s inline-button card flow is OrchestraOS-specific
and has no Hermes equivalent — this is the one piece of real engineering work
implied by the brief, not a rebuild candidate, an integration one.

**0C. Dream State Mapping.**
```
CURRENT STATE                       THIS BRIEF                          12-MONTH IDEAL
Bespoke stdlib Telegram   --->      Dedicated Hermes instance   --->    Operator's fleet-command
long-poll bridge in                 running Hermes's native              channel(s) run on the same
orchestraos, approval                Telegram adapter, approval          richer runtime the operator
cards via scripts/                   cards reimplemented on top,         already uses for other work,
approval.py, single                  old bridge kept disabled            multi-protocol only if a
channel (Telegram only).             as instant rollback.                real second-network need
                                                                          shows up (not speculative).
```
This brief moves toward the ideal on the "richer runtime" axis without
prejudging the "multi-protocol" axis, which the operator hasn't confirmed is
actually wanted yet (brief §5).

**0D. Approach decision.** No new approach choice required here — the brief
already frames the one real fork (direct-Hermes vs. Hermes+Matrix) as an open
question for the operator (§5), correctly left pending rather than decided
inside this review. The only decision this review itself makes is the review
**mode** (HOLD SCOPE, above), which is an admin/process choice, not a plan
decision.

**0G. HOLD SCOPE checks.**
1. *Complexity check* — accepted scope (Recommendation 1 in §4: direct Hermes
   adapter, no Matrix) touches an estimated 2-3 new files (new Hermes instance
   config/env, a card-relay integration) plus reuse of existing `approval.py`
   logic — under the 8-file/2-new-service threshold that would trigger a
   fewer-moving-parts challenge.
2. *Minimum changes* — the brief already proposes the minimum: no Matrix, no new
   bridge software, no schema changes. Deferrable-without-blocking: the Matrix
   layer (§3-4), explicitly deferred to its own future brief.
3. *Invariants* — the one hard invariant stated by gm's task and confirmed by
   reading `router.py`'s own docstring: the operator's ability to receive and
   act on approval cards must not regress. This is carried through Sections 1-2
   and the rollout plan below, not dropped.

**0I. Temporal Interrogation (sequencing, strategy-level).**
```
PHASE 1 (shadow):     stand up dedicated Hermes instance + native Telegram
                       adapter against a non-production bot/chat; prove
                       message parity and reimplemented approval-card
                       round-trip before touching production traffic.
PHASE 2 (cutover):    point the real bot token at Hermes; keep router.py +
                       its state on disk, disabled not deleted, for an
                       explicit burn-in window (instant rollback = config flip).
PHASE 3 (retire):     remove the old bridge only after burn-in shows no
                       regression in card delivery, latency, or missed-message
                       recovery (offset-tracking parity, unverified until
                       Phase 1 — see Section 2 below).
PHASE 4+ (optional):  Matrix multi-protocol layer, its own future brief,
                       gated on a confirmed second-network need.
```

---

## CEO REVIEW — Sections 1-10 (strategy-only depth)

### Section 1: Architecture Review

**Current scope:** HOLD SCOPE, Recommendation 1 only (direct Hermes-Telegram,
dedicated instance). No scope-expansion decisions were offered or accepted.

```
BEFORE                                   AFTER (Phase 2+)
operator <--Telegram Bot API--> router.py --msg_store--> gm
                                    |
                              approval.py <--buttons--> operator

operator <--Telegram Bot API--> Hermes (dedicated instance,
                                  native telegram adapter)
                                    |
                          [NEW] card-relay glue --msg_store--> gm
                                    |
                              approval.py <--buttons--> operator
                              (button semantics preserved, transport swapped)
```
- **Component boundaries:** Hermes becomes the sole Telegram transport; gm's
  msg_store inbox and `scripts/approval.py` stay the system of record — this
  is a transport swap, not an architecture change to gm/approval logic.
- **Coupling:** new coupling is OrchestraOS → a second, separately-versioned
  runtime (Hermes) it doesn't control the release cycle of. This is the
  architecturally material new risk — not called out explicitly enough in
  the brief's original draft. Justified only if the dedicated-instance
  isolation (brief §2, "de-enmeshment" finding) holds; a shared-instance
  design would additionally couple OrchestraOS's uptime to an unrelated
  business's (keonda) operational load.
- **SPOF:** identical to today — one bot token, one process. Moving that
  process into Hermes doesn't change the SPOF count, just which codebase
  owns it.
- **Security architecture:** no new auth boundary — same Bot API token model,
  same "operator's chat ID" allowlist concept. Card button taps still need to
  land through `approval.py`'s existing authorization, not a new path.
- **Production failure scenario:** Hermes instance crashes or its Telegram
  adapter loses connection — operator gets no messages and no cards. Identical
  blast radius to `router.py` crashing today; not worse, not better, unless
  Hermes's supervision/restart story is weaker than whatever currently restarts
  `router.py` under `orchestra up` — **unverified, flag for eng review.**
- **Rollback posture:** Phase 2's design (old bridge disabled-not-deleted) gives
  a same-minute rollback (flip config, restart). This is the correct posture
  given "operator's only channel" — no big-bang cutover without it.

**Decision gate:** no findings require a new decision; carried into Section 2
(the approval-card and offset-parity gaps) and flagged for eng review
(Hermes process supervision).

### Section 2: Error & Rescue Map (capability-level, strategy-only)

```
CAPABILITY                          | WHAT CAN GO WRONG                    | KNOWN SAFEGUARD TODAY
-------------------------------------|---------------------------------------|------------------------
Telegram Bot API long-poll/webhook   | Network blip, Telegram outage         | router.py never raises out
                                     |                                        | of its loop (verified,
                                     |                                        | docstring + code read)
Approval-card delivery               | Card fails to render/send             | none observed — unverified
Approval-card button tap → action    | Duplicate tap, stale card, tap after   | approval.py's existing
                                     | approval already resolved              | answer-once semantics
                                     |                                        | (not re-verified this pass)
Missed-message recovery (offset)     | Process restart mid-poll loses a       | router.py's `offset` file
                                     | message                                | (state/telegram/offset)
Hermes ↔ msg_store relay (NEW)       | Relay crashes between Hermes receiving | NONE YET — new codepath,
                                     | a message and it landing in gm's inbox | no equivalent exists today
```

| CAPABILITY | RESCUED? | RESCUE ACTION | USER SEES |
|---|---|---|---|
| Bot API long-poll | Y | loop continues, bad update logged+skipped | transparent (verified) |
| Approval-card delivery | UNKNOWN | not verified this pass | **owner: eng review, verify before Phase 2** |
| Button tap semantics | UNKNOWN (carried, not re-verified) | — | **owner: eng review, must match today's behavior exactly — this is the invariant from 0G/§3** |
| Offset/missed-message recovery | **GAP** — Hermes has no stated equivalent | none found in adapter.py read | **CRITICAL for Phase 1 exit**: silently-dropped operator messages during a Hermes restart would be a real regression from today's `offset` file. Owner: whoever runs Phase 1 must prove Hermes's Telegram adapter has update-offset persistence (`python-telegram-bot`'s own update_id tracking) before declaring parity. |
| Hermes↔msg_store relay (new) | GAP (nothing built yet) | must retry/queue, not drop | **CRITICAL — this is new code; its rescue behavior is entirely undesigned as of this brief.** Owner: eng review must design this, not just note it. |

**Decision gate:** two CRITICAL GAPs carried into the Failure Modes Registry and
Implementation Tasks below — both are exactly the kind of thing a strategy-only
brief should surface, not resolve.

### Section 3: Security & Threat Model

- **Attack surface:** none expands relative to today — same Bot API token,
  same "operator's chat only" trust model. The new relay codepath (Hermes →
  msg_store) is new *code*, not a new *trust boundary* — msg_store already
  accepts writes from other agents.
- **Secrets:** `TELEGRAM_BOT_TOKEN` moves from OrchestraOS's env to Hermes's
  env (`.hermes/config.yaml` / `.env`, per Hermes's `plugin.yaml` schema,
  verified). Same rotatability, same secret, new storage location — the
  Phase 1/2 rollout must ensure the token isn't sitting in both places
  readable-in-git at once (`.env.example` pattern in Hermes suggests it isn't
  committed, unverified for the actual runtime `.env`).
- **Isolation:** the dedicated-instance decision (§4/§2 of the brief) is
  itself a security-adjacent call — a shared keonda instance would mean
  OrchestraOS's fleet-command traffic and an unrelated business's WhatsApp
  traffic share one process's blast radius and one set of credentials on
  disk. Threat: Low likelihood, Medium impact (operational bleed, not data
  breach) — mitigated by the dedicated-instance recommendation, not by
  anything else in scope.
- **Audit logging:** approval-card actions are exactly the kind of sensitive
  operation (fleet actions get approved/declined) that need an audit trail —
  carried by existing `approval.py`, not new; verify it survives the
  transport swap unchanged (eng review).

**Decision gate:** no High-severity findings; Medium (shared-instance blast
radius) is already addressed by the brief's own recommendation, not left open.

### Section 4: Data Flow & Interaction Edge Cases

```
INPUT (Telegram msg) -> Hermes native telegram adapter -> [NEW relay] -> msg_store gm inbox -> gm processes -> reply/card -> Hermes -> Telegram
                                                              |
                             nil/empty: adapter's existing message-type gating (verified in plugin.yaml: mention/allowlist gating) — same behavior class as today
                             wrong type (photo/voice/doc): router.py downloads to <data>/state/uploads/telegram today — Hermes adapter's own media handling is UNVERIFIED to produce the same destination/format gm's downstream code expects
                             exception/timeout: relay crash — GAP, see Section 2
                             conflict/dup: two Hermes processes polling the same bot token would both consume updates (Telegram Bot API doesn't allow concurrent long-pollers cleanly) — must be single-instance, carried as a Phase-1 acceptance check, not assumed
```
**Interaction edge cases:**
| INTERACTION | EDGE CASE | HANDLED? | HOW? |
|---|---|---|---|
| Operator taps an approval-card button | Tap arrives after card already resolved elsewhere (e.g. dashboard) | Carried from today's `approval.py`, unverified post-swap | eng review must confirm the relay preserves exactly-once semantics |
| Operator sends a message mid-restart | Message sent while Hermes instance is restarting | **GAP** per Section 2's offset finding | must be resolved before Phase 1 exit, not deferred silently |
| Operator sends a photo/voice/document | Media path differs between adapters | UNVERIFIED | Phase 1 acceptance test, explicit |

**Decision gate:** findings carried to Test Review (Section 6) as required
Phase-1 acceptance checks, not new scope.

### Section 5: Code Quality Review

Not applicable in the traditional sense — no code is proposed or written by
this brief. The one code-quality-relevant call the brief itself makes:
prefer Hermes's existing native adapter (reuse) over standing up
`mautrix-telegram` (new infra for equivalent capability) — this **is** the
DRY/reuse judgment call Section 5 would otherwise be checking, and it's
already made correctly per §2-3 of the brief (verified: Hermes's `telegram`
adapter is Bot-API-based, architecturally identical to `router.py`'s own
approach, so no duplicate concept is being introduced).

### Section 6: Test Review (strategy-only)

Diagram of what a Phase 1 acceptance pass must cover, derived from Sections 2
and 4's gaps:
| ITEM | TEST TYPE | EXISTS? | HAPPY PATH | FAILURE PATH | EDGE CASE |
|---|---|---|---|---|---|
| Message parity (Hermes vs router.py) | Integration (manual/scripted, non-prod bot) | No — new | operator msg reaches gm inbox verbatim | Hermes restart mid-send | media (photo/voice/doc) round-trip |
| Approval-card round-trip | Integration | No — new | button tap resolves the approval | tap after resolution / stale card | two rapid taps (dedup) |
| Missed-message recovery | Integration | No — new | N/A | process killed mid-poll, then restarted | verify no message silently lost (the CRITICAL GAP from Section 2) |
| Rollback | Manual runbook, not automated | No — new | N/A | flip config back to router.py | confirm operator regains channel within the stated rollback time |

**Test ambition check:** the "ship at 2am Friday" test here is the
missed-message-recovery one — a silent drop on this specific channel means the
operator loses fleet control with no alarm, which is the single worst outcome
this migration could produce. That test is non-negotiable before Phase 2.

**Decision gate:** these become P1 Implementation Tasks below, not optional
follow-ups.

### Section 7: Performance Review

No new hot path, no new database, no new query pattern — this is a message
relay at human-conversation volume (one operator, occasional messages). No
N+1, indexing, or caching concerns apply. The only latency-relevant question
(add to Phase 1 checks, not a blocker): does routing through Hermes add
materially to operator-message → card-displayed latency versus today's direct
long-poll? Unverified, cheap to check empirically in Phase 1.

### Section 8: Observability & Debuggability Review

- **Logging:** `router.py` logs to stdout under supervisor capture today
  (verified in its docstring). Hermes's own logging conventions are
  unverified this pass — Phase 1 must confirm Hermes's Telegram-adapter logs
  are at least as debuggable (message received, card sent, button tap
  received) as today's, or debuggability regresses silently.
- **Alerting/runbook:** the new failure mode this migration introduces —
  "Hermes instance down, operator has no channel" — needs an explicit runbook
  entry and, ideally, a heartbeat/alert distinguishable from "gm is just idle."
  Nothing today alerts on `router.py` being down either (unverified this
  pass) — so this is a genuine opportunity to improve on the status quo, not
  strictly a new gap, but worth calling out since it's the operator's only
  channel.

**Decision gate:** runbook + heartbeat check added to Implementation Tasks as
P2 (valuable, not blocking Phase 1).

### Section 9: Deployment & Rollout Review

Already specified in 0I above (Phase 1 shadow → Phase 2 parallel-cutover with
instant rollback → Phase 3 retire → Phase 4+ optional Matrix). Restating the
rollout-specific checks:
- **Deploy-time risk window:** during Phase 2, only one of {router.py, Hermes}
  should hold the live bot token at a time — Section 4's "conflict/dup" edge
  case means running both against the same token concurrently is unsafe, not
  just redundant.
- **Rollback plan, explicit:** disable Hermes's Telegram platform, re-enable
  `router.py` (its process + state files kept intact, undeleted, per the
  brief's Phase 2 design) — same-minute, config-level, not a rebuild.
- **Post-deploy verification (first hour):** send a test message, confirm
  gm inbox receipt; trigger a real approval card, confirm button tap resolves
  it; kill and restart the Hermes instance, confirm no message is lost
  (the Section 2 CRITICAL GAP, verified in production-like conditions before
  calling Phase 2 done).

### Section 10: Long-Term Trajectory Review

- **Technical debt introduced:** minimal if Recommendation 1 is followed as
  scoped — the "new" code is a thin relay, not a bridge reimplementation.
  Debt risk is entirely in the deferred Matrix path if it's ever bolted on
  without its own review (explicitly guarded against in brief §4).
- **Reversibility: 4/5.** Not a 5 only because once the operator's muscle
  memory and any Telegram-side bot config (webhooks, bot settings) point at
  Hermes, reverting has a small non-zero coordination cost even though the
  code-level rollback is same-minute.
- **Ecosystem fit:** matches the operator's own already-stated architectural
  preference (de-enmeshment of separate deliverables, found verbatim in
  Hermes's own config — brief §2) — this recommendation is *more* consistent
  with the operator's existing conventions than the alternative
  (shared-instance) would have been.
- **1-year question:** yes, obvious to a future engineer — "OrchestraOS's
  Telegram channel runs through its own dedicated Hermes instance" is a
  simpler mental model than today's bespoke stdlib bridge, provided the
  Section 2 gaps get closed rather than silently shipped.

### Section 11: Design & UX Review

**SKIPPED (no UI scope)** — this is a backend transport/infra change; no
screens, components, or user-visible interaction surfaces beyond the
already-existing Telegram chat and approval-card UI, which are explicitly
required to stay behaviorally unchanged (the whole point of Section 1-4's
findings).

---

## NOT in scope

- **Matrix-based multi-protocol bridging** (mautrix-telegram, reviving the
  dormant Synapse stack) — deferred per brief §4, own future brief, gated on
  a confirmed second-network need from the operator. Rationale: HOLD SCOPE
  mode; not evidenced as needed yet; adding it now would be scope expansion
  against plan.md's non-interactive-decision instruction.
- **Shared Hermes instance (profile on the existing keonda gateway)** —
  rejected per brief §2/§4, matching the operator's own prior de-enmeshment
  decision found in Hermes's config. Not a TODO; a rejected alternative.

## What already exists

- Hermes's native `telegram` and `matrix` platform adapters (production-
  featured, verified by direct file read — brief §2).
- `scripts/approval.py`'s inline-button card mechanic and its integration
  with the dashboard/watch (reused, not rebuilt).
- `router.py`'s offset/missed-message tracking (the bar the new relay must
  clear, not something to reinvent from scratch — Hermes's own
  `python-telegram-bot`-based update tracking should be checked first before
  writing anything custom).

## Dream state delta

Per 0C: this brief, if Recommendation 1 ships cleanly, gets OrchestraOS to
"richer runtime, same channel count" — it does not yet get to "one channel,
many networks," which requires the operator's confirmation (brief §5) before
it's even in scope.

## Error & Rescue Registry

See Section 2 above — capability-level, two CRITICAL GAPs (offset/missed-
message parity, and the undesigned Hermes→msg_store relay rescue behavior).

## Failure Modes Registry

| CODEPATH (capability) | FAILURE MODE | RESCUED? | TEST? | USER SEES | LOGGED? |
|---|---|---|---|---|---|
| Hermes↔msg_store relay (new) | Relay crashes mid-delivery | N — GAP | N | Silent message loss | Unknown |
| Missed-message recovery | Restart mid-poll | N — GAP (Hermes's own offset behavior unverified) | N | Silent message loss | Unknown |
| Approval-card delivery post-swap | Card fails to send | UNKNOWN (carried) | N | Operator can't approve a pending action | Unknown |

Both GAP rows are **CRITICAL GAP** by the section's own rule (RESCUED=N,
TEST=N, USER SEES=Silent) — carried as P1 tasks below, not shipped silently.

## Implementation Tasks

_Strategy-only: these name the next research/verification action and owner,
not implementation contracts — per this review's chosen depth._

- [ ] **T1 (P1, human: ~1h / CC: ~10min)** — Hermes telegram adapter — verify
  update-offset/missed-message persistence exists and survives a process
  restart without dropping messages
  - Surfaced by: Section 2 (CRITICAL GAP), Section 6 (test plan)
  - Files: `/Users/flybyflow/.hermes/hermes-agent/plugins/platforms/telegram/adapter.py`
  - Verify: kill -9 the Hermes process mid-poll with a message in flight;
    confirm it's not lost on restart
- [ ] **T2 (P1, human: ~2h / CC: ~20min)** — Hermes→msg_store relay — design
  its rescue behavior (retry/queue, not drop) before writing it
  - Surfaced by: Section 2 (CRITICAL GAP), Section 4 (data flow)
  - Files: to be determined (new relay glue, owner TBD by eng review)
  - Verify: kill the relay process mid-delivery of a queued message; confirm
    it's retried, not dropped
- [ ] **T3 (P1, human: ~30min / CC: ~5min)** — approval-card round-trip
  parity check under the new transport
  - Surfaced by: Section 1, Section 3 (audit trail), Section 6
  - Files: `scripts/approval.py`
  - Verify: trigger a real card through the new path, confirm button tap
    resolves it exactly as today, including dedup on double-tap
- [ ] **T4 (P2, human: ~1h / CC: ~10min)** — runbook + heartbeat for "Hermes
  instance down" as a distinct alert from "gm idle"
  - Surfaced by: Section 8
  - Files: to be determined
  - Verify: manual runbook walkthrough
- _No new tasks from Section 5, 7, 9, 10, 11 — no findings requiring
  implementation action beyond what's captured above._

### Completion Summary

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
+====================================================================+
| Mode selected        | HOLD SCOPE                                  |
| System Audit         | No design doc / handoff for this task (gm   |
|                       | dispatched directly); unrelated 156-file    |
|                       | diff on this branch, not part of this brief |
| Step 0               | HOLD SCOPE, strategy-only depth, no scope   |
|                       | expansion offered or accepted               |
| Section 1  (Arch)    | 0 issues found (1 risk flagged: Hermes      |
|                       | process supervision, unverified)            |
| Section 2  (Errors)  | 5 capabilities mapped, 2 GAPS               |
| Section 3  (Security)| 1 issue found (Medium: shared-instance blast|
|                       | radius — already mitigated by the brief)    |
| Section 4  (Data/UX) | 5 edge cases mapped, 2 unverified (not      |
|                       | unhandled — flagged for Phase-1 proof)      |
| Section 5  (Quality) | N/A — no code in this brief; reuse judgment |
|                       | already correctly made                      |
| Section 6  (Tests)   | Diagram produced, 4 gaps (all new, expected)|
| Section 7  (Perf)    | 0 issues found                              |
| Section 8  (Observ)  | 1 gap found (Hermes-down alerting)          |
| Section 9  (Deploy)  | 1 risk flagged (dual-live-token window)     |
| Section 10 (Future)  | Reversibility: 4/5, debt items: 0 (0 if     |
|                       | Matrix stays deferred)                      |
| Section 11 (Design)  | SKIPPED (no UI scope)                       |
+--------------------------------------------------------------------+
| NOT in scope          | written (2 items)                          |
| What already exists   | written                                    |
| Dream state delta     | written                                    |
| Error/rescue registry | 5 rows, 2 CRITICAL GAPS                    |
| Failure modes         | 3 total, 2 CRITICAL GAPS                   |
| TODOS.md updates      | 0 items (all findings carried as tasks     |
|                        | above, not deferred)                       |
| Scope proposals       | 0 proposed (HOLD SCOPE)                    |
| CEO plan              | skipped by mode (HOLD SCOPE doesn't write  |
|                        | the 0H CEO archive)                        |
| Outside voice          | native Claude subagent (Plan) — see below  |
| Lake Score            | N/A — no coverage-scored questions asked   |
| Diagrams produced      | 6 (architecture, data flow, rollout        |
|                        | phasing, dream-state, deployment sequence, |
|                        | rollback via disable/re-enable)            |
| Stale diagrams found   | 0 (no prior diagrams in this doc)          |
| Unresolved decisions   | 0 NEW AskUserQuestion-style gates raised by |
|                        | this CEO review process itself — NOT the   |
|                        | same as brief §5's operator-facing open    |
|                        | questions (several, by design; see §5 and  |
|                        | the terminal report's own unresolved list  |
|                        | below — external review F16 flagged this   |
|                        | line as confusingly worded, fixed here)    |
+====================================================================+
```

## ENG REVIEW

**Scope:** primary accepted scope = **path (a)**, hardening `plugins/telegram/router.py`
in place (§4.2a) — this is what CEO review recommends shipping now. **Path (b)**
(Hermes relay-connector build) is reviewed at strategic/feasibility level only,
per this task's explicit instruction — it is not yet authorized scope.

### Scope Challenge

**A. What already solves it.** Read `plugins/telegram/router.py` in full
(369 lines). Its `State` class (lines 90-127) already persists `offset` via an
atomic tmp-file + `os.replace` write (lines 101-112) — this is crash-safe,
standard durable-write practice, not a naive in-memory counter. **This
materially changes the CEO review's Section 2 "CRITICAL GAP" framing**: that
gap was about whether a *new* Hermes-based relay would have equivalent
persistence — it was never a real gap in `router.py` itself. Verified, not
assumed: `poll_once()` (line 310) reads `self.state.offset`, calls
`getUpdates`, and sets `self.state.offset = int(upd["update_id"]) + 1` (line
323) as it processes each update — a crash between two updates loses at most
the update(s) not yet durably offset-advanced, same as any at-least-once
long-poll consumer, and re-fetches from the last durable offset on restart.
Correcting the record: **path (a) requires no new persistence work.**

**Complexity check.** Path (a)'s scope: 0 new files, 0 new classes/services —
add tests, and one narrow fix if the confidence-calibration read below finds a
real gap. Well under the 8-file/2-service threshold. **Skip Scope Challenge B**
(complexity selectors) — go directly to C.

**C. Findings.**
> **WRONG — corrected in §7.5, read that before trusting this finding.**
> This seat searched `scripts/` and missed `plugins/telegram/tests/
> test_router.py`, a real, 17-test file that shipped with router.py's
> original commit. Left here verbatim as the historical record of what
> this pass actually found (and got wrong), not edited in place.
1. `[P3]` (confidence: 6/10) `plugins/telegram/router.py` has zero dedicated
   test coverage — `scripts/test_approval_notify_telegram.py` exists but tests
   the approval-notify integration, not `Router`/`State`/`poll_once` directly
   (verified: grepped the test file's imports, it does not import
   `plugins.telegram.router`). For code that is the operator's only channel to
   the fleet, this is worth closing regardless of whether Hermes ever enters
   the picture — carried to Test Review below as the actual remedy, not a
   Matrix/Hermes-motivated gap.
2. No remedies from Section-2's original CRITICAL GAP survive this section's
   correction above — disposition: **resolved by re-reading the code**, not a
   pending choice; no AskUserQuestion needed (this is a factual correction
   under Decision procedure step 1, which explicitly allows recording a
   correction without a question when it changes no behavior).

Both findings above are informational/corrective, not remedies requiring a
pending decision — continuing directly to Section 1.

### Section 1: Architecture Review

* **Path (a):** no architecture change. `router.py` stays the sole Telegram
  transport; no new component, no new coupling, no new SPOF beyond what
  exists today. Nothing to diagram beyond what's already in CEO review
  Section 1 (unchanged by this correction, since that diagram already showed
  today's architecture as the baseline).
* **Path (b), strategic-only:** confirmed via `docs/relay-connector-contract.md`
  (read in the CEO review's §4a correction) that this is a **client-server
  architecture across a process/language boundary** (Python gateway ↔
  Node/TypeScript connector, WebSocket transport) — a materially different
  shape than "add a plugin." Any future eng review that takes path (b) to
  implementation-ready depth must treat it as a new service with its own
  deploy/supervision/failure story, not an extension of `router.py`'s. Not
  gating this review; flagged for whoever scopes it next.

### Section 2: Code Quality Review

Not applicable at meaningful depth for path (a) — no new code is in scope
beyond a possible test file and, if the confidence-calibration probe below
finds one, a narrow fix. `router.py` itself (read in full): stdlib-only,
single-file, dependency-injected seams (`api`, `deliver`, `answer`, `pending`,
`state`, `fetch` all injectable in `Router.__init__`, lines 173-186) —
already structured for testability, which is exactly why Finding 1 above (no
tests written against that seam) is the real gap, not the code's design.

### Section 3: Test Review

**Framework detection:** `pytest.ini` present at repo root (confirmed by
`ls` in the earlier CEO-review system audit) → pytest, matching the
`test_approval_notify_telegram.py` precedent already in `scripts/`.

**Coverage diagram** (path (a) scope only):
```
CODE PATHS                                          
[+] plugins/telegram/router.py
  ├── State.offset (get/set)          [GAP]  atomic write verified by code
  │                                           read (line 101-112), no test
  ├── Router.poll_once()               [GAP]  no test exercises update_id
  │                                           advancement or a getUpdates
  │                                           failure mid-poll
  ├── Router.handle_message()          [GAP]  no test for media download
  │                                           path (download_file, line 68)
  ├── Router.handle_callback()         [GAP]  no test for parse_callback's
  │                                           malformed-data branch (returns
  │                                           None, line 165-169 — silently
  │                                           ignored upstream, unverified
  │                                           this pass whether that's correct)
  └── push_pending_cards/replies       [GAP]  no test — this is the approval-
                                               card delivery path Section 1/2
                                               of the CEO review flagged as
                                               "UNKNOWN, unverified"

COVERAGE: 0/5 paths tested (0%) for router.py directly (the adjacent
approval-notify integration test does not exercise this module)
QUALITY: GAPS: 5
```

**REGRESSION RULE applies:** this is exactly the case the mandatory regression
rule is for — `router.py` is existing, working, sole-channel code with zero
direct test coverage. Recommend closing this gap regardless of which path
(a/b) the operator eventually picks for the "richer runtime" half of the
original ask, since it protects the fallback/rollback path either way (CEO
review's Phase 2 rollback posture depends on `router.py` still working
correctly when re-enabled).

**Proposed tests (unit, pytest, using the existing injectable seams — no new
test framework needed):**
- `test_state_offset_persists_across_instances` — write via one `State`
  instance, read via a fresh one pointed at the same dir; asserts the
  atomic-write path actually round-trips (protects the fact this review just
  used to correct the CEO review's CRITICAL GAP — don't let that correction
  rest on an untested assumption).
- `test_poll_once_advances_offset_only_after_processing` — **now a P0 fix
  verification, not a speculative test: §7.1 confirmed `poll_once` currently
  does NOT skip the offset advance on a failed update.** This test should
  assert the *fixed* behavior (offset does not pass a failed update) and
  will fail against the current code until T1-REVISED (§7.4) lands.
- `test_handle_callback_malformed_data_is_ignored_not_crashed` — feeds
  `parse_callback` inputs that fail its 3-part/`"a"`-prefix check; asserts
  `handle_callback` doesn't raise.
- `test_push_pending_cards_delivers_and_marks_notified` — injected fake
  `pending`/`api`; asserts a card is sent once and `notified()` records it
  (protects the approval-card path CEO review flagged as unverified).

**RESOLVED (§7.1, post-review addendum, operator's external adversarial
review prompted a re-check that this seat then confirmed directly against
the code):** `poll_once` (lines 310-324), not `handle_update` (this seat's
original line citation was off by one call frame) — `self.state.offset =
int(upd["update_id"]) + 1` sits **outside** the `try`/`except` wrapping
`self.handle_update(upd)` and runs unconditionally. **Confirmed: yes, a
single failing update's offset advances anyway, and Telegram will not
redeliver it — this is real, current, silent message loss, not a
hypothetical.** No longer an open verification item; now a confirmed P0 defect
with a concrete fix (§7.1, §7.4 T1-REVISED). This was in fact "the actual
highest-value verification for path (a)," as this section originally
guessed — the guess was right, and it's now proven, not just recommended
as a next step.

### Section 4: Performance Review

No findings. Single-operator message volume, no new hot path, no database.

### Outside Voice

**Not re-run for this eng-review pass.** An adversarial outside-voice review
(native Claude subagent, read-only) already ran once against this exact
document during CEO review (see GSTACK REVIEW REPORT above) and materially
changed the plan — its findings are integrated into §4/§4a and carried into
this eng review's Scope Challenge and Section 1. Running a second full
outside pass minutes later against largely the same content would be
redundant cost without new signal; noting this as a deliberate efficiency
judgment call by this non-interactive seat, not a skipped step. Coverage:
`outside_status: reused-from-ceo-review` (not a fresh run, not "unavailable").

### Eng Review Completion Summary

```
Section 1 (Architecture)  | 0 issues, 1 note (path (b) shape, strategic only)
Section 2 (Code Quality)  | 0 issues — N/A, no new code in path (a) scope
Section 3 (Tests)         | 5 GAPS (0/5 coverage on router.py directly),
                           | 4 tests proposed, 1 open verification item
                           | (error-mid-batch offset/deliver semantics)
Section 4 (Performance)   | 0 issues found
Outside Voice             | reused from CEO review (same document, same pass)
```

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open | HOLD SCOPE; original CRITICAL GAP (offset/missed-message parity) later corrected by eng review — see below; outside-voice overturned Recommendation 1's risk framing |
| Outside Review | native Claude subagent (Plan, read-only, dispatched via Agent tool — Codex CLI present but not probed/authenticated in this non-interactive seat) | Independent 2nd opinion | 1 | completed, issues_found | 6 findings; 5 integrated and resolved by revising §4/§4a/§5; 1 (Elon/Shaw rationale) already carried as an open operator question, unchanged |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 2 | issues_open | Run 1: path (a) scoped, CEO's CRITICAL GAP corrected, **falsely claimed 0/5 test coverage** (real gap: this seat searched `scripts/` and missed `plugins/telegram/tests/test_router.py`, 17 existing tests — corrected §7.5) + 1 open verification item. **Run 2 (§7): resolved the open item by direct code read — confirmed a real P0 bug, but the proposed fix ("never advance offset on failure") directly contradicted an existing test and was incomplete — build caught this implementing it (§7.5), correct fix splits transient-vs-permanently-malformed failures.** Recommendation refined to "path (a+)"; build's actual fix (commit `b834241`) supersedes this brief's own fix description |
| External Adversarial Review | operator-sourced PDF, transcribed to `docs/REVIEW_hermes-matrix-telegram-bridge-external.md` | Independent, real-citations technical review (Telegram Bot API, Hermes docs, Matrix spec, mautrix release notes) | 1 | completed, issues_found, reconciled | Endorsed path (a), refined to "path (a+)"; 16 numbered findings, 11-item risk register, reconciled in §7 — this seat independently verified the headline finding (confirmed the bug directly) rather than accepting on citation strength alone; no findings rejected, one scope concern considered and judged appropriate, not excessive (§7.3). **Inherited this brief's own wrong 0-coverage claim [B] without repo access to catch it — not this review's error, but worth noting its finding was only as good as this brief's own facts (§7.5).** |
| Build + Review (implementation + audit chain) | commits `b834241`, `a062d57`, `649cb44` + `docs/HANDOFF_build-next.md` / `docs/HANDOFF_review-next.md` | Ground truth — implemented, then audited, then audited again | 3 fixes / 2 review-of-implementation passes | completed, corrections found each pass | Fixed the confirmed P0 bug (`b834241`, mutation-tested + live kill-9 drill), then two more instances of the same silent-loss defect class found by `review` auditing each successive fix (`a062d57` — failed attachment download; `649cb44` — unrenderable message type), plus the false 0/5-coverage claim in this brief and the external review. See §7.5 — the honest lesson is not a layer count, it's that re-reviewing each fix (not the original bug) is what closed the class. |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | skipped (no UI scope) | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not requested | — |

- **OUTSIDE COVERAGE:** provider = native Claude subagent (Plan subagent type,
  read-only, dispatched in-host via the Agent tool), phase = plan-review,
  completion state = completed. Not an external-CLI (Codex) review — `codex` is
  installed on this machine but its auth/model-usability was not probed this
  session, so the in-host native fallback was used directly rather than routing
  through the full Codex preflight. This is native coverage, not outside-model
  coverage in the strict cross-model sense; treat findings as a second
  independent pass within the same harness family, not a different model's
  opinion.
- **CROSS-MODEL:** not applicable — no completed external-provider review to
  compare against; skip per the skill's own rule for native-fallback-only runs.
- **VERDICT:** CEO + ENG REVIEW + EXTERNAL ADVERSARIAL REVIEW + BUILD
  IMPLEMENTATION + REVIEW-OF-IMPLEMENTATION COMPLETE, RECONCILED INTO
  "PATH (A+)" — corrections landed at every stage of this brief's lifecycle,
  each caught by verifying the stage before it, not trusting it: outside-
  voice caught a wrong risk assessment in the original draft (§4a); eng
  review caught that CEO review's own CRITICAL GAP was itself overstated;
  the external adversarial review correctly predicted, and this seat's
  direct code read confirmed, a real P0 message-loss bug in `router.py`;
  build's implementation caught that eng review and the external review
  were both wrong about test coverage and that the proposed fix was
  incomplete; and `review`, auditing build's own fix, found two *further*
  independent instances of the same silent-loss defect class in the same
  function (§7.5) — each one found by checking the fix before it, not by
  re-deriving from the original bug. Every correction is now integrated.
  The underlying strategic recommendation has not moved once across any of
  these passes: harden the existing bridge, defer Hermes and Matrix.
  **The honest lesson (§7.5) is not "how many layers" — it's that this
  defect class survived a declared "fixed" four separate times, and what
  actually exhausted it was re-reviewing each fix, not re-reviewing the
  original finding.** Ready for the operator's one load-bearing decision
  (§5, path a vs. b). **Not** a green light to build path (b) — path (a)'s
  known instances of this defect class are fixed and review-cleared
  (`b834241`, `a062d57`, `649cb44` — the last not yet merged to `main`,
  §7.5); T3-REVISED/T5/T6/T7/T8 (§7.4) remain open, unaffected.

**UNRESOLVED DECISIONS:**
- Path (a) vs. path (b) for the Telegram leg — operator decision, §4.2/§5, not
  decidable by this non-interactive review (this is exactly the kind of
  material, non-narrowest-scope fork plan.md instructs this seat not to
  self-decide; it changes the entire engineering scope downstream).
- Dedicated-instance vs. shared-gateway topology — only live if path (b) is
  chosen (§5). **New data point (2026-09-29, via the Silicon Jungle strategic
  brief's own addendum):** the shared keonda gateway already runs a live,
  `observe_only` WhatsApp presence for a real Silicon Jungle event — real
  precedent for staying on a shared gateway in restricted mode, which the
  operator may want to weigh against this brief's dedicated-instance
  recommendation for OrchestraOS's own channel specifically. See
  `docs/PLAN_silicon-jungle-agentic-platform.md` §0b/§6.
- ~~The "Elon orchestration layer" reference~~ — **RESOLVED** (§2, §5: xAI's
  Grok Bot, operator-sourced, verified).
- ~~Who is "Shaw"~~ — **RESOLVED** (§2, §5: Shaw Cole, real co-founder and
  confirmed OrchestraOS author, verified via Silicon Jungle brief §0c). The
  **Hermes/NousResearch mismatch is still open, now on firmer footing** —
  Shaw's confirmed project is ListMagic, not Hermes; Hermes remains
  NousResearch's — worth the operator having this precisely, given Shaw wrote
  this fleet's own codebase (§2, §5).
- Whether the operator has a concrete second network in mind for Matrix, or
  whether Matrix was long-term direction only (§5).
- **(Added per §7.2, external review F12)** Whether Telegram is genuinely the
  operator's only channel or one of three (with dashboard/watch) — changes
  the real severity of every failure mode discussed (§5, §7.2).
- **(Added per §7.2, external review F7)** Whether the operator still wants
  path (b) with the precise understanding that it means gm implements the
  gateway side of Hermes's relay contract, not "use the Hermes agent" (§5,
  §7.2).
- **(Not blocking, editorial only)** D4/D5/D6 in the external review's own
  §8 (severity model, edit/delete semantics, isolation topology if (b) ever
  happens) — largely already covered by this brief's existing open questions
  above; flagged for completeness, not new substance.
- + 1 unresolved from the prior CEO+Eng review pass, now resolved — the
  offset/commit-ordering verification item (§7.1) is fixed and verified by
  build (commit `b834241`), not by the fix description this brief itself
  proposed in §7.4, which §7.5 found was incomplete.
