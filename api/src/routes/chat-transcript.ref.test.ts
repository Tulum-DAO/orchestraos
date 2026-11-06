/**
 * Server-stamped message refs on the poll route and the stream (reactions build step 1): GET /:id/transcript items
 * carry `ref = <session>~<uuid>#<part>`, the same on every re-read and after the log grows, and the transcript-tail
 * snapshot/delta give each item the SAME ref, so a reaction made from either view names one message.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/chat-transcript.ref.test.ts   (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, appendFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';

const SEAT = 'zz-ref-seat';                       // never a real tmux session on any box
const SID = '55555555-2222-4333-8444-000000000001';

const root = testTmpDir('transcript-ref-');
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

const user = (uuid: string, text: string) => JSON.stringify({
  type: 'user', uuid, timestamp: '2026-11-02T10:00:00Z', message: { role: 'user', content: text } });
const twoBlocks = (uuid: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: '2026-11-02T10:00:01Z',
  message: { id: `m-${uuid}`, role: 'assistant', model: 'claude-opus-5-5', content: [
    { type: 'text', text: 'looking' }, { type: 'tool_use', id: `t-${uuid}`, name: 'Read', input: { file_path: '/repo/a' } },
  ] },
});
const LOG = join(projects, `${SID}.jsonl`);
writeFileSync(LOG, [user('u1', 'hello'), twoBlocks('a1')].join('\n') + '\n');
writeFileSync(join(orch, 'state', 'agents', `${SEAT}.json`), JSON.stringify({ session_id: SID, cwd: '/repo' }));

const { default: router, resolveTranscriptPath, chatItemsFor } = await import('./chat-transcript.js');
const { TranscriptTailer } = await import('../services/transcript-tail.js');
const express = (await import('express')).default;
const app = express();
app.use('/api/agents', router);
const server = app.listen(0);
after(() => { server.close(); rmSync(root, { recursive: true, force: true }); });
const url = `http://127.0.0.1:${(server.address() as any).port}/api/agents/${SEAT}/transcript`;
const get = async (): Promise<any> => (await fetch(url)).json();
const pairs = (items: any[]) => items.filter((i) => i.ref).map((i) => [i.uuid, i.kind, i.ref]);

test('the poll route stamps refs; re-reading, and the log growing, never change an old one', async () => {
  const first = await get();
  assert.deepEqual(first.items.map((i: any) => i.ref), [`${SID}~u1#0`, `${SID}~a1#0`, `${SID}~a1#1`]);
  assert.deepEqual((await get()).items.map((i: any) => i.ref), first.items.map((i: any) => i.ref), 'a re-read');
  assert.equal(first.render_items.some((n: any) => 'ref' in n), false, 'render_items are untouched (contract)');
  appendFileSync(LOG, [user('u2', 'more'), twoBlocks('a2')].join('\n') + '\n');
  const grown = await get();
  assert.deepEqual(grown.items.map((i: any) => i.ref), [
    `${SID}~u1#0`, `${SID}~a1#0`, `${SID}~a1#1`, `${SID}~u2#0`, `${SID}~a2#0`, `${SID}~a2#1`]);
});

test('the stream gives every item the same ref as the poll route (snapshot and delta)', async () => {
  const tailer = new TranscriptTailer({ agentId: SEAT, resolve: () => resolveTranscriptPath(SEAT) });
  const events: any[] = [];
  const off = tailer.subscribe((ev) => events.push(ev));
  try {
    assert.equal(events[0].type, 'snapshot');
    assert.deepEqual(pairs(events[0].items), pairs((await get()).items));
    appendFileSync(LOG, user('u3', 'and more') + '\n');
    tailer.tick();
    const delta = events[events.length - 1];
    assert.equal(delta.type, 'delta');
    assert.deepEqual(delta.items.map((i: any) => i.ref), [`${SID}~u3#0`]);
    assert.deepEqual(pairs((await get()).items).slice(-1), pairs(delta.items));
  } finally { off(); }
});

test('a reaction wake and its silent/emoji answers: hidden on the poll route and the stream alike, same refs; chatItemsFor keeps them marked', async () => {
  const wake = '<reaction from="user:sam" emoji="👍" on="more"/> Reply, react with `orchestra react <emoji>`, or answer exactly NO_REPLY to stay silent.';
  const said = (uuid: string, text: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: '2026-11-02T10:00:05Z',
    message: { id: `m-${uuid}`, role: 'assistant', content: [{ type: 'text', text }] } });
  appendFileSync(LOG, [user('w1', wake), said('s1', 'NO_REPLY'), said('s2', '🎉'), said('s3', 'Real words.'), user('u9', 'ok'), said('s4', '👍')].join('\n') + '\n');
  const polled = await get();
  const texts = polled.items.filter((i: any) => i.kind === 'text' && i.role === 'assistant').map((i: any) => i.text);
  assert.deepEqual(texts.slice(-2), ['Real words.', '👍']);
  assert.ok(!texts.includes('NO_REPLY') && !texts.includes('🎉'));
  assert.equal(polled.render_items.some((n: any) => n.text === 'NO_REPLY' || n.text === '🎉'), false);
  const tailer = new TranscriptTailer({ agentId: SEAT, resolve: () => resolveTranscriptPath(SEAT) });
  const events: any[] = [];
  const off = tailer.subscribe((ev) => events.push(ev));
  try { assert.deepEqual(pairs(events[0].items), pairs(polled.items)); } finally { off(); }
  const marked = chatItemsFor(SEAT).filter((i: any) => i.wake_reply).map((i: any) => [i.uuid, i.wake_reply, i.ref]);
  assert.deepEqual(marked, [['s1', 'silent', `${SID}~s1#0`], ['s2', 'emoji', `${SID}~s2#0`]]);
});
