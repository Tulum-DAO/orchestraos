/**
 * A message that was being answered when a headless seat's CLI went down is carried, with the same uuid and text, into
 * the rebuilt conversation and answered there. Both generations' logs then hold it; the stitched chat shows it once,
 * at the newer generation's position, and paging and cursors still work around it.
 *
 * Run: npx tsx --test src/routes/chat-transcript.carried.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const SEAT = 'zz-carried-seat';                // never a real tmux session on any box
const G1 = '44444444-2222-4333-8444-000000000001';
const G2 = '44444444-2222-4333-8444-000000000002';
const G3 = '44444444-2222-4333-8444-000000000003';
const T12 = '2026-10-21T00:00:00Z';
const T23 = '2026-10-22T00:00:00Z';

const root = mkdtempSync(join(tmpdir(), 'transcript-carried-'));
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
  const g = db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (?,?,?,?,?,?)');
  g.run(1, SEAT, 1, G1, '2026-10-20T00:00:00Z', T12);
  g.run(2, SEAT, 2, G2, T12, T23);
  g.run(3, SEAT, 3, G3, T23, null);
  db.prepare("INSERT INTO canonical VALUES (?, 3, ?, 'online')").run(SEAT, SEAT);
  db.close();
}

const user = (uuid: string, text: string, ts = '2026-10-22T10:00:00Z') =>
  JSON.stringify({ type: 'user', uuid, timestamp: ts, message: { role: 'user', content: text } });
const reply = (uuid: string, text: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: '2026-10-22T10:00:01Z',
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] },
});
const ASK = 'Sample question: what is in example.md?';
const L1 = [user('a1', 'hello first'), reply('a2', 'first reply')];
// The crash: the question was being answered when the CLI went down, so this generation never replied to it.
const L2 = [user('b1', 'hello second'), reply('b2', 'second reply'), user('x1', ASK, '2026-10-21T10:00:00Z')];
// The rebuilt conversation: the same entry, same uuid, answered once. `b1` again but with other text, the way a log
// that numbers its ids per file can repeat one by chance: not a carried message, so it stays.
const L3 = [user('x1', ASK, '2026-10-22T10:00:00Z'), reply('c1', 'Example title'), user('b1', 'a different message')];
const write = (sid: string, lines: string[]) => writeFileSync(join(projects, `${sid}.jsonl`), lines.join('\n') + '\n');
write(G1, L1); write(G2, L2); write(G3, L3);
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: G3, cwd: '/repo' }));

const { default: router, forgetPastFiles } = await import('./chat-transcript.js');

const ALL = ['a1', 'a2', `rot:${G2}`, 'b1', 'b2', `rot:${G3}`, 'x1', 'c1', 'b1'];

async function withServer<T>(fn: (get: (q: string) => Promise<any>) => Promise<T>): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`;
    return await fn(async (q) => (await fetch(`${base}${q}`)).json());
  } finally {
    server.close();
  }
}

async function walkBack(get: (q: string) => Promise<any>, first: string, limit: number) {
  const pages: any[] = [];
  let cursor = first;
  for (let n = 0; n < 50; n++) {
    const page = await get(`?before=${encodeURIComponent(cursor)}&limit=${limit}`);
    pages.unshift(page);
    if (!page.has_older) break;
    cursor = page.oldest_id;
  }
  return pages.flatMap((p) => p.items);
}

test('a message in two generations shows once, at the newer generation', async () => {
  await withServer(async (get) => {
    const env = await get('');
    assert.deepEqual(env.items.map((i: any) => i.uuid), ALL);
    const x = env.items.filter((i: any) => i.uuid === 'x1');
    assert.equal(x.length, 1);
    assert.equal(x[0].ts, '2026-10-22T10:00:00Z', 'the copy that was answered');
    assert.equal(env.items.filter((i: any) => i.text === 'hello second').length, 1, 'a chance id repeat with other text stays');
  });
});

test('pages across the boundary neither drop nor repeat it, at every page size', async () => {
  await withServer(async (get) => {
    for (let limit = 1; limit <= 6; limit++) {
      const live = await get('?limit=1');
      const items = await walkBack(get, live.items[0].uuid === 'b1' ? `${G3}~b1` : live.items[0].uuid, limit);
      assert.deepEqual([...items, ...live.items].map((i: any) => i.uuid), ALL, `page size ${limit}`);
    }
  });
});

test('a cursor naming the hidden older copy still resolves', async () => {
  await withServer(async (get) => {
    const older = await get(`?before=${G2}~x1&limit=10`);
    assert.equal(older.error, undefined);
    assert.deepEqual(older.items.map((i: any) => i.uuid), ['a1', 'a2', `rot:${G2}`, 'b1', 'b2']);
    assert.equal(older.has_older, false);
    const bare = await get('?before=x1&limit=2');
    assert.deepEqual(bare.items.map((i: any) => i.uuid), ['b2', `rot:${G3}`], 'a bare uuid is the newer copy');
  });
});

test('a generation that only held the carried message shows nothing, and one marker spans it', async () => {
  forgetPastFiles();
  write(G2, [user('x1', ASK, '2026-10-21T10:00:00Z')]);
  try {
    await withServer(async (get) => {
      const env = await get('');
      assert.deepEqual(env.items.map((i: any) => i.uuid), ['a1', 'a2', `rot:${G3}`, 'x1', 'c1', 'b1']);
      const m = env.items[2];
      assert.deepEqual([m.from_session, m.to_session], [G1, G3]);
      const old = await get(`?before=${G2}~x1&limit=5`);
      assert.deepEqual(old.items.map((i: any) => i.uuid), ['a1', 'a2'], 'its cursor still resolves');
      const marker = await get(`?before=rot:${G2}&limit=5`);
      assert.deepEqual(marker.items.map((i: any) => i.uuid), ['a1', 'a2'], 'so does its old marker');
    });
  } finally {
    write(G2, L2);
    forgetPastFiles();
  }
});
