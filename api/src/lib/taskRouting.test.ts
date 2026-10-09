/**
 * Task routing names only seats THIS install has: a project/client PM when it is registered, else
 * the install's manager (found by tier, whatever it is called), else nobody. Never a built-in seat.
 * Run: npx tsx --test src/lib/taskRouting.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { routeTask, installManager } from './taskRouting.js';

const REG = { boss: { tier: 'T0' }, 'pm-acme': { tier: 'T1' }, 'dev-acme': { tier: 'T2' } };

test('a registered project PM gets its project', () => {
  assert.equal(routeTask({ slug: 'acme' }, REG), 'pm-acme');
});

test('an unregistered PM is never invented: the manager gets it', () => {
  assert.equal(routeTask({ slug: 'northwind' }, REG), 'boss');
  assert.equal(routeTask({}, REG), 'boss');
});

test('the manager is found by tier, not by name', () => {
  assert.equal(installManager(REG), 'boss');
  assert.equal(installManager({ gm: { tier: 'T0' } }), 'gm');
});

test('an install with no seats routes to nobody', () => {
  assert.equal(routeTask({ slug: 'acme' }, {}), null);
});

test('no built-in seat name is ever returned', () => {
  for (const slug of ['infra', 'products', 'clients', 'orchestraos', undefined]) {
    const r = routeTask({ slug }, { gm: { tier: 'T0' } });
    assert.ok(r === 'gm', `${slug} -> ${r}`);
  }
});

test('the registry is read from the install, and an unreadable one routes to nobody', async () => {
  const { mkdtempSync, writeFileSync } = await import('fs');
  const { join } = await import('path');
  const { tmpdir } = await import('os');
  const { registeredAgents } = await import('./taskRouting.js');
  const d = mkdtempSync(join(tmpdir(), 'routing-'));
  assert.deepEqual(registeredAgents(d), {});
  writeFileSync(join(d, 'registry.json'), JSON.stringify({ agents: REG }));
  assert.equal(routeTask({ slug: 'acme' }, registeredAgents(d)), 'pm-acme');
});
