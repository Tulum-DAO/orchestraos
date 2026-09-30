/**
 * 2D Agents View step 3 — connection-line maths (spec §4, §12).
 *   node --experimental-strip-types dashboard/src/lib/topologyLines.test.mjs
 *
 * The one thing worth testing here is the property the sqrt scaling exists for: a quiet
 * but ALIVE line must be visually distinguishable from a dead one even when the busiest
 * line on screen is two orders of magnitude bigger. Linear scaling passes every other
 * assertion in this file and fails that one.
 */
import assert from 'node:assert';
import { lineWidthPx, pairKey, shortAgo, MAX_LINE_PX, isRetired } from './topologyLines.ts';

// No traffic is 1px, never 0 — a line you cannot see is a line you cannot click.
assert.equal(lineWidthPx(0, 150), 1);
assert.equal(lineWidthPx(-5, 150), 1);
assert.equal(lineWidthPx(NaN, 150), 1);
assert.equal(lineWidthPx(10, 0), 1, 'no busiest line yet (empty window) must not divide by zero');
assert.equal(lineWidthPx(10, NaN), 1);

// The busiest line is exactly the cap, and nothing exceeds it.
assert.equal(lineWidthPx(150, 150), MAX_LINE_PX);
assert.equal(lineWidthPx(9999, 150), MAX_LINE_PX, 'thickness caps (spec §12)');

// THE POINT: 3 messages against a 150-message busiest line must still be thicker than
// silence. This is the assertion linear scaling fails — 1 + 4*(3/150) rounds to 1px,
// identical to a dead line.
assert.ok(lineWidthPx(3, 150) > lineWidthPx(0, 150),
  'a quiet line must not render identically to a dead one');

// Monotonic: more traffic is never thinner.
let prev = 0;
for (const c of [0, 1, 3, 10, 40, 90, 150]) {
  const w = lineWidthPx(c, 150);
  assert.ok(w >= prev, `width must not decrease: ${c} gave ${w} after ${prev}`);
  assert.ok(w >= 1 && w <= MAX_LINE_PX, `width out of range at ${c}: ${w}`);
  prev = w;
}

// A pair is unordered — both directions are one line, so they must hash the same.
assert.equal(pairKey('gm', 'build'), pairKey('build', 'gm'));
assert.equal(pairKey('gm', 'build'), 'build|gm');
assert.equal(pairKey('a', 'a'), 'a|a');

// Relative time. `now` is injected so this is deterministic rather than clock-dependent.
const T = Date.parse('2026-09-29T12:00:00Z');
assert.equal(shortAgo(null, T), 'never');
assert.equal(shortAgo(undefined, T), 'never');
assert.equal(shortAgo('not a date', T), 'never');
assert.equal(shortAgo('2026-09-29T11:59:30Z', T), 'just now');
assert.equal(shortAgo('2026-09-29T11:45:00Z', T), '15m ago');
assert.equal(shortAgo('2026-09-29T09:00:00Z', T), '3h ago');
assert.equal(shortAgo('2026-09-26T12:00:00Z', T), '3d ago');
// A future stamp (clock skew between machines is normal in this fleet) reads as "just
// now" rather than a negative age.
assert.equal(shortAgo('2026-09-29T12:05:00Z', T), 'just now');

console.log('topologyLines: all assertions passed');

// ── partitionTopology: the tree must render every agent it is given ──────────────────────
// review found the fleet's only DOWN agent invisible in Topology (2026-09-30): gm-g2 is also
// tier T0, the old code used a singular find() for the root, and the orphan bucket only
// collected T2/T3 — so it matched no bucket and vanished. Header said "showing 1 of 14" over
// a graph of thirteen live boxes. Spec §2 says nothing important is hidden; §15 says any down
// agent is findable in two seconds. These assert the invariant, not the symptom.
import { partitionTopology } from './topologyLines.ts';

function allRendered(agents, label) {
  const p = partitionTopology(agents);
  const seen = [
    ...(p.root ? [p.root] : []),
    ...p.leads,
    ...Object.values(p.workersByLead).flat(),
    ...p.rest,
  ].map((a) => a.id);
  assert.deepEqual([...seen].sort(), agents.map((a) => a.id).sort(),
    `${label}: every agent must be rendered exactly once — got ${JSON.stringify(seen)}`);
  assert.equal(new Set(seen).size, seen.length, `${label}: no agent may be rendered twice`);
  return p;
}

