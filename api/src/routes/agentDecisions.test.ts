import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decisionStatus, menuAnswerDecisions, decisionDelivery } from './agents.js';

test('a card reads as pending, answered, expired, withdrawn, dismissed or replaced', () => {
  const now = Date.parse('2026-10-26T12:00:00Z');
  assert.equal(decisionStatus({ status: 'pending', expires_at: '2026-10-27T00:00:00Z' }, now), 'pending');
  // nothing expires on its own: past expires_at it is still waiting; only a swept card reads as expired
  assert.equal(decisionStatus({ status: 'pending', expires_at: '2026-10-25T00:00:00Z' }, now), 'pending');
  assert.equal(decisionStatus({ status: 'expired', expires_at: '2026-10-25T00:00:00Z' }, now), 'expired');
  assert.equal(decisionStatus({ status: 'resumed' }, now), 'answered');
  assert.equal(decisionStatus({ status: 'answered' }, now), 'answered');
  assert.equal(decisionStatus({ status: 'pending', retired_at: '2026-10-25T00:00:00Z' }, now), 'withdrawn');
  assert.equal(decisionStatus({ status: 'retired' }, now), 'withdrawn');
  assert.equal(decisionStatus({ status: 'discarded' }, now), 'dismissed');
  assert.equal(decisionStatus({ status: 'pending', superseded_by: 'req-2' }, now), 'superseded');
});

test('menu answers: this session only, newest window, as one-line answered entries', () => {
  const since = Date.parse('2026-10-20T00:00:00Z');
  const lines = [
    JSON.stringify({ session: 'pm-demo', at: '2026-10-26T12:00:00Z', question: 'Proceed?', dismissed: true, option_n: null }),
    JSON.stringify({ session: 'pm-demo', at: '2026-10-26T12:05:00Z', question: 'Which one?', option_n: '2', label: 'The second', dismissed: false }),
    JSON.stringify({ session: 'other', at: '2026-10-26T12:06:00Z', question: 'Not ours', option_n: '1' }),
    JSON.stringify({ session: 'pm-demo', at: '2026-10-01T00:00:00Z', question: 'Too old', option_n: '1' }),
    'not json',
  ];
  const out = menuAnswerDecisions(lines, 'pm-demo', 'pm-demo', since);
  assert.equal(out.length, 2);
  assert.equal(out[0].kind, 'menu_answer');
  assert.equal(out[0].answer, 'dismiss');
  assert.equal(out[0].state, 'answered');
  assert.equal(out[1].option_n, '2');
  assert.deepEqual(out[1].menu, { options: [{ n: '2', label: 'The second' }] });
  assert.notEqual(out[0].id, out[1].id);
});

test('delivery: held while an answer waits for its agent, delivered once it is in the pane', () => {
  assert.equal(decisionDelivery({ status: 'answered', resumed_at: null }), 'held');
  assert.equal(decisionDelivery({ status: 'resumed', resumed_at: '2026-10-27T04:00:02Z' }), 'delivered');
  assert.equal(decisionDelivery({ status: 'answered', resumed_at: '2026-10-27T04:00:02Z' }), 'delivered');
  assert.equal(decisionDelivery({ status: 'pending' }), null);
  assert.equal(decisionDelivery({ status: 'expired' }), null);
});
