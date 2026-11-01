import { test } from 'node:test';
import assert from 'node:assert/strict';
import { needsYouView } from './needsYou.ts';

test('Needs You: per agent, most waiting first, pending only; empty and failed say so', () => {
  const v = needsYouView({ items: [
    { agent_id: 'dev-demo', status: 'pending' }, { agent_id: 'qa-demo', status: 'pending' },
    { agent_id: 'qa-demo', status: 'pending' }, { agent_id: 'pm-demo', status: 'approved' }, { agent_id: null, status: 'pending' },
  ] }, false);
  assert.deepEqual(v, { state: 'rows', rows: [
    { kind: 'agent', key: 'agent:qa-demo', agent: 'qa-demo', count: 2, label: '2 cards waiting for you' },
    { kind: 'agent', key: 'agent:dev-demo', agent: 'dev-demo', count: 1, label: '1 card waiting for you' },
  ] });
  assert.deepEqual(needsYouView({ items: [{ agent_id: 'pm-demo', status: 'denied' }] }, false), { state: 'empty' });
  assert.deepEqual(needsYouView({}, false), { state: 'empty' });
  assert.deepEqual(needsYouView(undefined, false), { state: 'loading' });
  assert.deepEqual(needsYouView(undefined, true), { state: 'error' }, 'a failed read is never calm');
});

test('pending questionnaires get one row, counted as the bell counts them', () => {
  const v = needsYouView({ items: [] }, false, { questionnaires: [{ status: 'pending' }, { status: 'answered' }] });
  assert.deepEqual(v, { state: 'rows', rows: [{ kind: 'questionnaires', key: 'questionnaires', count: 1, label: '1 questionnaire waiting for your input' }] });
  const both = needsYouView({ items: [{ agent_id: 'qa-demo', status: 'pending' }] }, false, { pending: 2 });
  assert.deepEqual(both.state === 'rows' && both.rows.map((r) => r.label), ['1 card waiting for you', '2 questionnaires waiting for your input']);
  assert.deepEqual(needsYouView({ items: [] }, false, { pending: 0, questionnaires: [] }), { state: 'empty' });
});
