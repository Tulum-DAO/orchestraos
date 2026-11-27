/**
 * Tail-first pages (step 2 part B): a chat page costs the bytes of the page, never a parse of the whole log, and every
 * page equals the whole-file build's. Two generations: a 1 MB retired log and a 3 MB current one, the current one
 * opening with the message the retired one was answering (carried over: shown once).
 *
 * Run: npx tsx --test src/routes/chat-transcript.tail-pages.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, statSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SEAT = 'zz-tail-pages-seat';
const G1 = '44444444-2222-4333-8444-000000000001';
const G2 = '44444444-2222-4333-8444-000000000002';
const T12 = '2026-11-25T00:00:00Z';

const root = testTmpDir('transcript-tail-pages-');
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
  g.run(1, SEAT, 1, G1, '2026-11-24T00:00:00Z', T12);
  g.run(2, SEAT, 2, G2, T12, null);
  db.prepare("INSERT INTO canonical VALUES (?, 2, ?, 'online')").run(SEAT, SEAT);
  db.close();
}

let seq = 0;
const T = (n: number) => new Date(Date.UTC(2026, 10, 25, 1, 0, 0) + n * 1000).toISOString();
const user = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: T(++seq), message: { role: 'user', content: text } });
const reply = (uuid: string, text: string, tool = false) => JSON.stringify({
  type: 'assistant', uuid, timestamp: T(++seq),
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', usage: { input_tokens: 100 + seq, output_tokens: 7 },
    content: tool
      ? [{ type: 'text', text }, { type: 'tool_use', id: `t-${uuid}`, name: 'Read', input: { file_path: '/repo/a' } }]
      : [{ type: 'text', text }] },
});
const toolResult = (uuid: string, forUuid: string, content = 'the file') => JSON.stringify({
  type: 'user', uuid, timestamp: T(++seq),
  message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: `t-${forUuid}`, content }] },
});
const filler = 'x'.repeat(900);
const queued = (uuid: string, text: string) => JSON.stringify({ type: 'attachment', uuid, timestamp: T(++seq),
  attachment: { type: 'queued_command', prompt: text, origin: { kind: 'human' }, commandMode: 'prompt' } });
const bigToolResult = (uuid: string, forUuid: string) => JSON.stringify({
  type: 'user', uuid, timestamp: T(++seq),
  message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: `t-${forUuid}`, content: 'y'.repeat(70 * 1024) }] },
});

/** Turns of (user, reply [+ tool call, its result, a closing reply]) until the log is about `mb` MB. Every 7th turn
 *  is long (a 70 KB tool result between two assistant texts: the turn's closing reply is far from its opener); every
 *  11th is a MACHINE turn (agent mail) with more tool calls than a page holds and more bytes (~360 KB) than the first
 *  chunk, so a chunk edge can land inside it; every 5th carries a queued copy of the next turn's message (shown once,
 *  as the real turn). */
