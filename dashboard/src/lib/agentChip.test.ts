import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { asChip, chipNeedsYou, contextLeftLabel, contextLeftLow, providerTitle, railStatus, railPendingMenu, kindLabel } from './agentChip.ts';
import { sectionFor } from './agentRail.ts';

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

test('the rail follows the chip, so the dashboard says what every surface says', () => {
  // codex R1: a chip that says down must not draw as working (a stalled seat sat in Working).
  assert.equal(railStatus({ status: 'down' }, 'stalled'), 'crashed');
  assert.equal(sectionFor({ id: 'x', status: railStatus({ status: 'down' }, 'stalled') }), undefined);
  assert.equal(railStatus({ status: 'down' }, 'offline'), 'offline');
  assert.equal(railStatus({ status: 'needs_you' }, 'idle'), 'waiting');       // waiting on its own card
  assert.equal(sectionFor({ id: 'x', status: railStatus({ status: 'needs_you' }, 'idle') }), 'needsYou');
  assert.equal(railStatus({ status: 'needs_you' }, 'stranded'), 'stranded');
  assert.equal(railStatus({ status: 'working' }, 'idle'), 'working');
  assert.equal(railStatus(undefined, 'stalled'), 'stalled');                  // no chip: detector stands
});

test('with a chip, a stale detector menu flag cannot outrank it', () => {
  // codex R2: sectionFor puts has_pending_menu first, so it must not ride along with a chip.
  const row = { id: 'x', status: railStatus({ status: 'working' }, 'working'),
                has_pending_menu: railPendingMenu({ status: 'working' }, true) };
  assert.equal(sectionFor(row), 'working');
  assert.equal(railPendingMenu(undefined, true), true);                      // no chip: the flag stands
});

test('services and external seats are labelled, and external claims no state', () => {
  const byName = Object.fromEntries(FIXTURE.map((c: any) => [c.chip.name, c.chip]));
  assert.equal(kindLabel(byName['watch-gateway']), 'Service');
  assert.equal(kindLabel(byName['remote-seat']), 'External');
  assert.equal(kindLabel(byName.gm), null);
  assert.ok(asChip(byName['remote-seat']));
  assert.equal(railStatus(byName['remote-seat'], 'external'), 'external');   // never turned into down
});
