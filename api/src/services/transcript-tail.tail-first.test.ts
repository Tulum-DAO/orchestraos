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
const { TranscriptTailer } = await import('./transcript-tail.js');
const { clearLogCache, logCacheFullParses, logCacheAppends } = await import('./transcript-log-cache.js');

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
