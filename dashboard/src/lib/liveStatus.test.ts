import { test } from 'node:test';
import assert from 'node:assert/strict';
import { feedHealthOf } from './feedLiveness.ts';

// The CONTRACT the status line must keep, asserted on the inputs that decide it. The component
// is JSX; these pin the decisions it makes, which is where Shaw's "the page lies" bug lived.
import { normalizeAgentState, offersResume } from './agentStatus.ts';

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

// ---------------------------------------------------------------------------
// offersResume — a retired seat must not be offered resurrection.
// ---------------------------------------------------------------------------

test('Resume is offered for the three states that mean something FAILED', () => {
  for (const s of ['stopped', 'crashed', 'offline']) {
    assert.equal(offersResume(s), true, `${s} should offer Resume`);
  }
});

test('Resume is NOT offered for a RETIRED seat', () => {
  // retired = the intentionally-decommissioned `seat-gN` row every lineage rotation leaves
  // behind. Resuming it would put a generation somebody deliberately ended back on the fleet,
  // which is a different act from restarting something that fell over.
  assert.equal(offersResume('retired'), false);
});

test('Resume is NOT offered to a healthy or busy seat', () => {
  // Positive control in the other direction: the rule must distinguish, not just exclude one
  // value. A working agent is reachable and needs no restart.
  for (const s of ['idle', 'working', 'thinking', 'waiting', 'stranded', 'stalled', undefined]) {
    assert.equal(offersResume(s), false, `${s} should not offer Resume`);
  }
});