function turns(prefix: string, mb: number, firstUser?: string): string[] {
  const lines: string[] = firstUser ? [firstUser] : [];
  let i = 0;
  let bytes = lines.join('\n').length;
  while (bytes < mb * 1024 * 1024) {
    const u = `${prefix}-u${i}`;
    const opener = i % 11 === 10 ? `[MSG from gm | high] (sent 0m ago) mail ${i} ${filler}` : `question ${i} ${filler}`;
    const batch = [user(u, opener), reply(`${prefix}-a${i}`, `answer ${i} ${filler}`, i % 3 === 0)];
    if (i % 3 === 0) batch.push(toolResult(`${prefix}-r${i}`, `${prefix}-a${i}`), reply(`${prefix}-c${i}`, `closing ${i} ${filler}`));
    if (i % 7 === 6) {
      batch.push(reply(`${prefix}-b${i}`, `before the long tool ${i}`, true), bigToolResult(`${prefix}-R${i}`, `${prefix}-b${i}`),
        reply(`${prefix}-d${i}`, `after the long tool ${i} ${filler}`));
    }
    if (i % 11 === 10) {
      for (let k = 0; k < 60; k++) {
        batch.push(reply(`${prefix}-m${i}-${k}`, `step ${k}`, true), toolResult(`${prefix}-n${i}-${k}`, `${prefix}-m${i}-${k}`, 'z'.repeat(6 * 1024)));
      }
      batch.push(reply(`${prefix}-e${i}`, `mail handled ${i}`));
    }
    if (i % 5 === 4) batch.push(queued(`${prefix}-q${i}`, `question ${i + 1} ${filler}`));
    for (const l of batch) bytes += l.length + 1;
    lines.push(...batch);
    i++;
  }
  return lines;
}
const L1 = turns('g1', 1);
L1.push(user('g1-last', `the question being answered when the CLI went down ${filler}`));
// The rebuilt conversation opens with that message again (same uuid, same text) and answers it.
const L2 = turns('g2', 3, user('g1-last', `the question being answered when the CLI went down ${filler}`));
const write = (sid: string, lines: string[]) => writeFileSync(join(projects, `${sid}.jsonl`), lines.join('\n') + '\n');
write(G1, L1); write(G2, L2);
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: G2, cwd: '/repo' }));
const SIZE2 = statSync(join(projects, `${G2}.jsonl`)).size;

const { default: router, tailFirstForTests, chatWindow } = await import('./chat-transcript.js');
const { clearLogCache, logCacheBytesRead, logCacheFullParses } = await import('../services/transcript-log-cache.js');
const { clearFirstPageCache } = await import('../services/transcript-first-page-cache.js');

