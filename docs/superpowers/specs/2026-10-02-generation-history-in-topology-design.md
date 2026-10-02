# Generation history from the topology view

**Date:** 2026-10-02
**Status:** design, approved by the operator
**Follows:** #147 (the `N gens` chip on agent cards), #148 (keying that chip's count on the canonical root)

## The gap

#147 put generation history on the agent **card** and nowhere else. The topology view has no
route to it: the operator's words were "there's no way to see the previous generations of an
agent from topology". Topology is the view you watch the fleet in, so a lineage fact that is
only reachable by switching to Cards is, in practice, not reachable.

## What this is not

Not a change to the graph. The nodes, their click behaviour, and the layout are untouched. An
earlier option — a `N gens` chip on the node itself, mirroring the card — was rejected by the
operator in favour of the drawer, and the reasons are worth recording because they would
otherwise be rediscovered:

- A node is `min-w-[110px]` and already carries a status dot, name, tier badge, task subtitle
  and machine label. A chip plus a count makes it wider, which re-lays out the whole graph.
- The node's `onClick` is already taken: it selects the agent and opens its panel
  (`Agents.tsx`, `onSelectAgent={openAgentPanel}`). A chip inside it needs
  `stopPropagation` to avoid firing selection too.
- A popover anchored inside a diagram that scrolls (`overflow-x-auto` on the container) clips
  at the edge.

The drawer has none of these problems, and `Agents.tsx` already carries a comment recording
that the panel was deliberately made `fixed` so that **opening it never reflows the graph**.
That decision is what makes this placement cheap.

## Architecture

The drawer that topology already opens is `AgentDetailPanel`, reached by clicking any node.
It has sections for the Tier/Machine facts, Live feed, Message and Recent problems, and no
generations. That is the thing to fill.

Three files:

1. **`dashboard/src/components/GenerationList.tsx`** (new). Owns one job: render an array of
   generation rows. Holds the `current`/`retired` row shape and the `when()` date guard that
   `GenerationHistory.tsx` currently keeps inline.
2. **`dashboard/src/components/GenerationHistory.tsx`** (changed). Keeps its chip and popover
   for the card view; renders `GenerationList` inside the popover instead of mapping rows
   itself.
3. **`dashboard/src/components/AgentDetailPanel.tsx`** (changed). Gains a collapsible
   `Generations` section rendering `GenerationList`.

One fact, one renderer, two surfaces. The extraction is the point: without it the row logic
and the date guard exist twice, and two copies of one rule drifting apart is exactly what
produced the keying defect #148 had to fix — there, a prefix was stripped in one lookup and
not in the identical lookup eight lines above it.

## Data flow

No API change. Both surfaces call the existing `GET /api/agents/:id/generations` from #147,
which inherits the per-agent scope check from `router.param('id', agentScopeParam)` (#146).

The count does **not** cost a request. `generations_total` is already stamped on every row of
`GET /api/agents`, so the drawer renders its header — `Generations  55` — from data the page
has polled anyway. The row fetch fires only on expand.

Each panel instance fetches once and holds the rows in component state; collapsing and
re-expanding does not refetch. Opening a drawer therefore costs zero extra requests, which is
what keeps clicking around the graph cheap.

## Behaviour

Collapsed by default: a header reading `Generations` with the count and a chevron. Expanding
reveals the list in a scroll area capped at roughly 240px, so a deep lineage (`gm` has 55)
cannot push Live feed and Message off the bottom of the drawer.

Ordering is whatever the API returns, which is already correct and deliberate: current first,
then by time rather than by generation number. Generation numbers reset — `gm`'s live head is
generation 2 while its history runs to 87 — so sorting by number puts a retired generation at
the top. #147 shipped a first cut with that bug and caught it in a staging screenshot; this
spec inherits the fix by not re-sorting.

| condition | behaviour |
| --- | --- |
| `generations_total` >= 2 | collapsed header with the count, expandable |
| `generations_total` absent, or < 2 | section is not rendered at all |
| expanded, request in flight | `Loading…` |
| expanded, empty array returned | `No history recorded.` |
| route returns non-OK (e.g. 403 for a scoped-out agent) | treated as empty; the section shows the empty state, never an error |

An agent that has never rotated shows no section. That matches the card chip's threshold. The
alternative — always showing the section so the drawer can state "never rotated" explicitly —
was considered and dropped as the less useful default, since 179 of 285 agents today have no
history and would each gain an empty section.

Absent data is silence, never an error. #147 established that rule for the chip (no identity
DB means no count and no chip) and this follows it.

## Testing

`GenerationList` is a pure render over its rows, so it is testable without a server:

- current-first ordering as given is preserved, and exactly one row carries the `· current`
  marker
- a non-date `promoted_at` renders as `—`, not `Invalid Date` — real rows hold non-date
  markers in that column, which is why `when()` exists
- an empty array renders the empty state
- a row with a null `model` or null timestamps renders without throwing

`AgentDetailPanel` gets one behavioural test: **no fetch before expand**. That is the property
that makes opening a drawer free, so it is the one that must not regress silently.

## Out of scope

- Any change to the topology graph, its nodes, or its layout.
- Any change to the API: no new route, no new field.
- A generation's `resume_command` or `note` beyond what the list already shows.
- The `agent: any` prop on `AgentCard`. Typing it would have caught #147's missing
  `generations_total` at the boundary instead of at a runtime lookup, but it cascades through
  the component and belongs in its own change.

## One implementation note the above depends on

`AgentDetailPanel`'s `agent` prop is **not** `any` — unlike `AgentCard`'s, it is an explicit
inline shape (`id: string; tier?: string; status?: string; machine?: string; ...`). So the
panel must add `generations_total?: number` to that shape to read the count. This is a
feature, not an obstacle: the compiler will refuse the read until the contract is declared,
which is the boundary check `AgentCard` gives up by taking `any`.
