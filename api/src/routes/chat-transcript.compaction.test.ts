/**
 * The chat and the conversation store (provider-agnostic rotation, Release 1): compaction notices merged into the
 * transcript as kind `compaction` (in range on pages, gone when the seat hides them, sent live as `event:
 * compaction`), the earlier-conversation block stripped from the entry marked seen-only (and only that one), seed
 * rows skipped, a person's /compact line hidden, and the per-agent settings route. Fixture HOME, data dir and store.
 *
 * Run: npx tsx --test src/routes/chat-transcript.compaction.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, rmSync } from 'fs';
import { join } from 'path';
import Database from 'better-sqlite3';

const SEAT = 'zz-compaction-seat';            // never a real tmux session on any box
const SID = '33333333-3333-4333-8444-000000000001';
const root = testTmpDir('transcript-cmp-');
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

const BLOCK = '<earlier-conversation>\nEarlier: the Sample plan, step 2 is next.\n</earlier-conversation>\n';
const at = (s: number) => new Date(Date.parse('2026-10-31T07:00:00Z') + s * 1000).toISOString();
const user = (uuid: string, text: string, s: number) => JSON.stringify({ type: 'user', uuid, timestamp: at(s), message: { role: 'user', content: text } });
const reply = (uuid: string, text: string, s: number) => JSON.stringify({
  type: 'assistant', uuid, timestamp: at(s), message: { id: `m-${uuid}`, role: 'assistant', model: 'sample-model', content: [{ type: 'text', text }] },
});
const LINES = [
  user('s1', 'SEEDWORD seed line', 0), user('s2', 'SEEDWORD last seed', 1),
  user('u1', 'You are zz-compaction-seat. brief', 2), reply('a1', 'first reply', 3),
  user('u2', BLOCK + 'What is next?', 10), reply('a2', 'Step 2.', 11),
  user('u3', BLOCK + 'I pasted this block myself', 20), reply('a3', 'noted', 21),
  user('u4', '/compact', 30), user('u5', 'Please run /compact when you can', 31), reply('a5', 'ok', 32),
  user('u6', '<command-name>/compact</command-name>\n<command-args></command-args>', 40), reply('a6', 'later', 41),
];
writeFileSync(join(projects, `${SID}.jsonl`), LINES.join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));
writeFileSync(join(orch, 'registry.json'), JSON.stringify({ agents: { [SEAT]: { runtime: 'claude' }, 'zz-codex-seat': { runtime: 'codex' } } }));

// The store (scripts/episodic_memory.py's tables): u2 carried the block; s1..s2 are seed; two notices.
const store = new Database(join(orch, 'state', 'episodic-memory.db'));
store.exec(`CREATE TABLE seen_only (provider_session_id TEXT, uuid TEXT, marker TEXT, PRIMARY KEY (provider_session_id, uuid));
  CREATE TABLE seed_boundary (provider_session_id TEXT PRIMARY KEY, through_uuid TEXT NOT NULL);
  CREATE TABLE compaction_notices (id INTEGER PRIMARY KEY, agent TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, before_tokens INTEGER, after_tokens INTEGER, from_session TEXT, to_session TEXT, trigger TEXT NOT NULL DEFAULT 'auto');`);
store.prepare('INSERT INTO seen_only VALUES (?, ?, ?)').run(SID, 'u2', 'earlier-conversation');
store.prepare('INSERT INTO seed_boundary VALUES (?, ?)').run(SID, 's2');
const addNotice = store.prepare('INSERT INTO compaction_notices(agent, state, started_at, finished_at, before_tokens, after_tokens) VALUES (?, ?, ?, ?, ?, ?)');
addNotice.run(SEAT, 'compacted', at(5), at(9), 150000, 40000);
addNotice.run(SEAT, 'compacting', at(25), null, 160000, null);
store.close();

const { sanitizeClaudeUserText, default: router } = await import('./chat-transcript.js');
const { forgetStoreCache } = await import('../services/conversation-store.js');

async function serve(fn: (base: string) => Promise<void>, ...routers: any[]) {
  const express = (await import('express')).default;
  const a = express();
  a.use(express.json());
  for (const r of routers.length ? routers : [router]) a.use('/api/agents', r);
  const server = a.listen(0);
  try { await fn(`http://127.0.0.1:${(server.address() as any).port}`); } finally { server.close(); }
}

test('/compact as the whole message is hidden like /model and /exit; a sentence mentioning it stays', () => {
  assert.equal(sanitizeClaudeUserText('/compact'), '');
  assert.equal(sanitizeClaudeUserText('  /compact keep the Sample notes  '), '');
  assert.equal(sanitizeClaudeUserText('<command-name>/compact</command-name>\n<command-args></command-args>'), '');
  assert.equal(sanitizeClaudeUserText('Please run /compact when you can'), 'Please run /compact when you can');
  assert.equal(sanitizeClaudeUserText('/compacting is a word'), '/compacting is a word');
});

test('GET /transcript: notices by time, the seen-only block stripped from its entry only, seed rows gone, /compact hidden', async () => {
  forgetStoreCache();
  await serve(async (base) => {
    const env = await (await fetch(`${base}/api/agents/${SEAT}/transcript`)).json() as any;
    const ids = env.items.map((i: any) => i.uuid);
    assert.deepEqual(ids, ['u1', 'a1', 'cmp-1', 'u2', 'a2', 'u3', 'a3', 'cmp-2', 'u5', 'a5', 'a6']);
    const byId = (u: string) => env.items.find((i: any) => i.uuid === u);
    assert.equal(byId('u2').text, 'What is next?');
    assert.ok(byId('u3').text.includes('<earlier-conversation>'), 'a person typing the block elsewhere is never stripped');
    assert.deepEqual(byId('cmp-1'), { kind: 'compaction', id: 'cmp-1', uuid: 'cmp-1', state: 'compacted', trigger: 'auto', ts: at(5), finished_at: at(9),
                                      before_tokens: 150000, after_tokens: 40000, audience: 'user' });
    const node = env.render_items.find((n: any) => n.kind === 'compaction' && n.id === 'cmp-2');
    assert.deepEqual([node.state, node.key, node.finished_at, node.audience], ['compacting', 'cmp-2', null, 'user']);
    assert.ok(!JSON.stringify(env).includes('SEEDWORD'), 'seed rows never show');
  });
});

test('paged results carry each notice once, in range', async () => {
  forgetStoreCache();
  await serve(async (base) => {
    const newest = await (await fetch(`${base}/api/agents/${SEAT}/transcript?limit=4`)).json() as any;
    assert.deepEqual(newest.items.map((i: any) => i.uuid), ['a3', 'cmp-2', 'u5', 'a5', 'a6'], 'the newest window: its notice in range');
    const seen = new Set(newest.items.filter((i: any) => i.kind === 'compaction').map((i: any) => i.id));
    let cursor = `${SID}~${newest.items.find((i: any) => i.kind !== 'compaction').uuid}`;
    for (let n = 0; n < 10; n++) {
      const page = await (await fetch(`${base}/api/agents/${SEAT}/transcript?before=${encodeURIComponent(cursor)}&limit=2`)).json() as any;
      for (const it of page.items) if (it.kind === 'compaction') { assert.ok(!seen.has(it.id), `${it.id} twice`); seen.add(it.id); }
      if (!page.has_older || !page.oldest_id) break;
      cursor = page.oldest_id;
    }
    assert.deepEqual([...seen].sort(), ['cmp-1', 'cmp-2'], 'every notice, once, across the pages');
  });
});

test('a seat with show_compaction off gets no notices, poll or stream', async () => {
  forgetStoreCache();
  writeFileSync(join(orch, 'state', 'seat-settings.json'), JSON.stringify({ seats: { [SEAT]: { show_compaction: false } }, history: [] }));
  try {
    await serve(async (base) => {
      const env = await (await fetch(`${base}/api/agents/${SEAT}/transcript`)).json() as any;
      assert.ok(!env.items.some((i: any) => i.kind === 'compaction'));
      assert.ok(!env.render_items.some((i: any) => i.kind === 'compaction'));
    });
    const { visibleNotices } = await import('./transcript-stream.js');
    assert.deepEqual(visibleNotices(SEAT), []);
  } finally {
    rmSync(join(orch, 'state', 'seat-settings.json'));
  }
});

test('the live stream sends event: compaction with the same object', async () => {
  forgetStoreCache();
  const stream = (await import('./transcript-stream.js')).default;
  await serve(async (base) => {
    const ac = new AbortController();
    const res = await fetch(`${base}/api/agents/${SEAT}/transcript/stream`, { signal: ac.signal });
    const reader = res.body!.getReader();
    let text = '';
    const until = Date.now() + 5000;
    while (Date.now() < until && (text.match(/event: compaction/g) || []).length < 2) {
      const { value, done } = await reader.read();
      if (done) break;
      text += new TextDecoder().decode(value);
    }
    ac.abort();
    const frames = text.split('\n\n').filter((f) => f.startsWith('event: compaction'))
      .map((f) => JSON.parse(f.split('\n').find((l) => l.startsWith('data: '))!.slice(6)));
    assert.deepEqual(frames.map((f) => [f.id, f.state]), [['cmp-1', 'compacted'], ['cmp-2', 'compacting']]);
    assert.equal(frames[0].kind, 'compaction');
  }, router, stream);
});

test('GET/PUT /api/agents/:id/settings: per agent, checked, recorded with who changed it', async () => {
  const agents = (await import('./agents.js')).default;
  await serve(async (base) => {
    const get = await (await fetch(`${base}/api/agents/${SEAT}/settings`)).json() as any;
    assert.deepEqual([get.rotates, get.auto_compact, get.show_compaction, get.compact_at, get.guard_pct], [true, false, true, 65, 90]);
    // a seat our rotation can't drive (another runtime, Arturo, an unknown id): rotates false, a change refused
    for (const id of ['zz-codex-seat', 'arturo', 'zz-unknown-seat']) {
      const g = await (await fetch(`${base}/api/agents/${id}/settings`)).json() as any;
      assert.equal(g.rotates, false, id);
      const r = await fetch(`${base}/api/agents/${id}/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ compact_at: 70 }) });
      assert.equal(r.status, 409, id);
      assert.equal((await r.json() as any).error, "This agent can't compact yet");
    }
    const put = (body: unknown) => fetch(`${base}/api/agents/${SEAT}/settings`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    assert.equal((await put({ compact_at: 39 })).status, 400);
    assert.equal((await put({ compact_at: 86 })).status, 400);
    assert.equal((await put({ show_compaction: 'no' })).status, 400);
    assert.equal((await put({ guard_pct: 95 })).status, 400);
    const ok = await (await put({ compact_at: 70, auto_compact: true })).json() as any;
    assert.deepEqual([ok.ok, ok.compact_at, ok.auto_compact, ok.source.compact_at], [true, 70, true, 'seat']);
    assert.deepEqual(ok.history.map((h: any) => [h.field, h.old, h.new]), [['compact_at', 65, 70], ['auto_compact', false, true]]);
    assert.ok(ok.history.every((h: any) => typeof h.by === 'string' && h.by), 'the principal is recorded');
  }, agents);
  rmSync(join(orch, 'state', 'seat-settings.json'));
});

test('a failed /compact shows its one line; an automatic failure never shows', async () => {
  forgetStoreCache();
  const db = new Database(join(orch, 'state', 'episodic-memory.db'));
  const add = db.prepare("INSERT INTO compaction_notices(agent, state, started_at, finished_at, trigger) VALUES (?, 'failed', ?, ?, ?)");
  const manual = add.run(SEAT, at(33), at(34), 'manual').lastInsertRowid;
  const auto = add.run(SEAT, at(35), at(36), 'auto').lastInsertRowid;
  db.close();
  try {
    await serve(async (base) => {
      const env = await (await fetch(`${base}/api/agents/${SEAT}/transcript`)).json() as any;
      const ids = env.items.filter((i: any) => i.kind === 'compaction').map((i: any) => [i.id, i.state, i.trigger]);
      assert.ok(ids.some((x: any) => x[0] === `cmp-${manual}` && x[1] === 'failed' && x[2] === 'manual'));
      assert.ok(!ids.some((x: any) => x[0] === `cmp-${auto}`), 'an automatic failure has no line');
      const node = env.render_items.find((n: any) => n.id === `cmp-${manual}`);
      assert.deepEqual([node.state, node.trigger], ['failed', 'manual']);
    });
    const { visibleNotices } = await import('./transcript-stream.js');
    forgetStoreCache();
    const live = visibleNotices(SEAT).map((i: any) => i.id);
    assert.ok(live.includes(`cmp-${manual}`) && !live.includes(`cmp-${auto}`), 'the stream sends the same');
  } finally {
    const d = new Database(join(orch, 'state', 'episodic-memory.db'));
    d.prepare('DELETE FROM compaction_notices WHERE id IN (?, ?)').run(manual, auto);
    d.close();
  }
});
