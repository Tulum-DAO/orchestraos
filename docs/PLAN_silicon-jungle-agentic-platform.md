# Strategic Brief: Silicon Jungle as an Agentic Product Lab Platform

- **Requested by:** operator, via gm (msg_6568c41b_74190686 + msg_0d4c2eea_74781096
  ground-truth confirmation + msg_6a2b5afd_74610984 domains/partners/event
  addendum, 2026-09-29), as an addendum to the Hermes/Matrix Telegram bridge brief
- **Authored by:** plan (BSHR research + brief, non-interactive)
- **Status:** CEO + ENG REVIEWED, CLEARED for accepted (narrow) scope — awaiting
  the operator's answer to §6's load-bearing question before acting. **§0b
  (below) is a post-review addendum** folded in after CEO+eng review completed —
  reinforces the accepted recommendation with real operational precedent found
  during verification; does not change the reviewed scope or verdict. Coherence
  checked against §2-§6, no contradiction found.
- **Scope:** strategic brief only. No implementation authorized. Kept as a
  **separate document from** `docs/PLAN_hermes-matrix-telegram-bridge.md`, which
  it references — per gm's explicit instruction not to let the bridge brief
  balloon into a full platform redesign without flagging the split back to gm.
  **That split is the first thing this brief flags:** these are two different
  sizes of decision, and treating the bridge swap as "step one" of this platform
  vision would be a mistake — see §5.

## 0a. Ground truth confirmed after this brief's first draft (gm addendum 3)

gm relayed the operator-confirmed live URL `http://sje.ploy.build`. **Fetched
and verified directly** (not taken on gm's summary alone), matching gm's
relay with added detail:

- **Project:** Silicon Jungle Experience (SJE) — "Where founders come to
  rumble." An industry-expert venture studio matching operators (existing
  customer relationships) with technical talent to build+launch real products.
- **The Weekender** (the entry program): 2 days, Friday–Sunday, at **La
  Reserva Tulum** (a jungle property near Aldea Zama — 62-ft pool, firepit,
  library pavilion, organic restaurant, co-working space). Cross-domain teams
  form Saturday morning, build all day, demo by Sunday 5pm — deliberately
  mixed outside each member's own expertise.
- **Two application funnels, confirmed exact fields:** **Door 1 (The
  Weekender)** — name, email, industry/expertise, customer relationships,
  current/desired projects. **Door 2 (Direct Track / "Skip Ahead")** — name,
  email, company/market, desired collaboration scope, optional
  website/LinkedIn.
- **Confirmed progression path, with names:** 48 hours → **The Weekender**;
  2-6 weeks → **The Experience** (focused MVP sprint); 16 weeks → **The
  Incubator** (cross-domain pods building the company together). This is the
  literal, already-productized on-ramp into "Silicon Ventures" from addendum 1
  — not a stated intention, a real named program with a real page live today.
- **Team listed on the page:** Mo Sersouri (Technical Lead, AI Systems — this
  is the operator), Stephan Mai (Senior Fullstack/DevOps, Tulum-based),
  Richard Rygg (Product Strategy/Systems Design). No pricing or dates listed.

**This changes how confidently this brief can talk about "the funnel"**: it is
not speculative product strategy, it's a live, named, three-stage program
(Weekender → Experience → Incubator) that already exists as a public page.
The Hermes/Matrix bridge + WhatsApp-auth RBAC work (§4.2 below) is explicitly
the connective tissue that lets Door 1/Door 2 applicants actually interact
with the agent fleet once accepted — per gm's own framing of the close.

## 0b. Second ground-truth addendum (post-review, gm addendum 2) — domains,
partners, reference event, and a real operational precedent found by mistake

gm relayed a second addendum (sent *before* addendum 3 chronologically, but
delivered to this seat after CEO+eng review had already completed — folded in
here rather than re-running the full review, per the coherence check noted at
the top of this file). Three items independently verified, one item corrects
gm's own flagged uncertainty, and one is a genuinely significant finding this
seat connected from research already done for the bridge brief.

**Domain resolution (gm flagged this as uncertain — checked directly, both
claims turn out true simultaneously, not contradictory):**
- `siliconjungle.io` — **verified via `dig`**: has active MX records (email
  forwarding through `registrar-servers.com`) but **no A/AAAA records** — a
  real, owned, email-configured domain with no website currently hosted there.
- `sje.ploy.build` — **reconfirmed live** (already fetched directly in §0a;
  gm's speculation that this might be a garbled deploy-preview URL doesn't
  hold — it's the real, current landing page, consistent across two
  independent fetches).
- **Read together:** `siliconjungle.io` is the reserved brand domain (email
  live, no site); `sje.ploy.build` is where the actual product/funnel lives
  today. Not a discrepancy to resolve — just two different pieces of the same
  real setup.

**Partners and reference event (relayed by gm as already fetched/verified —
not independently re-fetched by this seat, cited as gm's finding):** Tulum
Co-Working is a live collaborator (hosted the reference "BUILD-A-THON" event
at KAN Tulum, operator as partner); Tulum Dinner Club is the next planned
collaboration, explicitly named as the pilot venue for "Keonda" (the
community-relationship-graph product). The BUILD-A-THON format — Day 1 team
formation + building, Day 2 demos/feedback, on-site meals, office hours — maps
almost exactly onto SJE's own confirmed Weekender format (§0a), reinforcing
that the funnel's format is proven in practice, not just described on a
landing page.

**Operator's stated thesis (verbatim, relayed by gm, directly shapes
prioritization):** *"our current thesis is that the tech moat is gone and
we're doing a big short on technology to go long on relationships first which
gets us attention and distribution, especially in Tulum — the land of
influencers."* This reframes §3/§4's isolation-vs-facilitation sequencing
question usefully: if relationships/distribution are the actual current bet,
not technology, that's a real argument *for* this brief's "don't over-build
the isolation layer yet" recommendation, not just a security-driven one — the
operator's own stated strategy agrees with staying lean on infrastructure
right now, independent of this brief's technical risk findings.

**The significant finding: the operator has already piloted a version of
exactly what this brief recommends, on the existing shared Keonda gateway —
found by re-checking research already done for the bridge brief, not newly
fetched.** `/Users/flybyflow/.hermes/config.yaml` (read directly, lines
47-48 and 66-76) shows:
```yaml
observe_only:
  - 120363427836751184@g.us   # BUILD-A-THON.v8 — observe + operator-broadcast
                               #   ONLY, never auto-reply
profile_routes:
  - name: silicon-jungle-buildathon
    platform: whatsapp
    chat_id: 120363427836751184@g.us
    profile: sj-fac
    enabled: true
```
The **same WhatsApp group chat** is both explicitly `observe_only` (no
autonomous agent replies) and has a live, `enabled: true` profile route named
`silicon-jungle-buildathon`. **This is real, live, already-running precedent**
for a cautious, non-agentic "watch and let the operator broadcast" posture on
Silicon Jungle's own event community — independently arrived at by the
operator before this brief was ever requested. Two implications for §4:
1. **Directly validates the "start cautious, human-facilitated, no
   unsupervised agent access" sequencing** this brief already recommended —
   this isn't a novel idea this brief is introducing, it's consistent with
   what the operator already does in practice.
