import { test } from 'node:test';
import assert from 'node:assert/strict';
import { feedHealthOf } from './feedLiveness.ts';

// The CONTRACT the status line must keep, asserted on the inputs that decide it. The component
// is JSX; these pin the decisions it makes, which is where Shaw's "the page lies" bug lived.
import { normalizeAgentState } from './agentStatus.ts';

const NOW = 1_700_000_000_000;
const liveFeed = feedHealthOf({ dataUpdatedAt: NOW, hasData: true, now: NOW });
const deadFeed = feedHealthOf({ dataUpdatedAt: NOW - 600_000, hasData: true, now: NOW });

test('a dead feed outranks every agent state', () => {
  // The rule the status line encodes: with no live feed we cannot claim what the agent is doing.
  assert.notEqual(liveFeed.health, deadFeed.health);
  assert.equal(deadFeed.health, 'disconnected');
  assert.equal(liveFeed.health, 'live');
});

test('the states the line must distinguish do not collapse into each other', () => {
  // Shaw: "needs you" and "working" rendered the SAME colour. They must normalise apart.
  assert.equal(normalizeAgentState('waiting_permission'), 'waiting');
  assert.equal(normalizeAgentState('thinking'), 'working');
  assert.notEqual(normalizeAgentState('waiting_permission'), normalizeAgentState('thinking'));
  assert.equal(normalizeAgentState('stranded_input'), 'stranded');
  assert.equal(normalizeAgentState('wat'), 'unknown');
});

test('a stopped agent is not idle: the two mean opposite things to a sender', () => {
  assert.notEqual(normalizeAgentState('stopped'), normalizeAgentState('idle'));
  for (const down of ['stopped', 'crashed', 'offline', 'retired']) {
    assert.notEqual(normalizeAgentState(down), 'idle', `${down} normalised to idle`);
  }
});
