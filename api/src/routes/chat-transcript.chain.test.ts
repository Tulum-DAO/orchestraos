/**
 * The whole lineage in one chat: GET /transcript stitches every generation this box can read,
 * oldest first, with ONE quiet marker item between two generations, and pages backwards with
 * `before=<cursor>`. Without `before` the envelope is exactly the one it always was. Each log is
 * parsed once and kept until it changes; a warm-up the supervisor marked is left out.
 *
 * Run: npx tsx --test src/routes/chat-transcript.chain.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, appendFileSync, readFileSync, renameSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const SEAT = 'zz-chain-seat';                  // never a real tmux session on any box
const G1 = '33333333-2222-4333-8444-000000000001';
const G2 = '33333333-2222-4333-8444-000000000002';
const G3 = '33333333-2222-4333-8444-000000000003';
const T12 = '2026-10-21T00:00:00Z';            // G2 took over from G1
const T23 = '2026-10-22T00:00:00Z';            // G3 took over from G2

const root = mkdtempSync(join(tmpdir(), 'transcript-chain-'));
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
  g.run(1, SEAT, 7, G1, '2026-10-20T00:00:00Z', T12);
  g.run(2, SEAT, 8, G2, T12, T23);
  g.run(3, SEAT, 1, G3, T23, null);              // numbers reset: order is by the swaps, never by number
  db.prepare("INSERT INTO canonical VALUES (?, 3, ?, 'online')").run(SEAT, SEAT);
  db.close();
}

const user = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-10-22T10:00:00Z', message: { role: 'user', content: text } });
const reply = (uuid: string, text: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: '2026-10-22T10:00:01Z',
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] },
});
// One log line, two items (a reply that also calls a tool): a page must never split it.
const twoBlocks = (uuid: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: '2026-10-22T10:00:02Z',
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [
    { type: 'text', text: 'looking' }, { type: 'tool_use', id: `t-${uuid}`, name: 'Read', input: { file_path: '/repo/a' } },
  ] },
});
const L1 = [user('a1', 'hello first'), reply('a2', 'first reply'), user('a3', 'more'), reply('a4', 'ok')];
const L2 = [user('b1', 'hello second'), twoBlocks('b2'), reply('b3', 'second reply')];
const L3 = [user('c1', 'hello third'), reply('c2', 'third reply')];
const write = (sid: string, lines: string[]) => writeFileSync(join(projects, `${sid}.jsonl`), lines.join('\n') + '\n');
write(G1, L1); write(G2, L2); write(G3, L3);
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: G3, cwd: '/repo' }));

const { default: router, normalizeTranscript, forgetPastFiles } = await import('./chat-transcript.js');
const { logCacheParses, clearLogCache } = await import('../services/transcript-log-cache.js');
const { lineageChainOf } = await import('../services/identity-store-reader.js');

const ALL = ['a1', 'a2', 'a3', 'a4', `rot:${G2}`, 'b1', 'b2', 'b2', 'b3', `rot:${G3}`, 'c1', 'c2'];

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

/** Every page from `first` back to the start, oldest page first, joined. */
async function walkBack(get: (q: string) => Promise<any>, first: string, limit: number) {
  const pages: any[] = [];
  let cursor = first;
  for (let n = 0; n < 50; n++) {
    const page = await get(`?before=${encodeURIComponent(cursor)}&limit=${limit}`);
    pages.unshift(page);
    if (!page.has_older) break;
    cursor = page.oldest_id;
  }
  return { pages, items: pages.flatMap((p) => p.items) };
}

test('the chain is every generation, oldest first, by the swaps', () => {
  const chain = lineageChainOf(G3, orch);
  assert.deepEqual(chain?.links.map((l) => l.session_id), [G1, G2, G3]);
  assert.equal(lineageChainOf(G2, orch), null, 'an old generation is not a head');
});

