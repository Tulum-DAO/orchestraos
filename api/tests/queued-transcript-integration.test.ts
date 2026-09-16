/**
 * End-to-end producer seam for queued-native-render (P1): the exact call the
 * route makes — normalizeTranscript(lines, agent, sid, limit, false, includeQueued=true)
 * — merges msg_store rows into BOTH items[] and render_items[]. Proves the route
 * path, not just mergeQueuedItems in isolation. Also proves includeQueued=false
 * (the SSE tailer's call) leaves the envelope free of queued nodes.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const dir = mkdtempSync(join(tmpdir(), 'qint-'));
const DBP = join(dir, 'tasks.db');

function seed() {
  const db = new Database(DBP);
  db.exec(`CREATE TABLE messages (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    body TEXT, status TEXT NOT NULL DEFAULT 'pending', metadata TEXT,
    created_at TEXT NOT NULL, delivered_at TEXT, acknowledged_at TEXT);`);
  const ins = db.prepare(`INSERT INTO messages (id,type,from_agent,to_agent,body,status,metadata,created_at,delivered_at,acknowledged_at)
    VALUES (@id,@type,@from_agent,@to_agent,@body,@status,@metadata,@created_at,@delivered_at,@acknowledged_at)`);
  ins.run({ id: 'h1', type: 'held_message', from_agent: 'operator', to_agent: 'gm', body: 'phone says hi',
    status: 'pending', metadata: null, created_at: '2026-09-14T13:45:00.000+00:00', delivered_at: null, acknowledged_at: null });
  ins.run({ id: 'b1', type: 'task', from_agent: 'pm-infra', to_agent: 'gm', body: 'batched note',
    status: 'acknowledged', metadata: JSON.stringify({ batch_id: 'BX', processed_ts: '2026-09-14T13:20:10.000+00:00' }),
    created_at: '2026-09-14T13:20:00.000+00:00', delivered_at: '2026-09-14T13:20:05.000+00:00', acknowledged_at: '2026-09-14T13:20:10.000+00:00' });
  db.close();
}
seed();
process.env.MSG_DB_PATH = DBP;

const { normalizeTranscript } = await import('../src/routes/chat-transcript.js');

const JSONL = [
  JSON.stringify({ type: 'assistant', uuid: 'u1', timestamp: '2026-09-14T13:00:00.000+00:00', message: { role: 'assistant', content: [{ type: 'text', text: 'hello' }] } }),
];

test('route seam: includeQueued=true merges queued turn + batch div into items and render_items', () => {
  const env = normalizeTranscript(JSON.parse(JSON.stringify(JSONL)) as string[], 'gm', 'sid', 150, false, true);
  const q = env.items.find((i: any) => i.kind === 'text' && i.queued);
  assert.ok(q, 'queued phone turn present in items[]');
  assert.equal(q.text, 'phone says hi');
  const batch = env.items.find((i: any) => i.kind === 'queued_batch');
  assert.ok(batch, 'queued_batch present in items[]');
  assert.equal(batch.entries[0].body, 'batched note');
  const rbatch = env.render_items.find((n: any) => n.kind === 'queued_batch');
  assert.ok(rbatch, 'queued_batch present in render_items[]');
  const ruser = env.render_items.find((n: any) => n.kind === 'user' && n.queued);
  assert.ok(ruser, 'queued flag carried into render user node');
});

test('SSE-safety: includeQueued=false (tailer path) leaves the envelope free of queued nodes', () => {
  const env = normalizeTranscript(JSON.parse(JSON.stringify(JSONL)) as string[], 'gm', 'sid', 150, false, false);
  assert.ok(!env.items.some((i: any) => i.queued || i.kind === 'queued_batch'), 'no queued nodes when includeQueued=false');
  assert.ok(!env.render_items.some((n: any) => n.kind === 'queued_batch' || n.queued));
});

process.on('exit', () => { try { rmSync(dir, { recursive: true, force: true }); } catch {} });