async function withServer<T>(fn: (get: (q: string) => Promise<any>) => Promise<T>, dividers = true): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`;
    // The divider between generations is an operator's view (?dividers=1); this suite counts it, and once reads the
    // default (no divider: the generations read as one conversation).
    const extra = (q: string) => (dividers ? `${q.includes('?') ? '&' : '?'}dividers=1` : '');
    return await fn(async (q) => (await fetch(`${base}${q}${extra(q)}`)).json());
  } finally {
    server.close();
  }
}

/** The first page, then every older page back to the start, oldest page first. */
async function walkAll(get: (q: string) => Promise<any>, first: number, limit: number) {
  const pages: any[] = [];
  let page = await get(`?limit=${first}`);
  pages.unshift(page);
  for (let n = 0; page.has_older && n < 500; n++) {
    assert.ok(page.oldest_id, 'a page with older items names its cursor');
    page = await get(`?before=${encodeURIComponent(page.oldest_id)}&limit=${limit}`);
    assert.equal(page.error, undefined, `page ${n + 1}: ${page.error}`);
    pages.unshift(page);
  }
  assert.equal(page.has_older, false);
  return pages;
}

const fresh = () => { clearLogCache(); clearFirstPageCache(); };

/** Items as the whole-file build gives them (refs included: a block's ref never depends on how its page was read). */
const flat = (pages: any[]) => pages.flatMap((p) => p.items);

test('every page equals the whole-file build, from the first page back to the first generation, the carried message once', async () => {
  await withServer(async (get) => {
    tailFirstForTests({ want0: Number.POSITIVE_INFINITY });
    fresh();
    const oracle = await walkAll(get, 40, 100);
    tailFirstForTests({ want0: 256 * 1024 });
    fresh();
    const pages = await walkAll(get, 40, 100);
    assert.deepEqual(flat(pages), flat(oracle));
    // another page size: the breaks land elsewhere (inside long turns, between a queued copy and its real turn)
    fresh();
    assert.deepEqual(flat(await walkAll(get, 23, 37)), flat(oracle));
    assert.equal(flat(oracle).filter((it: any) => it.text === `question 5 ${filler}` && String(it.uuid).startsWith('g2-')).length, 1, 'precondition: the queued copy of a message is deduped against the real turn');
    assert.ok(flat(oracle).some((it: any) => it.exchange && String(it.text ?? '').startsWith('mail handled')), 'precondition: a machine turn carries the exchange of the person turn before it');
    const uuids = flat(pages).map((it: any) => it.uuid);
    assert.equal(uuids.filter((u: string) => u === 'g1-last').length, 1, 'the carried-over message shows once, at the newer copy');
    assert.equal(uuids.filter((u: string) => u === `rot:${G2}`).length, 1, 'one marker between the generations');
    assert.ok(uuids.indexOf('g1-u0') < uuids.indexOf(`rot:${G2}`) && uuids.indexOf(`rot:${G2}`) < uuids.indexOf('g2-u0'));
    // page boundaries never split a line: a reply with a tool call keeps both items on one page
    for (const p of pages) {
      const first = p.items[0];
      const last = p.items[p.items.length - 1];
      if (first?.kind === 'tool_use') assert.notEqual(p.items[1]?.uuid, first.uuid, 'a page never opens on the second item of a line');
      if (last?.kind === 'text' && last.role === 'assistant') {
        const nextOnOlder = pages[pages.indexOf(p) + 1]?.items[0];
        assert.notEqual(nextOnOlder?.uuid, last.uuid, 'a page never ends on the first item of a line');
      }
    }
    // cursor shape: a byte offset, then the uuid
    assert.match(pages[pages.length - 1].oldest_id, new RegExp(`^${G2}~@\\d+~g2-`));
  });
});

test('without dividers (the default) every page, at every chunk size, equals the whole-file build: one conversation across the generations', async () => {
  await withServer(async (get) => {
    tailFirstForTests({ want0: Number.POSITIVE_INFINITY });
    fresh();
    const oracle = flat(await walkAll(get, 40, 100));
    const uuids = oracle.map((it: any) => it.uuid);
    assert.ok(!uuids.includes(`rot:${G2}`), 'precondition: no marker between the generations');
    assert.ok(uuids.includes('g1-u0') && uuids.includes('g2-u0'), 'precondition: both generations');
    for (const want0 of [256 * 1024, 16 * 1024]) {
      tailFirstForTests({ want0 });
      fresh();
      assert.deepEqual(flat(await walkAll(get, 40, 100)), oracle, `pages at want0 ${want0}`);
      fresh();
      assert.deepEqual(flat(await walkAll(get, 23, 37)), oracle, `pages of 23/37 at want0 ${want0}`);
    }
    tailFirstForTests({ want0: 256 * 1024 });
  }, false);
});

test('the first page of a 3 MB log reads under 1 MB and parses nothing in full; an older page reads a window, not the file', async () => {
  await withServer(async (get) => {
    tailFirstForTests({ want0: 256 * 1024 });
    fresh();
    const read0 = logCacheBytesRead();
    const full0 = logCacheFullParses();
    const first = await get('?limit=40');
    assert.equal(first.items.length, 40);
    assert.equal(first.has_older, true);
    const firstRead = logCacheBytesRead() - read0;
    assert.ok(firstRead < 1024 * 1024, `first page read ${firstRead} bytes of ${SIZE2}`);
    assert.equal(logCacheFullParses(), full0, 'no whole-file parse for the first page');
    const read1 = logCacheBytesRead();
    const older = await get(`?before=${encodeURIComponent(first.oldest_id)}&limit=100`);
    assert.ok(older.items.length >= 100 && older.items.length <= 101, `a page of 100, plus the rest of a split line: ${older.items.length}`);
    const olderRead = logCacheBytesRead() - read1;
    // Bounded by the TURNS around the cursor (here a ~360 KB machine turn, reached by doubling the window), never by the file.
    assert.ok(olderRead < 2.5 * 1024 * 1024, `an older page read ${olderRead} bytes`);
    assert.equal(logCacheFullParses(), full0, 'no whole-file parse for an older page');
    // the two pages are contiguous: the older page's newest item is the line just before the first page's oldest
    const all = readFileSync(join(projects, `${G2}.jsonl`), 'utf-8').split('\n').filter(Boolean).map((l) => JSON.parse(l).uuid);
    const i0 = all.indexOf(first.items[0].uuid);
    assert.equal(all[i0 - 1], older.items[older.items.length - 1].uuid);
  });
});

test('a cursor that names nothing is an unknown cursor, never a page from the wrong place; the old forms still resolve', async () => {
  await withServer(async (get) => {
    tailFirstForTests({ want0: 256 * 1024 });
    fresh();
    const first = await get('?limit=40');
    const m = /^(.*)~@(\d+)~(.*)$/.exec(first.oldest_id)!;
    const [, sid, off, uuid] = m;
    const byOffset = await get(`?before=${encodeURIComponent(first.oldest_id)}&limit=20`);
    assert.equal(byOffset.error, undefined);
    assert.equal((await get(`?before=${encodeURIComponent(`${sid}~@${SIZE2 + 10}~${uuid}`)}&limit=20`)).error, 'unknown cursor', 'past the end');
    assert.equal((await get(`?before=${encodeURIComponent(`${sid}~@${Number(off) + 3}~${uuid}`)}&limit=20`)).error, 'unknown cursor', 'not a line start');
    assert.equal((await get(`?before=${encodeURIComponent(`${sid}~@${off}~g2-u0`)}&limit=20`)).error, 'unknown cursor', 'another uuid at that offset');
    // the older forms (a client holding a cursor from before this change) read the same page
    const byUuid = await get(`?before=${encodeURIComponent(`${sid}~${uuid}`)}&limit=20`);
    const bare = await get(`?before=${encodeURIComponent(uuid)}&limit=20`);
    assert.deepEqual(byUuid.items, byOffset.items);
    assert.deepEqual(bare.items, byOffset.items);
    assert.equal((await get(`?before=${encodeURIComponent(`${sid}~no-such-uuid`)}&limit=20`)).error, 'unknown cursor');
  });
});

test('has_older is true at the start of a chunk that is not the start of the file', async () => {
  await withServer(async (get) => {
    tailFirstForTests({ want0: 64 * 1024 });   // a small first chunk: the walk reaches its start often
    fresh();
    let page = await get('?limit=20');
    let n = 0;
    while (page.has_older && n < 2000) { page = await get(`?before=${encodeURIComponent(page.oldest_id)}&limit=20`); n++; }
    assert.equal(page.has_older, false);
    assert.equal(page.items[0].uuid, 'g1-u0', 'the walk ends at the first generation\'s first line, never early');
    tailFirstForTests({ want0: 256 * 1024 });
  });
});

/** The byte offset of the line whose uuid is `uuid` in the G2 log. */
function offsetOf(uuid: string): number {
  const buf = readFileSync(join(projects, `${G2}.jsonl`));
  let off = 0;
  for (const line of buf.toString('utf-8').split('\n')) {
    if (line && JSON.parse(line).uuid === uuid) return off;
    off += Buffer.byteLength(line, 'utf8') + 1;
  }
  throw new Error(`no line ${uuid}`);
}

test('a cursor on the long tool result: the page equals the whole-file page (the turn\'s closing reply is past the cursor)', async () => {
  await withServer(async (get) => {
    const cursor = `${G2}~@${offsetOf('g2-R6')}~g2-R6`;
    tailFirstForTests({ want0: Number.POSITIVE_INFINITY });
    fresh();
    const want = await get(`?before=${encodeURIComponent(cursor)}&limit=6`);
    tailFirstForTests({ want0: 256 * 1024 });
    fresh();
    const got = await get(`?before=${encodeURIComponent(cursor)}&limit=6`);
    assert.equal(got.error, undefined);
    assert.equal(got.items[got.items.length - 1].uuid, 'g2-b6', 'the item just older than the cursor');
    assert.deepEqual(got.items, want.items);
    assert.equal(got.items[got.items.length - 1].audience, 'agent', 'the reply before the long tool is not the closing reply');
  });
});

test('a tail chunk that holds no item at all widens until it does', () => {
  // The log ends in 400 KB of sidechain rows (a subagent's lines: Claude grammar, so the chunk is read as Claude, but a
  // plain reader shows none of them): the first page is the items above them.
  const path = join(projects, 'tail-empty.jsonl');
  const lines = [user('e-u0', 'first question'), reply('e-a0', 'first answer'), user('e-u1', 'second question'), reply('e-a1', 'second answer')];
  for (let i = 0; i < 400; i++) lines.push(JSON.stringify({ type: 'user', uuid: `side-${i}`, timestamp: T(++seq), isSidechain: true, message: { role: 'user', content: 'p'.repeat(1024) } }));
  writeFileSync(path, lines.join('\n') + '\n');
  tailFirstForTests({ want0: 64 * 1024 });
  clearLogCache();
  const win = chatWindow('zz-tail-empty', [{ session: 'e-session', generation: 1, promotedAt: null, file: () => path }], 10, null);
  tailFirstForTests({ want0: 256 * 1024 });
  assert.ok(win, 'a window');
  assert.deepEqual(win!.items.map((it: any) => it.uuid), ['e-u0', 'e-a0', 'e-u1', 'e-a1']);
  assert.equal(win!.has_older, false);
});

/** The byte offset of the line whose uuid is `uuid` in the log at `path`. */
function offsetIn(path: string, uuid: string): number {
  let off = 0;
  for (const line of readFileSync(path, 'utf-8').split('\n')) {
    if (line && JSON.parse(line).uuid === uuid) return off;
    off += Buffer.byteLength(line, 'utf8') + 1;
  }
  throw new Error(`no line ${uuid}`);
}

test('two queued copies in one turn, the page break between them: the older page shows neither copy (their real turns are past the break)', () => {
  // The operator sends two messages while the agent is mid-turn; the turn runs on (a 70 KB tool result) and ends; the
  // harness then writes both as real user turns, answered in order. The whole-file build shows each once, as the real
  // turn. A page whose cursor sits between the two copies must read forward past the copy after the cursor (a person
  // opener, but not a real turn) to the real turns, or the copy before the cursor survives on the older page.
  const path = join(projects, 'queued-pair.jsonl');
  const lines = [
    user('p-u0', `question 0 ${filler}`), reply('p-a0', `answer 0 ${filler}`, true),
    queued('p-q1', `question 1 ${filler}`),
    toolResult('p-r0', 'p-a0'),                                      // the cursor: between the two copies
    queued('p-q2', `question 2 ${filler}`),
    reply('p-b0', 'before the long tool 0', true),                   // the other cursor: after both copies
    bigToolResult('p-R0', 'p-b0'), reply('p-c0', `closing 0 ${filler}`),
    user('p-u1', `question 1 ${filler}`), reply('p-a1', `answer 1 ${filler}`, true),
    bigToolResult('p-R1', 'p-a1'), reply('p-c1', `closing 1 ${filler}`),   // the first real turn runs long too
    user('p-u2', `question 2 ${filler}`), reply('p-a2', `answer 2 ${filler}`),
    user('p-u3', `question 3 ${filler}`), reply('p-a3', `answer 3 ${filler}`),
  ];
  writeFileSync(path, lines.join('\n') + '\n');
  const segs = [{ session: 'p-session', generation: 1, promotedAt: null, file: () => path }];
  const pageAt = (uuid: string, limit: number) => {
    const cursor = `p-session~@${offsetIn(path, uuid)}~${uuid}`;
    tailFirstForTests({ want0: Number.POSITIVE_INFINITY });
    clearLogCache();
    const want = chatWindow('zz-queued-pair', segs, limit, cursor);
    tailFirstForTests({ want0: 4 * 1024 });
    clearLogCache();
    const got = chatWindow('zz-queued-pair', segs, limit, cursor);
    tailFirstForTests({ want0: 256 * 1024 });
    assert.ok(want && got, 'both windows');
    return { want: want!, got: got! };
  };
  // the cursor between the two copies: the copy after it is a person opener but not the end of the turn
  const between = pageAt('p-r0', 3);
  assert.deepEqual(between.want.items.map((it: any) => it.uuid), ['p-u0', 'p-a0', 'p-a0'], 'precondition: the whole-file page holds no queued copy');
  assert.deepEqual(between.got.items, between.want.items);
  // the cursor after both copies: the first real turn after it is the first copy's, not the end of the search
  // (the second copy's real turn lies past the first real turn's long tool result)
  const after = pageAt('p-b0', 4);
  assert.deepEqual(after.want.items.map((it: any) => it.uuid), ['p-u0', 'p-a0', 'p-a0', 'p-r0'], 'precondition: neither copy on the whole-file page');
  assert.deepEqual(after.got.items, after.want.items);
  // the newest page, read tail-first from a small chunk, shows each message once too
  clearLogCache();
  tailFirstForTests({ want0: 4 * 1024 });
  const newest = chatWindow('zz-queued-pair', segs, 50, null);
  tailFirstForTests({ want0: 256 * 1024 });
  assert.deepEqual(newest!.items.filter((it: any) => it.role === 'user' && it.kind === 'text').map((it: any) => it.uuid), ['p-u0', 'p-u1', 'p-u2', 'p-u3']);
});

test('a log of 100 KiB lines: a page costs about the bytes of the page, not several times them (the widening is sized by the lines seen)', async () => {
  // Every step is a short reply with a tool call and a 100 KiB tool result (3 items per ~100 KiB), a person turn every
  // ten steps. The first chunk
  // holds two or three lines: the next read must be sized from them, not doubled again and again (each round reads
  // the whole window once more).
  const path = join(projects, 'long-lines.jsonl');
  const lines: string[] = [user('l-u0', `go ${filler}`)];
  for (let i = 0; i < 200; i++) {
    if (i % 10 === 9) lines.push(user(`l-u${i}`, `and then ${i} ${filler}`));   // a person turn every ~1 MB
    lines.push(reply(`l-a${i}`, `step ${i}`, true), JSON.stringify({
      type: 'user', uuid: `l-r${i}`, timestamp: T(++seq),
      message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: `t-l-a${i}`, content: 'w'.repeat(100 * 1024) }] },
    }));
  }
  lines.push(reply('l-end', `done ${filler}`));
  writeFileSync(path, lines.join('\n') + '\n');
  const size = statSync(path).size;
  assert.ok(size > 19 * 1024 * 1024, `a ${size}-byte log`);
  const segs = [{ session: 'l-session', generation: 1, promotedAt: null, file: () => path }];
  const pageBytes = (items: any[]) => {
    const uuids = new Set(items.map((it: any) => it.uuid));
    return lines.filter((l) => uuids.has(JSON.parse(l).uuid)).reduce((n, l) => n + l.length + 1, 0);
  };
  tailFirstForTests({ want0: 256 * 1024 });
  clearLogCache();
  const full0 = logCacheFullParses();
  let read0 = logCacheBytesRead();
  const first = chatWindow('zz-long-lines', segs, 100, null);
  const firstRead = logCacheBytesRead() - read0;
  assert.ok(first && first.items.length >= 100 && first.items.length <= 102, `a page of 100: ${first?.items.length}`);
  const firstBytes = pageBytes(first!.items);
  assert.ok(firstRead < 1.6 * firstBytes + 256 * 1024, `the first page (${firstBytes} bytes of lines) read ${firstRead} bytes`);
  read0 = logCacheBytesRead();
  const older = chatWindow('zz-long-lines', segs, 100, first!.oldest_id);
  const olderRead = logCacheBytesRead() - read0;
  assert.ok(older && older.items.length >= 100 && older.items.length <= 102, `an older page of 100: ${older?.items.length}`);
  const olderBytes = pageBytes(older!.items);
  assert.ok(olderRead < 1.6 * olderBytes + 512 * 1024, `an older page (${olderBytes} bytes of lines) read ${olderRead} bytes`);
  // the two pages are contiguous: the older page's newest item is the line just before the first page's oldest
  const all = lines.map((l) => JSON.parse(l).uuid);
  assert.equal(all[all.indexOf(first!.items[0].uuid) - 1], older!.items[older!.items.length - 1].uuid);
  assert.equal(logCacheFullParses(), full0, 'no whole-file parse');
});

test('a retired generation whose tail chunk shows nothing is widened, never skipped: its messages and the marker stay', () => {
  // The retired log ends in 400 KB of sidechain rows (a subagent's lines: nothing the chat shows). The page just
  // older than the current generation's first line must come from the retired log's visible messages, under one
  // rotation marker, and has_older must be true while the retired log has more.
  const p1 = join(projects, 'skip-g1.jsonl');
  const p2 = join(projects, 'skip-g2.jsonl');
  const l1: string[] = [];
  for (let i = 0; i < 30; i++) l1.push(user(`s1-u${i}`, `retired question ${i} ${filler}`), reply(`s1-a${i}`, `retired answer ${i} ${filler}`));
  for (let i = 0; i < 400; i++) l1.push(JSON.stringify({ type: 'user', uuid: `s1-side-${i}`, timestamp: T(++seq), isSidechain: true, message: { role: 'user', content: 'p'.repeat(1024) } }));
  writeFileSync(p1, l1.join('\n') + '\n');
  const l2 = [user('s2-u0', `current question 0 ${filler}`), reply('s2-a0', `current answer 0 ${filler}`), user('s2-u1', `current question 1 ${filler}`), reply('s2-a1', `current answer 1 ${filler}`)];
  writeFileSync(p2, l2.join('\n') + '\n');
  const segs = [
    { session: 's1-session', generation: 1, promotedAt: null, file: () => p1 },
    { session: 's2-session', generation: 2, promotedAt: '2026-11-25T02:00:00Z', file: () => p2 },
  ];
  const run = (want0: number, before: string | null, limit: number) => {
    tailFirstForTests({ want0 }); clearLogCache();
    const w = chatWindow('zz-skip', segs, limit, before);
    tailFirstForTests({ want0: 256 * 1024 });
    return w;
  };
  const oracleFirst = run(Number.POSITIVE_INFINITY, null, 10)!;
  const first = run(64 * 1024, null, 10)!;
  assert.deepEqual(first.items, oracleFirst.items, 'the first page (the current log is small: all of it, the marker, and the retired tail)');
  const oracleOlder = run(Number.POSITIVE_INFINITY, first.oldest_id, 10)!;
  const older = run(64 * 1024, first.oldest_id, 10)!;
  assert.ok(oracleOlder.items.some((it: any) => String(it.uuid).startsWith('s1-')), 'precondition: the whole-file page reaches the retired messages');
  assert.deepEqual(older.items, oracleOlder.items);
  assert.equal(older.has_older, oracleOlder.has_older);
});

test('the consumed-turn mark survives the page-context strip: a dashboard send\'s real turns still end the search past both copies', () => {
  // Dashboard sends carry a <page-context> tag; the real turns are rewritten (text stripped) after the dedupe marks
  // them, so the mark must be carried, or the forward widen stops at the first real turn and the second copy stays.
  const path = join(projects, 'queued-pair-ctx.jsonl');
  const ctx = (n: number) => `question ${n} ${filler} <page-context>The person is looking at this page in the dashboard as they send this: /agents/zz. </page-context>`;
  const lines = [
    user('c-u0', ctx(0)), reply('c-a0', `answer 0 ${filler}`, true),
    queued('c-q1', ctx(1)), toolResult('c-r0', 'c-a0'), queued('c-q2', ctx(2)),
    reply('c-b0', 'before the long tool 0', true), bigToolResult('c-R0', 'c-b0'), reply('c-c0', `closing 0 ${filler}`),
    user('c-u1', ctx(1)), reply('c-a1', `answer 1 ${filler}`, true), bigToolResult('c-R1', 'c-a1'), reply('c-c1', `closing 1 ${filler}`),
    user('c-u2', ctx(2)), reply('c-a2', `answer 2 ${filler}`),
    user('c-u3', ctx(3)), reply('c-a3', `answer 3 ${filler}`),
  ];
  writeFileSync(path, lines.join('\n') + '\n');
  const segs = [{ session: 'c-session', generation: 1, promotedAt: null, file: () => path }];
  const cursor = `c-session~@${offsetIn(path, 'c-b0')}~c-b0`;
  tailFirstForTests({ want0: Number.POSITIVE_INFINITY }); clearLogCache();
  const want = chatWindow('zz-queued-ctx', segs, 4, cursor)!;
  tailFirstForTests({ want0: 4 * 1024 }); clearLogCache();
  const got = chatWindow('zz-queued-ctx', segs, 4, cursor)!;
  tailFirstForTests({ want0: 256 * 1024 });
  assert.deepEqual(want.items.map((it: any) => it.uuid), ['c-u0', 'c-a0', 'c-a0', 'c-r0'], 'precondition: neither copy on the whole-file page');
  assert.deepEqual(got.items, want.items);
});

test('a queued copy is deduped against a real turn AFTER it, never one before: a page reads the same as the whole file', () => {
  // The same words were a real turn early in the log, then sent again mid-turn much later and never written as a
  // real turn. The whole-file build must show the copy (it is a message of its own), and so must a chunk that does
  // not reach the early turn.
  const path = join(projects, 'queued-early.jsonl');
  const lines = [user('e-u0', `again ${filler}`), reply('e-a0', `first answer ${filler}`)];
  for (let i = 1; i < 60; i++) lines.push(user(`e-u${i}`, `question ${i} ${filler}`), reply(`e-a${i}`, `answer ${i} ${filler}`, i % 2 === 0));
  lines.push(user('e-u60', `question 60 ${filler}`), reply('e-a60', `answer 60 ${filler}`, true), queued('e-q', `again ${filler}`), toolResult('e-r60', 'e-a60'), reply('e-c60', `closing 60 ${filler}`));
  lines.push(user('e-u61', `question 61 ${filler}`), reply('e-a61', `answer 61 ${filler}`));
  writeFileSync(path, lines.join('\n') + '\n');
  const segs = [{ session: 'e-session', generation: 1, promotedAt: null, file: () => path }];
  tailFirstForTests({ want0: Number.POSITIVE_INFINITY }); clearLogCache();
  const want = chatWindow('zz-queued-early', segs, 12, null)!;
  tailFirstForTests({ want0: 8 * 1024 }); clearLogCache();
  const got = chatWindow('zz-queued-early', segs, 12, null)!;
  tailFirstForTests({ want0: 256 * 1024 });
  assert.ok(want.items.some((it: any) => it.uuid === 'e-q'), 'precondition: the whole-file build shows the copy (no real turn after it)');
  assert.deepEqual(got.items, want.items);
});

test('when the widening rounds run out the page is built from the whole file, never null', () => {
  // A page whose chunk holds many tiny person turns above a long opener-less stretch: sized steps are tiny, and 64
  // rounds do not reach the opener below. The build must fall back to the whole file instead of failing the page.
  const path = join(projects, 'rounds.jsonl');
  const lines = [user('r-u0', `the opener ${filler}`), reply('r-a0', 'ack', true)];
  for (let i = 0; i < 60; i++) lines.push(toolResult(`r-t${i}`, 'r-a0', 'm'.repeat(8 * 1024)));
  for (let i = 0; i < 400; i++) lines.push(user(`r-s${i}`, `s${i}`), reply(`r-b${i}`, `b${i}`));
  writeFileSync(path, lines.join('\n') + '\n');
  const segs = [{ session: 'r-session', generation: 1, promotedAt: null, file: () => path }];
  tailFirstForTests({ want0: Number.POSITIVE_INFINITY }); clearLogCache();
  const want = chatWindow('zz-rounds', segs, 900, null);
  tailFirstForTests({ want0: 512 }); clearLogCache();
  const got = chatWindow('zz-rounds', segs, 900, null);
  tailFirstForTests({ want0: 256 * 1024 });
  assert.ok(want && got, 'both windows');
  assert.deepEqual(got!.items, want!.items);
});
