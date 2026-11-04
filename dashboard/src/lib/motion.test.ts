import { test } from 'node:test';
import assert from 'node:assert/strict';
import { reducedFor } from './motion.ts';

test('Reduced always reduces, Full never does, Follow system does what the OS says', () => {
  assert.deepEqual([reducedFor('reduced', false), reducedFor('reduced', true)], [true, true]);
  assert.deepEqual([reducedFor('full', false), reducedFor('full', true)], [false, false]);
  assert.deepEqual([reducedFor('system', false), reducedFor('system', true)], [false, true]);
});
