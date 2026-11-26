/**
 * Tail reads of the parsed-log cache (step 2 part B): a page reads the bytes of the page, not the whole file.
 * `cachedLogTail(path, parser, wantBytes)` parses the file from a line start at or under size - wantBytes; the
 * entry carries `start` (0 = the whole file), ranges only widen, an append extends a tail entry like any other,
 * and a grammar that cannot be read from a chunk (codex/grok/antigravity) falls back to the whole file once.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/transcript-log-cache.tail.test.ts (from api/)
 */
import { testTmpDir } from '../lib/testTmp.js';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, writeFileSync, readFileSync, openSync, readSync, closeSync } from 'node:fs';
import { join } from 'node:path';

const dir = testTmpDir('log-cache-tail-');
process.env.ORCHESTRA_DIR = join(dir, 'orch');
process.env.HOME = join(dir, 'home');
if (!process.env.ORCHESTRA_CONFIG) {
  process.env.ORCHESTRA_CONFIG = join(new URL('../../..', import.meta.url).pathname, 'orchestra.example.toml');
}
const { cachedLog, cachedLogTail, logRange, clearLogCache, logCacheParses, logCacheFullParses, logCacheAppends, logCacheBytesRead } =
  await import('./transcript-log-cache.js');
const { logParser, logItems } = await import('../routes/chat-transcript.js');

const T = (n: number) => new Date(Date.UTC(2026, 10, 26, 3, 0, n % 60, Math.floor(n / 60))).toISOString();
let seq = 0;
const user = (text: string, uuid?: string) => JSON.stringify({ type: 'user', uuid: uuid ?? `u-${++seq}`, timestamp: T(++seq), message: { role: 'user', content: text } });
const assistant = (id: string, text: string, usage?: { input_tokens: number; output_tokens: number }, uuid?: string) => JSON.stringify({
  type: 'assistant', uuid: uuid ?? `a-${++seq}`, timestamp: T(++seq),
  message: { id, role: 'assistant', content: [{ type: 'text', text }], ...(usage ? { usage } : {}) },
});
const toolUse = (id: string, uuid: string) => JSON.stringify({
  type: 'assistant', uuid, timestamp: T(++seq),
  message: { id, role: 'assistant', content: [{ type: 'tool_use', id: `toolu_${seq}`, name: 'Bash', input: { command: 'ls' } }] },
});

/** What a fresh, whole-file parse says: the oracle every tail read must be a suffix of. */
function oracle(path: string): any[] {
  return logItems(readFileSync(path, 'utf-8').split('\n'), false);
}

/** The items as a whole-file build would stamp them, minus the line fields the tail read adds. */
const bare = (items: readonly any[]) => items.map(({ line_off, line_part, ...rest }: any) => rest);

const isOpener = (it: any) => it?.kind === 'text' && it.role === 'user' && !it.mid_turn;

/** A tail read equals the whole-file parse from the chunk's first turn opener on: the items above that opener belong
 *  to a turn whose opener is above the chunk (a page never serves them: the route's enough-rule), so their audience is
 *  not the whole-file build's. */
function assertTailEqualsOracle(path: string, tail: { items: readonly any[] }): void {
  const all = oracle(path);
  const from = tail.items.findIndex(isOpener);
  assert.ok(from >= 0, 'the chunk holds a turn opener');
  const got = bare(tail.items.slice(from));
  assert.deepEqual(got, all.slice(all.length - got.length));
}

function fresh(): { path: string; parser: ReturnType<typeof logParser> } {
  clearLogCache();
  const path = join(dir, `log-${Date.now()}-${Math.random().toString(36).slice(2)}.jsonl`);
  return { path, parser: logParser(false) };
}

