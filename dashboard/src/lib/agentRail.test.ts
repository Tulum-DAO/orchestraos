import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sectionFor, groupForRail, needsYouCount, type RailAgent } from './agentRail.ts';

const a = (id: string, status?: string, extra: Partial<RailAgent> = {}): RailAgent =>
  ({ id, status, ...extra });

test('a pending menu outranks every detector state', () => {
  // The whole point: a question already on screen is the most actionable thing the rail has.
  for (const st of ['working', 'idle', 'thinking', 'stalled', 'offline', 'crashed', undefined]) {
    assert.equal(sectionFor(a('x', st, { has_pending_menu: true })), 'needsYou',
      `a menu was outranked by status=${st}`);
  }
});

test('waiting and stranded are the human-blocked states', () => {
  assert.equal(sectionFor(a('x', 'waiting')), 'needsYou');
  assert.equal(sectionFor(a('x', 'waiting_permission')), 'needsYou');  // detector vocab
  assert.equal(sectionFor(a('x', 'stranded')), 'needsYou');
  assert.equal(sectionFor(a('x', 'stranded_input')), 'needsYou');      // detector vocab
});

test('stalled is WORKING, not needs-you: a long turn must not cry for a human', () => {
  assert.equal(sectionFor(a('x', 'stalled')), 'working');
  assert.equal(sectionFor(a('x', 'working')), 'working');
  assert.equal(sectionFor(a('x', 'thinking')), 'working');   // detector alias
  assert.equal(sectionFor(a('x', 'running')), 'working');    // legacy alias
});

test('an UNKNOWN or absent state is never idle', () => {
  // "we do not know" rendering as "alive at the prompt" is absent evidence passing as evidence.
  assert.equal(sectionFor(a('x', undefined)), undefined);
  assert.equal(sectionFor(a('x', 'wat')), undefined);
  assert.equal(sectionFor(a('x', 'unknown')), undefined);
  assert.equal(sectionFor(a('x', 'idle')), 'idle');
  assert.equal(sectionFor(a('x', 'ready')), 'idle');         // legacy alias
});

test('down and retired agents are not in the three sections', () => {
  for (const st of ['stopped', 'crashed', 'offline', 'retired']) {
    assert.equal(sectionFor(a('x', st)), undefined, `${st} claimed a rail section`);
  }
});

test('NOTHING IS DROPPED: every input lands in exactly one bucket', () => {
  const agents = [
    a('menu', 'working', { has_pending_menu: true }), a('wait', 'waiting'),
    a('work', 'working'), a('slow', 'stalled'), a('idle1', 'idle'),
    a('dead', 'crashed'), a('mystery', undefined), a('old', 'retired'),
  ];
  const g = groupForRail(agents);
  const total = g.needsYou.length + g.working.length + g.idle.length + g.other.length;
  assert.equal(total, agents.length, 'the rail lost an agent');
  const seen = [...g.needsYou, ...g.working, ...g.idle, ...g.other].map((x) => x.id).sort();
  assert.deepEqual(seen, agents.map((x) => x.id).sort(), 'an agent was duplicated or dropped');
  assert.deepEqual(g.needsYou.map((x) => x.id), ['menu', 'wait']);
  assert.deepEqual(g.working.map((x) => x.id), ['work', 'slow']);
  assert.deepEqual(g.idle.map((x) => x.id), ['idle1']);
  assert.deepEqual(g.other.map((x) => x.id).sort(), ['dead', 'mystery', 'old']);
});

test('input order is preserved within a section, so the caller owns the sort', () => {
  const g = groupForRail([a('b', 'idle'), a('a', 'idle'), a('c', 'idle')]);
  assert.deepEqual(g.idle.map((x) => x.id), ['b', 'a', 'c']);
});

test('the needs-you badge counts people-blocked agents only', () => {
  assert.equal(needsYouCount([
    a('1', 'waiting'), a('2', 'working', { has_pending_menu: true }),
    a('3', 'working'), a('4', 'idle'), a('5', 'crashed'), a('6', 'stalled'),
  ]), 2);
  assert.equal(needsYouCount([]), 0);
});

test('an empty rail is empty, not a section of undefineds', () => {
  assert.deepEqual(groupForRail([]), { needsYou: [], working: [], idle: [], other: [] });
});
