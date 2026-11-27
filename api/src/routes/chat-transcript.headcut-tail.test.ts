/**
 * The head cut on tail-first pages: a page and the live stream read a CHUNK of the log (services/transcript-log-cache.ts),
 * and a chunk cannot see the hold phase it may start in. Every page must equal the whole-file build's, whether the
 * promotion falls inside the first tail window, below it, or is not known at all; and every cursor form resolves to
 * the same place or says "unknown cursor", never the wrong items:
 *   `~@<offset>~<uuid>` (a line cursor) names its log line, so the cut moves nothing it names; one that names a line
 *       the cut left out is unknown.
 *   `~<uuid>` resolves to that item, unknown when the cut left it out.
 *   `~#h1.<index>` (a position cursor) counts places in the view as cut; a bare `~#<index>` is from before the cut.
 *
 * Run: npx tsx --test src/routes/chat-transcript.headcut-tail.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SWAP = '2026-11-20T08:00:00Z';
const root = testTmpDir('transcript-headcut-tail-');
const home = join(root, 'home');
const orch = join(root, 'orch');
const projects = join(home, '.claude', 'projects', '-repo');
mkdirSync(projects, { recursive: true });
mkdirSync(join(orch, 'state', 'agents'), { recursive: true });
process.env.HOME = home;
process.env.ORCHESTRA_DIR = orch;
process.env.MSG_DB_PATH = join(orch, 'no-tasks.db');   // no msg_store: nothing merged
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}

// Three seats, one generation each (never real tmux sessions on any box):
//   INSIDE  promoted with a short hold phase: the takeover lies inside the first tail window.
//   BELOW   promoted with a long chat after the takeover: the first tail window starts after the promotion.
//   NONE    no promotion time: nothing is cut.
const SEATS = {
  inside: { seat: 'zz-ht-inside', sid: '66666666-2222-4333-8444-000000000001', promoted: SWAP, after: 4 },
  below: { seat: 'zz-ht-below', sid: '66666666-2222-4333-8444-000000000002', promoted: SWAP, after: 40 },
  none: { seat: 'zz-ht-none', sid: '66666666-2222-4333-8444-000000000003', promoted: null, after: 4 },
} as const;
{
  const db = new Database(join(orch, 'state', 'orchestra-registry.db'));
  db.exec(`CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  let id = 0;
  for (const s of Object.values(SEATS)) {
    id++;
    db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(s.seat, '/repo');
    db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (?,?,?,?,?,?)')
      .run(id, s.seat, 3, s.sid, s.promoted, null);
    db.prepare("INSERT INTO canonical VALUES (?, ?, ?, 'online')").run(s.seat, id, s.seat);
  }
  db.close();
}

let seq = 0;
/** Seconds from 07:00 (the hold phase) or from 08:00:30 (after the promotion). */
const at = (base: string, n: number) => new Date(Date.parse(base) + n * 1000).toISOString();
const filler = 'x'.repeat(700);
const user = (uuid: string, ts: string, text: string) =>
  JSON.stringify({ type: 'user', uuid, timestamp: ts, message: { role: 'user', content: text } });
const say = (uuid: string, ts: string, text: string, tool = false) => JSON.stringify({
  type: 'assistant', uuid, timestamp: ts,
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: tool
    ? [{ type: 'text', text }, { type: 'tool_use', id: `t-${uuid}`, name: 'Bash', input: { command: 'true' } }]
    : [{ type: 'text', text }] },
});
const result = (uuid: string, ts: string, forUuid: string) => JSON.stringify({ type: 'user', uuid, timestamp: ts,
  message: { role: 'user', content: [{ type: 'tool_result', tool_use_id: `t-${forUuid}`, content: `ok ${filler}` }] } });
const mail = (from: string, body: string) => `[MSG from ${from} | high] (sent 0m ago) ${body} — Read /tmp/agent-msg-x.md and act on it.`;

