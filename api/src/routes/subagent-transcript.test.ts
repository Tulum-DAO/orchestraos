/**
 * One subagent's own transcript: GET /api/agents/:id/subagents/:aid/transcript (+ /transcript/stream),
 * the agent-tree drill-in. A subagent log is sidechain on every row; the seat's chat must still drop them,
 * and none of the seat's own window rules (store rules, warmup, cut turns) may touch a subagent's log.
 *
 * Run: npx tsx --test src/routes/subagent-transcript.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, appendFileSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import Database from 'better-sqlite3';
import type { Request, Response } from 'express';
import { normalizeTranscript, resolveSubagentTranscriptPath, handleSubagentTranscript, SAFE_SUBAGENT_ID } from './chat-transcript.js';
import { TranscriptTailer, type TailEvent } from '../services/transcript-tail.js';
import { handleSubagentStream, liveSubagentKeys, subagentTailerKey } from './transcript-stream.js';

const SUB = 'a0000000000000def';

function line(uuid: string, text: string, sidechain: boolean, type = 'assistant'): string {
  return JSON.stringify({ type, uuid, isSidechain: sidechain, agentId: sidechain ? SUB : undefined,
    timestamp: '2026-11-04T22:00:00Z', message: { role: type, content: [{ type: 'text', text }] } });
}

function seatDir() {
  const dir = testTmpDir('sub-tx-');
  const seat = join(dir, 'sid-1.jsonl');
  writeFileSync(seat, line('m1', 'seat says hi', false) + '\n');
  mkdirSync(join(dir, 'sid-1', 'subagents'), { recursive: true });
  const sub = join(dir, 'sid-1', 'subagents', `agent-${SUB}.jsonl`);
  writeFileSync(sub, [line('s1', 'survey the code', true, 'user'), line('s2', 'found 3 hooks', true)].join('\n') + '\n');
  return { seat, sub, resolveSeat: () => ({ path: seat, sid: 'sid-1' }) };
}

const texts = (items: any[]) => items.filter((i) => i.kind === 'text').map((i) => i.text);

test('the seat chat still drops sidechain rows; keepSidechain keeps them', () => {
  const lines = [line('m1', 'seat', false), line('s1', 'sub', true)];
  assert.deepEqual(texts(normalizeTranscript(lines, 'pm-x', 'sid').items), ['seat']);
  const kept = normalizeTranscript(lines, 'pm-x', 'sid', 150, false, false, null, false, true).items;
  assert.deepEqual(texts(kept), ['seat', 'sub']);
});

test("a subagent's log ignores the seat session's store rules (a seed boundary would hide it all)", () => {
  const prev = process.env.ORCHESTRA_DIR;
  const orch = testTmpDir('sub-orch-');
  mkdirSync(join(orch, 'state'), { recursive: true });
  const db = new Database(join(orch, 'state', 'episodic-memory.db'));
  db.exec('CREATE TABLE seen_only (provider_session_id TEXT, uuid TEXT, marker TEXT, PRIMARY KEY (provider_session_id, uuid));'
    + 'CREATE TABLE seed_boundary (provider_session_id TEXT PRIMARY KEY, through_uuid TEXT NOT NULL);');
  db.prepare('INSERT INTO seed_boundary VALUES (?, ?)').run('sid-rules', 'seat-only-uuid');
  db.close();
  process.env.ORCHESTRA_DIR = orch;
  try {
    const lines = [line('s1', 'survey', true, 'user'), line('s2', 'found', true)];
    assert.deepEqual(texts(normalizeTranscript(lines, 'pm-x', 'sid-rules', 150, false, false, null, false, false).items), [],
      'the seat rule applies to the seat view (its boundary uuid is not in this log)');
    assert.deepEqual(texts(normalizeTranscript(lines, 'pm-x', 'sid-rules', 150, false, false, null, false, true).items),
      ['survey', 'found']);
  } finally {
    if (prev === undefined) delete process.env.ORCHESTRA_DIR; else process.env.ORCHESTRA_DIR = prev;
  }
});

test('resolves <session>/subagents/agent-<id>.jsonl under the seat session', () => {
  const { sub, resolveSeat } = seatDir();
  assert.deepEqual(resolveSubagentTranscriptPath('pm-x', SUB, resolveSeat), { path: sub, sid: 'sid-1' });
});

test('refuses unsafe ids before building a path, and misses come back null', () => {
  const { resolveSeat } = seatDir();
  let asked = 0;
  const spy = () => { asked++; return resolveSeat(); };
  for (const bad of ['../x', 'abc', 'ABCDEF12', `${SUB}/../../etc`, '']) {
    assert.equal(resolveSubagentTranscriptPath('pm-x', bad, spy).path, null, bad);
    assert.equal(SAFE_SUBAGENT_ID.test(bad), false, bad);
  }
  assert.equal(resolveSubagentTranscriptPath('../pm', SUB, spy).path, null);
  assert.equal(asked, 0);
  assert.equal(resolveSubagentTranscriptPath('pm-x', 'aaaaaaaa00', resolveSeat).path, null);   // no such file
  const codex = () => ({ path: '/tmp/rollout-1.json', sid: 'c' });                               // not a Claude log
  assert.equal(resolveSubagentTranscriptPath('pm-x', SUB, codex).path, null);
});

test('a subagent tailer streams the sidechain rows and their deltas, without the seat cut turns', () => {
  const { sub } = seatDir();
  const events: TailEvent[] = [];
  const tailer = new TranscriptTailer({ agentId: 'pm-x', resolve: () => ({ path: sub, sid: 'sid-1' }), keepSidechain: true });
  tailer.subscribe((e) => events.push(e));
  assert.equal(events[0].type, 'snapshot');
  assert.deepEqual(texts(events[0].items), ['survey the code', 'found 3 hooks']);
  appendFileSync(sub, line('s3', 'done', true) + '\n');
  tailer.tick();
  assert.equal(events.at(-1)!.type, 'delta');
  assert.deepEqual(texts(events.at(-1)!.items), ['done']);

  const plain: TailEvent[] = [];
  new TranscriptTailer({ agentId: 'pm-x', resolve: () => ({ path: sub, sid: 'sid-1' }),
    interrupted: { read: () => [], sig: () => '' } }).subscribe((e) => plain.push(e));
  assert.equal(texts(plain[0].items).length, 0);                 // without the option the stream would be empty
});

test('the stream route refuses a bad subagent id without opening a tailer', () => {
  const calls: { status?: number; json?: any; head?: number } = {};
  const res = {
    status(c: number) { calls.status = c; return res; },
    json(b: any) { calls.json = b; return res; },
    writeHead(c: number) { calls.head = c; return res; },
  } as unknown as Response;
  handleSubagentStream({ params: { id: 'pm-x', aid: '../../x' }, query: {}, headers: {} } as unknown as Request, res);
  assert.equal(calls.status, 400);
  assert.equal(calls.head, undefined);
  assert.deepEqual(liveSubagentKeys(), []);
});

test('subagent tailers have their own namespaced keys', () => {
  assert.notEqual(subagentTailerKey('pm-x', SUB), subagentTailerKey('pm-x', 'abcdef12'));
  assert.notEqual(subagentTailerKey('pm-x', SUB), subagentTailerKey('pm-y', SUB));
  assert.equal(subagentTailerKey('pm-x', SUB), `sub:pm-x:${SUB}`);
});

function fakeRes() {
  const calls: { status?: number; json?: any } = {};
  const res = {
    status(c: number) { calls.status = c; return res; },
    json(b: any) { calls.json = b; return res; },
  } as unknown as Response;
  return { res, calls };
}
const subReq = (aid: string) => ({ params: { id: 'pm-x', aid }, query: {} } as unknown as Request);

test('the poll route: the envelope with subagent_id; a miss is 404; a bad id is 400', () => {
  const { sub } = seatDir();
  const a = fakeRes();
  handleSubagentTranscript(subReq(SUB), a.res, () => ({ path: sub, sid: 'sid-1' }));
  assert.equal(a.calls.status, undefined);
  assert.equal(a.calls.json.subagent_id, SUB);
  assert.deepEqual(texts(a.calls.json.items), ['survey the code', 'found 3 hooks']);
  const b = fakeRes();
  handleSubagentTranscript(subReq(SUB), b.res, () => ({ path: null, sid: 'sid-1' }));
  assert.equal(b.calls.status, 404);
  const c = fakeRes();
  handleSubagentTranscript(subReq('../x'), c.res, () => { throw new Error('must not resolve'); });
  assert.equal(c.calls.status, 400);
});

test('the poll route never sends the read error (it names the absolute log path)', () => {
  const dir = join(testTmpDir('sub-unreadable-'), `agent-${SUB}.jsonl`);
  writeFileSync(dir, line('s1', 'x', true) + '\n');
  chmodSync(dir, 0o000);                                        // EACCES: the error text names the path
  const r = fakeRes();
  const err = console.error;
  console.error = () => {};
  try { handleSubagentTranscript(subReq(SUB), r.res, () => ({ path: dir, sid: 'sid-1' })); } finally { console.error = err; }
  assert.equal(r.calls.status, 500);
  assert.ok(!JSON.stringify(r.calls.json).includes(dir), 'no host path in the response');
});
