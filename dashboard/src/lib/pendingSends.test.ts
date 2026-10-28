import { test } from 'node:test';
import assert from 'node:assert/strict';
import { kindFor, userRows, visiblePending, afterIdle, IDLE_BACKSTOP_MS, type PendingSend } from './pendingSends.ts';
import { usePendingSends, rowIdsAtSend } from '../stores/pendingSends.ts';
import type { ChatItem } from './transcript.ts';

const p = (over: Partial<PendingSend>): PendingSend => ({
  id: 'p1', agentId: 'pm-demo', display: 'hello', text: 'hello', kind: 'sent', createdAt: 0, ...over,
});
const item = (over: Record<string, unknown>) => ({ kind: 'text', role: 'user', ...over }) as unknown as ChatItem;

test('kindFor: delivered to an idle agent is a plain sent bubble', () => {
  assert.equal(kindFor('delivered', 'idle'), 'sent');
});
test('kindFor: delivered mid-turn (working, thinking, stalled) gets the queued chip', () => {
  for (const s of ['working', 'thinking', 'stalled']) assert.equal(kindFor('delivered', s), 'queuedInTurn');
});
test('kindFor: held and store-queued are their own (amber) kinds', () => {
  assert.equal(kindFor('held'), 'held');
  assert.equal(kindFor('queued'), 'queued');
});

test('userRows: operator rows keyed by uuid; assistant and system rows skipped', () => {
  const rows = userRows([
    item({ uuid: 'u1', text: 'first' }),
    item({ role: 'assistant', uuid: 'a1', text: 'reply' }),
    item({ uuid: 's1', text: 'env', is_system: true }),
    item({ uuid: 'u2', text: 'second' }),
  ]);
  assert.deepEqual(rows, [{ id: 'u1', text: 'first' }, { id: 'u2', text: 'second' }]);
});
test('userRows: the ids do not shift when the window slides (uuid, never index)', () => {
  const a = userRows([item({ uuid: 'u1', text: 'x' }), item({ uuid: 'u2', text: 'y' })]);
  const b = userRows([item({ uuid: 'u2', text: 'y' }), item({ uuid: 'u3', text: 'z' })]);
  assert.equal(a[1].id, b[0].id);
});
test('userRows: a drained queued batch offers each entry', () => {
  const rows = userRows([{ kind: 'queued_batch', key: 'msg:1', entries: [{ body: 'one', sent_ts: 't1' }, { body: 'two', sent_ts: 't2' }] } as unknown as ChatItem]);
  assert.deepEqual(rows.map((r) => r.text), ['one', 'two']);
});

test('a bubble whose text carries an attachment marker matches the row the agent logged', () => {
  const sent = '[IMAGE: /home/demo/.orchestra/state/uploads/1791455816_dr29ix.png] what is this?';
  assert.equal(visiblePending([p({ text: sent })], [{ id: 'r1', text: sent }]).length, 0);
});
test('a row that was on screen at send time never retires the bubble', () => {
  const bubble = p({ text: 'yes', priorRowIds: new Set(['old']) });
  assert.equal(visiblePending([bubble], [{ id: 'old', text: 'yes' }]).length, 1);
});

test('idle backstop: busy marks the bubble as read-in-progress, the next idle drops it', () => {
  const busy = afterIdle([p({})], false, 1000);
  assert.equal(busy[0].sawBusy, true);
  assert.equal(afterIdle(busy, true, 2000).length, 0);
});
test('idle backstop: an idle agent never seen busy keeps a fresh bubble', () => {
  assert.equal(afterIdle([p({ createdAt: 0 })], true, 1000).length, 1);
});
test('idle backstop: ...until the floor passes (a short turn the status feed missed)', () => {
  assert.equal(afterIdle([p({ createdAt: 0 })], true, IDLE_BACKSTOP_MS).length, 0);
});
test('idle backstop: held and stored bubbles wait for their own row, not for idle', () => {
  const held = afterIdle([p({ kind: 'held', sawBusy: true }), p({ id: 'p2', kind: 'queued', sawBusy: true })], true, 60_000);
  assert.equal(held.length, 2);
});

test('store: a bubble whose row already arrived during the POST is never added', () => {
  const s = usePendingSends.getState();
  s.setRows('agent-a', [{ id: 'r1', text: 'old' }]);
  const prior = rowIdsAtSend('agent-a');
  s.setRows('agent-a', [{ id: 'r1', text: 'old' }, { id: 'r2', text: 'fresh message' }]);
  s.add(p({ agentId: 'agent-a', text: 'fresh message', priorRowIds: prior }));
  assert.equal(usePendingSends.getState().byAgent['agent-a'], undefined);
});
test('store: the arriving row retires the bubble; nothing else does', () => {
  const s = usePendingSends.getState();
  s.setRows('agent-b', [{ id: 'r1', text: 'yes' }]);
  s.add(p({ agentId: 'agent-b', text: 'yes', priorRowIds: rowIdsAtSend('agent-b') }));
  assert.equal(usePendingSends.getState().byAgent['agent-b'].length, 1);
  s.setRows('agent-b', [{ id: 'r1', text: 'yes' }, { id: 'r9', text: 'look into it' }]);
  assert.equal(usePendingSends.getState().byAgent['agent-b'].length, 1);
  s.setRows('agent-b', [{ id: 'r1', text: 'yes' }, { id: 'r2', text: 'yes' }]);
  assert.equal(usePendingSends.getState().byAgent['agent-b'].length, 0);
});
