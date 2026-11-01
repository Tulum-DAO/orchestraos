/**
 * Leaving a seat's past generations out of the agents list must not touch its own history: GET /:id/generations still
 * returns every past generation, and a hidden leftover row is still addressable by id.
 *
 * The data dir is a temp tree, set before the router is imported because the readers fix it at load time.
 *
 * Run: npx tsx --test src/routes/agents.generations-route.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import express from 'express';
import type { AddressInfo } from 'node:net';
import { mkdtempSync, mkdirSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import Database from 'better-sqlite3';

const orch = mkdtempSync(join(tmpdir(), 'agents-generations-'));
mkdirSync(join(orch, 'state'));
writeFileSync(join(orch, 'registry.json'), JSON.stringify({
  agents: {
    'dev-demo': { lineage_root: 'dev-demo', status: 'online' },
    'dev-demo-gen1': { lineage_root: 'dev-demo', status: 'retired' },
    'dev-demo-gen2': { lineage_root: 'dev-demo', status: 'parked' },
  },
}));
const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
db.exec(`
  CREATE TABLE canonical (root TEXT, generation_id INTEGER, tmux_session TEXT, status TEXT);
  CREATE TABLE generations (
    id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
    conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT,
    promoted_at TEXT, promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
`);
const ins = db.prepare(
  'INSERT INTO generations (id,root,generation,session_id,spawned_at,promoted_at,retired_at) VALUES (?,?,?,?,?,?,?)',
);
ins.run(1, 'dev-demo', 1, 's1', '2026-09-26T10:00:00Z', '2026-09-26T10:01:00Z', '2026-09-26T11:00:00Z');
ins.run(2, 'dev-demo', 2, 's2', '2026-09-26T11:00:00Z', '2026-09-26T11:01:00Z', '2026-09-26T12:00:00Z');
ins.run(3, 'dev-demo', 3, 's3', '2026-09-26T12:00:00Z', '2026-09-26T12:01:00Z', null);
db.prepare('INSERT INTO canonical (root,generation_id,tmux_session,status) VALUES (?,?,?,?)').run('dev-demo', 3, 'dev-demo', 'active');
db.close();
process.env.ORCHESTRA_DIR = orch;

async function withApp(fn: (base: string) => Promise<void>) {
  const { default: agentsRouter } = await import('./agents.js');
  const app = express();
  app.use('/api/agents', agentsRouter);
  const server = app.listen(0);
  const { port } = server.address() as AddressInfo;
  try { await fn(`http://127.0.0.1:${port}`); } finally { server.close(); }
}

test('GET /:id/generations still returns the past generations the list leaves out', async () => {
  await withApp(async (base) => {
    const r = await fetch(`${base}/api/agents/dev-demo/generations`);
    assert.equal(r.status, 200);
    const { generations } = await r.json() as { generations: { generation: number; current: boolean }[] };
    assert.deepEqual(generations.map((g) => [g.generation, g.current]), [[3, true], [2, false], [1, false]]);
  });
});

test('a hidden leftover row is still addressable by id', async () => {
  await withApp(async (base) => {
    // The scope param resolves the id; a hidden row must not 404 there.
    const r = await fetch(`${base}/api/agents/dev-demo-gen2/generations`);
    assert.equal(r.status, 200);
    assert.deepEqual(await r.json(), { generations: [] });
  });
});
