import { test } from 'node:test';
import assert from 'node:assert/strict';
import { feedHealthOf } from './feedLiveness.ts';

// The CONTRACT the status line must keep, asserted on the inputs that decide it. The component
// is JSX; these pin the decisions it makes, which is where Shaw's "the page lies" bug lived.
import { normalizeAgentState, offersResume, STATE_STYLE, QUEUED_LINE } from './agentStatus.ts';

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

// ---- queued_input (live 0fa16b8c7e, upstreamed) ---------------------------------------------
// The detector's queued_input: the CLI ACCEPTED a submit while busy and ended the turn without
// running it, so the text is still at the prompt. Before this it fell through to 'unknown' and
// the web painted it dark: an agent holding the person's message looked like no evidence at all.

test('queued_input normalises to its own state, not unknown, stranded, working or idle', () => {
  assert.equal(normalizeAgentState('queued_input'), 'queued');
  assert.equal(normalizeAgentState('queued'), 'queued');            // gateway / already-mapped vocab
  for (const other of ['unknown', 'stranded', 'working', 'idle']) {
    assert.notEqual(normalizeAgentState('queued_input'), other);
  }
});

test('queued has its own label and the stranded purple (same "needs a re-send" class)', () => {
  assert.equal(STATE_STYLE.queued.label, 'queued, not running');
  assert.equal(STATE_STYLE.queued.dot, STATE_STYLE.stranded.dot);
});

test('the queued status line says the message did not run, not that it will', () => {
  // Live's line read "a message will wait until it starts", which describes a queue that drains.
  // queued_input is the opposite: the turn ENDED and the message never ran; it needs a re-send.
  assert.match(QUEUED_LINE, /did not run/);
  assert.doesNotMatch(QUEUED_LINE, /will wait/);
});

test('a queued seat is not offered Resume: it is running, at its prompt', () => {
  assert.equal(offersResume('queued_input'), false);
});
