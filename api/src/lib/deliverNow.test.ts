/** Run: npx tsx --test src/lib/deliverNow.test.ts (from api/) */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sessionFor, held, deliverNow, type DeliverNowDeps } from './deliverNow.js';

function deps(over: Partial<DeliverNowDeps> = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  const d: DeliverNowDeps = {
    token: () => 'tok', gatewayUrl: () => 'http://gw.test',
    fetchJson: async (url, init) => { calls.push({ url, init }); return { status: 200, body: { ok: true } }; },
    ...over,
  };
  return { d, calls };
}

test('an agent id resolves to its tmux session as the inject route does', () => {
  const reg = { agents: { gm: { tmux_session: 'gm-g7' }, ops: {} } };
  assert.equal(sessionFor(reg, 'gm'), 'gm-g7');
  assert.equal(sessionFor(reg, 'ops'), 'ops');
  assert.equal(sessionFor(reg, 'unregistered:scratch'), 'scratch');
  assert.equal(sessionFor(reg, 'nobody'), null);
});

test('held asks the gateway with the bearer and an encoded session', async () => {
  const { d, calls } = deps();
  await held(d, 'a b');
  assert.equal(calls[0].url, 'http://gw.test/held?session=a%20b');
  assert.equal((calls[0].init.headers as any).Authorization, 'Bearer tok');
});

test('deliver-now posts only the session', async () => {
  const { d, calls } = deps();
  await deliverNow(d, 'gm');
  assert.equal(calls[0].url, 'http://gw.test/deliver-now');
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { session: 'gm' });
});

test('no gateway token is a 502 and no call', async () => {
  const { d, calls } = deps({ token: () => '' });
  assert.equal((await deliverNow(d, 'gm')).status, 502);
  assert.equal(calls.length, 0);
});