2. **A genuine open question this brief hadn't surfaced before:** this
   precedent runs on the *shared* keonda Hermes instance (the same one the
   bridge brief's de-enmeshment finding argued against reusing for
   OrchestraOS's *own* operator channel). Whether Silicon Jungle's WhatsApp
   presence should stay on the shared gateway indefinitely (in this same
   restricted mode), or eventually get the same "dedicated instance"
   treatment the bridge brief recommends for the operator's channel, is a
   real, undecided architecture question — added to §6 below, not decided
   here.

## 0. Source material used (checked before finalizing, per gm's instruction)

Checked for a new operator-authored file under `docs/` or elsewhere before
writing this — none found (searched files modified in the last 2 hours across
the home directory and repo; nothing new). Found and read two existing operator
documents instead, both in `~/Downloads/`, dated 2026-09-06 through 2026-09-16
(i.e. written before this task, not the "will drop a doc" follow-up gm
mentioned — if that lands later, fold it in as an update to this brief):
- `silicon-jungle-north-star-v8.md` (the most recent version found, superseding
  v4/v5) — the full internal North Star: mission, "Regenerative Community
  Organism" structure (non-profit Circle + petal companies), the Embassy
  (physical Tulum venue, 84-unit capacity, currently ~20% full), the build
  weekend program, four protected values (trust the spark / tell the truth
  warmly / do ordinary things extraordinarily well / own what's yours build for
  something bigger).
- `silicon-jungle-brief.md.pdf` — a shorter, externally-shareable version of the
  same content, no additional technical detail.

**Neither document mentions OrchestraOS, agent fleets, RBAC, or the specific
technical mechanism gm's message describes** (external builders getting scoped
access to the internal planning/engineering process). That technical framing is
new, sourced from gm's cleaned-up voice-transcript addendum, not yet written
down anywhere the operator can cross-check it against. **This brief is where
that technical framing gets its first real scrutiny — treat findings below as
first-draft, not confirmed against the operator's own words.**

One concrete anchor the North Star doc does give, and it matters a lot for
scope: **"Our memory lives in WhatsApp"** — the doc already commits, in the
operator's own words, to WhatsApp (not Telegram, not a bespoke app) as the
community's existing communication substrate. That's a real, pre-existing
product decision this brief should build on, not relitigate — and it directly
overlaps with the Hermes/Matrix bridge brief's finding that WhatsApp is already
the one network with real (if dormant) bridge infra on this machine
(`mautrix-whatsapp` at `/Users/flybyflow/substrate/infra/matrix/`).

## 1. The ask, as parsed (gm's cleaned-up transcript + confirmed ground truth)

Silicon Jungle Experience's **Weekender** (48hr, La Reserva Tulum) is
top-of-funnel: attendees get "basic agent-fleet setup" to build with,
initially facilitated, with the explicit goal that the operator stops having
to personally divide attention across every builder — instead, builders run
through the *same* planning/engineering process (BSHR research, CEO/design/eng
review gauntlet) this internal OrchestraOS fleet already uses. The confirmed
progression (§0a) — Weekender → the Experience (2-6wk sprint) → the Incubator
(16wk) — is the real on-ramp into "Silicon Ventures," joint ventures built on
what's already there. Proposed auth: WhatsApp phone number as identity, tied to
token-gated membership (event attendance/payment → token → access). Also wants
a recursive loop where the fleet (plus "Shaw," described as a co-founder of the
lab and OrchestraOS's likely original author) works on evolving OrchestraOS
itself. Guiding imperatives for prioritization: increase prosperity, reduce
suffering/friction, encourage curiosity and learning.

## 2. What already exists (verified this session, not assumed)

This is the most load-bearing section of this brief — a large fraction of what
the ask describes **is already running today**, inside this very fleet:

- **The planning/engineering process itself.** Think → Plan → Build → Review →
  Test → Ship → Reflect (`prompts/{think,plan,build,review,test,ship,reflect}.md`)
  is the existing internal loop, with a real review gauntlet (CEO/design/eng/DX
  review skills) already gating Build. This is not a thing to build — it's a
  thing to *expose*, which is a completely different (and much smaller)
  engineering problem than building it from scratch.
- **The recursive self-improvement loop the operator asked for, largely already
  running.** `reflect`'s job (`prompts/reflect.md`) is explicitly "compounding —
  deciding what this cycle taught, what to remember, and what should change
  before the next cycle" — writing to `learnings.jsonl` and retros. This
  session's own work (this brief, the bridge brief, the recent `ea` T1 seat
  addition per git log) is literally the fleet evolving OrchestraOS. What's
  *not* yet true: "Shaw" contributing to that loop as a stated co-founder — an
  access/identity question, not an architecture one (§4).
- **A tiered trust model, but a single-tenant one.** `registry.json` shows a
  real T0 (gm) → T1 (plan/build/review/ea) → T2 (bshr/think/test/ship/reflect/
  brain/builder-N) hierarchy with defined parent/child relationships — this is
  real infrastructure, not a green-field design. But every tier today is
  **fully trusted**: every seat gets real filesystem/bash/process access under
  the single operator's identity. There is no notion of "this seat belongs to
  an external, semi-trusted human" anywhere in it.
