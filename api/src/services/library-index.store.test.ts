/**
 * Library attribution honours the conversation store (provider-agnostic rotation): an earlier-conversation block in
 * an entry marked seen-only, and seed rows, are never anyone's work. Temp data dir, real files.
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import Database from 'better-sqlite3';
import { mkdirSync, writeFileSync, utimesSync } from 'node:fs';
import { join } from 'node:path';
import { LibraryIndex } from './library-index.js';
import { forgetStoreCache } from './conversation-store.js';

const T0 = Date.now() - 3_600_000;
const iso = (s: number) => new Date(T0 + s * 1000).toISOString();
let n = 0;
const L = (o: any) => JSON.stringify({ cwd: '/repo', ...o }) + '\n';

test('a seen-only block and seed rows give no library items; the same words typed by a person do', () => {
  forgetStoreCache();
  const data = testTmpDir('lib-store-');
  mkdirSync(join(data, 'state', 'uploads'), { recursive: true });
  const up1 = join(data, 'state', 'uploads', 'sample-a.png');
  const up2 = join(data, 'state', 'uploads', 'sample-b.png');
  const seedFile = join(data, 'seed-made.md');
  for (const f of [up1, up2, seedFile]) { writeFileSync(f, 'x'); const t = new Date(T0 + 5_000); utimesSync(f, t, t); }
  const sid = 'sess-lib';
  const db = new Database(join(data, 'state', 'episodic-memory.db'));
  db.exec(`CREATE TABLE seen_only (provider_session_id TEXT, uuid TEXT, marker TEXT, PRIMARY KEY (provider_session_id, uuid));
           CREATE TABLE seed_boundary (provider_session_id TEXT PRIMARY KEY, through_uuid TEXT NOT NULL);`);
  db.prepare('INSERT INTO seen_only VALUES (?, ?, ?)').run(sid, 'brief', 'earlier-conversation');
  db.prepare('INSERT INTO seed_boundary VALUES (?, ?)').run(sid, 'seed-r');
  db.close();
  const block = (p: string) => `<earlier-conversation>\nThe operator uploaded ${p} earlier.\n</earlier-conversation>\nCarry on.`;
  const log = join(data, `${sid}.jsonl`);
  writeFileSync(log,
    L({ type: 'user', uuid: 'seed-u', timestamp: iso(0), message: { role: 'user', content: 'seed turn' } })
    + L({ type: 'assistant', uuid: 'seed-a', timestamp: iso(4), message: { role: 'assistant', content: [{ type: 'tool_use', id: 'w1', name: 'Write', input: { file_path: seedFile } }] } })
    + L({ type: 'user', uuid: 'seed-r', timestamp: iso(6), message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'w1', content: 'File created successfully' }] } })
    + L({ type: 'user', uuid: 'brief', timestamp: iso(10), message: { role: 'user', content: block(up1) } })
    + L({ type: 'user', uuid: 'typed', timestamp: iso(20), message: { role: 'user', content: block(up2) } }));
  n++;
  const ix = new LibraryIndex({ dataDir: data, scratch: () => false, agents: () => [{ id: 'qa-demo', cwd: '/repo' }], logsOf: () => [{ path: log, session: sid }] });
  ix.pass();
  const paths = (ix.list({ agent: 'qa-demo' }) as any[]).map((i) => i.path);
  assert.ok(!paths.includes(seedFile), 'a Write in the seed is not this agent\'s work');
  assert.ok(!paths.includes(up1), 'an upload named only inside a seen-only block is not counted');
  assert.ok(paths.includes(up2), 'the same block typed by a person is read as written');
});