/** A ~5 MB Claude log: 200-line batches of (user, assistant) with 1.5 KB of filler each. */
function bigLog(path: string, mb = 5): string[] {
  const chunk: string[] = [];
  const filler = 'x'.repeat(1500);
  let i = 0;
  while (chunk.join('\n').length < mb * 1024 * 1024) {
    for (let k = 0; k < 200; k++) {
      chunk.push(user(`question ${i} ${filler}`));
      chunk.push(assistant(`msg_${i}`, `answer ${i} ${filler}`, { input_tokens: 1000 + i, output_tokens: 50 }));
      i++;
    }
  }
  writeFileSync(path, chunk.join('\n') + '\n');
  return chunk;
}

/** The uuid of the JSON line that starts at byte `off` of the file. */
function uuidAtOffset(path: string, off: number): string {
  const fd = openSync(path, 'r');
  try {
    const buf = Buffer.alloc(8192);
    const n = readSync(fd, buf, 0, buf.length, off);
    const text = buf.subarray(0, n).toString('utf-8');
    return JSON.parse(text.slice(0, text.indexOf('\n'))).uuid;
  } finally {
    closeSync(fd);
  }
}

test('a tail read parses only the end of a 5 MB log: a suffix of the whole-file parse, under 1 MB read, start on a line', () => {
  const { path, parser } = fresh();
  bigLog(path);
  const all = oracle(path);
  const read0 = logCacheBytesRead();
  const full0 = logCacheFullParses();
  const tail = cachedLogTail(path, parser, 256 * 1024);
  assert.ok(tail.start > 0, 'a tail entry does not start at the file start');
  assert.ok(logCacheBytesRead() - read0 < 1024 * 1024, `read ${logCacheBytesRead() - read0} bytes for a 256 KB tail`);
  assert.equal(logCacheFullParses(), full0, 'no whole-file parse');
  assert.ok(tail.items.length >= 100 && tail.items.length < all.length);
  assertTailEqualsOracle(path, tail);
  // the start is a record boundary: the byte before it is a newline and the line there parses
  assert.equal(readFileSync(path).subarray(tail.start - 1, tail.start)[0], 0x0a);
  assert.equal(uuidAtOffset(path, tail.start), tail.items[0].uuid);
});

test('every item carries the byte offset of its log line and its index within that line', () => {
  const { path, parser } = fresh();
  bigLog(path, 1);
  const tail = cachedLogTail(path, parser, 64 * 1024);
  for (const it of tail.items.slice(0, 20)) {
    assert.equal(typeof it.line_off, 'number');
    assert.equal(uuidAtOffset(path, it.line_off), it.uuid, 'line_off is where the item\'s own line starts');
    assert.equal(it.line_part, 0, 'one item per line in this fixture');
  }
  const offs = tail.items.map((it: any) => it.line_off);
  assert.deepEqual(offs, [...offs].sort((a, b) => a - b), 'offsets rise with the items');
  assert.equal(offs[0], tail.start);
});

test('ranges only widen: a bigger want re-parses from an earlier start and replaces the entry; a smaller want then hits', () => {
  const { path, parser } = fresh();
  bigLog(path, 2);
  const small = cachedLogTail(path, parser, 128 * 1024);
  const parses0 = logCacheParses();
  const big = cachedLogTail(path, parser, 512 * 1024);
  assert.ok(big.start < small.start);
  assert.equal(logCacheParses(), parses0 + 1);
  assert.ok(big.items.length > small.items.length);
  assert.equal(cachedLogTail(path, parser, 128 * 1024), big, 'the wider entry serves the smaller want');
  assert.equal(logCacheParses(), parses0 + 1, 'nothing parsed for the smaller want');
  // the whole file is the widest range: it replaces a tail entry and then serves every want
  const whole = cachedLog(path, parser);
  assert.equal(whole.start, 0);
  assert.deepEqual(bare(whole.items), oracle(path));
  assert.equal(cachedLogTail(path, parser, 64 * 1024), whole);
  assert.equal(cachedLog(path, parser), whole);
});

