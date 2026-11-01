/**
 * A live-stream tailer for a seat with no transcript and no tmux window stops after GONE_TICKS beats in a row instead
 * of asking forever; a seat that has a transcript, or still has its window, is never stopped; a new subscriber starts a
 * stopped tailer again.
 *
 * Run: npx tsx --test src/services/transcript-tail.gone.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TranscriptTailer, GONE_TICKS } from './transcript-tail.js';

const noTurns = { read: () => [], sig: () => '' };

function tailer(path: string | null, gone: () => boolean) {
  const calls = { resolve: 0 };
  const t = new TranscriptTailer({ agentId: 'dev-demo', limit: 10, interrupted: noTurns, windowGone: gone,
    resolve: () => { calls.resolve++; return { path, sid: null }; } });
  t.subscribe(() => {});
  return { t, calls };
}

test('no transcript and no window: gone after GONE_TICKS beats, then no more work', () => {
  const { t, calls } = tailer(null, () => true);
  const out: unknown[] = [];
  for (let i = 0; i < GONE_TICKS + 5; i++) out.push(t.tick());
  assert.equal(out.indexOf('gone'), GONE_TICKS - 1);
  assert.equal(out.filter((x) => x === 'gone').length, 1);
  assert.equal(t.isStopped, true);
  const n = calls.resolve;
  t.tick(); t.tick();
  assert.equal(calls.resolve, n);                           // stopped: nothing asked any more
  t.restart();                                              // a new subscriber
  t.tick();
  assert.equal(calls.resolve, n + 1);
});

test('a window that is still there is never gone (a seat that has not written its transcript yet)', () => {
  const { t } = tailer(null, () => false);
  for (let i = 0; i < GONE_TICKS * 2; i++) assert.equal(t.tick(), undefined);
});

test('a seat with a transcript is never gone, even with no window', () => {
  const f = join(mkdtempSync(join(tmpdir(), 'tail-gone-')), 's.jsonl');
  writeFileSync(f, JSON.stringify({ type: 'user', uuid: 'u1', message: { role: 'user', content: 'Sample' } }) + '\n');
  const { t } = tailer(f, () => true);
  for (let i = 0; i < GONE_TICKS * 2; i++) assert.equal(t.tick(), undefined);
});

test('the count is of beats IN A ROW: a window seen again starts it over', () => {
  let gone = true;
  const { t } = tailer(null, () => gone);
  for (let i = 0; i < GONE_TICKS - 1; i++) t.tick();
  gone = false; t.tick();
  gone = true;
  for (let i = 0; i < GONE_TICKS - 1; i++) assert.equal(t.tick(), undefined);
  assert.equal(t.tick(), 'gone');
});
