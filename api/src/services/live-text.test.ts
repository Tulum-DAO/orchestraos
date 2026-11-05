/**
 * Live text from a headless seat's supervisor log: text deltas build the words so far, the
 * finished line in the transcript takes them out (once), an ended or cut turn clears them, and
 * nothing but the main chain's text ever shows.
 *
 * Run: npx tsx --test src/services/live-text.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, appendFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { LiveText, LiveTextState, LogFollower, LAND_GRACE_MS } from './live-text.js';

const MID = 'msg_example_1';
const WALL = '2026-10-31T03:00:00.000Z';
const out = (ev: any, extra: any = {}) => ({ t: 0, wall: WALL, gen: 1, kind: 'out', ev: { parent_tool_use_id: null, ...ev }, ...extra });
const se = (event: any, mid = MID, parent: string | null = null) => out({ type: 'stream_event', event, api_message_id: mid, parent_tool_use_id: parent });
const start = (mid = MID) => se({ type: 'message_start', message: { id: mid, role: 'assistant', content: [] } }, mid);
const blockStart = (index: number, type = 'text', mid = MID, parent: string | null = null) =>
  se({ type: 'content_block_start', index, content_block: type === 'text' ? { type, text: '' } : { type } }, mid, parent);
const delta = (index: number, text: string, mid = MID, parent: string | null = null) =>
  se({ type: 'content_block_delta', index, delta: { type: 'text_delta', text } }, mid, parent);
const thinking = (index: number, text: string) => se({ type: 'content_block_delta', index, delta: { type: 'thinking_delta', thinking: text } });
const stop = (index: number, mid = MID) => se({ type: 'content_block_stop', index }, mid);
const finished = (uuid: string, text: string, mid = MID) =>
  out({ type: 'assistant', uuid, message: { id: mid, role: 'assistant', content: [{ type: 'text', text }] } });
const result = (wall = WALL) => out({ type: 'result', subtype: 'success' }, { wall });
const never = () => false;

test('text deltas of the main chain build the words so far; thinking and subagents never show', () => {
  const s = new LiveTextState();
  for (const r of [start(), blockStart(0, 'thinking'), thinking(0, 'private'), blockStart(1), delta(1, 'Hello'), delta(1, ' there.'),
    blockStart(0, 'text', 'msg_sub', 'toolu_1'), delta(0, 'subagent words', 'msg_sub', 'toolu_1')]) s.apply(r);
  s.prune(never);
  const v = s.view();
  assert.equal(v.text, 'Hello there.');
  assert.equal(v.message_id, MID);
  assert.deepEqual(v.blocks, [{ uuid: null, text: 'Hello there.' }]);
  assert.equal(v.done, false);
});

test('the finished line replaces the live words exactly once: gone when the transcript has its uuid', () => {
  const s = new LiveTextState();
  for (const r of [start(), blockStart(0), delta(0, 'First para.'), stop(0), finished('u-1', 'First para.'),
    blockStart(1), delta(1, 'Second')]) s.apply(r);
  s.prune(never);
  assert.deepEqual(s.view().blocks, [{ uuid: 'u-1', text: 'First para.' }, { uuid: null, text: 'Second' }]);
  assert.equal(s.view().text, 'First para.\n\nSecond');
  s.prune((u) => u === 'u-1');
  assert.deepEqual(s.view().blocks, [{ uuid: null, text: 'Second' }], 'the landed block left, the one still written stays');
  s.prune((u) => u === 'u-1');
  assert.equal(s.view().text, 'Second', 'and it never comes back');
});

test('a turn that ends waits for its last line, then clears (never longer than the grace)', () => {
  const s = new LiveTextState();
  const end = Date.parse(WALL);
  for (const r of [start(), blockStart(0), delta(0, 'All done.'), stop(0), finished('u-2', 'All done.'), result()]) s.apply(r);
  s.prune(never, end + 100);
  assert.equal(s.view().text, 'All done.', 'not landed yet: still shown, so there is no gap');
  assert.equal(s.view().done, true);
  s.prune(never, end + LAND_GRACE_MS + 1);
  assert.equal(s.view().text, '', 'a line that never lands does not stay forever');
});

test('an interrupted turn clears at once: a block cut mid-way never lands', () => {
  const s = new LiveTextState();
  for (const r of [start(), blockStart(0), delta(0, 'I was say')]) s.apply(r);
  s.prune(never);
  assert.equal(s.view().text, 'I was say');
  s.apply(out({ type: 'result', subtype: 'error_during_execution' }));
  s.prune(never);
  assert.equal(s.view().text, '');
  // the process exiting mid-block (a crash, a stop) does the same
  const t = new LiveTextState();
  for (const r of [start(), blockStart(0), delta(0, 'half')]) t.apply(r);
  t.apply({ kind: 'sup', what: 'exit', wall: WALL, code: 143 });
  t.prune(never);
  assert.equal(t.view().text, '');
});

test('the follower reads only new complete lines, starts mid-file at a line, and starts over on a shorter file', () => {
  const dir = mkdtempSync(join(tmpdir(), 'live-text-'));
  const p = join(dir, 'events.jsonl');
  const f = new LogFollower(p, 40);
  assert.deepEqual(f.read(), { lines: [], reset: false }, 'no log yet');
  writeFileSync(p, `${'x'.repeat(60)}\n{"a":1}\n{"b":`);
  assert.deepEqual(f.read().lines, ['{"a":1}'], 'the backlog starts at a line, a torn line waits');
  appendFileSync(p, '2}\n');
  assert.deepEqual(f.read().lines, ['{"b":2}']);
  writeFileSync(p, '{"c":3}\n');
  const again = f.read();
  assert.equal(again.reset, true);
  assert.deepEqual(again.lines, ['{"c":3}']);
});

test('LiveText reports a change once, and a seat that is not headless (or in Dev) shows nothing', () => {
  const dir = mkdtempSync(join(tmpdir(), 'live-text-'));
  const p = join(dir, 'events.jsonl');
  const lines = [start(), blockStart(0), delta(0, 'Working on it')].map((r) => JSON.stringify(r));
  writeFileSync(p, lines.join('\n') + '\n');
  const lt = new LiveText(p);
  assert.equal(lt.step(never)?.text, 'Working on it');
  assert.equal(lt.step(never), null, 'unchanged: nothing to send');
  appendFileSync(p, JSON.stringify(delta(0, ' now.')) + '\n');
  assert.equal(lt.step(never)?.text, 'Working on it now.');
  assert.equal(lt.step(never, false)?.text, '', 'switched off: cleared');
  const off = new LiveText(p);
  assert.equal(off.step(never, false), null, 'never on: never a frame');
});
