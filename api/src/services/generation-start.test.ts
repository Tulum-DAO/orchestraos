import { test } from 'node:test';
import assert from 'node:assert/strict';
import { currentGenerationStart, beforeGeneration, firstTimestamp } from './generation-start.js';

const deps = (promoted: string | null, first: string | null, path: string | null = '/t/sess.jsonl') => ({
  promotedAt: () => promoted, transcriptPath: () => path, firstTs: () => first,
});

test('a rotation: the later of promoted_at and the new session\'s first line', () => {
  assert.equal(currentGenerationStart('pm-demo', deps('2026-10-24T20:00:00Z', '2026-10-24T20:03:43Z')), '2026-10-24T20:03:43.000Z');
  assert.equal(currentGenerationStart('pm-demo', deps('2026-10-25T09:00:00Z', '2026-10-24T20:03:43Z')), '2026-10-25T09:00:00.000Z');
});
test('a seat never rotated: its session\'s first line alone', () => {
  assert.equal(currentGenerationStart('pm-demo', deps(null, '2026-10-23T14:22:04.000Z')), '2026-10-23T14:22:04.000Z');
});
test('nothing known: null, and then no card counts as earlier', () => {
  assert.equal(currentGenerationStart('pm-demo', deps(null, null, null)), null);
  assert.equal(beforeGeneration('2020-01-01T00:00:00Z', null), false);
});
test('a card filed before the session began is earlier; one after is not', () => {
  const start = '2026-10-24T20:03:43.000Z';
  assert.equal(beforeGeneration('2026-10-24T17:11:49.370265+00:00', start), true);
  assert.equal(beforeGeneration('2026-10-24T20:12:21+00:00', start), false);
});
test('firstTimestamp skips metadata lines and a partial line', () => {
  const body = '{"type":"summary"}\n{"type":"file-history-snapshot"}\nnot json\n{"type":"user","timestamp":"2026-10-24T20:03:43.000Z"}\n{"timestamp":"2026-10-24T21:00:00Z"}\n';
  assert.equal(firstTimestamp('/x/a.jsonl', () => body), '2026-10-24T20:03:43.000Z');
  assert.equal(firstTimestamp('/x/b.jsonl', () => '{"type":"summary"}\n'), null);
});
