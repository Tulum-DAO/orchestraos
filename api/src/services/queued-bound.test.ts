/**
 * Bounding the historical batch rows in a transcript window (quest-orchestra, 2026-10-07).
 *
 * Imports the REAL functions. A test that re-implements the bound it covers passes with the
 * bound deleted — that class left a reports_to gate green earlier today.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { boundQueuedBatches, earliestTurnMs, QUEUED_BATCH_MAX } from './queued-bound.js';

const batch = (ts: string) => ({ kind: 'queued_batch', ts, entries: [], count: 0 });
const turn = (ts: string) => ({ kind: 'text', role: 'assistant', ts });

test('THE BUG: history older than the oldest rendered turn is dropped', () => {
  // The measured shape, with synthetic clocks: batches from weeks back against today's turns.
  // Timestamps here are deliberately NOT the real ones — a millisecond-precision clock
  // correlates to one private transcript, which the operator-identifier gate catches.
  const batches = [batch('2025-11-03T14:35:04.343218+00:00'), batch('2026-01-02T03:09:09.468483+00:00')];  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  const floor = earliestTurnMs([turn('2026-01-02T03:04:05.678Z')]);  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  const out = boundQueuedBatches(batches, floor);
  assert.equal(out.length, 1);
  assert.match(out[0].ts, /^2026-01-02/);
});

test('THE TRAP: microseconds+offset vs milliseconds+Z is parsed, not string-compared', () => {
  // The two sources really do spell the same instant differently: msg_store writes SIX
  // fractional digits and a '+00:00' offset, the JSONL writes THREE and a 'Z'. So the strings
  // diverge at the 4th fractional digit, where a DIGIT meets 'Z' — and every digit sorts
  // BELOW 'Z'. A raw `<` therefore drops a batch that is strictly NEWER than the floor.
  const newer = '2026-01-02T03:04:05.678999+00:00';   // 216 microseconds AFTER the floor  operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  const floorStr = '2026-01-02T03:04:05.678Z';  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  assert.ok(newer < floorStr, 'precondition: naive string compare really does invert here');
  assert.ok(Date.parse(newer) >= Date.parse(floorStr), 'and it is genuinely the newer instant');
  const out = boundQueuedBatches([batch(newer)], earliestTurnMs([turn(floorStr)]));
  assert.equal(out.length, 1, 'a batch newer than the floor must survive');
});

test('and the equal-instant spelling survives too', () => {
  // Same instant, two spellings: '+' (0x2B) sorts below 'Z' (0x5A), so a raw `<` drops it.
  const out = boundQueuedBatches(
    [batch('2026-01-02T03:04:05.678+00:00')], earliestTurnMs([turn('2026-01-02T03:04:05.678Z')]));  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  assert.equal(out.length, 1);
});

test('a batch exactly AT the floor is kept', () => {
  const ts = '2026-01-02T03:04:05.678Z';  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  assert.equal(boundQueuedBatches([batch(ts)], earliestTurnMs([turn(ts)])).length, 1);
});

test('an UNPARSEABLE ts is kept, never hidden', () => {
  // A display bound must not delete a message because its clock looked odd.
  const out = boundQueuedBatches([batch('not-a-date')], earliestTurnMs([turn('2026-01-02T03:04:05.678Z')]));  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  assert.equal(out.length, 1);
});

test('with NO turn to anchor to, it caps by count instead of keeping everything', () => {
  const many = Array.from({ length: QUEUED_BATCH_MAX + 40 }, (_, i) =>
    batch(`2026-09-${String((i % 28) + 1).padStart(2, '0')}T00:00:00.000Z`));
  const out = boundQueuedBatches(many, null);
  assert.equal(out.length, QUEUED_BATCH_MAX);
});

test('the count cap keeps the NEWEST, not whichever came first', () => {
  const out = boundQueuedBatches(
    [batch('2025-01-01T00:00:00.000Z'), batch('2026-01-02T00:00:00.000Z')], null, 1);  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  assert.match(out[0].ts, /^2026-01-02/);
});

test('earliestTurnMs ignores synthetics, which would anchor the window to themselves', () => {
  const ms = earliestTurnMs([
    batch('2025-11-03T00:00:00.000Z'),  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
    { kind: 'text', role: 'user', ts: '2025-11-04T00:00:00.000Z', queued: true },  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
    turn('2026-01-02T03:04:05.678Z'),  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  ]);
  assert.equal(ms, Date.parse('2026-01-02T03:04:05.678Z'),  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
    'a queued_batch or a queued turn must never become the floor');
});

test('earliestTurnMs is null when the window carries no real turn', () => {
  assert.equal(earliestTurnMs([batch('2025-11-03T00:00:00.000Z')]), null);  // operator-id-ok: synthetic clock (01-02T03:04:05), shape required to pin the ms-vs-us compare
  assert.equal(earliestTurnMs([]), null);
});
