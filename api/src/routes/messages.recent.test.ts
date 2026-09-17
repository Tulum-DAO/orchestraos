/**
 * RED-first (Tier 0 item 3): seat-to-seat mail is visible in the web Inbox.
 *  - msg_store.py is CODE (checkout), never looked up under the data dir;
 *  - GET /api/messages/recent lists the latest rows across every seat (both directions),
 *    with status + acknowledged_at, straight from <data>/state/tasks.db.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, existsSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import Database from 'better-sqlite3';
import { resolveMsgStorePath, recentMessages } from './messages.js';

test('msg_store.py resolves from the checkout, not the data dir', () => {
  const p = resolveMsgStorePath({ ORCHESTRA_ROOT: '/opt/orchestraos' }, import.meta.url);
  assert.equal(p, '/opt/orchestraos/msg_store.py');
  const q = resolveMsgStorePath({}, import.meta.url);
  assert.ok(q.endsWith('/msg_store.py') && !q.includes('/api/'), q);
  assert.ok(existsSync(q), `checkout msg_store.py must exist at ${q}`);
});

test('recentMessages reads both directions with status from tasks.db', () => {
  const data = mkdtempSync(join(tmpdir(), 'orch-data-'));
  mkdirSync(join(data, 'state'));
  const db = new Database(join(data, 'state', 'tasks.db'));
  db.exec(`create table messages (id text primary key, conversation_id text, from_agent text, to_agent text,
           type text, subject text, body text, priority text, status text, created_at text,
           delivered_at text, acknowledged_at text, archived_at text, metadata text)`);
  db.prepare(`insert into messages (id,from_agent,to_agent,type,subject,body,priority,status,created_at,acknowledged_at)
              values (?,?,?,?,?,?,?,?,?,?)`).run('msg_a1', 'alpha', 'beta', 'task_request', 'hello beta', 'x', 'medium', 'acknowledged', '2026-09-17T10:00:00+00:00', '2026-09-17T10:00:30+00:00');
  db.prepare(`insert into messages (id,from_agent,to_agent,type,subject,body,priority,status,created_at)
              values (?,?,?,?,?,?,?,?,?)`).run('msg_b1', 'beta', 'alpha', 'reply', 're: hello beta', 'y', 'medium', 'pending', '2026-09-17T10:01:00+00:00');
  db.close();
  const rows = recentMessages(data, 10);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].id, 'msg_b1');            // newest first
  assert.equal(rows[1].status, 'acknowledged');
  assert.equal(rows[1].acknowledged_at, '2026-09-17T10:00:30+00:00');
  assert.deepEqual(rows.map(r => `${r.from_agent}->${r.to_agent}`), ['beta->alpha', 'alpha->beta']);
  assert.deepEqual(recentMessages(join(data, 'nowhere'), 10), []);   // no db = empty, never throws
});
