/**
 * Queued rows are drawn at their DELIVERY position; the log's own order never changes.
 *
 * The bug (the operator's report, 2026-10-22): a message typed a second before a reply landed,
 * and taken in after it, was drawn ABOVE the reply, because merging any queued row sorted the
 * WHOLE transcript by ts.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { placeByDelivery, LIVE_ANCHOR } from './queued-merge.js';

const say = (uuid: string, ts: string, text: string) => ({ kind: 'text', role: 'assistant', text, ts, uuid });
const user = (uuid: string, ts: string, text: string, queued = false) =>
  ({ kind: 'text', role: 'user', text, ts, uuid, ...(queued ? { queued: true } : {}) });
const batch = (ts: string, body: string, key = 'msg:b') =>
  ({ kind: 'queued_batch', count: 1, entries: [{ agent: 'gm', sent_ts: ts, body }], ts, key });
const ids = (xs: any[]) => xs.map((x) => x.uuid || x.key);

test('the reported case: a message taken in after a reply stays below it, whatever its ts', () => {
  const log = [user('u0', '2026-01-01T10:00:00.000Z', 'earlier'),
               say('r1', '2026-01-01T10:00:02.000Z', 'the reply'),
               user('u2', '2026-01-01T10:00:01.000Z', 'his message, typed before the reply landed')];
  // Any synthetic present used to trigger the whole-list ts sort.
  const out = placeByDelivery(log, [], [batch('2026-01-01T09:00:00.000Z', 'an old batch with no logged message here', 'msg:old')], []);
  assert.deepEqual(ids(out), ['msg:old', 'u0', 'r1', 'u2']);
});

test('the log keeps its order even when its own timestamps are not monotonic', () => {
  const log = [say('a', '2026-10-22T05:00:05.000Z', 'x'), say('b', '2026-10-22T05:00:01.000Z', 'y'),
               say('c', '2026-10-22T05:00:09.000Z', 'z')];
  const out = placeByDelivery(log, [user('queued:p', '2026-10-22T04:00:00.000Z', 'pending', true)], [], []);
  assert.deepEqual(ids(out), ['a', 'b', 'c', 'queued:p']);
});

test('a still-pending message is drawn after everything delivered', () => {
  const log = [say('a', '2026-10-22T05:00:00.000Z', 'x'), say('b', '2026-10-22T05:10:00.000Z', 'y')];
  const pending = [user('queued:1', '2026-10-22T05:05:00.000Z', 'sent mid-turn', true),
                   user('queued:2', '2026-10-22T05:06:00.000Z', 'and another', true)];
  assert.deepEqual(ids(placeByDelivery(log, pending, [], [])), ['a', 'b', 'queued:1', 'queued:2']);
});

test('a live card is drawn where its message entered the log', () => {
  const body = 'a message from gm long enough to be distinctive for binding';
  const logged = user('q1', '2026-10-22T05:01:00.000Z', body, true);
  const log = [say('a', '2026-10-22T05:00:00.000Z', 'x'), logged, say('b', '2026-10-22T04:00:00.000Z', 'y')];
  const card = batch('2026-10-22T09:00:00.000Z', body, 'msg:live');
  LIVE_ANCHOR.set(card, logged);
  assert.deepEqual(ids(placeByDelivery(log, [], [], [card])), ['a', 'msg:live', 'q1', 'b']);
});

test('a processed batch goes where the log shows one of its messages', () => {
  const body = 'a batched message from pm-x that the log also recorded as queued';
  const log = [say('a', '2026-10-22T05:00:00.000Z', 'x'), user('q1', '2026-10-22T05:00:30.000Z', body, true),
               say('b', '2026-10-22T05:01:00.000Z', 'y')];
  const out = placeByDelivery(log, [], [batch('2026-10-22T06:00:00.000Z', body)], []);
  assert.deepEqual(ids(out), ['a', 'msg:b', 'q1', 'b']);
});

test('a batch the log does not show goes between items by its time, moving none of them', () => {
  const log = [say('a', '2026-10-22T05:00:00.000Z', 'x'), say('b', '2026-10-22T05:10:00.000Z', 'y'),
               say('c', '2026-10-22T05:20:00.000Z', 'z')];
  const out = placeByDelivery(log, [], [batch('2026-10-22T05:15:00.000Z', 'short')], []);
  assert.deepEqual(ids(out), ['a', 'b', 'msg:b', 'c']);
  const late = placeByDelivery(log, [], [batch('2026-10-22T07:00:00.000Z', 'short', 'msg:late')], []);
  assert.deepEqual(ids(late), ['a', 'b', 'c', 'msg:late']);
});

test('two batches sharing an opening each take their own logged message', () => {
  const open = 'a status update from pm-x that opens the same way every single time: ';
  const log = [user('q1', '2026-01-01T10:00:01.000Z', open + 'first', true), say('a', '2026-01-01T10:00:02.000Z', 'x'),
               user('q2', '2026-01-01T10:00:03.000Z', open + 'second', true)];
  const b1 = batch('2026-01-01T10:00:05.000Z', open + 'first', 'msg:b1');
  const b2 = batch('2026-01-01T10:00:06.000Z', open + 'second', 'msg:b2');
  assert.deepEqual(ids(placeByDelivery(log, [], [b1, b2], [])), ['msg:b1', 'q1', 'a', 'msg:b2', 'q2']);
});
