import { test } from 'node:test';
import assert from 'node:assert/strict';
import { threadFromJson } from './arturoThreads';

test("a loaded thread keeps its last_brain, so reopening it restores the brain", () => {
  const t = threadFromJson({ id: 'c1', title: 'q', turns: [{ role: 'user', content: 'hi' }],
                             last_brain: { provider: 'codex', model: 'gpt-5.6-terra' } });
  assert.deepEqual(t?.last_brain, { provider: 'codex', model: 'gpt-5.6-terra' });
  assert.equal(t?.turns.length, 1);
});

test('a thread from before the migration (no last_brain) reads as the default brain', () => {
  assert.equal(threadFromJson({ id: 'old', title: '', turns: [] })?.last_brain, null);
});

test('no thread is null', () => {
  assert.equal(threadFromJson(null), null);
});
