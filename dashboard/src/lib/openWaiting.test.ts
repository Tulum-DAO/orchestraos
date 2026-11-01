import { test } from 'node:test';
import assert from 'node:assert/strict';
import { openWaitingCard, openNotice, pendingQuestionnaires } from './openWaiting.ts';

const card = (id: string, agent: string, at: string) => ({ id, agent_id: agent, created_at: at, status: 'pending' });
const CARDS = [card('req-new', 'qa-demo', '2026-10-31T02:00:00Z'), card('req-old', 'qa-demo', '2026-10-31T01:00:00Z'), card('req-dev', 'dev-demo', '2026-10-31T01:30:00Z')];

function harness(chat: string[] | Error, pending = CARDS) {
  const went: string[] = []; const focused: string[] = [];
  return {
    went, focused,
    deps: {
      navigate: (to: string) => went.push(to),
      focus: (a: string, id: string) => focused.push(`${a}:${id}`),
      readPending: async () => pending,
      readChatIds: async () => { if (chat instanceof Error) throw chat; return chat; },
    },
  };
}

test('a waiting card opens in the chat that draws it, focused by its id; the oldest first', async () => {
  const h = harness(['req-old', 'req-new']);
  assert.deepEqual(await openWaitingCard('qa-demo', h.deps), { opened: 'chat', id: 'req-old' });
  assert.deepEqual(h.focused, ['qa-demo:req-old']);
  assert.deepEqual(h.went, ['/agent/qa-demo']);
});

test('a card the chat does not draw (or a chat that cannot be read) opens on the Approvals page', async () => {
  const h = harness(['req-new']);
  assert.deepEqual(await openWaitingCard('qa-demo', h.deps), { opened: 'approvals', id: 'req-old' });
  assert.deepEqual(h.went, ['/approvals?focus=req-old']);
  assert.deepEqual(h.focused, []);
  const down = harness(new Error('down'));
  assert.deepEqual(await openWaitingCard('qa-demo', down.deps), { opened: 'approvals', id: 'req-old' });
});

test('nothing waiting any more, or the feed failing, opens nothing and says why', async () => {
  const gone = harness([], []);
  const r = await openWaitingCard('qa-demo', gone.deps);
  assert.deepEqual(r, { opened: null, reason: 'gone' });
  assert.deepEqual(gone.went, []);
  assert.match(openNotice('qa-demo', r)!, /Nothing from qa-demo is waiting any more/);
  const fail = await openWaitingCard('qa-demo', { navigate: () => {}, readPending: async () => { throw new Error('x'); } });
  assert.deepEqual(fail, { opened: null, reason: 'error' });
  assert.match(openNotice('qa-demo', fail)!, /try again/);
  assert.equal(openNotice('qa-demo', { opened: 'chat', id: 'x' }), null);
});

test('pending questionnaires: the route\'s count, else the pending ones in its list', () => {
  assert.equal(pendingQuestionnaires({ pending: 3, questionnaires: [] }), 3);
  assert.equal(pendingQuestionnaires({ questionnaires: [{ status: 'pending' }, { status: 'answered' }, { status: 'pending' }] }), 2);
  assert.equal(pendingQuestionnaires({ pending: 0, questionnaires: [] }), 0);
  assert.equal(pendingQuestionnaires(undefined), 0);
});