test('three generations read whole, in order, each item once, ONE marker between two', async () => {
  await withServer(async (get) => {
    const env = await get('');
    assert.deepEqual(env.items.map((i: any) => i.uuid), ALL);
    const markers = env.items.filter((i: any) => i.kind === 'rotation');
    assert.deepEqual(markers.map((m: any) => [m.from_generation, m.to_generation, m.from_session, m.to_session, m.ts]),
      [[7, 8, G1, G2, T12], [8, 1, G2, G3, T23]]);
    assert.ok(markers.every((m: any) => m.audience === 'user' && m.exchange === undefined));
    assert.equal(env.render_items.filter((n: any) => n.kind === 'rotation').length, 2);
  });
});

test('without before: exactly the old envelope (keys and count), and the live window is the newest items', async () => {
  await withServer(async (get) => {
    const env = await get('?limit=5');
    assert.deepEqual(Object.keys(env), ['agent_id', 'session_id', 'grammar_version', 'items', 'render_items']);
    assert.equal(env.items.length, 5);
    assert.deepEqual(env.items.map((i: any) => i.uuid), ALL.slice(-5));
    // a seat whose current log fills the window: the same as the one-log builder gives, plus the server's message
    // ref on each log item (reactions build step 1; normalizeTranscript, the contract's builder, stays ref-free)
    const two = await get('?limit=2');
    const lines = readFileSync(join(projects, `${G3}.jsonl`), 'utf-8').split('\n');
    assert.deepEqual(two.items.map((i: any) => i.ref), [`${G3}~c1#0`, `${G3}~c2#0`]);
    const withoutRefs = { ...two, items: two.items.map(({ ref: _ref, ...it }: any) => it) };
    assert.deepEqual(withoutRefs, normalizeTranscript(lines, SEAT, G3, 2, false, true));
  });
});

test('every log item carries <session>~<uuid>#<part>: per generation, the same in any window, page or re-read', async () => {
  await withServer(async (get) => {
    const env = await get('');
    const refs = env.items.map((i: any) => i.ref ?? null);
    assert.deepEqual(refs, [
      `${G1}~a1#0`, `${G1}~a2#0`, `${G1}~a3#0`, `${G1}~a4#0`, null,
      `${G2}~b1#0`, `${G2}~b2#0`, `${G2}~b2#1`, `${G2}~b3#0`, null,
      `${G3}~c1#0`, `${G3}~c2#0`,
    ], 'markers have none; the two blocks of one line are #0 and #1');
    const cut = await get('?limit=5');
    assert.equal(cut.items[0].uuid, 'b2', 'precondition: this window starts inside b2\'s line');
    assert.equal(cut.items[0].ref, `${G2}~b2#1`, 'numbered from the line, not from the window');
    const again = await get('');
    assert.deepEqual(again.items.map((i: any) => i.ref ?? null), refs, 're-read: the same refs');
    for (const limit of [1, 2, 3]) {
      const { items } = await walkBack(get, 'c1', limit);
      assert.deepEqual(items.map((i: any) => i.ref ?? null), refs.slice(0, 10), `pages of ${limit}: the same refs`);
    }
  });
});

test('pages walk back to the first item across both boundaries, with no gap and no repeat, at every page size', async () => {
  await withServer(async (get) => {
    for (let limit = 1; limit <= 8; limit++) {
      const live = await get('?limit=2');
      const { pages, items } = await walkBack(get, live.items[0].uuid, limit);
      assert.deepEqual([...items, ...live.items].map((i: any) => i.uuid), ALL, `page size ${limit}`);
      assert.equal(pages[0].has_older, false, 'the very first page says nothing is older');
      assert.ok(pages.slice(1).every((p) => p.has_older === true));
      assert.ok(pages.every((p) => Object.keys(p).includes('oldest_id') && p.items.length <= limit + 1),
        'a page is the limit, or one more to keep a line whole');
      const b2 = pages.filter((p) => p.items.some((i: any) => i.uuid === 'b2'));
      assert.equal(b2.length, 1, 'one line\'s items are never split between pages');
    }
  });
});

