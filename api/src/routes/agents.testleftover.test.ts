/**
 * The agents list shows one row per seat: it leaves out a parked rotation leftover and a retired past generation of
 * any lineage, test or real, the way the gateway's /agents does. It never hides a row that is alive, a canonical root
 * or a root's live head. These call the predicate the route itself calls (hiddenFromListing), not a copy of it.
 *
 * Run: npx tsx --test src/routes/agents.testleftover.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isGenerationLeftover, hiddenFromListing } from './agents.js';
import { buildCanonicalIndex } from '../services/rotation-leftovers.js';

const defs = {
  'qa-demo': { test: true, lineage_root: 'qa-demo', status: 'online' },
  'qa-demo-gen1': { lineage_root: 'qa-demo', status: 'retired' },
  'dev-demo': { lineage_root: 'dev-demo', status: 'online' },
  'dev-demo-gen1': { lineage_root: 'dev-demo', status: 'retired' },
  'dev-demo-gen2': { lineage_root: 'dev-demo', status: 'parked' },
  // A canonical root of its own whose name merely looks like a generation of dev-demo.
  'dev-demo-g3': { lineage_root: 'dev-demo-g3', status: 'parked' },
  'pm-demo': { lineage_root: 'pm-demo', status: 'online' },
} as Record<string, Record<string, unknown>>;

const index = buildCanonicalIndex({
  'qa-demo': { root: 'qa-demo', tmux_session: 'qa-demo' },
  'dev-demo': { root: 'dev-demo', tmux_session: 'dev-demo' },
  'dev-demo-g3': { root: 'dev-demo-g3', tmux_session: 'dev-demo-g3' },
  'pm-demo': { root: 'pm-demo', tmux_session: 'pm-demo-gen4' },
});

const hidden = (id: string, alive = false, idx = index) =>
  hiddenFromListing({ id, def: defs[id], defs, alive, index: idx });

test('a parked -genN leftover of a real root is hidden', () => {
  assert.equal(hidden('dev-demo-gen2'), true);
});

test('a retired generation of a real root is hidden', () => {
  assert.equal(hidden('dev-demo-gen1'), true);
  // The registry's own evidence (retired + lineage_root) is enough without the canonical table.
  assert.equal(hidden('dev-demo-gen1', false, null), true);
});

test('a retired generation of a test lineage is still hidden', () => {
  assert.equal(hidden('qa-demo-gen1'), true);
  assert.equal(isGenerationLeftover({ id: 'qa-demo-x', def: { status: 'retired', test: true }, defs, alive: false, index }), true);
});

test('a parked -gN row that is itself a canonical root is kept', () => {
  assert.equal(hidden('dev-demo-g3'), false);
});

test('an alive leftover is kept, parked or retired', () => {
  assert.equal(hidden('dev-demo-gen2', true), false);
  assert.equal(hidden('dev-demo-gen1', true), false);
  assert.equal(hidden('qa-demo-gen1', true), false);
});

test("a retired row that is a root's live head is kept", () => {
  const d = { ...defs, 'pm-demo-gen4': { lineage_root: 'pm-demo', status: 'retired' } };
  assert.equal(hiddenFromListing({ id: 'pm-demo-gen4', def: d['pm-demo-gen4'], defs: d, alive: false, index }), false);
});

test('a retired generation whose root is not in the registry is kept', () => {
  const def = { lineage_root: 'sample-gone', status: 'retired' };
  assert.equal(hiddenFromListing({ id: 'sample-gone-gen1', def, defs: { ...defs, 'sample-gone-gen1': def }, alive: false, index }), false);
});

test('a retired root is not a generation of itself', () => {
  const def = { lineage_root: 'example-seat', status: 'retired' };
  assert.equal(hiddenFromListing({ id: 'example-seat', def, defs: { 'example-seat': def }, alive: false, index: null }), false);
});

test('the live seats stay', () => {
  for (const id of ['qa-demo', 'dev-demo', 'pm-demo']) assert.equal(hidden(id), false, id);
});
