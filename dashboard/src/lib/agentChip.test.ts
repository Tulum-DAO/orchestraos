import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { asChip, chipNeedsYou, contextLeftLabel, contextLeftLow, providerTitle } from './agentChip.ts';

const FIXTURE = JSON.parse(readFileSync(new URL('../../../contract/fixtures/agent-chips.json', import.meta.url), 'utf8'));

test('the published fixture draws as the contract says', () => {
  const byName = Object.fromEntries(FIXTURE.map((c: any) => [c.chip.name, c.chip]));
  assert.equal(contextLeftLabel(byName.gm), '92% left');
  assert.equal(contextLeftLabel(byName.ops), null);               // no reading: nothing, never 0% or 100%
  assert.equal(chipNeedsYou(byName.ops), true);                   // a card it filed is waiting on you
  assert.equal(chipNeedsYou(byName.gm), false);
  assert.equal(providerTitle('Claude', byName.gm), 'Runs on Claude · Opus 5.5');
  assert.equal(providerTitle('Claude', byName.builder), 'Runs on Claude');
});

test('low context left turns amber at 20% or less', () => {
  assert.equal(contextLeftLow({ context_left_pct: 20 }), true);
  assert.equal(contextLeftLow({ context_left_pct: 21 }), false);
  assert.equal(contextLeftLow({ context_left_pct: null }), false);
});

test('a malformed chip is no chip', () => {
  for (const v of [null, 'x', {}, { status: 3 }]) assert.equal(asChip(v), undefined);
  for (const v of [NaN, -1, 101, '50' as any]) assert.equal(contextLeftLabel({ context_left_pct: v }), null);
});
