/**
 * A snoozed task for you carries snoozed_until on every route that hands the dashboard a card: the chat's
 * GET /api/agents/:id/decisions and a card reference's GET /api/approvals/unified/:id/card. Without it the card offers
 * Snooze again, and each tap pushes the task back another 2 h.
 *
 * The column comes with the human_task columns, which an install arms on its own schedule. A SELECT that names a
 * missing column comes back with no rows at all (lib/db queryDb swallows the error), which would empty the chat. So
 * both routes probe for it per request. Runs the real routers against a temp data dir: first a store without the
 * column (the card still shows, with no snoozed_until), then the same store once the column is added (it is carried).
 *
 * Run: npx tsx --test src/routes/snoozedUntilRoutes.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';

const dir = testTmpDir('snoozed-routes-');
mkdirSync(join(dir, 'state'));
process.env.ORCHESTRA_DIR = dir;
const DB = join(dir, 'state', 'tasks.db');

// Every column the two SELECTs name, except snoozed_until (an install with the human_task columns unarmed).
const store = new Database(DB);
store.exec(`CREATE TABLE approval_requests (
  id TEXT PRIMARY KEY, from_agent TEXT, kind TEXT, status TEXT, question TEXT, summary TEXT, options TEXT, menu TEXT,
  risk_level TEXT, reversibility TEXT, answer TEXT, answer_text TEXT, option_n TEXT, answered_by TEXT,
  answer_surface TEXT, answer_device TEXT, created_at TEXT, answered_at TEXT, expires_at TEXT, retired_at TEXT,
  superseded_by TEXT, resumed_at TEXT, resume_acked_at TEXT, op_key TEXT, origin TEXT)`);
store.prepare(`INSERT INTO approval_requests (id, from_agent, kind, status, question, options, created_at, op_key)
  VALUES (?, ?, 'human_task', 'pending', 'Plug in the test phone', '["done","cant","snooze"]', ?, 'task-1')`)
  .run('apr_task_snz1', 'dev-demo', new Date().toISOString());
store.close();

async function withApp(fn: (get: (path: string) => Promise<any>) => Promise<void>) {
  const { default: agentsRouter } = await import('./agents.js');
  const { default: unifiedRouter } = await import('./unified-approvals.js');
  const app = express();
  app.use(express.json());
  app.use('/api/agents', agentsRouter);
  app.use('/api/approvals/unified', unifiedRouter);
  const server = app.listen(0);
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  try {
    await fn(async (path) => {
      const r = await fetch(base + path);
      return { status: r.status, body: await r.json() };
    });
  } finally { server.close(); }
}

const decisionsCard = async (get: (p: string) => Promise<any>) => {
  const r = await get('/api/agents/dev-demo/decisions');
  assert.equal(r.status, 200);
  return (r.body.decisions as any[]).find((d) => d.id === 'apr_task_snz1');
};
const referenceCard = async (get: (p: string) => Promise<any>) => {
  const r = await get('/api/approvals/unified/apr_task_snz1/card');
  assert.equal(r.status, 200, JSON.stringify(r.body));
  return r.body.card;
};

test('without the column: both routes still return the card, with no snoozed_until', async () => {
  await withApp(async (get) => {
    for (const card of [await decisionsCard(get), await referenceCard(get)]) {
      assert.ok(card, 'the card is not lost');
      assert.equal(card.kind, 'human_task');
      assert.ok(!('snoozed_until' in card));
    }
  });
});

test('with the column (added at runtime, as an arm does): both routes carry snoozed_until', async () => {
  const until = new Date(Date.now() + 2 * 3600_000).toISOString();
  const db = new Database(DB);
  db.exec('ALTER TABLE approval_requests ADD COLUMN snoozed_until TEXT');
  db.prepare('UPDATE approval_requests SET snoozed_until = ? WHERE id = ?').run(until, 'apr_task_snz1');
  db.close();
  await withApp(async (get) => {
    assert.equal((await decisionsCard(get))?.snoozed_until, until, '/decisions');
    assert.equal((await referenceCard(get))?.snoozed_until, until, '/:id/card');
  });
});
