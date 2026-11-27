// Relic-vs-crashed rule tests — must stay in lockstep with the iOS gateway
// (watch_gateway.py compute_agents). Run from api/: npm run test:folders (tsx runs the TypeScript source,
// so no build is needed; importing dist/ made this file fail on every clean checkout).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyNoSession, RELIC_SELF_STATES } from '../src/services/agent-status.js';

test('always_on agent whose blob claims alive -> crashed (needs attention)', () => {
  for (const s of ['running', 'active', 'working', 'ready', 'spawning', '']) {
    const r = classifyNoSession(true, s);
    assert.equal(r.status, 'crashed', `self-status ${JSON.stringify(s)}`);
    assert.equal(r.activity, 'No tmux session');
  }
});

test('always_on RELIC (self-reported dead) -> offline with honest reason', () => {
  // stopped/dead only: retired/archived are DECOMMISSIONED and read 'retired' (next test).
  for (const s of ['stopped', 'dead']) {
    const r = classifyNoSession(true, s);
    assert.equal(r.status, 'offline');
    assert.equal(r.activity, `Not running (self-reported ${s})`);
  }
});

test('DECOMMISSIONED (self-reported retired/archived) -> retired, whether or not the row claims always_on', () => {
  for (const alwaysOn of [true, false]) {
    for (const s of ['retired', 'archived']) {
      assert.deepEqual(classifyNoSession(alwaysOn, s),
        { status: 'retired', activity: `Decommissioned (self-reported ${s})` });
    }
  }
});

test('non-always_on agent -> offline regardless of blob', () => {
  assert.deepEqual(classifyNoSession(false, 'running'),
    { status: 'offline', activity: 'Not running' });
  assert.deepEqual(classifyNoSession(false, 'stopped'),
    { status: 'offline', activity: 'Not running (self-reported stopped)' });
});

test('pathological blob status (free-text diary) is not a relic marker', () => {
  const essay = 'RETIRING ~81% ctx (gen-7 ...) HANDOFF WRITTEN: ...';
  const r = classifyNoSession(true, essay);
  assert.equal(r.status, 'crashed');   // unknown text = claims-alive default
});

test('relic set matches the gateway rule exactly', () => {
  assert.deepEqual([...RELIC_SELF_STATES].sort(),
    ['archived', 'dead', 'retired', 'stopped']);
});