- **A single-tenant data model.** `orchestra.toml`'s `[operator]` section is
  explicit: `id = "operator"` is described as "the default tenant/operator id
  used wherever a row needs an owner." The word "tenant" already exists in the
  schema's vocabulary — but as a singular default, not a multi-tenant concept.
  Grepped for `rbac`, `multi-tenant`, `sandbox`, `guest agent` across the
  codebase (Python/Markdown): no hits describing an external-user permission
  boundary. `scripts/identity_store/` is real and extensive, but it's about
  **fleet-internal** seat/process/pane identity reconciliation (keeping the
  registry, tmux panes, and DB in sync for *this operator's own agents*) — not
  human end-user identity or RBAC. Do not confuse the two; a future reader
  skimming `identity_store/`'s file list could easily assume this problem is
  half-solved. It isn't.
- **A real, if partial, WhatsApp channel.** `orchestra.toml` has a
  `[notify.whatsapp]` section (token + phone_id, outbound notify only) and the
  bridge brief (§2 of that document) found a dormant `mautrix-whatsapp` Matrix
  bridge stack on this machine, built for a different project. Neither is an
  auth system; both are a real head start on the *transport*, not the
  *identity/membership* layer the operator's ask actually needs.

**Net finding: the process/orchestration layer is mostly done. The
security/identity/multi-tenancy layer is entirely missing.** That inversion —
easy part done, hard part not started — should directly shape phasing (§5).

## 3. The load-bearing risk this brief exists to surface

Giving semi-trusted external humans (event attendees, most of whom the operator
has never worked with before) scoped access to a system whose native
trust model is "every agent has real bash/filesystem access under one
operator's identity" is not a config change. Dispatched a dedicated research
pass on this specifically (auth precedent, RBAC/sandboxing patterns, and
self-modifying-agent-fleet guardrails); its findings sharpen and partly
correct the framing below.

- **Execution sandboxing itself is a solved, commoditized problem — this is
  NOT the hard part, don't over-invest here.** Per-session microVM isolation
  (Firecracker/gVisor) is the 2026 industry baseline for untrusted-agent code
  execution — E2B and Vercel Sandbox both run this way, and OpenAI's Codex
  cloud uses a two-phase model (setup gets network+secrets; the agent-execution
  phase runs network-off with secrets stripped). Off-the-shelf tooling exists;
  this brief should not propose building a bespoke sandbox runtime.
- **The actual unsolved problem is sharper and worse than generic "sandboxing":
  Silicon Jungle's plan requires external-tenant sessions to call into the
  *same shared orchestration/reasoning layer* used for internal CEO/eng
  reviews — a single trust domain, not per-tenant isolated instances.** That
  breaks isolation in ways execution-sandboxing doesn't reach at all: shared
  LLM-serving KV-cache can leak prompt-prefix content across tenants; one
  measured multi-tenant agent-memory system leaked cross-tenant data via
  semantic search at a **43.9% rate** even with direct-ID lookups blocked, per
  the research pass's citation (arXiv 2606.24535); and writing "don't reveal
  tenant B's data" as a system-prompt instruction is not a real control — a
  confused or cleverly-prompted session simply ignores it. The empirically
  common real-world root cause in disclosed incidents is banal and directly
  relevant here: **one shared credential/API key used across all tenants**,
  which makes the shared orchestrator itself the blast-radius multiplier.
  Real disclosed incidents in this exact class (agent + fs/bash/network access,
  found by the research pass): a GitHub Copilot prompt-injection that
  exfiltrated a live token via a symlink + JSON-schema URL with zero user
  interaction; a patched Claude Code sandbox-escape CVE via unvalidated
  `sed`/`echo` chaining; an AWS Bedrock AgentCore sandbox escape via DNS
  tunneling (DNS itself is an exfil channel most egress controls miss).
  Simon Willison's "lethal trifecta" (private data + untrusted content + an
  exfil channel) is the right mental model for evaluating any specific design
  before it ships, not a general reassurance that this is handled.
- **Practical implication for §4:** "give external builders the same fleet"
  cannot mean literally the same running orchestration process/context as
  internal CEO/eng review, no matter how good the RBAC layer on top is. Some
  form of per-tenant isolated orchestration (separate agent-runtime instances
  or sessions, not just separate credentials/roles inside one shared instance)
  is required if this ever runs unfacilitated — this is a materially bigger
  build than "add a permission check," and it's the reason §4 sequences it
  last, not first.
- **"Facilitated at first" is doing a lot of work in the operator's framing,
  and it's the right call given the above.** The ask explicitly starts with
  human facilitation and only later wants the operator "not having to
  personally divide attention." Read literally, and now doubly justified by
  the shared-orchestration finding above: **the isolation problem does not
  need to be solved before the first build weekend or two run**, only before
  attendee count outpaces what a human facilitator can watch directly, or
  before the Experience/Incubator stages hand a team more sustained,
  less-supervised access than a single 48hr Weekender.
- **Token-gated membership and WhatsApp-phone identity each have known,
  well-understood failure modes**, confirmed by the research pass with real
  incidents, not just theoretical concern: SIM-swap/number-reassignment for
  phone-as-identity (NIST SP 800-63B already rates SMS/PSTN OTP "restricted"
  for this reason; one carrier study found 171/259 reassigned numbers still
  tied to prior owners' live accounts); wallet-drain phishing and Sybil/
  multi-account allowlist farming for token-gating (real precedent: BAYC's
  2022 Instagram-phishing wallet drain, ~$3M). Solvable — Twilio
  Verify/Authsignal/Meta Account Kit already productize WhatsApp OTP as a
  primary auth factor — but not free, and not this brief's first-order problem.
- **Closest external precedent for the whole model, found but not validated:**
  no public program combines shared-agent-fleet tooling + hackathon sourcing +
  equity/JV graduation in one. Closest partial matches: Alloy Partners (a
  venture studio sharing ~1,133 AI agents across portfolio companies, no
  hackathon funnel) and EasyA's Kickstart (hackathon-to-VC funnel, no shared
  agent tooling). Read this as **an open position, not validated whitespace** —
  worth a dedicated competitive-landscape pass before the operator treats it
  as a differentiator in outward-facing materials.

## 4. Proposed shape (strategic, not implementation-ready)

Three separable pieces, matching the three separable problems found above —
**deliberately not proposing to build all three now**:

