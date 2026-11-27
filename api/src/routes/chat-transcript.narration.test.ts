// Narration saved as thinking (Claude Code 2.1.284 / Opus 5.5): within one reply, an empty thinking block then a
// thinking block WITH text = the words the terminal shows as a message. Bubbles and Modern must show them too.
//
// Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/routes/chat-transcript.narration.test.ts (from api/)
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('narration-');
process.env.ORCHESTRA_DIR = join(dir, 'orch');
process.env.HOME = join(dir, 'home');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { normalizeTranscript, logParser } = await import('./chat-transcript.js');
const { cachedLog, cachedLogTail, clearLogCache } = await import('../services/transcript-log-cache.js');

const user = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-11-21T15:03:44Z', message: { role: 'user', content: text } });
const block = (uuid: string, id: string, b: object, ts = '2026-11-21T15:03:52Z') => JSON.stringify({
  type: 'assistant', uuid, timestamp: ts, message: { id, role: 'assistant', model: 'claude-opus-5-5', content: [b] },
});
const think = (t: string) => ({ type: 'thinking', thinking: t, signature: 'sig' });
const tool = { type: 'tool_use', id: 'toolu_1', name: 'Bash', input: { command: 'ls' } };

const said = (items: any[]) => items
  .filter((it: any) => it.role === 'assistant' && (it.kind === 'text' || it.kind === 'thinking'))
  .map((it: any) => `${it.kind}:${it.text}`);
const texts = (lines: string[]) => said((normalizeTranscript(lines, 'seat', 's', 200) as any).items);

test('empty thinking then thinking with text in one reply: the second is the assistant speaking', () => {
  const lines = [user('u1', 'make it like this'),
    block('a1', 'msg_A', think('')), block('a2', 'msg_A', think('I will look up how other apps show usage.')), block('a3', 'msg_A', tool)];
  assert.deepEqual(texts(lines), ['thinking:', 'text:I will look up how other apps show usage.']);
});

test('a thinking block with text and no empty one before it stays thinking (thinking shown in full)', () => {
  const lines = [user('u1', 'hi'), block('a1', 'msg_B', think('Let me reason about this privately.')), block('a2', 'msg_B', { type: 'text', text: 'Done.' })];
  assert.deepEqual(texts(lines), ['thinking:Let me reason about this privately.', 'text:Done.']);
});

test('the pairing is per reply: an empty block in one reply does not turn the next reply’s thinking into speech', () => {
  const lines = [user('u1', 'hi'), block('a1', 'msg_C', think('')), block('a2', 'msg_C', tool),
    block('a3', 'msg_D', think('private thought of the next reply'))];
  assert.deepEqual(texts(lines), ['thinking:', 'thinking:private thought of the next reply']);
});

test('the narration is drawn for the person (audience user), like any assistant text', () => {
  const lines = [user('u1', 'go'), block('a1', 'msg_E', think('')), block('a2', 'msg_E', think('Building it now.')), block('a3', 'msg_E', tool)];
  const it = (normalizeTranscript(lines, 'seat', 's', 200) as any).items.find((x: any) => x.text === 'Building it now.');
  assert.equal(it.kind, 'text');
  assert.equal(it.narration, true);
  assert.notEqual(it.audience, 'agent');
});

test('the parsed-log cache: the empty block in one read and its text in the next append is still narration', () => {
  // The SSE tailer reads a live log as it is written: the reply's empty block can land one beat before its text.
  clearLogCache();
  const path = join(dir, 'append.jsonl');
  writeFileSync(path, [user('u1', 'go'), block('a1', 'msg_F', think(''))].join('\n') + '\n');
  cachedLog(path, logParser(false));
  appendFileSync(path, [block('a2', 'msg_F', think('Reading the config next.')), block('a3', 'msg_F', tool)].join('\n') + '\n');
  assert.deepEqual(said(cachedLog(path, logParser(false)).items as any[]), ['thinking:', 'text:Reading the config next.']);
});

test('a tail chunk read: a reply’s empty block and its text never part at the chunk edge', () => {
  // main's tail-first reads (#665) start a chunk at a line start, moved back while the line before is of the same reply
  // (transcript-log-cache.ts chunkStartAt): the edge falls between replies, so the pair is read together.
  clearLogCache();
  const path = join(dir, 'tail.jsonl');
  const filler = 'x'.repeat(2000);
  const lines: string[] = [];
  for (let i = 0; i < 40; i++) lines.push(user(`u${i}`, `question ${i} ${filler}`), block(`e${i}`, `msg_${i}`, think('')),
    block(`n${i}`, `msg_${i}`, think(`narrated ${i}`)), block(`t${i}`, `msg_${i}`, { ...tool, id: `toolu_${i}` }));
  writeFileSync(path, lines.join('\n') + '\n');
  // An edge inside the last reply's lines: n39's line is in the chunk, its empty block's line (e39) only via the move back.
  const tail = cachedLogTail(path, logParser(false), Buffer.byteLength(lines.slice(-2).join('\n') + '\n') + 1);
  assert.ok(tail.start > 0, 'a chunk, not the whole file');
  const got = said(tail.items as any[]);
  assert.ok(got.length > 0);
  assert.ok(got.every((s) => s === 'thinking:' || s.startsWith('text:narrated ')), got.join(' | '));
  assert.ok(got.includes('text:narrated 39'));
});
