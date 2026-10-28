/**
 * The status stream's step lines: changes only, a new page gets just the current ones, and a missing or
 * half-written file is skipped. Pure over the file contents (nothing watched, nothing written).
 *
 * Run: npx tsx --test src/services/status-stream.steps.test.ts   (from api/)
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { stepDeltas, freshSteps, readSteps, STEP_FRESH_S } from './status-stream.js';

test('only a changed line goes out, per seat', () => {
  const last = new Map<string, string>();
  assert.deepEqual(stepDeltas({ arturo: { text: 'Reading a file', ts: 10 } }, last), [{ seat: 'arturo', text: 'Reading a file', ts: 10 }]);
  assert.deepEqual(stepDeltas({ arturo: { text: 'Reading a file', ts: 11 } }, last), []);
  assert.deepEqual(stepDeltas({ arturo: { text: 'Messaging gm', ts: 12 }, gm: { text: 'x', ts: 12 } }, last).map((d) => d.seat), ['arturo', 'gm']);
  assert.deepEqual(stepDeltas({ arturo: { text: '', ts: 13 } }, last), [{ seat: 'arturo', text: '', ts: 13 }]);   // the turn ended
});

test('a new page is handed only current lines with words', () => {
  const now = 1000;
  const got = freshSteps({ a: { text: 'now', ts: now - 5 }, b: { text: 'old', ts: now - STEP_FRESH_S - 1 }, c: { text: '', ts: now } }, now);
  assert.deepEqual(got, [{ seat: 'a', text: 'now', ts: now - 5 }]);
});

test('a long line is cut, and a missing or half-written file is nothing', () => {
  const last = new Map<string, string>();
  assert.equal(stepDeltas({ a: { text: 'x'.repeat(500), ts: 1 } }, last)[0].text.length, 160);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'steps-'));
  assert.deepEqual(readSteps(path.join(dir, 'none.json')), {});
  fs.writeFileSync(path.join(dir, 'half.json'), '{"seats": {"a": ');
  assert.deepEqual(readSteps(path.join(dir, 'half.json')), {});
  fs.writeFileSync(path.join(dir, 'ok.json'), JSON.stringify({ seats: { a: { text: 'hi', ts: 2 } } }));
  assert.deepEqual(readSteps(path.join(dir, 'ok.json')), { a: { text: 'hi', ts: 2 } });
});
