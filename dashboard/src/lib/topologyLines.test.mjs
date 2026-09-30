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
import { lineWidthPx, pairKey, shortAgo, MAX_LINE_PX } from './topologyLines.ts';

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
