/**
 * The queued merge reads an agent's B1/B2 rows from the message store ONCE per store state: a second page build under
 * the same queuedStateSig reads nothing (B2's LIKE over every row of a 250 MB store was 0.2-1 s on every first-page
 * miss, whatever the log's size); a new row, or a row leaving pending, is a new state and is read. The merge's output
 * under a kept state equals a fresh read's.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/queued-merge.cache.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';

const dir = testTmpDir('queued-merge-cache-');
mkdirSync(join(dir, 'state'), { recursive: true });
process.env.ORCHESTRA_DIR = dir;
process.env.MSG_DB_PATH = join(dir, 'state', 'tasks.db');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const db = new Database(process.env.MSG_DB_PATH);
db.exec(`CREATE TABLE messages (
  id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT, type TEXT NOT NULL, from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL, subject TEXT, body TEXT, priority TEXT NOT NULL DEFAULT 'medium', source TEXT DEFAULT 'system',
  status TEXT NOT NULL DEFAULT 'pending', retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 5,
  metadata TEXT, depends_on TEXT, gather_mode TEXT DEFAULT 'gather_all', tenant_id TEXT DEFAULT 'shaw',
  created_at TEXT NOT NULL DEFAULT (datetime('now')), attempted_at TEXT, delivered_at TEXT, acknowledged_at TEXT,
  archived_at TEXT, error TEXT)`);
const ins = db.prepare(`INSERT INTO messages (id, type, from_agent, to_agent, body, status, metadata, created_at, acknowledged_at, delivered_at)
  VALUES (@id, @type, @from, @to, @body, @status, @metadata, @created, @acked, @delivered)`);
const AGENT = 'zz-queued-seat';
const T = (s: number) => `2026-11-26T05:00:${String(s).padStart(2, '0')}.000Z`;
ins.run({ id: 'msg_pending1', type: 'held_message', from: 'shaw', to: AGENT, body: 'a held message from the phone, long enough to be a bubble', status: 'pending', metadata: null, created: T(5), acked: null, delivered: null });
ins.run({ id: 'msg_b1', type: 'task', from: 'pm-x', to: AGENT, body: 'batched message one, long enough to be distinctive for a card', status: 'acknowledged', metadata: JSON.stringify({ batch_id: 'batch-A' }), created: T(1), acked: T(2), delivered: T(2) });
ins.run({ id: 'msg_b2', type: 'task', from: 'pm-y', to: AGENT, body: 'batched message two, long enough to be distinctive for a card', status: 'acknowledged', metadata: JSON.stringify({ batch_id: 'batch-A' }), created: T(1), acked: T(2), delivered: T(2) });
ins.run({ id: 'msg_other', type: 'task', from: 'pm-x', to: 'someone-else', body: 'not this agent', status: 'pending', metadata: null, created: T(3), acked: null, delivered: null });

const { mergeQueuedItems, queuedMergeReads, clearQueuedMergeCache, queuedStateSig } = await import('./queued-merge.js');

const items = () => [{ kind: 'text', role: 'user', text: 'the operator said something', ts: T(0), uuid: 'u1' },
  { kind: 'text', role: 'assistant', text: 'and the agent answered', ts: T(4), uuid: 'a1' }];
const keys = (xs: any[]) => xs.map((x) => x.uuid || x.key);

test('a second build under the same store state reads nothing and gives the same merge', () => {
  clearQueuedMergeCache();
  const r0 = queuedMergeReads();
  const first = mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 1);
  const k = keys(first);
  assert.equal(k.length, 4, `log items, one batch card, one bubble: ${k.join(',')}`);
  assert.ok(k.indexOf('u1') < k.indexOf('a1') && k[k.length - 1] === 'queued:msg_pending1', 'log order kept, the pending bubble last');
  assert.ok(first.some((x: any) => x.kind === 'queued_batch' && x.count === 2 && String(x.key).startsWith('msg:msg_b')), 'the batch of two');
  assert.ok(first.some((x: any) => x.uuid === 'queued:msg_pending1'), 'the pending bubble');
  const second = mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 1, 'same state: no read');
  assert.deepEqual(keys(second), keys(first));
  // the oracle: a fresh read gives the same merge
  clearQueuedMergeCache();
  const fresh = mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 2);
  assert.deepEqual(keys(fresh), keys(first));
});

test('a new row is a new state: read again, and the new bubble is there', () => {
  const r0 = queuedMergeReads();
  const sig0 = queuedStateSig(AGENT);
  ins.run({ id: 'msg_pending2', type: 'held_message', from: 'shaw', to: AGENT, body: 'a second held message from the watch, also long enough', status: 'pending', metadata: null, created: T(6), acked: null, delivered: null });
  assert.notEqual(queuedStateSig(AGENT), sig0);
  const out = mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 1);
  assert.ok(out.some((x: any) => x.uuid === 'queued:msg_pending2'));
  mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 1, 'and kept again');
});

test('a row leaving pending is a new state: read again, and its bubble is gone', () => {
  const r0 = queuedMergeReads();
  db.prepare(`UPDATE messages SET status = 'acknowledged', acknowledged_at = ?, delivered_at = ? WHERE id = 'msg_pending2'`).run(T(7), T(7));
  const out = mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 1);
  assert.equal(out.some((x: any) => x.uuid === 'queued:msg_pending2'), false);
});

test('another agent has its own rows: its first build reads, and never this agent\'s rows', () => {
  const r0 = queuedMergeReads();
  const out = mergeQueuedItems(items(), 'someone-else');
  assert.equal(queuedMergeReads(), r0 + 1);
  assert.equal(out.some((x: any) => String(x.uuid || x.key).includes('msg_pending1')), false);
  mergeQueuedItems(items(), AGENT);
  assert.equal(queuedMergeReads(), r0 + 1, 'this agent\'s rows are still kept');
});
