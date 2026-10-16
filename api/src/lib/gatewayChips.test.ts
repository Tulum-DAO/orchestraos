/**
 * gatewayChips: /api/agents carries the gateway's chip, never blocks a poll, and keeps the last good
 * answer when the gateway is down. Run: npx tsx --test src/lib/gatewayChips.test.ts (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { chipsFromAgents, createChipSource, CHIP_TTL_MS, type ChipDeps } from './gatewayChips.js';

const FIXTURE = JSON.parse(readFileSync(new URL('../../../contract/fixtures/agent-chips.json', import.meta.url), 'utf8'));
const BODY = { ok: true, agents: FIXTURE.map((c: any) => ({ id: c.row.id, chip: c.chip })) };

function deps(over: Partial<ChipDeps> & { clock?: { t: number } } = {}) {
  const clock = over.clock || { t: 0 };
  const calls: string[] = [];
  const d: ChipDeps = {
    token: () => 'tok', gatewayUrl: () => 'http://gw.test', now: () => clock.t,
    fetchJson: async (url) => { calls.push(url); return { status: 200, body: BODY }; },
    ...over,
  };
  return { d, calls, clock };
}

test('the published fixture parses into one chip per agent', () => {
  const m = chipsFromAgents(BODY);
  assert.equal(m.size, FIXTURE.length);
  assert.deepEqual(m.get('gm'), FIXTURE[0].chip);
});

test('a row with a malformed chip is skipped, not guessed', () => {
  const m = chipsFromAgents({ agents: [{ id: 'a', chip: { status: 'busy' } }, { id: 'b' }, { chip: FIXTURE[0].chip }] });
  assert.equal(m.size, 0);
});

test('the first poll does not wait for the gateway; the next one has the chips', async () => {
  const { d, calls } = deps();
  const src = createChipSource(d);
  assert.equal(src.current().size, 0);        // served at once, refresh kicked in the background
  await new Promise((r) => setImmediate(r));
  assert.equal(calls.length, 1);
  assert.equal(src.current().get('gm')?.status, 'working');
});

test('a gateway that goes down keeps the last good chips', async () => {
  const clock = { t: 0 };
  let up = true;
  const { d } = deps({ clock, fetchJson: async () => { if (!up) throw new Error('down'); return { status: 200, body: BODY }; } });
  const src = createChipSource(d);
  await src.refresh();
  up = false; clock.t += CHIP_TTL_MS + 1;
  src.current();
  await new Promise((r) => setImmediate(r));
  assert.equal(src.current().get('gm')?.provider, 'claude');
});

test('no gateway token means no call and no chips', async () => {
  const { d, calls } = deps({ token: () => '' });
  const src = createChipSource(d);
  await src.refresh();
  assert.equal(calls.length, 0);
  assert.equal(src.current().size, 0);
});
