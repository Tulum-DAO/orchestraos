/**
 * The first page never splits a log line: when the window's oldest unit is the second item of a
 * line (assistant text + tool_use, one uuid), the rest of that line is on the page too, so the
 * page's oldest_id names a whole line and the ?before= page from it continues without losing an
 * item. Page 1 and page 2 together are the whole log, every item exactly once.
 *
 * Run: npx tsx --test src/routes/chat-transcript.firstpage-line.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SEAT = 'zz-firstpage-line-seat';
const SID = '77777777-2222-4333-8444-000000000001';
const root = testTmpDir('transcript-firstpage-line-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
{
  const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
  db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(SEAT, '/repo');
  db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at) VALUES (1, ?, 1, ?, ?)').run(SEAT, SID, '2026-10-20T00:00:00Z');
  db.prepare("INSERT INTO canonical VALUES (?, 1, ?, 'online')").run(SEAT, SEAT);
  db.close();
}
const ts = (i: number) => new Date(Date.UTC(2026, 9, 22, 10, 0, i)).toISOString();
const user = (i: number) => JSON.stringify({ type: 'user', uuid: `u${i}`, timestamp: ts(i), message: { role: 'user', content: `question ${i}` } });
const reply = (i: number) => JSON.stringify({ type: 'assistant', uuid: `a${i}`, timestamp: ts(i),
  message: { role: 'assistant', content: [{ type: 'text', text: `answer ${i} ${'x'.repeat(120)}` }] } });
// One line, two items: the assistant's text and its tool call share uuid a1.
const twoItemLine = JSON.stringify({ type: 'assistant', uuid: 'a1', timestamp: ts(1),
  message: { role: 'assistant', content: [{ type: 'text', text: 'let me look' }, { type: 'tool_use', id: 'toolu_1', name: 'Read', input: { file_path: '/repo/x' } }] } });
// 42 items: u0, a1(text), a1(tool_use), then 39 more.
const lines = [user(0), twoItemLine];
for (let i = 2; i < 41; i++) lines.push(i % 2 === 0 ? user(i) : reply(i));
writeFileSync(join(projects, `${SID}.jsonl`), lines.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));

const { default: router } = await import('./chat-transcript.js');

async function withServer<T>(fn: (get: (q: string) => Promise<any>) => Promise<T>): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`;
    return await fn(async (q) => (await fetch(`${base}${q}`)).json());
  } finally {
    server.closeAllConnections();
    server.close();
  }
}
const refs = (env: any) => env.items.map((it: any) => it.ref);

test('the first page keeps a split line whole, and page 1 + page 2 are the whole log, each item once', async () => {
  await withServer(async (get) => {
    const whole = await get('?limit=500');
    assert.equal(whole.items.length, 42, 'precondition: 42 items, the a1 line holding two');
    assert.deepEqual(refs(whole).slice(0, 3), [`${SID}~u0#0`, `${SID}~a1#0`, `${SID}~a1#1`]);
    const first = await get('');
    assert.equal(first.has_older, true);
    assert.equal(first.items[0].ref, `${SID}~a1#0`, 'the window landed inside the a1 line: its first item is pulled in too');
    assert.equal(first.items.length, 41, 'one more than the limit, never a line cut in two');
    assert.equal(first.oldest_id, `${SID}~a1`);
    const second = await get(`?before=${encodeURIComponent(first.oldest_id)}`);
    assert.deepEqual(refs(second), [`${SID}~u0#0`]);
    assert.equal(second.has_older, false);
    const seen = [...refs(second), ...refs(first)];
    assert.deepEqual(seen, refs(whole), 'page 2 then page 1 is the whole log, every item exactly once');
  });
});
