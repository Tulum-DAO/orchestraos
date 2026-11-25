/**
 * The first page of a seat's chat, as a seat switch loads it.
 *
 * - No cursor: the newest 40 items by default (was 150), and the envelope says whether more is
 *   above (has_older, oldest_id) so the client can ask for the next page without guessing.
 * - ?limit=150 still gives 150; a before= page is unchanged.
 * - The finished first page is kept per seat (services/transcript-first-page-cache.ts) while the
 *   log and the seat's queued state are unchanged: a second request parses nothing and returns the
 *   same bytes; one appended line, or one new queued row, shows on the very next request.
 *
 * Run: npx tsx --test src/routes/chat-transcript.firstpage.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, appendFileSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SEAT = 'zz-firstpage-seat';              // never a real tmux session on any box
const SID = '44444444-2222-4333-8444-000000000001';

const root = testTmpDir('transcript-firstpage-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
process.env.MSG_DB_PATH = join(orch, 'state', 'tasks.db');
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
{
  // The message store the queued merge reads (tests/queued-merge.test.ts has the same shape).
  const db = new Database(process.env.MSG_DB_PATH);
  db.exec(`CREATE TABLE messages (
    id TEXT PRIMARY KEY, conversation_id TEXT, task_id TEXT, parent_id TEXT,
    type TEXT NOT NULL, from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
    subject TEXT, body TEXT, priority TEXT DEFAULT 'medium', source TEXT,
    status TEXT NOT NULL DEFAULT 'pending', retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 5, metadata TEXT, depends_on TEXT,
    gather_mode TEXT, tenant_id TEXT DEFAULT 'operator',
    created_at TEXT NOT NULL, attempted_at TEXT, delivered_at TEXT,
    acknowledged_at TEXT, archived_at TEXT, error TEXT);`);
  db.close();
}

const ts = (i: number) => new Date(Date.UTC(2026, 9, 22, 10, 0, i)).toISOString();
const user = (i: number) => JSON.stringify({ type: 'user', uuid: `u${i}`, timestamp: ts(i), message: { role: 'user', content: `question ${i}` } });
const reply = (i: number) => JSON.stringify({
  type: 'assistant', uuid: `a${i}`, timestamp: ts(i),
  message: { role: 'assistant', content: [{ type: 'text', text: `answer ${i} ${'x'.repeat(200)}` }] },
});
// 200 lines = 200 items: well past 150, so every default and explicit window is a strict cut.
const lines: string[] = [];
for (let i = 0; i < 100; i++) lines.push(user(2 * i), reply(2 * i + 1));
const LOG = join(projects, `${SID}.jsonl`);
writeFileSync(LOG, lines.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));

const { default: router } = await import('./chat-transcript.js');
const { logCacheParses } = await import('../services/transcript-log-cache.js');
const { firstPageCacheStats, clearFirstPageCache } = await import('../services/transcript-first-page-cache.js');

async function withServer<T>(fn: (get: (q: string) => Promise<{ status: number; text: string; body: any; headers: Headers }>) => Promise<T>): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`;
    return await fn(async (q) => {
      const r = await fetch(`${base}${q}`);
      const text = await r.text();
      return { status: r.status, text, body: JSON.parse(text), headers: r.headers };
    });
  } finally {
    server.closeAllConnections();   // an SSE client that dropped mid-frame must not hold the process open
    server.close();
  }
}

const uuids = (env: any) => env.items.map((it: any) => it.uuid);

test('the default first page is 40 items and says there is more above', async () => {
  clearFirstPageCache();
  await withServer(async (get) => {
    const env = (await get('')).body;
    assert.equal(env.items.length, 40);
    assert.equal(env.has_older, true, 'the first page tells the client the log goes on above it');
    assert.equal(env.oldest_id, `${SID}~u160`, 'oldest_id is the cursor of the first item shown');
    assert.deepEqual(uuids(env).slice(-2), ['u198', 'a199'], 'the newest items');
    assert.equal(env.items[0].uuid, 'u160');
    // The next page from that cursor continues without a gap or a repeat.
    const older = (await get(`?before=${encodeURIComponent(env.oldest_id)}&limit=40`)).body;
    assert.equal(older.items.length, 40);
    assert.deepEqual(uuids(older).slice(-1), ['a159']);
    assert.equal(older.has_older, true);
  });
});

test('?limit=150 still gives 150, and a page that reaches the start says so', async () => {
  await withServer(async (get) => {
    const env = (await get('?limit=150')).body;
    assert.equal(env.items.length, 150);
    assert.equal(env.has_older, true);
    const whole = (await get('?limit=500')).body;
    assert.equal(whole.items.length, 200);
    assert.equal(whole.has_older, false, 'the window reached the start of the log');
    assert.equal(whole.oldest_id, `${SID}~u0`);
  });
});

test('a before= page is unchanged by the new default', async () => {
  await withServer(async (get) => {
    const page = (await get(`?before=${SID}~u100`)).body;
    assert.equal(page.items.length, 100, 'a cursor page still defaults to 100');
    assert.deepEqual(uuids(page).slice(-1), ['a99']);
    assert.equal(page.has_older, false);
    assert.equal(page.oldest_id, `${SID}~u0`);
  });
});

test('a second request for the same first page parses nothing and returns the same bytes', async () => {
  clearFirstPageCache();
  await withServer(async (get) => {
    const first = await get('');
    const parsesAfterFirst = logCacheParses();
    const hitsBefore = firstPageCacheStats().hits;
    const second = await get('');
    assert.equal(second.text, first.text, 'byte-identical body');
    assert.equal(logCacheParses(), parsesAfterFirst, 'no parse on a hit');
    assert.equal(firstPageCacheStats().hits, hitsBefore + 1, 'served from the first-page cache');
    assert.ok(first.headers.get('etag'), 'the body still carries an ETag');
    assert.equal(second.headers.get('etag'), first.headers.get('etag'));
    // Different limits are different pages.
    const other = (await get('?limit=41')).body;
    assert.equal(other.items.length, 41);
  });
});

test('one appended line shows on the very next request (the log changed)', async () => {
  await withServer(async (get) => {
    const before = (await get('')).body;
    assert.deepEqual(uuids(before).slice(-1), ['a199']);
    appendFileSync(LOG, user(200) + '\n');
    const after = (await get('')).body;
    assert.deepEqual(uuids(after).slice(-1), ['u200'], 'the new line is in the page');
    assert.equal(after.items.length, 40);
    assert.equal(after.items[0].uuid, 'a161', 'the window moved by one');
  });
});

test('a new queued row shows on the very next request (the log did not change)', async () => {
  await withServer(async (get) => {
    const before = (await get('')).body;
    assert.ok(!before.items.some((it: any) => it.queued), 'nothing queued yet');
    const db = new Database(process.env.MSG_DB_PATH!);
    db.prepare(`INSERT INTO messages (id, type, from_agent, to_agent, body, status, created_at)
                VALUES ('h1', 'held_message', 'operator', ?, 'hello from the phone', 'pending', ?)`).run(SEAT, ts(300));
    db.close();
    const after = (await get('')).body;
    const held = after.items.find((it: any) => it.queued);
    assert.ok(held, 'the held message is in the page');
    assert.equal(held.text, 'hello from the phone');
    // Delivered: the card goes (a status change, not a new row).
    const db2 = new Database(process.env.MSG_DB_PATH!);
    db2.prepare(`UPDATE messages SET status = 'delivered', delivered_at = ? WHERE id = 'h1'`).run(ts(301));
    db2.close();
    const gone = (await get('')).body;
    assert.ok(!gone.items.some((it: any) => it.queued), 'the delivered message is no longer a card');
  });
});

test('If-None-Match on a cached page is a 304', async () => {
  await withServer(async (get) => {
    const first = await get('');
    const express = (await import('express')).default;
    const a = express();
    a.use('/api/agents', router);
    const server = a.listen(0);
    try {
      // node:http, not fetch: fetch adds `Cache-Control: no-cache` to any conditional request (the
      // fetch spec's cache mode), which rightly makes every validator stale. A browser does not.
      const http = await import('node:http');
      const status = await new Promise<number>((resolve, reject) => {
        http.get(`http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`,
          // The same variant as `first` (fetch accepted gzip): the gzipped body has its own ETag.
          { headers: { 'if-none-match': first.headers.get('etag')!, 'accept-encoding': 'gzip' } },
          (r) => { r.resume(); resolve(r.statusCode ?? 0); }).on('error', reject);
      });
      assert.equal(status, 304);
    } finally { server.closeAllConnections(); server.close(); }
  });
});

test('Server-Timing says how long the server took, and whether the page was kept', async () => {
  clearFirstPageCache();
  await withServer(async (get) => {
    const miss = (await get('')).headers.get('server-timing') || '';
    assert.match(miss, /\bread;dur=\d+(\.\d+)?/, `a read duration in ms (${miss})`);
    assert.match(miss, /cache;desc=miss/, 'the first request built the page');
    const hit = (await get('')).headers.get('server-timing') || '';
    assert.match(hit, /cache;desc=hit/, 'the second was served from the kept page');
    const cursor = (await get(`?before=${SID}~u100`)).headers.get('server-timing') || '';
    assert.match(cursor, /\bread;dur=/, 'a cursor page is timed too');
    assert.doesNotMatch(cursor, /cache;/, 'and is never kept');
  });
});

test('a rewritten agent-sessions.json is read on the very next request (the parsed file is kept by stat)', async () => {
  // Resolution step 2: an explicit conversation_path for the seat. The file did not exist so far; then it
  // points at another log; then it is rewritten to point back. Each change must show at once.
  const other = join(projects, '44444444-2222-4333-8444-000000000002.jsonl');
  writeFileSync(other, [user(900), reply(901)].join('\n') + '\n');
  const sessions = join(orch, 'state', 'agent-sessions.json');
  const { utimesSync, rmSync } = await import('fs');
  const bump = (i: number) => { const t = new Date(Date.now() + i * 2000); utimesSync(sessions, t, t); };   // mtime moves even inside one tick
  await withServer(async (get) => {
    const before = (await get('')).body;
    assert.equal(before.session_id, SID);
    // Point the seat at the other log through the registry-free path: the state file first, the sessions file second.
    rmSync(join(orch, 'state', 'agents', `${SEAT}.json`));
    writeFileSync(sessions, JSON.stringify({ [SEAT]: { session_id: SID, conversation_path: other } }));
    bump(1);
    const moved = (await get('')).body;
    assert.deepEqual(uuids(moved), ['u900', 'a901'], 'the sessions file is read: the other log is the page');
    writeFileSync(sessions, JSON.stringify({ [SEAT]: { session_id: SID, conversation_path: LOG } }));
    bump(2);
    const back = (await get('')).body;
    assert.ok(uuids(back).length > 2 && !uuids(back).includes('u900'), 'rewritten again: seen on the very next request');
    writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));
  });
});

test('a call journal rewritten in place shows within CALLS_SIG_TTL_MS (the signature is kept for a second)', async () => {
  const { CALLS_SIG_TTL_MS } = await import('./chat-transcript.js');
  const { callsDir } = await import('../lib/agentCalls.js');
  const dir = callsDir(orch);
  mkdirSync(dir, { recursive: true });
  const journal = join(dir, 'vc_test1.json');
  const doc = (state: string) => JSON.stringify({ call_id: 'vc_test1', agent_id: SEAT, state, started_at: ts(10), ended_at: state === 'ended' ? ts(20) : null, events: [] });
  await withServer(async (get) => {
    writeFileSync(journal, doc('live'));
    await new Promise((r) => setTimeout(r, CALLS_SIG_TTL_MS + 50));
    const a = await get('');
    writeFileSync(journal, doc('ended'));                   // rewritten in place: the directory's mtime does not move
    const within = await get('');
    assert.equal(within.text, a.text, 'inside the second: the kept page (the signature is not recomputed)');
    await new Promise((r) => setTimeout(r, CALLS_SIG_TTL_MS + 50));
    const after = await get('');
    assert.notEqual(after.headers.get('server-timing')?.includes('cache;desc=hit'), true, 'after the second: the journal change is seen and the page is built again');
  });
});