test('an append extends a tail entry through the append path: no re-parse of the range, items equal the oracle\'s end', () => {
  const { path, parser } = fresh();
  bigLog(path, 1);
  const tail = cachedLogTail(path, parser, 64 * 1024);
  const appends0 = logCacheAppends();
  const full0 = logCacheFullParses();
  appendFileSync(path, [user('the newest question'), assistant('msg_new', 'the newest answer', { input_tokens: 5, output_tokens: 5 })].join('\n') + '\n');
  const grown = cachedLogTail(path, parser, 64 * 1024);
  assert.equal(logCacheAppends(), appends0 + 1);
  assert.equal(logCacheFullParses(), full0);
  assert.equal(grown.start, tail.start, 'the range keeps its start');
  assertTailEqualsOracle(path, grown);
  assert.equal(grown.items[grown.items.length - 1].text, 'the newest answer');
  assert.equal(uuidAtOffset(path, grown.items[grown.items.length - 1].line_off), grown.items[grown.items.length - 1].uuid);
});

test('a want that covers the whole file is the whole file (start 0), as is a file smaller than the want', () => {
  const { path, parser } = fresh();
  writeFileSync(path, [user('a'), user('b'), user('c')].join('\n') + '\n');
  const got = cachedLogTail(path, parser, 1024 * 1024);
  assert.equal(got.start, 0);
  assert.deepEqual(bare(got.items), oracle(path));
  assert.equal(cachedLog(path, parser), got, 'one entry: the whole-file read hits it');
});

// A codex rollout (chat-transcript.codex.test.ts rec): a whole-file grammar; a chunk without the header is not readable as codex.
const codexRec = (ordinal: number, type: string, payload: any) => JSON.stringify({ timestamp: T(++seq), ordinal, type, payload });
const codexUser = (ordinal: number, text: string) => codexRec(ordinal, 'response_item',
  { type: 'message', id: `m${ordinal}`, role: 'user', content: [{ type: 'input_text', text }] });
const codexMeta = (ordinal: number) => codexRec(ordinal, 'session_meta', { id: 'c-1', cwd: '/w', originator: 'codex' });

test('a grammar the chunk cannot resume (codex) falls back to the whole file once; later tail wants hit that entry', () => {
  const { path, parser } = fresh();
  const lines = [codexMeta(1)];
  for (let i = 2; i < 3000; i++) lines.push(codexUser(i, `codex line ${i} ${'y'.repeat(400)}`));
  writeFileSync(path, lines.join('\n') + '\n');
  const full0 = logCacheFullParses();
  const got = cachedLogTail(path, parser, 16 * 1024);
  assert.equal(got.start, 0, 'fell back to the whole file');
  assert.equal(logCacheFullParses(), full0 + 1);
  assert.deepEqual(got.items, oracle(path));
  assert.ok(got.items.length > 2000);
  const parses = logCacheParses();
  assert.equal(cachedLogTail(path, parser, 16 * 1024), got, 'the whole entry serves the tail want');
  assert.equal(logCacheParses(), parses);
});

test('a chunk never starts between two lines that share a uuid: the start moves back to the first of them', () => {
  const { path, parser } = fresh();
  // filler, then an assistant reply written as two lines with ONE uuid (text, then its tool call), then more lines
  const lines: string[] = [];
  const filler = 'z'.repeat(2000);
  for (let i = 0; i < 40; i++) lines.push(user(`q${i} ${filler}`));
  lines.push(assistant('msg_pair', `the text part ${filler}`, undefined, 'shared-uuid'));
  lines.push(toolUse('msg_pair', 'shared-uuid'));
  const after: string[] = [];
  for (let i = 0; i < 3; i++) after.push(user(`after ${i} ${filler}`));
  lines.push(...after);
  writeFileSync(path, lines.join('\n') + '\n');
  // a want that lands exactly inside the pair: the bytes of the lines after the pair plus the tool line
  const text = readFileSync(path, 'utf-8');
  const toolLineStart = text.indexOf(lines[41]);
  const want = Buffer.byteLength(text) - toolLineStart - 1;   // one byte short of the tool line's start
  const got = cachedLogTail(path, parser, want);
  assert.ok(got.start <= text.indexOf(lines[40]), 'the start moved back to the text line of the pair');
  assert.equal(got.items[0].uuid, 'shared-uuid');
  assert.equal(got.items[0].kind, 'text');
  assert.equal(got.items[1].kind, 'tool_use');
  assert.equal(got.items[0].line_part, 0);
  assert.equal(got.items[1].line_part, 0, 'parts count within a LINE; the tool call is the first item of its own line');
  assertTailEqualsOracle(path, got);
});