// THE REGRESSION: a second T0 that is the only down agent.
{
  const p = allRendered([
    { id: 'gm', tier: 'T0' },
    { id: 'gm-g2', tier: 'T0' },          // retired predecessor, alive false, no parent
    { id: 'build', tier: 'T1' },
    { id: 'builder-1', tier: 'T2', parent: 'build' },
  ], 'second T0');
  assert.equal(p.root.id, 'gm');
  assert.ok(p.rest.some((a) => a.id === 'gm-g2'), 'the extra T0 must land in rest, not nowhere');
}

// ── FIX 2 (2026-09-30): root selection had no liveness preference ────────────────────────
// find() = first-in-array. The block above passes today only because `gm` happens to be
// listed first — it does not exercise liveness at all (neither fixture agent even sets
// `alive`). These do, and the first one deliberately lists the retired agent FIRST so an
// order-lucky fix cannot pass it.

// (a) The live T0 roots the tree even when it is not first in the array.
{
  const p = allRendered([
    { id: 'gm-g2', tier: 'T0', alive: false, retired_at: '2026-09-29T20:34:04Z' },
    { id: 'gm', tier: 'T0', alive: true },
    { id: 'build', tier: 'T1' },
    { id: 'builder-1', tier: 'T2', parent: 'build' },
  ], 'retired T0 listed before the live one');
  assert.equal(p.root.id, 'gm', 'the LIVE T0 must root the tree regardless of array order');
  assert.ok(p.rest.some((a) => a.id === 'gm-g2'), 'the retired T0 still renders, just not as root');
}

// (b)'s repro, tested at this layer too (defense in depth, not just relying on the caller to
// filter retired out of the Down view): if a retired T0 is the ONLY T0 in the array — the
// exact shape Topology received under the Down filter before fix (b) — it must still not
// become root. No root beats a wrong one.
{
  const p = partitionTopology([
    { id: 'gm-g2', tier: 'T0', alive: false, retired_at: '2026-09-29T20:34:04Z' },
  ]);
  assert.equal(p.root, undefined, 'a retired-only T0 array must not crown a root');
  assert.deepEqual(p.rest.map((a) => a.id), ['gm-g2'], 'the retired agent still renders as a node');
}

// MUST NOT REGRESS (af199c1, "the graph silently dropped agents, including the only DOWN
// one"): a genuinely down T0 — NOT retired, just unreachable — with no live alternative must
// still root the tree. Retired is what disqualifies a root candidate; merely being down is not.
{
  const p = allRendered([
    { id: 'gm', tier: 'T0', alive: false },
    { id: 'build', tier: 'T1' },
  ], 'genuinely down T0, no retired_at');
  assert.equal(p.root?.id, 'gm', 'a down-but-not-retired T0 still roots rather than the tree going rootless');
}

// A worker whose parent is not a lead, and one with no parent at all.
{
  const p = allRendered([
    { id: 'gm', tier: 'T0' },
    { id: 'build', tier: 'T1' },
    { id: 'orphan-a', tier: 'T2' },                        // no parent
    { id: 'orphan-b', tier: 'T2', parent: 'nobody' },      // parent is not a lead
    { id: 'orphan-c', tier: 'T2', parent: 'gm' },          // parent is the root, not a lead
    { id: 'builder-1', tier: 'T2', parent: 'build' },
  ], 'unparented workers');
  assert.deepEqual(p.workersByLead.build.map((a) => a.id), ['builder-1']);
  assert.deepEqual(p.rest.map((a) => a.id).sort(), ['orphan-a', 'orphan-b', 'orphan-c']);
}

// An unknown tier, and a missing tier — neither may disappear.
allRendered([
  { id: 'gm', tier: 'T0' },
  { id: 'weird', tier: 'T9' },
  { id: 'untyped' },
], 'unknown tiers');

// Degenerate shapes.
allRendered([], 'empty');
allRendered([{ id: 'solo', tier: 'T2', parent: 'gone' }], 'no root at all');
{
  const p = partitionTopology([]);
  assert.equal(p.root, undefined);
  assert.deepEqual(p.leads, []);
  assert.deepEqual(p.rest, []);
}

console.log('partitionTopology: every-agent-rendered invariant holds');

