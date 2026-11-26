/**
 * The parsed-log cache reads TAIL-FIRST: a log that only grew since it was parsed is read from the old end,
 * the new lines are parsed with the Claude grammar's carried state, and only the whole-array passes run again.
 * A full parse happens once per file, and again only when the file is not an append of what was parsed
 * (shorter, rewritten under the old end, or replaced: another inode).
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/transcript-log-cache.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, writeFileSync, readFileSync, renameSync, truncateSync, statSync, utimesSync } from 'node:fs';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';

const dir = testTmpDir('log-cache-');
process.env.ORCHESTRA_DIR = join(dir, 'orch');
process.env.HOME = join(dir, 'home');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { cachedLog, clearLogCache, logCacheParses, logCacheFullParses, logCacheAppends } = await import('./transcript-log-cache.js');
const { logParser, logItems } = await import('../routes/chat-transcript.js');

const T = (n: number) => new Date(Date.UTC(2026, 10, 26, 3, 0, n % 60, Math.floor(n / 60))).toISOString();
let seq = 0;
const user = (text: string) => JSON.stringify({ type: 'user', uuid: `u-${++seq}`, timestamp: T(seq), message: { role: 'user', content: text } });
const assistant = (id: string, text: string, usage?: { input_tokens: number; output_tokens: number }) => JSON.stringify({
  type: 'assistant', uuid: `a-${++seq}`, timestamp: T(seq),
  message: { id, role: 'assistant', content: [{ type: 'text', text }], ...(usage ? { usage } : {}) },
});
/** An assistant line that draws nothing (an empty content list): its usage waits for the next drawn item. */
const drewNothing = (id: string, usage: { input_tokens: number; output_tokens: number }) => JSON.stringify({
  type: 'assistant', uuid: `a-${++seq}`, timestamp: T(seq), message: { id, role: 'assistant', content: [], usage },
});

/** What a fresh, whole-file parse says (the oracle the incremental read must equal). */
function oracle(path: string): any[] {
  return logItems(readFileSync(path, 'utf-8').split('\n'), false);
}

function fresh(): { path: string; parser: ReturnType<typeof logParser> } {
  clearLogCache();
  const path = join(dir, `log-${Date.now()}-${Math.random().toString(36).slice(2)}.jsonl`);
  return { path, parser: logParser(false) };
}

test('an appended log is read from its old end: no second full parse, and the items equal a fresh whole-file parse', () => {
  const { path, parser } = fresh();
  writeFileSync(path, [user('hello'), assistant('msg_1', 'hi', { input_tokens: 10, output_tokens: 2 }), user('second')].join('\n') + '\n');
  const full0 = logCacheFullParses();
  const first = cachedLog(path, parser);
  assert.equal(logCacheFullParses(), full0 + 1);
  assert.deepEqual(first.items, oracle(path));
  // the same file again: nothing read
  const reads = logCacheParses();
  assert.equal(cachedLog(path, parser), first);
  assert.equal(logCacheParses(), reads);
  // two lines appended: one read of the new bytes, no full parse, items as a fresh parse would give them
  appendFileSync(path, [assistant('msg_2', 'answer two', { input_tokens: 20, output_tokens: 4 }), user('third')].join('\n') + '\n');
  const appends0 = logCacheAppends();
  const grown = cachedLog(path, parser);
  assert.equal(logCacheFullParses(), full0 + 1, 'no full parse on an append');
  assert.equal(logCacheAppends(), appends0 + 1);
  assert.equal(logCacheParses(), reads + 1, 'an append counts as a parse for the callers that count reads');
  assert.deepEqual(grown.items, oracle(path));
  assert.equal(grown.items.length, 5);
});

test('the Claude usage state survives the append boundary: a reply split across it is counted once, a drawn-nothing reply carries', () => {
  const { path, parser } = fresh();
  // reply msg_1: two lines; the first drawn item carries the usage, the second (after the boundary) must not count it again
  writeFileSync(path, [user('q'), assistant('msg_1', 'part one', { input_tokens: 100, output_tokens: 5 })].join('\n') + '\n');
  cachedLog(path, parser);
  appendFileSync(path, [assistant('msg_1', 'part two', { input_tokens: 100, output_tokens: 5 }),
    // reply msg_2 draws nothing: its tokens carry to msg_3's first drawn item
    drewNothing('msg_2', { input_tokens: 7, output_tokens: 1 }),
    assistant('msg_3', 'after the tool', { input_tokens: 30, output_tokens: 3 })].join('\n') + '\n');
  const grown = cachedLog(path, parser);
  assert.deepEqual(grown.items, oracle(path));
  const withUsage = grown.items.filter((it: any) => it.usage);
  assert.equal(withUsage.length, 2, 'msg_1 once, msg_3 once (msg_2 carried into it)');
  assert.equal(grown.items.filter((it: any) => it.text === 'part two')[0].usage, undefined);
  const after = grown.items.filter((it: any) => it.text === 'after the tool')[0];
  assert.equal(after.usage.input_tokens, 37, 'msg_2 (7) carried into msg_3 (30)');
});

