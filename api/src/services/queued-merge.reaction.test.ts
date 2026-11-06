/**
 * Place 4 of the wake's hiding (spec): a held_message row carrying a reaction wake never becomes a `queued:` bubble.
 * The wake is sent with no hold, so it should never be stored; this is the net if one ever is.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/queued-merge.reaction.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { rmSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';

const dir = testTmpDir('qmerge-reaction-');
const DBP = join(dir, 'tasks.db');
after(() => rmSync(dir, { recursive: true, force: true }));
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { reactionWakeText } = await import('../lib/reaction-wake-text.js');

const db = new Database(DBP);
db.exec(`CREATE TABLE messages (
  id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
  type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
  subject TEXT, body TEXT, priority TEXT DEFAULT 'medium', source TEXT,
  status TEXT NOT NULL DEFAULT 'pending', retry_count INTEGER DEFAULT 0,
  max_retries INTEGER DEFAULT 5, metadata TEXT, depends_on TEXT,
  gather_mode TEXT, tenant_id TEXT DEFAULT 'operator',
  created_at TEXT NOT NULL, attempted_at TEXT, delivered_at TEXT,
  acknowledged_at TEXT, archived_at TEXT, error TEXT);`);
const ins = db.prepare(`INSERT INTO messages (id, type, from_agent, to_agent, body, status, created_at)
  VALUES (?, 'held_message', 'operator', 'gm', ?, 'pending', ?)`);
ins.run('h1', 'hello from phone', '2026-11-02T10:00:00.000+00:00');
ins.run('h2', reactionWakeText({ user: 'sam', emoji: '👍', on: 'done' }), '2026-11-02T10:00:05.000+00:00');
db.close();
process.env.MSG_DB_PATH = DBP;

const { mergeQueuedItems } = await import('./queued-merge.js');

test('a held wake row is not a queued bubble; a real held message still is', () => {
  const q = mergeQueuedItems([], 'gm').filter((i: any) => i.kind === 'text' && i.queued);
  assert.deepEqual(q.map((i: any) => i.text), ['hello from phone']);
});
