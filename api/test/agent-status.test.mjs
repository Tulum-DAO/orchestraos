// Relic-vs-crashed rule tests — must stay in lockstep with the iOS gateway
// (watch_gateway.py compute_agents). Run: node --test test/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyNoSession, RELIC_SELF_STATES } from '../dist/services/agent-status.js';

test('always_on agent whose blob claims alive -> crashed (needs attention)', () => {
  for (const s of ['running', 'active', 'working', 'ready', 'spawning', '']) {
    const r = classifyNoSession(true, s);
    assert.equal(r.status, 'crashed', `self-status ${JSON.stringify(s)}`);
    assert.equal(r.activity, 'No tmux session');
  }
});

test('always_on RELIC (self-reported dead) -> offline with honest reason', () => {
  for (const s of ['stopped', 'retired', 'dead', 'archived']) {
    const r = classifyNoSession(true, s);
    assert.equal(r.status, 'offline');
    assert.equal(r.activity, `Not running (self-reported ${s})`);
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
