/**
 * RED-first unit test for queued-merge (queued-native-render P1, DEC-1789392107605493).
 *
 * Exercises the msg_store merge that turns:
 *  - B1: still-pending held_message rows (phone/watch) -> synthetic queued user turns,
 *        deduped on the row's OWN delivered_at (a delivered row is dropped — its real
 *        JSONL turn takes over). NO body-match heuristic (would silently drop a distinct
 *        message — D2/W1).
 *  - B2: acknowledged self-bound rows grouped by metadata.batch_id -> ONE queued_batch
 *        node per batch, entries NEWEST->OLDEST.
 *
 * Hermetic: seeds its own tasks.db and points MSG_DB_PATH at it BEFORE importing the
 * module under test.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const dir = mkdtempSync(join(tmpdir(), 'qmerge-'));
const DBP = join(dir, 'tasks.db');

const MESSAGES_SCHEMA = `
CREATE TABLE messages (
  id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
  type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
  subject TEXT, body TEXT, priority TEXT DEFAULT 'medium', source TEXT,
  status TEXT NOT NULL DEFAULT 'pending', retry_count INTEGER DEFAULT 0,
  max_retries INTEGER DEFAULT 5, metadata TEXT, depends_on TEXT,
  gather_mode TEXT, tenant_id TEXT DEFAULT 'operator',
  created_at TEXT NOT NULL, attempted_at TEXT, delivered_at TEXT,
  acknowledged_at TEXT, archived_at TEXT, error TEXT
);`;

function seed() {
  const db = new Database(DBP);
  db.exec(MESSAGES_SCHEMA);
  const ins = db.prepare(
    `INSERT INTO messages (id, type, from_agent, to_agent, body, status, metadata, created_at, delivered_at, acknowledged_at)
     VALUES (@id, @type, @from_agent, @to_agent, @body, @status, @metadata, @created_at, @delivered_at, @acknowledged_at)`);
  const rows = [
    // B1 pending held phone message -> should surface as queued user turn
    { id: 'h1', type: 'held_message', from_agent: 'operator', to_agent: 'gm', body: 'hello from phone',
      status: 'pending', metadata: null, created_at: '2026-09-14T13:40:00.000+00:00', delivered_at: null, acknowledged_at: null },
    // B1 delivered held message -> deduped (dropped), its real JSONL turn takes over
    { id: 'h2', type: 'held_message', from_agent: 'operator', to_agent: 'gm', body: 'already delivered',
      status: 'delivered', metadata: null, created_at: '2026-09-14T13:41:00.000+00:00',
      delivered_at: '2026-09-14T13:41:05.000+00:00', acknowledged_at: null },
    // B1 held for a DIFFERENT agent -> not in gm's transcript
    { id: 'h3', type: 'held_message', from_agent: 'operator', to_agent: 'other-agent', body: 'not gm',
      status: 'pending', metadata: null, created_at: '2026-09-14T13:42:00.000+00:00', delivered_at: null, acknowledged_at: null },
    // B2 batch B1: two acknowledged agent-mail rows (older A, newer B)
    { id: 'b_a', type: 'task', from_agent: 'pm-infra', to_agent: 'gm', body: 'batch msg A (older)',
      status: 'acknowledged', metadata: JSON.stringify({ batch_id: 'BATCH1', processed_ts: '2026-09-14T13:30:10.000+00:00' }),
      created_at: '2026-09-14T13:30:00.000+00:00', delivered_at: '2026-09-14T13:30:08.000+00:00', acknowledged_at: '2026-09-14T13:30:10.000+00:00' },
    { id: 'b_b', type: 'report', from_agent: 'orchestra-builder', to_agent: 'gm', body: 'batch msg B (newer)',
      status: 'acknowledged', metadata: JSON.stringify({ batch_id: 'BATCH1', processed_ts: '2026-09-14T13:30:10.000+00:00' }),
      created_at: '2026-09-14T13:30:05.000+00:00', delivered_at: '2026-09-14T13:30:09.000+00:00', acknowledged_at: '2026-09-14T13:30:10.000+00:00' },
    // B2 not-yet-acknowledged batch row -> NOT rendered (batch mid-processing)
    { id: 'b_c', type: 'task', from_agent: 'pm-infra', to_agent: 'gm', body: 'unacked batch',
      status: 'pending', metadata: JSON.stringify({ batch_id: 'BATCH2' }),
      created_at: '2026-09-14T13:35:00.000+00:00', delivered_at: null, acknowledged_at: null },
  ];
  const tx = db.transaction(() => rows.forEach((r) => ins.run(r)));
  tx();
  db.close();
}

seed();
process.env.MSG_DB_PATH = DBP;

const { mergeQueuedItems } = await import('../src/services/queued-merge.js');

test('B1: pending held phone message surfaces as a queued user turn', () => {
  const out = mergeQueuedItems([], 'gm');
  const q = out.filter((i: any) => i.kind === 'text' && i.queued);
  assert.equal(q.length, 1);
  assert.equal(q[0].role, 'user');
  assert.equal(q[0].text, 'hello from phone');
  assert.equal(q[0].queued, true);
});

test('B1 dedup: a delivered held row is dropped (no dup with the real JSONL turn)', () => {
  const out = mergeQueuedItems([], 'gm');
  assert.ok(!out.some((i: any) => i.text === 'already delivered'), 'delivered row must not surface');
});

test('B1 scope: another agent\'s held message never appears in gm transcript', () => {
  const out = mergeQueuedItems([], 'gm');
  assert.ok(!out.some((i: any) => i.text === 'not gm'));
});

test('B2: acknowledged batch renders ONE queued_batch node, entries newest->oldest', () => {
  const out = mergeQueuedItems([], 'gm');
  const batches = out.filter((i: any) => i.kind === 'queued_batch');
  assert.equal(batches.length, 1);
  const b = batches[0];
  assert.equal(b.count, 2);
  assert.equal(b.key, 'batch:BATCH1');
  assert.equal(b.entries.length, 2);
  // newest -> oldest
  assert.equal(b.entries[0].body, 'batch msg B (newer)');
  assert.equal(b.entries[0].agent, 'orchestra-builder');
  assert.equal(b.entries[1].body, 'batch msg A (older)');
  // each entry carries sent_ts
  assert.ok(b.entries[0].sent_ts && b.entries[1].sent_ts);
});

test('B2: an unacknowledged batch does NOT render (batch still mid-processing)', () => {
  const out = mergeQueuedItems([], 'gm');
  assert.ok(!out.some((i: any) => i.kind === 'queued_batch' && i.key === 'batch:BATCH2'));
});

test('interleave: synthetic items sort by ts among JSONL items, stable', () => {
  const jsonl = [
    { kind: 'assistant', role: 'assistant', text: 'early reply', ts: '2026-09-14T13:00:00.000+00:00' },
    { kind: 'assistant', role: 'assistant', text: 'late reply', ts: '2026-09-14T13:50:00.000+00:00' },
  ];
  const out = mergeQueuedItems(jsonl, 'gm');
  const texts = out.map((i: any) => i.text);
  // batch (13:30) sits between the two replies; the pending phone turn (13:40) after the batch, before late reply
  const iEarly = texts.indexOf('early reply');
  const iBatchEntryA = out.findIndex((i: any) => i.kind === 'queued_batch');
  const iPhone = texts.indexOf('hello from phone');
  const iLate = texts.indexOf('late reply');
  assert.ok(iEarly < iBatchEntryA, 'early reply before batch');
  assert.ok(iBatchEntryA < iPhone, 'batch before pending phone turn');
  assert.ok(iPhone < iLate, 'pending phone turn before late reply');
});

test('no queued data for an unrelated agent -> items returned unchanged', () => {
  const base = [{ kind: 'text', role: 'user', text: 'x', ts: '2026-09-14T12:00:00.000+00:00' }];
  const out = mergeQueuedItems(base, 'nobody');
  assert.deepEqual(out, base);
});

process.on('exit', () => { try { rmSync(dir, { recursive: true, force: true }); } catch {} });
