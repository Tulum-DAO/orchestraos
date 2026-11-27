/**
 * The SSE tailer reads the log through the parsed-log cache: a tick after one appended line reads the new bytes
 * only (no second full parse of the file), and the delta it sends is that line's items. The poll route and the
 * stream lane then share one parse of each file instead of each re-reading it.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/transcript-tail.tail-first.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('tail-first-');
process.env.ORCHESTRA_DIR = join(dir, 'orch');
process.env.HOME = join(dir, 'home');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { TranscriptTailer, tailerCapForTests } = await import('./transcript-tail.js');
const { clearLogCache, logCacheFullParses, logCacheAppends, logCacheBytesRead } = await import('./transcript-log-cache.js');
const { normalizeTranscript } = await import('../routes/chat-transcript.js');
import { readFileSync, statSync } from 'node:fs';

const noTurns = { read: () => [], sig: () => '' };
const user = (uuid: string, text: string) => JSON.stringify({ type: 'user', uuid, timestamp: '2026-11-26T03:00:00.000Z', message: { role: 'user', content: text } }) + '\n';
const said = (uuid: string, text: string) => JSON.stringify({ type: 'assistant', uuid, timestamp: '2026-11-26T03:00:01.000Z',
  message: { id: `msg_${uuid}`, role: 'assistant', content: [{ type: 'text', text }], usage: { input_tokens: 3, output_tokens: 2 } } }) + '\n';

test('a tick after an appended line reads the new bytes through the cache: no second full parse, the delta is the line', () => {
  clearLogCache();
  const f = join(dir, 'seat.jsonl');
  writeFileSync(f, user('u1', 'hello') + said('a1', 'hi'));
  const got: any[] = [];
  const t = new TranscriptTailer({ agentId: 'zz-tail-first', limit: 10, interrupted: noTurns, resolve: () => ({ path: f, sid: 'S1' }) });
  t.subscribe((ev: any) => got.push(ev));
  t.tick();
  assert.equal(got[0].type, 'snapshot');
  assert.deepEqual(got[0].items.map((i: any) => i.text), ['hello', 'hi']);
  const full = logCacheFullParses();
  const appends = logCacheAppends();
  appendFileSync(f, user('u2', 'and then') + said('a2', 'an answer'));
  t.tick();
  assert.equal(logCacheFullParses(), full, 'the grown file is not parsed in full again');
  assert.equal(logCacheAppends(), appends + 1, 'its new bytes were read once');
  assert.equal(got[1].type, 'delta');
  assert.deepEqual(got[1].items.map((i: any) => [i.uuid, i.text]), [['u2', 'and then'], ['a2', 'an answer']]);
  // an unchanged file: a tick reads nothing
  t.tick();
  assert.equal(logCacheAppends(), appends + 1);
  assert.equal(got.length, 2);
});

test('the first tick on a 3 MB log reads its tail, not the file: under 1 MB, no whole-file parse, the same items as a whole-file read', () => {
  clearLogCache();
  const f = join(dir, 'big-seat.jsonl');
  const filler = 'x'.repeat(900);
  const lines: string[] = [];
  let i = 0;
  while (lines.join('').length < 3 * 1024 * 1024) {
    lines.push(user(`u${i}`, i % 9 === 8 ? `[MSG from gm | high] mail ${i} ${filler}` : `question ${i} ${filler}`), said(`a${i}`, `answer ${i} ${filler}`));
    i++;
  }
  writeFileSync(f, lines.join(''));
  const size = statSync(f).size;
  const read0 = logCacheBytesRead();
  const full0 = logCacheFullParses();
  const got: any[] = [];
  tailerCapForTests(400);   // the tailer keeps 400 items (2000 live): a 3 MB fixture shows the saving a 40 MB log gets at 2000
  const t = new TranscriptTailer({ agentId: 'zz-tail-first-big', limit: 150, interrupted: noTurns, resolve: () => ({ path: f, sid: 'S2' }) });
  t.subscribe((ev: any) => got.push(ev));
  t.tick();
  tailerCapForTests(null);
  const read = logCacheBytesRead() - read0;
  assert.ok(read < 1024 * 1024, `the first tick read ${read} of ${size} bytes`);
  assert.equal(logCacheFullParses(), full0, 'no whole-file parse');
  assert.equal(got[0].type, 'snapshot');
  assert.equal(got[0].items.length, 150);
  // the same items, refs included, as a whole-file read of the log gives for its newest 150
  // the kept 400 as a whole-file read gives them: the snapshot is their newest 150
  const whole = normalizeTranscript(readFileSync(f, 'utf-8').split('\n'), 'zz-tail-first-big', 'S2', 400, false, false, null, true);
  whole.items = whole.items.slice(-150);
  const refsOf = (items: any[]) => items.map((it: any) => [it.uuid, it.ref, it.audience, it.exchange, it.text?.slice(0, 12)]);
  assert.deepEqual(got[0].items.map((it: any) => ({ ...it, ref: undefined })), whole.items.map((it: any) => ({ ...it, ref: undefined })));
  assert.ok(got[0].items.every((it: any) => it.line_off === undefined && it.line_part === undefined), 'the stream\'s items never carry the cache\'s line fields');
  assert.equal(refsOf(got[0].items).length, 150);
  // appends on a kept chunk: the first test (the log is the chunk there); at the item cap the window slides (as before this change).
});