test('at the very start: nothing older, and an unknown cursor is an empty page that says so', async () => {
  await withServer(async (get) => {
    const start = await get(`?before=${G1}~a1`);
    assert.deepEqual(start.items, []);
    assert.equal(start.has_older, false);
    assert.equal(start.oldest_id, null);
    const marker = await get(`?before=rot:${G2}&limit=2`);
    assert.deepEqual(marker.items.map((i: any) => i.uuid), ['a3', 'a4'], 'the page above a marker is the older generation\'s tail');
    const bad = await get('?before=no-such-item');
    assert.deepEqual(bad.items, []);
    assert.equal(bad.has_older, false);
    assert.equal(bad.error, 'unknown cursor');
  });
});

test('a page reads each file once: cached until the file changes, and an append is seen at once', async () => {
  clearLogCache();
  const start = logCacheParses();
  await withServer(async (get) => {
    await get('');
    const parsed = logCacheParses();
    assert.equal(parsed - start, 3, 'each generation parsed once');
    await get('');
    await get(`?before=${G2}~b1&limit=3`);
    assert.equal(logCacheParses(), parsed, 'unchanged files: nothing parsed again');
    appendFileSync(join(projects, `${G3}.jsonl`), reply('c3', 'appended') + '\n');
    try {
      const env = await get('');
      assert.equal(env.items[env.items.length - 1].uuid, 'c3', 'the append is in the next poll');
      assert.equal(logCacheParses(), parsed + 1, 'only the file that changed was parsed again');
    } finally {
      write(G3, L3);
    }
  });
});

test('a generation whose file this box cannot read is passed over: one marker across it', async () => {
  forgetPastFiles();
  const gone = join(root, 'aside.jsonl');
  renameSync(join(projects, `${G2}.jsonl`), gone);
  try {
    await withServer(async (get) => {
      const env = await get('');
      assert.deepEqual(env.items.map((i: any) => i.uuid), ['a1', 'a2', 'a3', 'a4', `rot:${G3}`, 'c1', 'c2']);
      const m = env.items[4];
      assert.deepEqual([m.from_generation, m.to_generation, m.from_session, m.to_session], [7, 1, G1, G3]);
    });
  } finally {
    renameSync(gone, join(projects, `${G2}.jsonl`));
    forgetPastFiles();
  }
});

test('a warm-up the supervisor marked is left out of the chat, the live stream builder too', async () => {
  const dir = join(orch, 'state', 'headless', SEAT);
  mkdirSync(dir, { recursive: true });
  const mark = join(dir, 'warmup.json');
  const lines = [user('w1', 'handoff: here is where we are'), reply('w2', 'understood'), ...L3];
  write(G3, lines);
  try {
    writeFileSync(mark, JSON.stringify({ sessions: { [G3]: { uuids: ['w1'] } } }));
    await withServer(async (get) => {
      const env = await get('?limit=4');
      assert.deepEqual(env.items.map((i: any) => i.uuid), ['b3', `rot:${G3}`, 'c1', 'c2'], 'the warm-up exchange is gone, its reply too');
      writeFileSync(mark, JSON.stringify({ sessions: { [G3]: { through: 'w2' } } }));
      const through = await get('?limit=4');
      assert.deepEqual(through.items.map((i: any) => i.uuid), ['b3', `rot:${G3}`, 'c1', 'c2'], 'or everything up to a uuid');
      const before = await get(`?before=${G3}~c1&limit=2`);
      assert.deepEqual(before.items.map((i: any) => i.uuid), ['b3', `rot:${G3}`], 'pages skip it too');
    });
    const env = normalizeTranscript(lines, SEAT, G3, 150);
    assert.deepEqual(env.items.map((i: any) => i.uuid), ['c1', 'c2']);
    writeFileSync(mark, JSON.stringify({ sessions: { 'another-session': { uuids: ['w1'] } } }));
    assert.equal(normalizeTranscript(lines, SEAT, G3, 150).items.length, 4, 'a mark for another session leaves this one whole');
  } finally {
    rmSync(mark, { force: true });
    write(G3, L3);
  }
});