test('payloads with escaped newlines and a code block: chunk starts land on record edges only', () => {
  const { path, parser } = fresh();
  const lines: string[] = [];
  for (let i = 0; i < 300; i++) {
    lines.push(user(`line ${i}\nsecond line\n\`\`\`sh\nls -la\n\`\`\`\n${'w'.repeat(300)}`));
    lines.push(assistant(`msg_${i}`, `ok\n\nparagraph two ${'v'.repeat(300)}`));
  }
  writeFileSync(path, lines.join('\n') + '\n');
  for (const want of [4 * 1024, 17 * 1024, 65 * 1024]) {
    clearLogCache();
    const got = cachedLogTail(path, parser, want);
    assert.ok(got.start > 0);
    assert.equal(uuidAtOffset(path, got.start), got.items[0].uuid, `want ${want}: the start is a record edge`);
    assertTailEqualsOracle(path, got);
  }
});

/** The byte offset of the line whose uuid is `uuid`. */
function offsetOf(path: string, uuid: string): number {
  let off = 0;
  for (const line of readFileSync(path, 'utf-8').split('\n')) {
    if (line && JSON.parse(line).uuid === uuid) return off;
    off += Buffer.byteLength(line, 'utf8') + 1;
  }
  throw new Error(`no line ${uuid}`);
}

test('a widened tail read parses only the lines above the entry it has: the bytes read grow by the prefix, the items equal a fresh read', () => {
  const { path, parser } = fresh();
  bigLog(path, 2);
  const small = cachedLogTail(path, parser, 128 * 1024);
  const read0 = logCacheBytesRead();
  const big = cachedLogTail(path, parser, 512 * 1024);
  const grew = logCacheBytesRead() - read0;
  assert.ok(big.start < small.start);
  assert.ok(grew < (small.start - big.start) + 64 * 1024, `widening read ${grew} bytes for a ${small.start - big.start}-byte prefix`);
  clearLogCache();
  const freshBig = cachedLogTail(path, parser, 512 * 1024);
  assert.equal(freshBig.start, big.start);
  assert.deepEqual(big.items, freshBig.items);
});

test('a cursor window extends either way from the window read before: back by the prefix, forward by the suffix, items equal a fresh read', () => {
  const { path, parser } = fresh();
  bigLog(path, 2);
  const mid = Math.floor((2 * 1024 * 1024) / 2);
  const first = logRange(path, parser, mid - 64 * 1024, mid + 64 * 1024);
  const read0 = logCacheBytesRead();
  const back = logRange(path, parser, mid - 256 * 1024, mid + 64 * 1024);
  const grewBack = logCacheBytesRead() - read0;
  assert.ok(back.start < first.start && back.end === first.end);
  assert.ok(grewBack < (first.start - back.start) + 64 * 1024, `widening back read ${grewBack} bytes for a ${first.start - back.start}-byte prefix`);
  const read1 = logCacheBytesRead();
  const fwd = logRange(path, parser, mid - 256 * 1024, mid + 320 * 1024);
  const grewFwd = logCacheBytesRead() - read1;
  assert.ok(fwd.start === back.start && fwd.end > back.end);
  assert.ok(grewFwd < (fwd.end - back.end) + 64 * 1024, `widening forward read ${grewFwd} bytes for a ${fwd.end - back.end}-byte suffix`);
  clearLogCache();
  const freshFwd = logRange(path, parser, mid - 256 * 1024, mid + 320 * 1024);
  assert.equal(freshFwd.start, fwd.start);
  assert.equal(freshFwd.end, fwd.end);
  assert.deepEqual(fwd.items, freshFwd.items);
});

