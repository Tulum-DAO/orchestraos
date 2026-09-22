import assert from 'node:assert';
import { test } from 'node:test';
import { genLabel } from './genLabel.ts';
test('genLabel matches the iOS chip rule: "gen N" only when a seat has been reincarnated', () => {
  assert.strictEqual(genLabel(4), 'gen 4');
  assert.strictEqual(genLabel('3'), 'gen 3');
  assert.strictEqual(genLabel(1), null);
  assert.strictEqual(genLabel(undefined), null);
  assert.strictEqual(genLabel(null), null);
  assert.strictEqual(genLabel('x'), null);
});
