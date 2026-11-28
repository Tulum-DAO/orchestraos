/**
 * The tailer's raw lines for the turn marks (services/raw-tail.ts): never the whole file. The first read takes the log's
 * last RAW_TAIL_LINES lines (reading back from the end, at most RAW_TAIL_MAX_BYTES), later reads only what was appended;
 * a turn the kept lines cannot decide is no step, never a guessed one.
 *
 * Run: ORCHESTRA_CONFIG=../orchestra.example.toml npx tsx --test src/services/raw-tail.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { appendFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { testTmpDir } from '../lib/testTmp.js';
import { RAW_TAIL_LINES, RawTail, rawTailBytesRead, rawTailBytesScanned } from './raw-tail.js';
import { TranscriptTailer } from './transcript-tail.js';
import { finalReplyUuids, turnEndFromLines } from './live-step.js';

const dir = testTmpDir('raw-tail-');
let n = 0;
const line = (o: object) => JSON.stringify({ uuid: `l-${++n}`, timestamp: `2026-11-27T06:00:${String(n % 60).padStart(2, '0')}Z`, ...o });
const said = (text: string, stop = 'end_turn') => line({ type: 'assistant', message: { id: `m-${n}`, stop_reason: stop, content: [{ type: 'text', text }] } });
const asked = (text: string) => line({ type: 'user', message: { role: 'user', content: text } });
const note = (pad = 0) => line({ type: 'attachment', attachment: { type: 'hook_success', pad: 'p'.repeat(pad) } });

test('a big log: the first read takes its last lines, not the file; an append reads only the appended bytes', () => {
  const f = join(dir, 'big.jsonl');
  const lines: string[] = [];
  for (let i = 0; i < 4000; i++) lines.push(i % 2 ? said(`answer ${i} ${'x'.repeat(900)}`) : asked(`question ${i} ${'y'.repeat(900)}`));
  writeFileSync(f, lines.join('\n') + '\n');                     // about 3.8 MB
  const t = new RawTail();
  const before = rawTailBytesRead();
  t.update(f);
  const read = rawTailBytesRead() - before;
  assert.deepEqual(t.lines, lines.slice(-RAW_TAIL_LINES));
  assert.equal(t.cut, false);
  assert.ok(read < 400 * 1024, `read ${read} bytes of ${lines.join('\n').length}`);
  const more = [asked('next'), said('done')];
  appendFileSync(f, more.join('\n') + '\n');
  const b2 = rawTailBytesRead();
  t.update(f);
  // the appended bytes, plus the bytes under the old end compared and the new end kept (64 each)
  const appended = Buffer.byteLength(more.join('\n') + '\n');
  const got = rawTailBytesRead() - b2;
  assert.ok(got >= appended && got <= appended + 128, `read ${got} for ${appended} appended`);
  assert.deepEqual(t.lines, lines.concat(more).slice(-RAW_TAIL_LINES));
  const b3 = rawTailBytesRead();
  t.update(f);
  assert.equal(rawTailBytesRead() - b3, 0, 'unchanged: a stat only');
});

test('a line still being written is not a line until its newline lands', () => {
  const f = join(dir, 'partial.jsonl');
  const a = asked('go');
  const b = said('done');
  writeFileSync(f, a + '\n' + b.slice(0, 20));
  const t = new RawTail();
  t.update(f);
  assert.deepEqual(t.lines, [a]);
  appendFileSync(f, b.slice(20) + '\n');
  t.update(f);
  assert.deepEqual(t.lines, [a, b]);
});

test('a replaced or shrunk file is read again from its end', () => {
  const f = join(dir, 'swap.jsonl');
  writeFileSync(f, [asked('one'), said('first')].join('\n') + '\n');
  const t = new RawTail();
  t.update(f);
  const fresh = [asked('two'), said('second')];
  writeFileSync(join(dir, 'swap.next'), fresh.join('\n') + '\n');
  renameSync(join(dir, 'swap.next'), f);
  t.update(f);
  assert.deepEqual(t.lines, fresh);
  writeFileSync(f, fresh[0] + '\n');
  t.update(f);
  assert.deepEqual(t.lines, [fresh[0]]);
});

test('lines too long for the byte cap: fewer kept, marked cut; a cut piece of a line is never kept', () => {
  const f = join(dir, 'long.jsonl');
  const lines = [asked('go'), ...Array.from({ length: 12 }, () => note(100 * 1024))];
  writeFileSync(f, lines.join('\n') + '\n');
  const t = new RawTail(RAW_TAIL_LINES, 256 * 1024);
  t.update(f);
  assert.equal(t.cut, true);
  assert.ok(t.lines.length >= 1 && t.lines.length < 4, `${t.lines.length} lines`);
  assert.deepEqual(t.lines, lines.slice(-t.lines.length), 'whole lines, the newest');
  // one line longer than the cap, still being written: nothing of it; its end, when it lands, is not a line either
  const g = join(dir, 'huge.jsonl');
  const huge = note(600 * 1024);
  writeFileSync(g, huge.slice(0, huge.length - 10));
  const h = new RawTail(RAW_TAIL_LINES, 256 * 1024);
  h.update(g);
  assert.deepEqual(h.lines, []);
  appendFileSync(g, huge.slice(-10) + '\n' + asked('after') + '\n');
  h.update(g);
  assert.equal(h.lines.length, 1);
  assert.equal(JSON.parse(h.lines[0]).message.content, 'after');
});

test('the tailer: turn() reads the raw tail, not the whole log, and says when the kept lines cannot decide the turn', () => {
  const f = join(dir, 'tailer.jsonl');
  const lines: string[] = [];
  for (let i = 0; i < 3000; i++) lines.push(i % 2 ? said(`answer ${i} ${'x'.repeat(900)}`) : asked(`question ${i} ${'y'.repeat(900)}`));
  writeFileSync(f, lines.join('\n') + '\n');
  const t = new TranscriptTailer({ agentId: 'seat', resolve: () => ({ path: f, sid: 's' }) });
  const off = t.subscribe(() => {});
  const turn = t.turn();
  assert.equal(turn.known, true);
  assert.equal(turn.ended, turnEndFromLines(lines));
  assert.deepEqual([...turn.finals].sort(), [...finalReplyUuids(lines)].sort());
  off();
  // A log whose newest lines are all longer than the cap together and none decides the turn: not known.
  const g = join(dir, 'tailer-long.jsonl');
  writeFileSync(g, [asked('go'), said('working', 'tool_use'), ...Array.from({ length: 60 }, () => note(100 * 1024))].join('\n') + '\n');
  const u = new TranscriptTailer({ agentId: 'seat', resolve: () => ({ path: g, sid: 's2' }) });
  const off2 = u.subscribe(() => {});
  assert.equal(u.turn().known, false);
  // Its next deciding line lands: known again.
  appendFileSync(g, said('done') + '\n');
  u.tick();
  assert.equal(u.turn().known, true);
  assert.ok(u.turn().ended);
  off2();
});

test('another CLI\'s log carries no turn marks: known, never "no step" for it', () => {
  const f = join(dir, 'codex.jsonl');
  const rows = Array.from({ length: 50 }, (_, i) => JSON.stringify({ timestamp: '2026-11-27T06:00:00Z', type: 'response_item',
    payload: { type: 'function_call_output', output: 'z'.repeat(100 * 1024), i } }));
  writeFileSync(f, rows.join('\n') + '\n');
  const t = new TranscriptTailer({ agentId: 'seat', resolve: () => ({ path: f, sid: 'c' }) });
  const off = t.subscribe(() => {});
  assert.equal(t.turn().known, true);
  off();
});

test('a turn the kept lines cannot decide is no step, whatever the items and the probe say; known: the transcript step', async () => {
  const { stepFromTurn } = await import('./live-step.js');
  const items = [{ kind: 'tool_use', id: 'a', tool: 'Bash', input: { command: 'ls', description: 'List the files' }, ts: '2026-11-27T06:00:00Z' }];
  const busy = { state: 'thinking', state_age_s: 1 };
  const now = Date.parse('2026-11-27T06:00:01Z');
  assert.equal(stepFromTurn(items, busy, { ended: null, finals: new Set(), known: false }, now), null);
  assert.equal(stepFromTurn(items, { state: 'idle', state_age_s: 0 }, { ended: null, finals: new Set(), known: false }, now), null);
  assert.deepEqual(stepFromTurn(items, busy, { ended: null, finals: new Set(), known: true }, now),
    { kind: 'tool', tool: 'Bash', summary: 'List the files', described: true, since: '2026-11-27T06:00:00Z' });
});

// First pass on #706 (grok-gm, msg_d93471e2): three cases the byte bookkeeping got wrong.
test('a cap that keeps no line at all cannot say whose log it is: not known (no step), never a guessed one', () => {
  const f = join(dir, 'one-huge-reply.jsonl');
  writeFileSync(f, [asked('go'), said('z'.repeat(5 * 1024 * 1024))].join('\n') + '\n');
  const t = new TranscriptTailer({ agentId: 'seat', resolve: () => ({ path: f, sid: 'h' }) });
  const off = t.subscribe(() => {});
  assert.equal(t.turn().known, false);
  off();
});

test('a file rewritten in place (same inode) is read again, not taken as an append', () => {
  const f = join(dir, 'rewrite.jsonl');
  const a = asked('first'), b = asked('second'), c = said('third');
  writeFileSync(f, a + '\n');
  const t = new RawTail();
  t.update(f);
  writeFileSync(f, b + '\n' + c + '\n');                         // truncates and rewrites the same inode, longer
  t.update(f);
  assert.deepEqual(t.lines, [b, c]);
});

test('a line still being written that ends inside a UTF-8 character: its bytes are counted, not its decoded text', () => {
  const f = join(dir, 'utf8.jsonl');
  writeFileSync(f, Buffer.concat([Buffer.from('first\n'), Buffer.from([0xe2])]));
  const t = new RawTail();
  t.update(f);
  assert.deepEqual(t.lines, ['first']);
  // 2 bytes appended: one byte held plus 2 is 3, the decoded length of the held byte (U+FFFD) is 3 too. Counting the
  // decoded text made this read look like "nothing new".
  appendFileSync(f, 'A\n');
  t.update(f);
  assert.equal(t.lines.length, 2);
  assert.ok(t.lines[1].endsWith('A'), t.lines[1]);
  appendFileSync(f, Buffer.from([0xe2, 0x82, 0xac, 0x0a]));    // "€" whole, then the newline
  t.update(f);
  assert.equal(t.lines[2], '€');
  // The same held byte, read by an append this time (not the first read)
  appendFileSync(f, Buffer.from([0xe2]));
  t.update(f);
  appendFileSync(f, 'B\n');
  t.update(f);
  assert.equal(t.lines.length, 4);
  assert.ok(t.lines[3].endsWith('B'), t.lines[3]);
});

// Gate round 1 on #706 (fenced reviewer): a failed read, and the first read's cost.
test('a raw read that fails while the parse succeeds is not known (no step), never an empty tail that looks known', () => {
  const f = join(dir, 'read-fails.jsonl');
  writeFileSync(f, [asked('go'), said('All done.')].join('\n') + '\n');
  const real = RawTail.prototype.update;
  let fails = 1;
  RawTail.prototype.update = function (this: RawTail, p: string) {
    if (fails-- > 0) throw Object.assign(new Error('EMFILE'), { code: 'EMFILE' });
    return real.call(this, p);
  };
  try {
    const t = new TranscriptTailer({ agentId: 'seat', resolve: () => ({ path: f, sid: 'e' }) });
    const off = t.subscribe(() => {});
    assert.ok(t.recentItems().length > 0, 'the parse read the log');
    assert.equal(t.turn().known, false);
    appendFileSync(f, asked('next') + '\n');                    // the log grows: read again, fresh
    t.tick();
    assert.equal(t.turn().known, true);
    off();
  } finally {
    RawTail.prototype.update = real;
  }
});

test('the first read counts each byte\'s newlines once, not the growing buffer again per block', () => {
  const f = join(dir, 'wide.jsonl');
  const lines = Array.from({ length: 300 }, (_, i) => said(`wide ${i} ${'w'.repeat(18 * 1024)}`));
  writeFileSync(f, lines.join('\n') + '\n');                     // the last 200 lines are about 3.6 MiB
  const r0 = rawTailBytesRead(), s0 = rawTailBytesScanned();
  const t = new RawTail();
  t.update(f);
  const read = rawTailBytesRead() - r0, scanned = rawTailBytesScanned() - s0;
  assert.equal(t.lines.length, RAW_TAIL_LINES);
  assert.ok(read > 3 * 1024 * 1024, `read ${read}`);
  assert.ok(scanned <= read, `scanned ${scanned} of ${read} read`);
});