1. **Expose (don't rebuild) the existing planning/engineering loop** to a
   scoped "guest" context, for the Weekender's first runs, human-facilitated —
   a facilitator (the operator or delegate) drives the fleet on the
   attendees' behalf, or watches every session directly. No new orchestration
   isolation needed yet, because nothing is unsupervised (§3).
2. **A genuinely new identity + membership layer, PLUS per-tenant orchestration
   isolation — these are one piece of work, not two.** §3's research finding
   means "WhatsApp-phone identity + token-gated membership + RBAC" is not
   sufficient by itself once attendees get unsupervised access (Experience/
   Incubator stages) — it has to be paired with genuinely isolated agent-
   runtime sessions per tenant (separate orchestration context, not shared
   credentials/process), or the RBAC layer is decoration on top of a shared
   trust domain. This is real, non-trivial, security-critical infrastructure
   that doesn't exist today in any form (§2) — the single biggest piece of new
   engineering this vision implies, larger than originally scoped before §3's
   research pass. Exact tier naming/trust semantics are eng review's job, not
   this brief's.
3. **Formalize the already-running self-improvement loop** to include Shaw as
   a stated contributor — primarily an access/process question (who can send
   tasks into this fleet and under what review gate), not a new architecture.
   If/when the fleet is ever tasked with modifying its own infrastructure as
   part of this loop, the research pass names four practices beyond the
   existing PR-review + operator-approval hard-stop worth eng review's
   attention: a frozen, agent-inaccessible eval suite scored before/after each
   self-authored change; non-LLM-judged (deterministic) gating, since an
   LLM-as-judge has been documented being gamed by a self-editing agent;
   staged/canary rollout of prompt/policy changes with auto-rollback triggers,
   not just ordinary code CI; and keeping kill-switch/audit logic outside the
   fleet's own editable runtime. **Caveat, preserved from the research pass:**
   two of the sources behind this specific list looked unusual enough
   (an atypical repo-username pattern, a 2026-dated blog post) that they
   should be independently re-verified before this list is treated as
   fully certified — flagging rather than silently certifying.

**Recommendation: sequence 1 → 3 → 2, not the reverse.** The instinct to start
with heavy human facilitation (per the operator's own framing) means the
hardest, highest-blast-radius piece (2, now confirmed to require per-tenant
orchestration isolation, not just an RBAC layer) is the one piece that can
legitimately wait until the first Weekender or two prove the *process* works
at all with a human in the loop watching everything. Building the isolation
layer first, before anyone's run a single weekend this way, risks
over-engineering a boundary for a usage pattern that hasn't been observed yet
— and per §3, building it *wrong* (RBAC without real per-tenant isolation)
would be worse than not building it yet, since it would look solved without
being solved.

## 5. The split gm asked to have flagged

This is explicitly **not** "phase 2 of the Telegram bridge swap." The bridge
brief (`docs/PLAN_hermes-matrix-telegram-bridge.md`) is a narrow, low-risk
transport question for the operator's own internal channel. This brief is a
platform-scale question about giving *other humans* access to the fleet at
all — an order of magnitude bigger in scope, risk, and review depth, and it
should get its own CEO/eng review track, its own phasing, and its own
go/no-go from the operator, not inherit the bridge brief's HOLD SCOPE posture.
**The one real technical link between them:** if WhatsApp becomes the
identity/membership channel for Silicon Jungle (§4.2), and the bridge brief's
Recommendation 1 is adopted for the *operator's* Telegram channel, these two
projects will eventually share infrastructure decisions (Hermes, if path (b)
is chosen there) — but neither should be scoped assuming the other's outcome
yet. Flagging this explicitly for gm, as asked, rather than silently building
one brief's assumptions into the other's plan.

## 6. Open questions for the operator (surfaced, not decided here)

- Does "facilitated at first" mean the operator is comfortable running one or
  two build weekends on today's system, human-watched, with zero new
  sandboxing infrastructure, before any of §4.2's identity/RBAC work starts?
  That reading drives the whole recommended sequence in §5.
- **(Added per §0b)** Silicon Jungle's WhatsApp presence already runs on the
  shared keonda Hermes gateway in `observe_only` mode (§0b) — should that stay
  on the shared instance in this same restricted posture, or does it get the
  same "dedicated instance" treatment the bridge brief recommends for the
  operator's own channel, once/if it moves beyond observe-only? Not decided
  by either brief; a real architecture question the two threads share.
- What does "Shaw contributing to the loop" concretely mean for access: read
  access to the fleet's decisions/learnings, or write access to send it tasks?
  Very different trust levels, same phrase in the transcript.
- Is there a target build-weekend date that puts a real clock on §4.2's
  identity-layer work, or is this multi-quarter exploration?
- Guiding imperatives (prosperity, reduced suffering/friction, curiosity) read
  as product principles more than acceptance criteria — worth the operator
  confirming whether CEO review should hold the plan to them explicitly (e.g.
  "does this feature increase friction for a first-time builder?") or treat
  them as background culture, not a gate.

## CEO REVIEW — Step 0

**Review depth:** strategy-only (no implementation authorized).

**Mode: SELECTIVE EXPANSION.** Self-decided by plan (non-interactive T2 seat,
per `prompts/plan.md`'s narrowest-scope instruction), because this is a
greenfield platform concept (the mode heuristic's default), but every cherry-
pick candidate below defaults to **Defer** rather than **Add**, since no human
is present to actually approve scope expansion — this mode is used for its
*exploration discipline* (10x framing, delight scan, explicit cherry-pick
dispositions), not to silently grow scope.

**0A. Premise Challenge.** Real problem: the operator (per §0a, confirmed as
Mo Sersouri, SJE's own Technical Lead) is already running a real, named,
three-stage program (Weekender → Experience → Incubator) and wants the
internal agent fleet's planning/engineering process to be the thing that
scales attendee support without scaling the operator's own attention. Do-
nothing cost: real — SJE's stated goal ("not having to personally divide
attention across every builder") doesn't happen without some version of this.
This plan solves the actual pain (attention doesn't scale) directly, not a
proxy — but §3/§4 found the naive version of the solution (open the fleet to
attendees) introduces a security problem bigger than the attention problem it
solves, so "solving it directly" still requires sequencing (§4/§5), not a
single build.

**0B. Existing Code Leverage.** Reusable: the entire Think→Plan→Build→Review→
Test→Ship→Reflect loop (§2) — proven, not hypothetical, this very session used
it twice. Not reusable as-is: the tiered trust model (`registry.json`,
`orchestra.toml`'s `[operator]` single-tenant assumption) — every existing
tier assumes one trusted operator identity; extending it to external humans is
new work, not a config change (§2/§3).

**0C. Dream State Mapping.**
```
CURRENT STATE                    THIS BRIEF                        12-MONTH IDEAL
Operator personally         --->  Weekenders run human-      --->   Weekender/Experience/
facilitates every                 facilitated on today's            Incubator attendees get
Weekender build; internal         system first (§4.1); Shaw         scoped, isolated agent-
fleet + review loop is            gets formalized loop               fleet access without
operator-only.                    access (§4.3); isolation           the operator in every
                                   layer (§4.2) built only            loop, once usage
                                   once real usage patterns           patterns justify the
                                   justify it.                        investment (not before).
```
This brief moves toward the ideal on the "expose the process" axis
immediately (§4.1) and the "formalize Shaw's access" axis soon (§4.3), while
deliberately not moving on the hardest axis (§4.2, isolated multi-tenancy)
until real Weekender usage data exists to design it against — avoiding the
common failure mode of over-building security infrastructure for a threat
model that's still speculative.

**0D. Approach decision.** No new approach choice required — §4's sequencing
recommendation (1→3→2) and §6's open questions already frame the one real
fork (how fast to move on isolation) as the operator's call, not decided here.

**0F/0G. Expansion framing + cherry-pick ceremony (SELECTIVE EXPANSION).**

*10x ambition, briefly:* the platonic-ideal version of this is Silicon Jungle
Experience's own fleet — a "Weekender edition" of gm's hierarchy, spun up
per-cohort, that facilitators supervise instead of drive, with the review
gauntlet (CEO/eng/design review) available to attendee teams as a genuine
product feature ("get your MVP plan reviewed like a real startup would"), not
just an internal engineering tool. That's a real differentiator vs. both
comparables the research pass found (Alloy Partners has the shared-agent
infra but no hackathon funnel or review-as-product; EasyA's Kickstart has the
funnel but no shared agent tooling) — but it's explicitly **not** what this
brief is recommending building now (§4).

*Delight scan (5 adjacent ideas, all deferred per this mode's default):*
1. Auto-generated "what your team built" recap card at Sunday 5pm demo,
   pulling from the fleet's own `reflect` output — near-zero marginal cost
   since `reflect` already produces cycle summaries.
2. A Door-1-application-to-Weekender-team-formation matcher that uses the
   same "who's good at what" surfacing language from the North Star doc,
   built on top of whatever identity layer §4.2 eventually is.
3. Post-Weekender: an automatic TODOS.md-style backlog handed to teams that
   graduate to the Experience stage, seeded from what the review gauntlet
   flagged during the weekend.
4. A "trust the spark" mode for the review gauntlet itself — CEO review
   already has a SCOPE EXPANSION posture; surfacing that explicitly to
   attendee teams would operationalize the North Star's first protected value
   ("curiosity first, justification later") inside the tool, not just the
   culture.
5. Using the Incubator's 16-week duration as the natural point to introduce
   real per-tenant isolation (§4.2) — by then, real usage data exists, and
   Incubator teams are exactly the ones with sustained, less-supervised access
   that most needs it.

**Cherry-pick dispositions (self-decided, all deferred — no human present to
approve scope expansion, per plan.md):**
| # | Proposal | Effort | Decision | Reasoning |
|---|----------|--------|----------|-----------|
| 1 | Auto-generated demo recap card | S | DEFERRED | Real, cheap, but zero urgency before a single Weekender has run on this system |
| 2 | Team-formation matcher | M | DEFERRED | Depends entirely on §4.2's identity layer existing first — sequencing, not rejection |
| 3 | Auto-seeded post-Weekender backlog | S | DEFERRED | Nice-to-have, not blocking the core sequencing decision in §4/§5 |
| 4 | "Trust the spark" review-gauntlet mode | M | DEFERRED | Real product idea, but a design/positioning decision the operator should make deliberately, not one this seat should default into scope |
| 5 | Incubator-stage isolation timing | — | ACCEPTED (already in §4's sequencing) | Not a new proposal — already the recommended sequence, restated here for completeness |

*(HOLD SCOPE checks, run as required within SELECTIVE EXPANSION mode:)*
**Complexity check:** the accepted scope (§4 items 1 and 3: expose the
existing loop human-facilitated, formalize Shaw's access) is 0 new files at
strategy-only depth — no threshold trip. **Minimum changes:** already what §4
proposes — explicitly not building §4.2 (the large piece) yet. **Invariants:**
none stated yet for this system (no prior locked behavior to preserve — this
is genuinely greenfield, unlike the bridge brief).

**0H. CEO plan persistence.** Storage policy for this non-interactive seat:
the working plan file itself (`docs/PLAN_silicon-jungle-agentic-platform.md`)
**is** the CEO plan and scope summary — writing a second, separate archive
copy under `~/.gstack/projects/` would fork the record for no reader's
benefit (gm and the operator read this repo-committed file, not a local
gstack cache) and isn't done here; treat this section plus the Cherry-pick
table above as the CEO plan content. **Spec review loop:** a dedicated
adversarial research pass already ran against this exact document's technical
claims before this review started (§3's citations) — it already surfaced
real, material corrections (the shared-orchestration-layer risk finding) that
reshaped §4's recommendation before this CEO pass even began. Treat that as
having discharged this step's intent (find what a first draft missed) rather
than re-running an equivalent pass with less specific instructions; noting
this as a deliberate efficiency call, not a silently skipped step.

**0I. Temporal Interrogation (sequencing, strategy-level, mirroring §4/§5).**
```
NEAR TERM (weeks):      Run 1-2 Weekenders human-facilitated on today's
                         system — no new infra. Facilitator drives the fleet
                         for attendee teams, or watches every session live.
SOON (Shaw access):     Formalize what "contributing to the loop" means
                         (read vs. write access, §6) — an access/process
                         decision, not new architecture.
LATER (isolation, §4.2): Only once Weekender usage data exists: design and
                         build per-tenant orchestration isolation, gated on
                         the Experience/Incubator stages actually needing
                         unsupervised access — not built speculatively.
NOT YET (full vision):  The "review gauntlet as attendee-facing product"
                         10x version (0F) — real differentiator, deliberately
                         not scoped into this brief.
```

## CEO REVIEW — Sections 1-10 (strategy-only depth)

### Section 1: Architecture Review

**Current scope:** SELECTIVE EXPANSION; accepted = §4 items 1 (expose loop,
human-facilitated) and 3 (formalize Shaw access); all 5 cherry-picks deferred
per the table above.

```
NEAR TERM (accepted scope)
attendee <--facilitator drives--> [existing T0-T2 fleet, unchanged] <--> gm
   (no new component; facilitator IS the access-control boundary, literally
    a human, not code)

LATER (explicitly NOT built now — §4.2, shown for context only)
attendee <--WhatsApp+token--> [new identity layer] --> [ISOLATED per-tenant
   orchestration session, not the shared T0-T2 fleet] --> reviewed output
   (this is the piece §3's research found genuinely unsolved off-the-shelf)
```
- **Component boundaries (accepted scope):** none change. The "boundary" for
  the first Weekenders is a human facilitator, which is a real, if unscalable,
  isolation mechanism — worth stating plainly since it's easy to read "no new
  code" as "no access control." There is one: it's a person.
- **Coupling:** zero new coupling for accepted scope. §4.2 (not built) would
  introduce coupling to whatever identity/isolation stack is chosen — that
  design doesn't exist yet, correctly left to a future eng review once §4.2
  is actually in scope.
- **SPOF:** the facilitator themselves, for near-term scope — if the operator
  or delegate can't be present, a Weekender can't safely run on this system
  yet. Real constraint, not a defect; matches "facilitated at first" exactly.
- **Security architecture:** unchanged for accepted scope (internal fleet,
  internal trust model, human in every loop). This is the entire point of
  sequencing §4.2 later — accepted scope has zero new attack surface.
- **Production failure scenario:** a facilitator's own agent session
  misbehaves (bad prompt, confused instruction) mid-Weekender — no different
  in kind from any internal fleet failure today, since the attendee never has
  direct tool access in accepted scope, only the facilitator does.
- **Rollback posture:** trivial — accepted scope adds no new running
  infrastructure to roll back.

**Decision gate:** no findings require a new decision.

### Section 2: Error & Rescue Map (capability-level, strategy-only)

```
CAPABILITY                    | WHAT CAN GO WRONG                | KNOWN SAFEGUARD TODAY
-------------------------------|-----------------------------------|------------------------
Facilitator-driven fleet use   | Facilitator unavailable, session  | none — this IS the boundary;
(accepted scope)               | misbehaves                        | no automated safeguard yet
Shaw loop access (accepted)    | Over-broad write access granted   | existing PR review + operator
                                | without a clear scope decision    | approval hard-stop (unchanged)
Identity/isolation layer       | Cross-tenant data leakage via      | NONE — genuinely unsolved,
(NOT accepted scope, §4.2)     | shared orchestration (§3)          | this is exactly why it's deferred
```

| CAPABILITY | RESCUED? | RESCUE ACTION | USER SEES |
|---|---|---|---|
| Facilitator-driven use | N/A — no automated rescue, human-supervised by design | — | facilitator catches it live |
| Shaw loop access | Y (existing gate, if scope is defined narrowly) | PR review + operator approval | unchanged from today |
| Identity/isolation layer | **GAP — but correctly out of accepted scope** | — | **owner: future eng review, only once §4.2 is actually authorized; not this review's job to design it prematurely** |

**Decision gate:** the one GAP is explicitly out of accepted scope by design
(§4's sequencing) — recorded here so it isn't silently forgotten when §4.2
does become live scope, not because it needs a remedy in this review.

### Section 3: Security & Threat Model

- **Attack surface (accepted scope):** zero expansion — no attendee gets
  direct system access; the facilitator does, and that's unchanged from
  today's internal-only model.
- **Attack surface (§4.2, not accepted):** already covered in exhaustive
  detail in the main brief's §3 — shared-orchestration cross-tenant leakage,
  credential-sharing blast radius, the "lethal trifecta" model. Not repeating
  here; that section **is** this review's Section 3 finding, surfaced before
  this formal review even started.
- **Secrets:** none new for accepted scope.
- **Audit logging:** Shaw's loop access (§4 item 3), once scoped, should get
  the same audit trail any T1 seat gets today (msg_store, approval.py) — no
  new mechanism needed, just applying the existing one to a new contributor.

**Decision gate:** no High-severity findings in accepted scope; the real
High-severity finding (§4.2's isolation gap) is correctly deferred, not
mitigated-and-shipped.

### Section 4: Data Flow & Interaction Edge Cases

For accepted scope, there is no new data flow to diagram — a human
facilitator using the existing fleet on the attendees' behalf produces
exactly the data flow that exists today (operator using their own fleet),
just with a different human's intent behind it. The interesting edge cases
all live in §4.2 (deferred):
| INTERACTION | EDGE CASE | HANDLED? | HOW? |
|---|---|---|---|
| Attendee's agent session reads untrusted content (a teammate's file, a web page) | Prompt injection reaching real tool access | **NOT HANDLED — deferred to §4.2** | Facilitator-in-the-loop is the mitigation for accepted scope |
| Two attendee teams' sessions run concurrently | Cross-tenant leakage (§3's KV-cache/semantic-memory finding) | **NOT HANDLED — deferred to §4.2** | N/A until isolation is built |

**Decision gate:** both edge cases correctly out of accepted scope; recorded
so §4.2's future eng review starts from this list, not from scratch.

### Section 5: Code Quality Review

Not applicable — zero code in accepted scope. The one quality-relevant
judgment call this brief itself makes: reusing the existing Think→Plan→Build→
Review→Test→Ship→Reflect loop rather than building a parallel "attendee
edition" from scratch (§0B) — correct, DRY, and the loop is already proven.

### Section 6: Test Review (strategy-only)

| ITEM | TEST TYPE | EXISTS? | HAPPY PATH | FAILURE PATH | EDGE CASE |
|---|---|---|---|---|---|
| Facilitator runs a Weekender team through the loop | Manual/observed (not automatable — this is a human-facilitated pilot) | No — new (as a *deliberate* process, not new code) | team gets a reviewed plan by Sunday demo | facilitator has to intervene mid-session | two teams need facilitator attention simultaneously — does this reveal the real ceiling on "facilitated at first"? |
| Shaw loop access, once scoped | Same as any T1 seat access — existing PR-review-gated pattern | Yes (existing pattern) | task flows in, gets reviewed, ships | over-broad access request | none new — same as any internal access-scoping question |

**Test ambition check:** the "ship at 2am Friday" test here isn't a code
test — it's the first live Weekender pilot itself. The real signal worth
capturing deliberately (not left to informal facilitator notes): how many
concurrent teams can one facilitator actually watch before something gets
missed? That number is the empirical input §4.2's future design needs, and
this brief recommends capturing it explicitly during the pilot, not
retroactively guessing at it later.

**Decision gate:** one concrete, low-cost recommendation surfaces here — log
facilitator attention/intervention data during the pilot Weekenders. Carried
to Implementation Tasks below as a P2 (process, not code).

### Section 7: Performance Review

No findings — no new system, no new load, for accepted scope.

### Section 8: Observability & Debuggability Review

- Accepted scope has no new automated system to observe — the facilitator
  *is* the observability mechanism for the pilot phase, which is honest and
  appropriate for this stage, not a gap to close prematurely.
- One real, cheap recommendation: have the facilitator log session notes
  (what worked, what needed intervention) using the fleet's own existing
  `reflect`/`learnings.jsonl` mechanism (§2) rather than an ad hoc doc — reuses
  infrastructure that already exists instead of inventing a new one.

**Decision gate:** carried to Implementation Tasks as a P3 (cheap, valuable,
not blocking).

### Section 9: Deployment & Rollout Review

- **Rollout order:** exactly §4/§5's sequence — 1 (expose, facilitated) → 3
  (Shaw access) → 2 (isolation, only once justified by real usage).
- **Deploy-time risk window:** none for accepted scope — nothing new is
  deployed, a human is using existing infrastructure.
- **Rollback plan:** trivial for accepted scope (stop facilitating, nothing to
  undo). §4.2, whenever it's built, needs its own rollback plan at that time —
  correctly not designed prematurely here.

### Section 10: Long-Term Trajectory Review

- **Technical debt introduced:** none for accepted scope. The real long-term
  risk this review flags: if §4.2 (isolation) ever gets built *reactively*,
  under time pressure from a Weekender that outgrew human facilitation before
  the isolation layer was ready, it will be built worse than if it's designed
  deliberately with real usage data in hand. **Recommend the operator treat
  "facilitator capacity" (Section 6's proposed metric) as the trigger to start
  §4.2, tracked explicitly, not discovered after the fact.**
- **Reversibility: 5/5** for accepted scope (nothing built, nothing to
  reverse). Not meaningful to score §4.2 yet — it doesn't exist.
- **Ecosystem fit:** strong — reuses the exact loop this fleet already runs
  for its own work, matching the North Star's own language ("the people who
  build it own it") applied to the fleet's tooling, not just the community.
- **1-year question:** the honest answer for accepted scope is "yes,
  obviously" (a human running the existing fleet on someone else's behalf
  needs no explanation). The real 1-year question is whether §4.2 gets built
  well — outside this review's scope to answer, but its trigger condition
  (Section 6/10 above) is this review's concrete contribution to getting
  there deliberately.

### Section 11: Design & UX Review

**SKIPPED (no UI scope)** — this brief is entirely strategic/organizational;
no screens, components, or interaction surfaces are proposed at this stage.

---

## NOT in scope

- **Per-tenant orchestration isolation (§4.2)** — the single largest piece of
  new engineering this vision implies, explicitly deferred until real
  Weekender usage data justifies its design (§4/§5/§9/§10). Not a TODO in the
  usual sense (nothing to schedule yet) — a real future eng-review-scale
  project, gated on a trigger condition this review defines (facilitator
  capacity data from the pilot Weekenders).
- **All 5 delight-scan cherry-picks (0F/0G)** — deferred, not rejected; see
  the cherry-pick disposition table in Step 0.
- **The "review gauntlet as attendee-facing product" 10x vision (0F)** — a
  real, differentiated idea, deliberately not scoped into this brief; worth
  its own future brief once §4.1/§4.3 are proven.

## What already exists

- The entire Think→Plan→Build→Review→Test→Ship→Reflect loop, proven
  internally, reusable as-is for facilitator-driven Weekender use (§2).
- The recursive self-improvement loop the operator asked for — `reflect` +
  `learnings.jsonl` already do this; "Shaw contributing" is an access
  question on top of existing infrastructure, not new architecture (§2).
- A real, live, named three-stage funnel (Weekender → Experience →
  Incubator) at `sje.ploy.build`, confirmed directly (§0a) — this brief
  didn't have to invent the product shape, only the fleet-access question.
- **(Added per §0b)** A live, real-world precedent for cautious external-event
  agent presence: the shared keonda Hermes gateway already has an `enabled`
  WhatsApp profile route for Silicon Jungle's actual BUILD-A-THON event group,
  running in `observe_only` (no autonomous replies) mode — the operator
  independently arrived at the same "don't give attendees unsupervised agent
  access yet" posture this brief recommends, before this brief existed.

## Dream state delta

Per 0C: this brief's accepted scope moves toward "operator's attention no
longer the bottleneck" on the cheap, low-risk axis (expose + formalize
access) immediately, while deliberately not yet moving on the expensive,
high-risk axis (isolated multi-tenancy) until real usage data exists to
design it against.

## Error & Rescue Registry

See Section 2 — one real GAP (per-tenant isolation), correctly out of
accepted scope, recorded for whenever §4.2 becomes live work.

## Failure Modes Registry

| CODEPATH (capability) | FAILURE MODE | RESCUED? | TEST? | USER SEES | LOGGED? |
|---|---|---|---|---|---|
| Facilitator-driven fleet use (accepted) | Facilitator misses something mid-session | Y — human-in-loop by design | N (this IS the pilot's test) | Facilitator catches it live, or doesn't | Recommend: yes, via reflect (Section 8) |
| Cross-tenant isolation (NOT accepted, §4.2) | Shared-orchestration leakage (§3) | N — GAP, deferred by design | N | N/A — not built | N/A |

Only one row is a true CRITICAL GAP by the section's rule, and it's
explicitly out of accepted scope, not silently shipped.

## Implementation Tasks

_Strategy-only: these name the next research/process action and owner, not
implementation contracts._

- [ ] **T1 (P2, human: ~0 extra / process change)** — during pilot Weekenders,
  have the facilitator explicitly log intervention/attention data (how many
  teams, how many times they had to step in) using the fleet's existing
  `reflect`/`learnings.jsonl` mechanism
  - Surfaced by: Section 6, Section 10
  - Files: none — process instruction for whoever facilitates, not code
  - Verify: after 1-2 Weekenders, this data exists and is queryable
- [ ] **T2 (P3, human: ~0 extra / process change)** — same mechanism, log
  general session notes (what worked, what needed a facilitator override)
  - Surfaced by: Section 8
  - Files: none
  - Verify: notes exist in `learnings.jsonl` after the first pilot
- [ ] **T3 (P3, human: TBD, own future brief)** — once facilitator-capacity
  data from T1 shows a real ceiling, or the Experience/Incubator stages need
  sustained unsupervised access, scope §4.2 (per-tenant orchestration
  isolation) as its own eng-review-scale project — not before
  - Surfaced by: Section 2, 4, 9, 10 (all converge on this trigger)
  - Files: to be determined — this is genuinely new infrastructure
  - Verify: trigger condition (T1's data, or a stage-progression need) is
    explicit and named before work starts, not retroactively justified

### Completion Summary

```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
+====================================================================+
| Mode selected        | SELECTIVE EXPANSION                         |
| System Audit         | No design doc (direct gm task); confirmed   |
|                       | ground truth at sje.ploy.build fetched      |
|                       | directly, not taken on gm's summary alone   |
| Step 0               | SELECTIVE EXPANSION; 5 cherry-picks all     |
|                       | deferred; accepted = expose loop (facil-    |
|                       | itated) + formalize Shaw access             |
| Section 1  (Arch)    | 0 issues (accepted scope adds no component; |
|                       | facilitator named explicitly as the         |
|                       | near-term access-control boundary)          |
| Section 2  (Errors)  | 3 capabilities mapped, 1 GAP (correctly     |
|                       | out of accepted scope)                      |
| Section 3  (Security)| 0 new issues in accepted scope (the real    |
|                       | finding already lives in brief §3/§4a)      |
| Section 4  (Data/UX) | 2 edge cases mapped, both deferred by design|
| Section 5  (Quality) | N/A — zero code in accepted scope           |
| Section 6  (Tests)   | 2 items, 1 concrete process recommendation  |
|                       | (log facilitator capacity data)             |
| Section 7  (Perf)    | 0 issues found                              |
| Section 8  (Observ)  | 1 process recommendation (reuse reflect)    |
| Section 9  (Deploy)  | 0 risks — no deploy in accepted scope       |
| Section 10 (Future)  | Reversibility: 5/5 (accepted scope); named  |
|                       | the real trigger condition for §4.2         |
| Section 11 (Design)  | SKIPPED (no UI scope)                       |
+--------------------------------------------------------------------+
| NOT in scope          | written (3 items)                          |
| What already exists   | written                                    |
| Dream state delta     | written                                    |
| Error/rescue registry | 3 rows, 1 CRITICAL GAP (deferred by design)|
| Failure modes         | 2 total, 1 CRITICAL GAP (deferred by design)|
| TODOS.md updates      | 0 items (findings carried as tasks above)  |
| Scope proposals       | 5 proposed, 0 accepted, 5 deferred (SELECTIVE)|
| CEO plan              | folded into this working plan file, not a  |
|                        | separate archive (see 0H)                  |
| Outside voice          | dedicated research pass, already run       |
|                        | before this review started (see 0H)        |
| Lake Score            | N/A — no coverage-scored questions asked   |
| Diagrams produced      | 4 (architecture near/later, dream-state,   |
|                        | temporal sequencing)                       |
| Stale diagrams found   | 0                                           |
| Unresolved decisions   | 0 from this review (4 open questions for   |
|                        | the operator remain in brief §6, by design)|
+====================================================================+
```

## ENG REVIEW

**Scope:** accepted CEO-review scope only — expose the existing fleet loop via
human facilitation (zero new code) and formalize Shaw's access to the
self-improvement loop (access/process question). Per-tenant orchestration
isolation (§4.2) is reviewed at strategic level only, not implementation-ready
— it is not accepted scope.

### Scope Challenge

**A. What already solves it.** Read `docs/agent-provisioning-guide.md`: seat
creation is already a one-command, fully-automated operation —
`orchestra agent create <name> --tier T0/T1/T2 --parent <seat>` fills a role
template, registers the seat, validates the runtime/model pair, spawns it, and
verifies it's alive. The guide is explicit: **"There is no manual step. You do
NOT hand-edit `registry.json`"** — this directly answers the "Shaw access"
question from an engineering standpoint: formalizing Shaw as a contributor is
literally `orchestra agent create shaw --tier T1 --parent gm` (or whatever
tier the operator's answer to brief §6 implies), using infrastructure that
already exists and is already the sanctioned way every other seat in this
fleet (including `ea`, added recently) came into existence. **This is not new
engineering — it's applying an existing, one-command mechanism to a new name.**

Also verified: `msg_store.py`'s `sender_verdict()` (line 236) already checks
for impersonation — `caller != from_agent` — before honoring a send. Whatever
seat Shaw ends up as, the existing identity-integrity check applies to them
automatically, not something this brief needs to design.

**Complexity check.** Accepted scope: 0 new files, 0 new classes/services —
one CLI invocation (seat creation) plus a scope decision (which tier/access
level) once brief §6 is answered. Well under the 8-file/2-service threshold.
**Skip Scope Challenge B** — go directly to C.

**C. Findings.**
1. `[P4]` (confidence: 4/10, low — flagging per the pre-emit gate rather than
   suppressing entirely since it's cheap to check) `orchestra agent create`'s
   `--tier` flag accepts T0/T1/T2 per the guide's own table — there is no T3
   or "guest" tier today. This isn't a problem for accepted scope (Shaw gets a
   normal tier), but it does confirm the CEO review's finding that §4.2
   (external/guest access) has literally no existing tier concept to extend —
   not even a stub. Not a remedy needed now; carried as grounding for whoever
   eventually scopes §4.2.
2. No other findings — accepted scope is thin by design (CEO review's own
   framing), and the one real question (does seat provisioning already work)
   checks out against real documentation and code, not assumption.

### Section 1: Architecture Review

No architecture change for accepted scope — confirmed by reading the
provisioning guide directly: adding Shaw as a seat uses the exact same
mechanism as every other seat in `registry.json`. The CEO review's diagram
(facilitator-as-boundary for Weekender use) is unchanged and doesn't need
re-verification here; it was already architecture-light by design.

**§4.2 (strategic-only, not accepted scope):** confirming the CEO review's own
scoping instruction — this eng review does not attempt to design the isolation
layer's interfaces, contracts, or tier semantics. The one engineering-relevant
addition worth recording for whoever picks it up: the tier model
(`T0/T1/T2` fixed, per the provisioning guide's own flag validation) would
need to be extended, not just configured, to support a genuinely
lower-trust tier — `orchestra doctor`/the CLI's flag validation logic is a
concrete, findable starting point for that future work, not a blank slate.

### Section 2: Code Quality Review

N/A — zero code in accepted scope, confirmed.

### Section 3: Test Review

**Framework/process, not code tests** — accepted scope has no codepaths to
diagram. The one concrete, carried-forward verification: after Shaw's seat is
created via `orchestra agent create`, confirm `orchestra doctor` (mentioned in
the provisioning guide as the tool that "lists" valid runtime/model pairs)
reports it healthy, and confirm one real `msg_store.py send` round-trip from
Shaw's seat lands in the intended inbox with `sender_verdict()`'s
impersonation check passing (i.e., Shaw's seat sends as itself, not spoofing
another seat) — this is a real, cheap, concrete acceptance check, not a
speculative one.

**REGRESSION RULE:** not applicable — nothing existing is being changed,
only a new seat added through an existing, unmodified mechanism.

### Section 4: Performance Review

No findings — no new load, no new codepath.

### Outside Voice

**Not re-run for this eng-review pass**, same reasoning as the CEO review's
0H: the dedicated research pass (auth precedents, RBAC/sandboxing, accelerator
precedents, self-modifying-fleet guardrails) already ran against this exact
document's technical claims before CEO review started, and its findings are
already integrated throughout §3/§4 and the CEO review sections. A second full
outside pass this soon, on the same accepted (thin) scope, would add cost
without new signal for the narrow work actually being gated here. Coverage:
`outside_status: reused-from-ceo-review`.

### Eng Review Completion Summary

```
Section 1 (Architecture)  | 0 issues; confirmed seat-provisioning mechanism
                           | already covers accepted scope, no new architecture
Section 2 (Code Quality)  | N/A — zero code in accepted scope
Section 3 (Tests)         | 1 concrete acceptance check (seat health + one
                           | real msg_store round-trip), no code test needed
Section 4 (Performance)   | 0 issues found
Outside Voice             | reused from CEO review (same document, same pass)
```

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open | SELECTIVE EXPANSION; accepted narrow scope (expose loop + formalize Shaw access), deferred the large isolation build (§4.2) and all 5 delight-scan cherry-picks until real usage data justifies them |
| Outside Review | dedicated research fork (general-purpose agent, web research: auth precedents, RBAC/sandboxing patterns, accelerator precedents, self-modifying-fleet guardrails) — reused as this review's spec-review-equivalent per 0H | Independent technical grounding | 1 | completed, issues_found | Found the shared-orchestration cross-tenant leakage risk (43.9% measured leak rate in one real system) that materially reshaped §4's recommendation before this CEO pass began; flagged 2 sources in its self-modifying-fleet section as needing independent re-verification |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | Accepted scope verified against real infrastructure (`docs/agent-provisioning-guide.md`'s one-command seat creation, `msg_store.py`'s existing impersonation check) — confirmed to require zero new code; 1 low-confidence informational finding (no T3/guest tier exists today, expected and not a remedy) |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | skipped (no UI scope) | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not requested | — |

- **OUTSIDE COVERAGE:** provider = general-purpose research subagent
  (dispatched via Agent tool, live web research, not a Codex/CLI pass),
  phase = pre-review technical grounding, completion state = completed. This
  ran *before* the formal CEO review as primary research input (per gm's
  explicit "run BSHR on this too" instruction), functioning as this review's
  adversarial/outside-voice pass — not a separate post-hoc challenge. Findings
  are cited with sources throughout §3/§4 of the main brief.
- **CROSS-MODEL:** not applicable — same harness family, not a distinct
  external model; skip per the skill's own rule.
- **VERDICT:** CEO + ENG REVIEW COMPLETE — the highest-value finding of this
  whole review (shared-orchestration multi-tenancy is genuinely unsolved
  off-the-shelf, not just "needs RBAC") arrived from the research pass
  *before* formal review started, and materially changed what got accepted
  vs. deferred. Accepted scope (facilitator-driven pilot + formalized Shaw
  access) verified by eng review against real, existing infrastructure
  (one-command seat provisioning, existing impersonation checks) — genuinely
  zero new code, CLEARED. The large piece (§4.2) is correctly NOT accepted
  yet — this is not a green light to design or build it; it's a deliberate
  "not yet, here's the trigger condition" call, reviewed only at strategic
  depth. **CEO + ENG CLEARED for the accepted (narrow) scope — ready to act
  on once the operator answers §6's load-bearing question** (is
  human-facilitated use of today's system acceptable before any isolation
  work starts). §4.2 needs its own future eng review once it's ever accepted
  scope.
- **POST-REVIEW ADDENDUM (§0b):** gm's second addendum (domains, partners,
  BUILD-A-THON reference event, operator's thesis) arrived after this review
  completed and was folded in as §0b with a coherence check, not a full
  re-review — it reinforces the accepted recommendation (real precedent for
  the "cautious, observe-only" posture already exists on the shared keonda
  gateway) rather than contradicting it. One new open question added to §6
  as a result (shared vs. dedicated gateway for Silicon Jungle's own WhatsApp
  presence). Verdict and accepted scope above are unchanged by this addendum.

**UNRESOLVED DECISIONS:**
- Whether the operator is comfortable running Weekenders human-facilitated on
  today's system before any isolation work starts (§6) — the load-bearing
  question this whole sequencing recommendation depends on.
- What "Shaw contributing to the loop" means concretely for access level (§6).
- Whether there's a real clock (target date) on the eventual §4.2 work (§6).
- Whether the guiding imperatives (prosperity/friction/curiosity) should gate
  future reviews explicitly or stay background culture (§6).
- The two research-pass sources flagged as needing independent re-verification
  (self-modifying-fleet guardrails, §4 item 3's caveat) — not blocking, since
  that whole item is itself deferred, but should be resolved before that
  section is ever relied on for a real self-modification decision.
- **(Added per §0b)** Whether Silicon Jungle's WhatsApp presence stays on the
  shared keonda gateway (current, restricted, observe-only) or eventually
  moves to its own dedicated instance — shared open question with the bridge
  brief, not decided by either.
- + 0 unresolved from prior reviews (first pass on this document).
