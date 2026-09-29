import assert from 'node:assert';
import { test } from 'node:test';
import { matchesTypeFilter, truncateBody } from './conversationPanel.ts';

test('matchesTypeFilter', () => {
  assert.strictEqual(matchesTypeFilter('task', 'All'), true);
  assert.strictEqual(matchesTypeFilter('reply', 'All'), true);
  assert.strictEqual(matchesTypeFilter('task', 'Task'), true);
  assert.strictEqual(matchesTypeFilter('reply', 'Task'), false);
  assert.strictEqual(matchesTypeFilter('reply', 'Reply'), true);
  assert.strictEqual(matchesTypeFilter('task_request', 'Task request'), true);
  assert.strictEqual(matchesTypeFilter('task_request', 'Task'), false);
  assert.strictEqual(matchesTypeFilter('progress', 'Task'), false);
  assert.strictEqual(matchesTypeFilter(undefined, 'Reply'), false);
});

test('truncateBody keeps short bodies intact', () => {
  const short = 'line1\nline2\nline3';
  assert.deepStrictEqual(truncateBody(short), { preview: short, truncated: false });
});

test('truncateBody cuts at 8 lines and flags truncation', () => {
  const long = Array.from({ length: 12 }, (_, i) => `line${i}`).join('\n');
  const result = truncateBody(long);
  assert.strictEqual(result.truncated, true);
  assert.strictEqual(result.preview.split('\n').length, 8);
  assert.strictEqual(result.preview.split('\n')[7], 'line7');
});

test('truncateBody respects a custom maxLines', () => {
  const long = Array.from({ length: 5 }, (_, i) => `line${i}`).join('\n');
  const result = truncateBody(long, 3);
  assert.strictEqual(result.truncated, true);
  assert.strictEqual(result.preview, 'line0\nline1\nline2');
});
