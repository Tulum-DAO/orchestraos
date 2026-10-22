/**
 * The stitched chat (ios-watch-dev msg_41c00a45, Shaw-approved): after a rotation, GET /transcript
 * shows the previous generation's tail, ONE divider item, then the current generation, with
 * `limit` across both. The previous generation is the one of the same root retired at the very
 * moment the current head was promoted (a swap stamps both at once), never "the next lower number".
 *
 * Run: npx tsx --test src/routes/chat-transcript.rotation.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import Database from 'better-sqlite3';

const SEAT = 'zz-rotation-seat';              // never a real tmux session on any box
const OLD = '22222222-2222-4333-8444-000000000001';
const CUR = '22222222-2222-4333-8444-000000000002';
const OTHER = '22222222-2222-4333-8444-000000000003';
const SWAP = '2026-10-22T11:15:56.814239+00:00';

const root = mkdtempSync(join(tmpdir(), 'transcript-rot-'));
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
const DB = join(orch, 'state', 'orchestra-registry.db');

function store(rows: [number, string, number, string | null, string | null, string | null][], canon: [string, number][]) {
  const db = new Database(DB);
  db.exec(`DROP TABLE IF EXISTS lineages; DROP TABLE IF EXISTS generations; DROP TABLE IF EXISTS canonical;
           CREATE TABLE lineages (root TEXT PRIMARY KEY, tier TEXT, runtime TEXT, reports_to TEXT,
             always_on INTEGER, purpose TEXT, machine TEXT, cwd TEXT);
           CREATE TABLE generations (id INTEGER PRIMARY KEY, root TEXT, generation INTEGER, session_id TEXT,
             conversation_path TEXT, model TEXT, spawned_at TEXT, spawned_by TEXT, promoted_at TEXT,
             promoted_by TEXT, retired_at TEXT, resume_command TEXT, note TEXT);
           CREATE TABLE canonical (root TEXT PRIMARY KEY, generation_id INTEGER, tmux_session TEXT, status TEXT);`);
  for (const r of [SEAT, 'zz-other-seat']) db.prepare('INSERT INTO lineages (root, cwd) VALUES (?, ?)').run(r, '/repo');
  // id, root, generation, session_id, promoted_at, retired_at
  for (const g of rows) {
    db.prepare('INSERT INTO generations (id, root, generation, session_id, promoted_at, retired_at) VALUES (?,?,?,?,?,?)').run(...g);
  }
  for (const [r, gid] of canon) db.prepare("INSERT INTO canonical VALUES (?, ?, ?, 'online')").run(r, gid, r);
  db.close();
}
// The usual swap: gen 87 retired at the moment gen 2 was promoted (numbers reset; gm's do).
const NORMAL: [number, string, number, string | null, string | null, string | null][] = [
  [1, SEAT, 86, '22222222-2222-4333-8444-0000000000aa', '2026-10-20T00:00:00Z', '2026-10-21T00:00:00Z'],
  [2, SEAT, 87, OLD, '2026-10-21T00:00:00Z', SWAP],
  [3, SEAT, 2, CUR, SWAP, null],
  [4, 'zz-other-seat', 1, OTHER, '2026-10-01T00:00:00Z', null],
];
store(NORMAL, [[SEAT, 3], ['zz-other-seat', 4]]);

const user = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-10-22T10:00:00Z', message: { role: 'user', content: text } });
const reply = (uuid: string, text: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: '2026-10-22T10:00:01Z',
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text }] },
});
const OLD_LINES = [user('o1', 'You are zz-rotation-seat. old brief'), user('o2', 'hello old gen'), reply('o3', 'old reply')];
const CUR_LINES = [user('c1', 'You are zz-rotation-seat. new brief'), reply('c2', 'new reply')];
writeFileSync(join(projects, `${OLD}.jsonl`), OLD_LINES.join('\n') + '\n');
writeFileSync(join(projects, `${CUR}.jsonl`), CUR_LINES.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: CUR, cwd: '/repo' }));

// imported after HOME / ORCHESTRA_DIR point at the fixture
const { normalizeTranscript, resolvePreviousTranscript, default: router } = await import('./chat-transcript.js');
const { predecessorOf } = await import('../services/identity-store-reader.js');
const { tagSynthetics } = await import('../lib/audience.js');

const DIVIDER = { kind: 'rotation', role: 'system', uuid: `rot:${CUR}`, ts: SWAP, from_generation: 87, to_generation: 2, from_session: OLD, to_session: CUR };

test('the predecessor is the generation retired at the moment the head was promoted, not the next lower number', () => {
  const p = predecessorOf(CUR, orch);
  assert.ok(p);
  assert.equal(p!.from_session, OLD);
  assert.equal(p!.from_generation, 87);
  assert.equal(p!.to_generation, 2);
  assert.equal(p!.promoted_at, SWAP);
  assert.equal(predecessorOf(OLD, orch), null, 'an old generation\'s session is not a head: nothing above it');
  assert.equal(predecessorOf(OTHER, orch), null, 'a head with nothing retired at its promotion');
});

test('two generations retired at that moment, or one with no session, is no predecessor', () => {
  try {
    store([...NORMAL, [5, SEAT, 88, '22222222-2222-4333-8444-0000000000bb', '2026-10-21T00:00:00Z', SWAP]], [[SEAT, 3]]);
    assert.equal(predecessorOf(CUR, orch), null);
    store([[2, SEAT, 87, null, '2026-10-21T00:00:00Z', SWAP], [3, SEAT, 2, CUR, SWAP, null]], [[SEAT, 3]]);
    assert.equal(predecessorOf(CUR, orch), null);
    store([[2, SEAT, 87, OLD, '2026-10-21T00:00:00Z', SWAP], [3, SEAT, 2, CUR, null, null]], [[SEAT, 3]]);
    assert.equal(predecessorOf(CUR, orch), null, 'a head never promoted');
  } finally {
    store(NORMAL, [[SEAT, 3], ['zz-other-seat', 4]]);
  }
});

test('the previous file resolves by the lineage project dir, only for this seat\'s lineage', () => {
  const prev = resolvePreviousTranscript(SEAT, CUR);
  assert.ok(prev);
  assert.equal(prev!.path, join(projects, `${OLD}.jsonl`));
  assert.deepEqual(prev!.divider, DIVIDER);
  assert.ok(resolvePreviousTranscript(`${SEAT}-g9`, CUR), 'a shadow root id is the same lineage');
  assert.equal(resolvePreviousTranscript('zz-other-seat', CUR), null, 'another seat never gets this thread');
  assert.equal(resolvePreviousTranscript(SEAT, null), null);
});

test('no previous file, or a session id that is not a plain id, stitches nothing', () => {
  try {
    writeFileSync(join(projects, '..', 'escape.jsonl'), OLD_LINES.join('\n'));   // a file the traversal WOULD reach
    store([[2, SEAT, 87, '../escape', '2026-10-21T00:00:00Z', SWAP], [3, SEAT, 2, CUR, SWAP, null]], [[SEAT, 3]]);
    assert.equal(resolvePreviousTranscript(SEAT, CUR), null);
    store([[2, SEAT, 87, '22222222-2222-4333-8444-0000000000cc', '2026-10-21T00:00:00Z', SWAP], [3, SEAT, 2, CUR, SWAP, null]], [[SEAT, 3]]);
    assert.equal(resolvePreviousTranscript(SEAT, CUR), null, 'no file on this box');
  } finally {
    store(NORMAL, [[SEAT, 3], ['zz-other-seat', 4]]);
  }
});

const prevOf = (lines: string[]) => ({ lines: () => lines, antigravity: false, divider: DIVIDER });

test('previous tail, ONE divider, then the current generation; each log keeps its own brief and exchanges', () => {
  const env = normalizeTranscript(CUR_LINES, SEAT, CUR, 150, false, false, prevOf(OLD_LINES));
  assert.deepEqual(env.items.map((i: any) => i.uuid), ['o1', 'o2', 'o3', `rot:${CUR}`, 'c1', 'c2']);
  const div = env.items[3];
  assert.deepEqual({ ...div, audience: undefined }, { ...DIVIDER, audience: undefined });
  assert.equal(div.audience, 'user', 'drawn in the conversation, never inside a strip');
  assert.equal(div.exchange, undefined);
  assert.equal(env.items[0].is_system, true, 'the old spawn brief is still a brief');
  assert.equal(env.items[4].is_system, true, 'and so is the new one');
  assert.equal(env.items[5].exchange, undefined, 'a reply after the divider never joins an old exchange');
  const node = env.render_items.find((n: any) => n.kind === 'rotation');
  assert.deepEqual(node, { kind: 'rotation', from_generation: 87, to_generation: 2, from_session: OLD, to_session: CUR, ts: SWAP, key: `rot:${CUR}`, audience: 'user' });
});

test('limit counts across both logs', () => {
  const env = normalizeTranscript(CUR_LINES, SEAT, CUR, 4, false, false, prevOf(OLD_LINES));
  assert.deepEqual(env.items.map((i: any) => i.uuid), ['o3', `rot:${CUR}`, 'c1', 'c2']);
});

test('a current log that fills the window never reads the previous one', () => {
  let read = 0;
  const env = normalizeTranscript(CUR_LINES, SEAT, CUR, 2, false, false,
    { lines: () => { read++; return OLD_LINES; }, antigravity: false, divider: DIVIDER });
  assert.equal(read, 0);
  assert.deepEqual(env.items.map((i: any) => i.uuid), ['c1', 'c2']);
});

test('an unreadable previous log leaves the current one alone, with no divider', () => {
  const env = normalizeTranscript(CUR_LINES, SEAT, CUR, 150, false, false,
    { lines: () => { throw new Error('EACCES'); }, antigravity: false, divider: DIVIDER });
  assert.deepEqual(env.items.map((i: any) => i.uuid), ['c1', 'c2']);
});

test('GET /transcript stitches for the seat, and the stream-less callers are unchanged', async () => {
  const express = (await import('express')).default;
  const a = express();
  a.use('/api/agents', router);
  const server = a.listen(0);
  try {
    const base = `http://127.0.0.1:${(server.address() as any).port}`;
    const env = await (await fetch(`${base}/api/agents/${SEAT}/transcript`)).json() as any;
    assert.equal(env.session_id, CUR);
    assert.deepEqual(env.items.map((i: any) => i.uuid), ['o1', 'o2', 'o3', `rot:${CUR}`, 'c1', 'c2']);
    const two = await (await fetch(`${base}/api/agents/${SEAT}/transcript?limit=2`)).json() as any;
    assert.deepEqual(two.items.map((i: any) => i.uuid), ['c1', 'c2']);
    // without its predecessor's file the chat is today's
    store([[2, SEAT, 87, '22222222-2222-4333-8444-0000000000cc', '2026-10-21T00:00:00Z', SWAP], [3, SEAT, 2, CUR, SWAP, null]], [[SEAT, 3]]);
    const alone = await (await fetch(`${base}/api/agents/${SEAT}/transcript`)).json() as any;
    assert.deepEqual(alone.items.map((i: any) => i.uuid), ['c1', 'c2']);
  } finally {
    server.close();
    store(NORMAL, [[SEAT, 3], ['zz-other-seat', 4]]);
  }
});

test('a merged synthetic after the divider never joins the previous generation\'s exchange', () => {
  const items = tagSynthetics([
    { kind: 'text', role: 'user', text: 'old ask', uuid: 'o2', audience: 'user', exchange: 'o2' },
    { ...DIVIDER, audience: 'user' },
    { kind: 'queued_batch', count: 1, entries: [] },
  ]);
  assert.equal(items[2].audience, 'agent');
  assert.equal(items[2].exchange, undefined);
  assert.equal(items[1].exchange, undefined);
});

test('order is by position: a previous-tail item written after the swap stays above the divider', () => {
  // the retiring blue keeps writing after promoted_at (measured: g38 sent mail 27 s after its swap)
  const late = JSON.stringify({
    type: 'assistant', uuid: 'o4', timestamp: '2026-10-22T11:16:23Z',
    message: { id: 'm-o4', role: 'assistant', model: 'claude-opus-5-5', content: [{ type: 'text', text: 'late old reply' }] },
  });
  const env = normalizeTranscript(CUR_LINES, SEAT, CUR, 150, false, false, prevOf([...OLD_LINES, late]));
  assert.deepEqual(env.items.map((i: any) => i.uuid), ['o1', 'o2', 'o3', 'o4', `rot:${CUR}`, 'c1', 'c2']);
  assert.ok(env.items[3].ts > env.items[4].ts, 'its ts is later than the divider and it still sits above it');
});

// A predecessor whose file is NOT in the lineage's project dir: only a path its row names can find it.
function predecessorRow(sid: string, conversationPath: string | null) {
  store([[2, SEAT, 87, sid, '2026-10-21T00:00:00Z', SWAP], [3, SEAT, 2, CUR, SWAP, null]], [[SEAT, 3]]);
  const db = new Database(DB);
  db.prepare('UPDATE generations SET conversation_path = ? WHERE id = 2').run(conversationPath);
  db.close();
}

test('a path a row names is used only when it is that session\'s own file under its runtime\'s directory', () => {
  const ELSE = '22222222-2222-4333-8444-0000000000dd';
  const otherDir = join(home, '.claude', 'projects', '-other');
  mkdirSync(otherDir, { recursive: true });
  writeFileSync(join(otherDir, `${OTHER}.jsonl`), OLD_LINES.join('\n'));      // another seat's conversation
  writeFileSync(join(otherDir, `${ELSE}.jsonl`), OLD_LINES.join('\n'));       // the predecessor's own, elsewhere
  mkdirSync(join(root, 'outside'), { recursive: true });
  writeFileSync(join(root, 'outside', `${ELSE}.jsonl`), OLD_LINES.join('\n')); // right name, wrong place
  try {
    predecessorRow(ELSE, join(otherDir, `${OTHER}.jsonl`));
    assert.equal(resolvePreviousTranscript(SEAT, CUR), null, 'another seat\'s file');
    predecessorRow(ELSE, join(root, 'outside', `${ELSE}.jsonl`));
    assert.equal(resolvePreviousTranscript(SEAT, CUR), null, 'outside ~/.claude/projects');
    predecessorRow(ELSE, join(otherDir, '..', '-other', `${ELSE}.jsonl`));
    assert.equal(resolvePreviousTranscript(SEAT, CUR)?.path, join(otherDir, `${ELSE}.jsonl`), 'its own file: used');
  } finally {
    store(NORMAL, [[SEAT, 3], ['zz-other-seat', 4]]);
  }
});

test('a Codex predecessor is read from its own rollout under ~/.codex/sessions, never a path elsewhere', () => {
  const CX = '22222222-2222-4333-8444-0000000000ee';
  const sessions = join(home, '.codex', 'sessions', '2026', '10', '22');
  mkdirSync(sessions, { recursive: true });
  const own = join(sessions, `rollout-2026-10-22T10-00-00-${CX}.jsonl`);
  writeFileSync(own, OLD_LINES.join('\n'));
  const db = new Database(join(home, '.codex', 'state_5.sqlite'));
  db.exec('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT)');
  db.prepare('INSERT OR REPLACE INTO threads VALUES (?, ?)').run(CX, own);
  db.close();
  try {
    predecessorRow(CX, null);
    assert.equal(resolvePreviousTranscript(SEAT, CUR)?.path, own);
    const db2 = new Database(join(home, '.codex', 'state_5.sqlite'));
    db2.prepare('UPDATE threads SET rollout_path = ? WHERE id = ?').run(join(projects, `${CUR}.jsonl`), CX);
    db2.close();
    assert.equal(resolvePreviousTranscript(SEAT, CUR), null, 'a rollout path pointing at another conversation');
  } finally {
    store(NORMAL, [[SEAT, 3], ['zz-other-seat', 4]]);
  }
});