/** A green's hold phase (its brief, hydrate digests and its answers to them, all machine), then `after` operator turns. */
function seatLog(seat: string, after: number): string[] {
  const hold0 = '2026-11-20T07:00:00Z';
  const lines = [user('h-brief', at(hold0, 0), `You are ${seat}. Read /tmp/agent-init-${seat}.md and follow all instructions in it.`),
    say('h-read', at(hold0, 5), `Reading the init file. ${filler}`, true), result('h-readr', at(hold0, 6), 'h-read')];
  for (let i = 0; i < 12; i++) {
    lines.push(user(`h-wal${i}`, at(hold0, 60 + i * 60), mail('lineage-daemon', `[WAL DELTA] hydrate from ${seat} blue ${i}`)),
      say(`h-ans${i}`, at(hold0, 61 + i * 60), `Hydrate ${i} taken in. ${filler}`, true), result(`h-ansr${i}`, at(hold0, 62 + i * 60), `h-ans${i}`));
  }
  const live0 = '2026-11-20T08:00:30Z';
  for (let i = 0; i < after; i++) {
    lines.push(user(`p-u${i}`, at(live0, i * 30), `question ${i} ${filler}`),
      say(`p-a${i}`, at(live0, i * 30 + 1), `working on ${i}`, true), result(`p-r${i}`, at(live0, i * 30 + 2), `p-a${i}`),
      say(`p-c${i}`, at(live0, i * 30 + 3), `answer ${i} ${filler}`));
  }
  seq += lines.length;
  return lines;
}
const paths: Record<string, string> = {};
for (const [k, s] of Object.entries(SEATS)) {
  paths[k] = join(projects, `${s.sid}.jsonl`);
  writeFileSync(paths[k], seatLog(s.seat, s.after).join('\n') + '\n');
  writeFileSync(join(orch, 'state', 'agents', `${s.seat}.json`), JSON.stringify({ session_id: s.sid, cwd: '/repo' }));
}

const { default: router, tailFirstForTests, normalizeTranscriptFile } = await import('./chat-transcript.js');
const { clearLogCache } = await import('../services/transcript-log-cache.js');
const { clearFirstPageCache } = await import('../services/transcript-first-page-cache.js');
const fresh = () => { clearLogCache(); clearFirstPageCache(); };