test('a prefix that ends in a reply whose tokens are still carried is not stitched: the widened read equals a fresh one', () => {
  // The reply just above the old chunk's start drew nothing (empty content): its tokens go on the next drawn item,
  // which is the old chunk's first. A stitched parse would lose them; the cache re-parses instead.
  const { path, parser } = fresh();
  const lines: string[] = [];
  for (let i = 0; i < 40; i++) {
    lines.push(user(`question ${i} ${'x'.repeat(1500)}`));
    lines.push(assistant(`msg_${i}`, `answer ${i} ${'x'.repeat(1500)}`, { input_tokens: 1000 + i, output_tokens: 50 }));
  }
  lines.push(JSON.stringify({ type: 'assistant', uuid: 'a-empty', timestamp: T(++seq),
    message: { id: 'msg_empty', role: 'assistant', content: [], usage: { input_tokens: 7777, output_tokens: 1 } } }));
  lines.push(user('after the empty reply', 'u-after'));
  lines.push(assistant('msg_after', 'the answer after', { input_tokens: 10, output_tokens: 10 }, 'a-after'));
  for (let i = 0; i < 10; i++) lines.push(user(`late ${i}`), assistant(`msg_late_${i}`, `late answer ${i}`, { input_tokens: 1, output_tokens: 1 }));
  writeFileSync(path, lines.join('\n') + '\n');
  const size = readFileSync(path).length;
  const small = cachedLogTail(path, parser, size - offsetOf(path, 'u-after'));
  assert.equal(small.start, offsetOf(path, 'u-after'), 'the chunk starts at the line after the empty reply');
  const big = cachedLogTail(path, parser, size - offsetOf(path, 'u-30'.replace('u-30', JSON.parse(lines[60]).uuid)));
  clearLogCache();
  const freshBig = cachedLogTail(path, parser, size - offsetOf(path, JSON.parse(lines[60]).uuid));
  assert.equal(big.start, freshBig.start);
  assert.deepEqual(big.items, freshBig.items);
  const after = big.items.find((it: any) => it.uuid === 'a-after');
  assert.ok(after?.usage, 'the first drawn reply of the old chunk carries tokens');
  assert.equal(after.usage.input_tokens, 7777 + 10, 'the empty reply\'s tokens rode onto it, as in the whole-file parse');
});

