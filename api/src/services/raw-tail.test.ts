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
import { RAW_TAIL_LINES, RawTail, rawTailBytesRead } from './raw-tail.js';
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
  assert.equal(rawTailBytesRead() - b2, Buffer.byteLength(more.join('\n') + '\n'));
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
