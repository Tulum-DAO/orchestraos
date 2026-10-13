import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sectionFor, groupForRail, needsYouCount, providersVary, contextLabel, contextHigh, showsContext, type RailAgent } from './agentRail.ts';

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
  assert.equal(sectionFor(a('x', 'queued_input')), 'needsYou');        // accepted, never ran: needs a re-send
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

// ---------------------------------------------------------------------------
// providersVary — the runtime badge must disappear when it distinguishes nothing.
// ---------------------------------------------------------------------------

test('the badge is HIDDEN when every row carries the same runtime', () => {
  // The defect: a column printing "CL" on all 43 rows, spending width to say nothing.
  const rows = ['a', 'b', 'c'].map((id) => a(id, 'idle', { provider: 'claude' }));
  assert.equal(providersVary(rows), false);
});

test('the badge is SHOWN as soon as two distinct runtimes are on screen', () => {
  const rows = [a('x', 'idle', { provider: 'claude' }), a('y', 'idle', { provider: 'codex' })];
  assert.equal(providersVary(rows), true);
});

test('an UNKNOWN runtime is not a second value', () => {
  // 138 of 320 live rows carry no runtime. If absence counted as variety the badge would be
  // permanently on by accident — which is the exact bug this function exists to prevent.
  assert.equal(providersVary([
    a('x', 'idle', { provider: 'claude' }),
    a('y', 'idle'),
    a('z', 'idle', { provider: '' }),
    a('w', 'idle', { provider: 'not-a-runtime' }),
  ]), false);
});

test('runtime matching is case-insensitive, so CLAUDE and claude are ONE value', () => {
  assert.equal(providersVary([
    a('x', 'idle', { provider: 'Claude' }),
    a('y', 'idle', { provider: 'CLAUDE' }),
  ]), false);
});

test('an empty rail does not show the badge', () => {
  assert.equal(providersVary([]), false);
});

test('context label: the detector string is shown as a whole percent, for every runtime', () => {
  // /api/agents carries context_pct as the detector wrote it: "NN%" (claude from its own meter,
  // codex from the rollout's token counts) or '' when there is no reading.
  assert.equal(contextLabel('42%'), '42%');
  assert.equal(contextLabel('7%'), '7%');
  assert.equal(contextLabel('100%'), '100%');
  assert.equal(contextLabel('0%'), '0%');      // a measured zero is a reading
});

test('context label: only the detector shape "NN%" is a reading (codex review, #436)', () => {
  // scripts/agent-status.py writes an integer followed by '%' or ''. Anything else is not a
  // reading from it, so it must not be dressed up as one.
  for (const v of ['42', '41.6%', '4 2%', '42%%', '42 %x', '0042%', ' 42% ', '42% ', ' 42%', 42, 41.6]) {
    assert.equal(contextLabel(v as unknown), null, `contextLabel(${JSON.stringify(v)}) was not null`);
  }
});

test('context label: no reading is null, never a made-up number', () => {
  // A blank must not render as 0% (that reads as "empty context", a claim) — the row shows a dash.
  for (const v of ['', '   ', undefined, null, '--', 'n/a', '%', 'abc%', '-5%', '101%', '1e3', NaN, -1, 101, {}, []]) {
    assert.equal(contextLabel(v as unknown), null, `contextLabel(${JSON.stringify(v)}) was not null`);
  }
});

test('context is high from 80% up, the band where seats rotate', () => {
  assert.equal(contextHigh('79%'), false);
  assert.equal(contextHigh('80%'), true);
  assert.equal(contextHigh('95%'), true);
  assert.equal(contextHigh(''), false);
  assert.equal(contextHigh(undefined), false);
});

test('only a running row shows a context figure; a row that is not running shows none', () => {
  // 299 of 320 live rows sit under "Not running": a dash on each would be a column of noise, and a
  // number on a stopped seat is a stale reading presented as current.
  assert.equal(showsContext(a('x', 'working')), true);
  assert.equal(showsContext(a('x', 'idle')), true);
  assert.equal(showsContext(a('x', 'waiting')), true);
  assert.equal(showsContext(a('x', 'idle', { has_pending_menu: true })), true);
  for (const st of ['stopped', 'offline', 'crashed', 'retired', 'unknown', undefined]) {
    assert.equal(showsContext(a('x', st)), false, `status=${st} showed context`);
  }
});