test('a chunk never starts between two lines of one reply that differ in uuid but share a message id: usage is counted once', () => {
  // Claude Code writes a streamed reply's content blocks as separate lines, each with its own uuid, all with the same
  // message.id (and the same usage). A tail start, or a stitch join, between them would count the usage twice.
  const { path, parser } = fresh();
  const lines: string[] = [];
  for (let i = 0; i < 30; i++) lines.push(user(`q ${i} ${'x'.repeat(1500)}`), assistant(`msg_${i}`, `a ${i} ${'x'.repeat(1500)}`, { input_tokens: 100 + i, output_tokens: 5 }));
  lines.push(user('the two-line reply\'s question', 'u-two'));
  lines.push(assistant('msg_two', 'first block', { input_tokens: 5000, output_tokens: 1 }, 'a-two-1'));
  lines.push(assistant('msg_two', 'second block', { input_tokens: 5000, output_tokens: 1 }, 'a-two-2'));
  for (let i = 0; i < 10; i++) lines.push(user(`late ${i}`), assistant(`msg_late_${i}`, `late answer ${i}`, { input_tokens: 1, output_tokens: 1 }));
  writeFileSync(path, lines.join('\n') + '\n');
  const size = readFileSync(path).length;
  const whole = oracle(path);
  const usageOf = (items: readonly any[]) => items.filter((it: any) => it.uuid === 'a-two-1' || it.uuid === 'a-two-2').map((it: any) => it.usage ?? null);
  // a tail start exactly on the second line: moved back to the first
  const tail = cachedLogTail(path, parser, size - offsetOf(path, 'a-two-2'));
  assert.ok(tail.start <= offsetOf(path, 'a-two-1'), 'the chunk starts at or before the first line of the reply');
  assert.deepEqual(usageOf(tail.items), usageOf(whole));
  // a stitch: an entry that starts at the second line (reached from the newer side) widened past the first
  clearLogCache();
  const late = cachedLogTail(path, parser, size - offsetOf(path, JSON.parse(lines[lines.length - 20]).uuid));
  assert.ok(late.start > offsetOf(path, 'a-two-2'));
  const widened = cachedLogTail(path, parser, size - offsetOf(path, 'u-two'));
  assert.deepEqual(usageOf(widened.items), usageOf(whole), 'the join never splits the reply');
  clearLogCache();
  assert.deepEqual(bare(widened.items), bare(cachedLogTail(path, parser, size - offsetOf(path, 'u-two')).items));
});

test('the cursor-window memo is bounded: an aggregate byte budget and a count, least recently used out first', async () => {
  const { logRangeMemoStats, RANGE_MEMO_MAX_FILES, RANGE_MEMO_MAX_TOTAL_BYTES } = await import('./transcript-log-cache.js');
  clearLogCache();
  const paths: string[] = [];
  for (let i = 0; i < RANGE_MEMO_MAX_FILES + 5; i++) {
    const p = join(dir, `range-${i}-${Date.now()}.jsonl`);
    bigLog(p, 1);
    paths.push(p);
    logRange(p, logParser(false), 256 * 1024, 512 * 1024);
    const s = logRangeMemoStats();
    assert.ok(s.files <= RANGE_MEMO_MAX_FILES, `${s.files} files kept`);
    assert.ok(s.bytes <= RANGE_MEMO_MAX_TOTAL_BYTES, `${s.bytes} bytes kept`);
  }
  assert.equal(logRangeMemoStats().files, RANGE_MEMO_MAX_FILES);
  // the newest ones are the ones kept: the first file's window is read again from disk, the last file's is a hit
  const read0 = logCacheBytesRead();
  logRange(paths[paths.length - 1], logParser(false), 256 * 1024, 512 * 1024);
  assert.ok(logCacheBytesRead() - read0 < 64 * 1024, 'the newest window is a hit (only the line-edge scans are read)');
  const read1 = logCacheBytesRead();
  logRange(paths[0], logParser(false), 256 * 1024, 512 * 1024);
  assert.ok(logCacheBytesRead() - read1 > 200 * 1024, 'the oldest window was evicted and is read again');
});

test('an entry over the cache budget is never kept, from the tail path or a stitched widening', async () => {
  const { logCacheBudgetForTests, logCacheStats } = await import('./transcript-log-cache.js');
  const { path, parser } = fresh();
  bigLog(path, 2);
  logCacheBudgetForTests(1024 * 1024);
  try {
    cachedLogTail(path, parser, 1.5 * 1024 * 1024);
    assert.equal(logCacheStats().bytes, 0, 'a 1.5 MB tail entry is not kept under a 1 MB budget');
    cachedLogTail(path, parser, 256 * 1024);
    assert.ok(logCacheStats().bytes > 0 && logCacheStats().bytes <= 1024 * 1024, 'a small tail entry is kept');
    cachedLogTail(path, parser, 1.5 * 1024 * 1024);
    assert.equal(logCacheStats().bytes, 0, 'the widened (stitched) entry over the budget is not kept either');
  } finally {
    logCacheBudgetForTests(null);
  }
});