async function withServer<T>(seat: string, fn: (get: (q: string) => Promise<any>) => Promise<T>): Promise<T> {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${seat}/transcript`;
    return await fn(async (q) => (await fetch(`${base}${q}`)).json());
  } finally {
    server.close();
  }
}

/** The first page, then every older page back to the start: every item, oldest first. */
async function walkAll(get: (q: string) => Promise<any>, first: number, limit: number): Promise<any[]> {
  const pages: any[] = [];
  let page = await get(`?limit=${first}`);
  pages.unshift(page);
  for (let n = 0; page.has_older && n < 500; n++) {
    page = await get(`?before=${encodeURIComponent(page.oldest_id)}&limit=${limit}`);
    assert.equal(page.error, undefined, `page ${n + 1}: ${page.error}`);
    pages.unshift(page);
  }
  assert.equal(page.has_older, false);
  return pages.flatMap((p) => p.items);
}

const ids = (items: any[]) => items.map((it) => String(it.uuid));
const isHold = (u: string) => u.startsWith('h-');

for (const [k, s] of Object.entries(SEATS)) {
  test(`${k}: every page and the live stream equal the whole-file build, at every chunk size`, async () => {
    await withServer(s.seat, async (get) => {
      tailFirstForTests({ want0: Number.POSITIVE_INFINITY });
      fresh();
      const oracle = await walkAll(get, 6, 5);
      const stream = (n: number) => ids(normalizeTranscriptFile(paths[k], s.seat, s.sid, n, false, false, false, false, true).items);
      const streamOracle = stream(500);
      if (s.promoted) {
        assert.ok(!ids(oracle).some(isHold), 'precondition: the whole-file build cuts the hold phase');
        assert.equal(ids(oracle)[0], 'p-u0', 'the chat opens on the first turn after the promotion');
      } else {
        assert.ok(ids(oracle).includes('h-ans0'), 'precondition: no promotion time, nothing cut');
      }
      for (const want0 of [16 * 1024, 4 * 1024, 1024]) {
        tailFirstForTests({ want0 });
        fresh();
        assert.deepEqual(ids(await walkAll(get, 6, 5)), ids(oracle), `pages at want0 ${want0}`);
        fresh();
        assert.deepEqual(ids(await walkAll(get, 2, 3)), ids(oracle), `pages of 2/3 at want0 ${want0}`);
        fresh();
        assert.deepEqual(stream(500), streamOracle, `stream at want0 ${want0}`);
        assert.deepEqual(stream(4), streamOracle.slice(-stream(4).length), `a short stream window at want0 ${want0}`);
      }
      tailFirstForTests({ want0: 256 * 1024 });
    });
  });
}

test('precondition: the chunk sizes put the takeover inside the first window for one seat and below it for the other', () => {
  const size = (k: string) => readFileSync(paths[k]).length;
  const holdBytes = (k: string) => readFileSync(paths[k], 'utf-8').split('\n').filter((l) => l.includes('"h-')).join('\n').length;
  assert.ok(holdBytes('inside') < size('inside') && size('inside') - holdBytes('inside') < 16 * 1024 && size('inside') > 16 * 1024,
    'INSIDE: a 16 KiB tail starts in the hold phase');
  assert.ok(size('below') - holdBytes('below') > 16 * 1024, 'BELOW: a 16 KiB tail starts after the takeover');
});

/** The byte offset of the line holding `uuid` in `path`. */
function lineOffset(path: string, uuid: string): number {
  const raw = readFileSync(path, 'utf-8');
  const i = raw.indexOf(`"uuid":"${uuid}"`);
  return Buffer.byteLength(raw.slice(0, raw.lastIndexOf('\n', i) + 1), 'utf-8');
}

test('cursor forms: a line cursor and a uuid cursor resolve as before; one naming a cut line is unknown', async () => {
  const s = SEATS.inside;
  await withServer(s.seat, async (get) => {
    for (const want0 of [Number.POSITIVE_INFINITY, 4 * 1024]) {
      tailFirstForTests({ want0 });
      fresh();
      const kept = `${s.sid}~@${lineOffset(paths.inside, 'p-u2')}~p-u2`;
      const page = await get(`?before=${encodeURIComponent(kept)}&limit=50`);
      assert.equal(page.error, undefined, `want0 ${want0}`);
      assert.deepEqual(ids(page.items), ['p-u0', 'p-a0', 'p-a0', 'p-r0', 'p-c0', 'p-u1', 'p-a1', 'p-a1', 'p-r1', 'p-c1']);
      // An older server's line cursor at a hold-phase line: the cut left that line out, so it is unknown.
      const cut = `${s.sid}~@${lineOffset(paths.inside, 'h-wal3')}~h-wal3`;
      assert.equal((await get(`?before=${encodeURIComponent(cut)}&limit=5`)).error, 'unknown cursor', `want0 ${want0}`);
      assert.equal((await get(`?before=${encodeURIComponent(`${s.sid}~h-ans3`)}&limit=5`)).error, 'unknown cursor');
      assert.deepEqual(ids((await get(`?before=${encodeURIComponent(`${s.sid}~p-u1`)}&limit=50`)).items),
        ['p-u0', 'p-a0', 'p-a0', 'p-r0', 'p-c0']);
    }
    tailFirstForTests({ want0: 256 * 1024 });
  });
});

test('cursor forms: a position cursor is versioned; a bare one is from before the cut and unknown', async () => {
  const s = SEATS.inside;
  await withServer(s.seat, async (get) => {
    fresh();
    // Position 3 of the cut view is p-r0; without the cut it was a hold-phase line.
    const now = await get(`?before=${encodeURIComponent(`${s.sid}~#h1.3`)}&limit=50`);
    assert.equal(now.error, undefined);
    assert.deepEqual(ids(now.items), ['p-u0', 'p-a0', 'p-a0']);
    assert.equal((await get(`?before=${encodeURIComponent(`${s.sid}~#3`)}&limit=5`)).error, 'unknown cursor');
    assert.equal((await get(`?before=${encodeURIComponent(`${s.sid}~#h0.3`)}&limit=5`)).error, 'unknown cursor');
  });
});
