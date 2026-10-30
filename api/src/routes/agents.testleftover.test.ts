/**
 * The agents list leaves out a retired generation of a test lineage (so the sidebar never shows it as "Not running"),
 * the way the gateway's /agents does; every other row, a real lineage's leftovers included, is unchanged.
 *
 * Run: npx tsx --test src/routes/agents.testleftover.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isTestLeftover } from './agents.js';

const defs = {
  'qa-demo': { test: true, lineage_root: 'qa-demo', status: 'online' },
  'qa-demo-gen1': { lineage_root: 'qa-demo', status: 'retired' },
  'dev-demo': { lineage_root: 'dev-demo', status: 'online' },
  'dev-demo-gen1': { lineage_root: 'dev-demo', status: 'retired' },
  'dev-demo-gen2': { lineage_root: 'dev-demo', status: 'parked' },
} as Record<string, Record<string, unknown>>;

test('a retired generation of a test lineage is left out', () => {
  assert.equal(isTestLeftover(defs['qa-demo-gen1'], defs), true);
  assert.equal(isTestLeftover({ status: 'retired', test: true }, defs), true);
});

test('the live test seat and every real lineage row stay', () => {
  for (const id of ['qa-demo', 'dev-demo', 'dev-demo-gen1', 'dev-demo-gen2']) assert.equal(isTestLeftover(defs[id], defs), false, id);
});