// ── FIX 2 ADDENDUM (2026-09-30, gm): generalize to ANY retired seat ───────────────────────
// build's own gen1->gen2 rotation produced `build-g2` the same night gm-g2 was found — every
// rotation leaves one of these behind, for any seat. isRetired() must key off explicit
// registry fields only, never an id shape (`-gN` etc.), or the next rotation with a
// differently-shaped id repeats this bug.

// isRetired(): the field/value contract this fix keys on, verified live against the real API
// tonight (both gm-g2 and build-g2 carry retired_at; `status` was NOT reliable pre-fix —
// observed 'offline' for one and transiently 'spawning' for the other — so status alone is
// covered too, for when the API side of this fix lands an explicit state).
assert.equal(isRetired({ retired_at: '2026-09-30T08:57:21Z' }), true, 'retired_at present -> retired');
assert.equal(isRetired({ retired_at: null, status: 'retired' }), true, 'status "retired" -> retired');
assert.equal(isRetired({ retired_at: null, status: 'archived' }), true, 'status "archived" -> retired');
assert.equal(isRetired({ retired_at: null, status: 'offline' }), false, 'merely offline is NOT retired');
assert.equal(isRetired({ status: 'spawning' }), false, 'merely spawning is NOT retired');
assert.equal(isRetired({}), false, 'no signal at all -> not retired');
// THE GENERALIZATION PROOF: no `-g` suffix, no "gm"/"build" in the id — an id-pattern check
// would pass every fixture above and still fail this one.
assert.equal(isRetired({ id: 'scout', retired_at: '2026-09-30T00:00:00Z' }), true,
  'retired is a field, not an id shape — a non "-gN" id must still read as retired');

// TWO retired ghosts SIDE BY SIDE (a retired T0 AND a retired T1), next to live ones — the
// exact composition Agents.tsx runs (`agents.filter(a => !isRetired(a))` before handing the
// result to partitionTopology). Assert neither retired seat draws anywhere in the tree: not
// root, not leads, not workersByLead, not even `rest`. That is the "excluded BY DEFAULT from
// the tree" half of the addendum; partitionTopology's own "never dropped" invariant is exactly
// why this must happen in the filter the caller applies, not by silently special-casing inside
// partitionTopology itself (see the note on TopologyPartition.rest).
{
  const raw = [
    { id: 'gm-g2', tier: 'T0', alive: false, retired_at: '2026-09-29T20:34:04Z' },      // retired T0, has the "-gN" shape
    { id: 'build-g2', tier: 'T1', alive: false, retired_at: '2026-09-30T08:57:21Z' },   // retired T1, has the "-gN" shape
    { id: 'scout', tier: 'T2', parent: 'build', status: 'retired' },                     // retired WORKER, no "-gN" shape at all
    { id: 'gm', tier: 'T0', alive: true },
    { id: 'build', tier: 'T1', alive: true },
    { id: 'builder-1', tier: 'T2', parent: 'build', alive: false },  // genuinely down, NOT retired
  ];
  const visible = raw.filter((a) => !isRetired(a));
  assert.deepEqual(visible.map((a) => a.id).sort(), ['build', 'builder-1', 'gm'],
    'exactly the three non-retired seats remain — this IS the denominator gm asked to check');

  const p = allRendered(visible, 'two retired ghosts + one retired worker, filtered before partitioning');
  const drawnIds = [
    ...(p.root ? [p.root.id] : []),
    ...p.leads.map((a) => a.id),
    ...Object.values(p.workersByLead).flat().map((a) => a.id),
    ...p.rest.map((a) => a.id),
  ];
  for (const ghost of ['gm-g2', 'build-g2', 'scout']) {
    assert.ok(!drawnIds.includes(ghost), `retired seat "${ghost}" must not appear anywhere in the tree`);
  }
  // MUST NOT REGRESS (af199c1): the genuinely down (not retired) worker is NOT collateral
  // damage of this fix — it still comes through the filter (isRetired is false for it) and
  // still renders, exactly where a live worker would, so it can still show red and still
  // appear under the Down filter upstream in Agents.tsx.
  assert.equal(p.root.id, 'gm', 'the live T0 still roots');
  assert.deepEqual(p.workersByLead.build?.map((a) => a.id), ['builder-1'],
    'the genuinely-down worker still renders under its lead, unaffected by the retired-ghost filter');
}

console.log('isRetired: generalizes off registry fields, not id shape — FIX 2 addendum holds');