test('a log that shrank, or was rewritten under its old end, is parsed in full again', () => {
  const { path, parser } = fresh();
  writeFileSync(path, [user('one'), user('two'), user('three')].join('\n') + '\n');
  cachedLog(path, parser);
  const full0 = logCacheFullParses();
  truncateSync(path, 0);
  writeFileSync(path, [user('uno'), user('dos')].join('\n') + '\n');
  assert.deepEqual(cachedLog(path, parser).items, oracle(path));
  assert.equal(logCacheFullParses(), full0 + 1, 'shorter: full parse');
  // same length as before, different bytes under the old end (the probe differs): full parse, not an append
  const before = readFileSync(path, 'utf-8');
  const rewritten = before.replace('"dos"', '"DOS"');
  assert.equal(rewritten.length, before.length);
  writeFileSync(path, rewritten + user('tres') + '\n');
  const got = cachedLog(path, parser);
  assert.deepEqual(got.items, oracle(path));
  assert.equal(got.items.some((it: any) => it.text === 'DOS'), true);
  assert.equal(logCacheFullParses(), full0 + 2, 'rewritten under the old end: full parse');
});

test('an atomic replace (new inode, same size and mtime) is a full parse', () => {
  const { path, parser } = fresh();
  writeFileSync(path, [user('a'), user('b')].join('\n') + '\n');
  const first = cachedLog(path, parser);
  const full0 = logCacheFullParses();
  const side = `${path}.new`;
  writeFileSync(side, [user('A'), user('B')].join('\n') + '\n');
  const { mtime, atime } = statSync(path);
  utimesSync(side, atime, mtime);
  renameSync(side, path);
  const got = cachedLog(path, parser);
  assert.notEqual(got, first);
  assert.equal(logCacheFullParses(), full0 + 1);
  assert.deepEqual(got.items, oracle(path));
});

test('a half-written last line is not parsed, and is read whole once its newline lands', () => {
  const { path, parser } = fresh();
  writeFileSync(path, user('whole') + '\n');
  cachedLog(path, parser);
  const partial = user('partial line');
  appendFileSync(path, partial.slice(0, 20));
  const mid = cachedLog(path, parser);
  assert.equal(mid.items.length, 1, 'the cut line is not an item');
  appendFileSync(path, partial.slice(20) + '\n');
  const done = cachedLog(path, parser);
  assert.equal(done.items.length, 2);
  assert.deepEqual(done.items, oracle(path));
  assert.equal(done.items[1].text, 'partial line');
});

test('two grammars on one file keep separate entries: a sidechain-keeping read never serves a plain one', () => {
  // CI on the first head of this PR: the subagent tailer (keepSidechain) parsed <sid>/subagents/agent-<id>.jsonl, then a
  // plain tailer on the same path got ITS three sidechain rows back from the cache instead of an empty log.
  const { path } = fresh();
  const side = (text: string) => JSON.stringify({ type: 'user', uuid: `s-${++seq}`, timestamp: T(seq), isSidechain: true,
    message: { role: 'user', content: text } });
  writeFileSync(path, [side('survey the code'), side('found 3 hooks')].join('\n') + '\n');
  const keeping = cachedLog(path, logParser(false, true));
  assert.equal(keeping.items.length, 2);
  const plain = cachedLog(path, logParser(false, false));
  assert.equal(plain.items.length, 0, 'a plain read of a sidechain-only log is empty whatever another reader cached');
  assert.notEqual(plain, keeping);
  // each entry hits on its own, and each follows an append with its own grammar
  const reads = logCacheParses();
  assert.equal(cachedLog(path, logParser(false, true)), keeping);
  assert.equal(cachedLog(path, logParser(false, false)), plain);
  assert.equal(logCacheParses(), reads);
  appendFileSync(path, side('done') + '\n');
  assert.equal(cachedLog(path, logParser(false, true)).items.length, 3);
  assert.equal(cachedLog(path, logParser(false, false)).items.length, 0);
});

test('a 5 MB log: an append costs a small fraction of the full parse', () => {
  const { path, parser } = fresh();
  const chunk: string[] = [];
  const filler = 'x'.repeat(1500);
  let i = 0;
  while (chunk.join('\n').length < 5 * 1024 * 1024) {
    for (let k = 0; k < 200; k++) {
      chunk.push(user(`question ${i} ${filler}`));
      chunk.push(assistant(`msg_${i}`, `answer ${i} ${filler}`, { input_tokens: 1000 + i, output_tokens: 50 }));
      i++;
    }
  }
  writeFileSync(path, chunk.join('\n') + '\n');
  const t0 = performance.now();
  const first = cachedLog(path, parser);
  const fullMs = performance.now() - t0;
  appendFileSync(path, [user('the newest question'), assistant('msg_new', 'the newest answer', { input_tokens: 5, output_tokens: 5 })].join('\n') + '\n');
  const t1 = performance.now();
  const grown = cachedLog(path, parser);
  const appendMs = performance.now() - t1;
  console.log(`5 MB log: full parse ${fullMs.toFixed(0)} ms, one append ${appendMs.toFixed(0)} ms, items ${grown.items.length}`);
  assert.equal(grown.items.length, first.items.length + 2);
  assert.equal(grown.items[grown.items.length - 1].text, 'the newest answer');
  // Half, not a fixed number: the box this runs on may be loaded (the whole-array passes dominate an append, the
  // read + JSON of 5 MB dominates a full parse; idle they measure ~20 ms vs ~400 ms, saturated ~190 ms vs ~500 ms).
  assert.ok(appendMs * 2 < fullMs, `an append (${appendMs.toFixed(0)} ms) must be well under half the full parse (${fullMs.toFixed(0)} ms)`);
});
