import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cliShift } from './terminalAnchor.ts';

test('no shift when the whole terminal fits', () => {
  assert.equal(cliShift(800, 15, 40, 50), 0);
});
test('keyboard open: the cursor row lands right at the bottom of the visible box', () => {
  // 50 rows x 15px = 750px terminal, box shrank to 400px, cursor on row 45
  assert.equal(cliShift(400, 15, 45, 50), 46 * 15 - 400);
});
test('cursor already visible above the fold: no shift', () => {
  assert.equal(cliShift(400, 15, 10, 50), 0);
});
test('never shifts past the end of the terminal', () => {
  assert.equal(cliShift(400, 15, 49, 50), 750 - 400);
  assert.equal(cliShift(400, 15, 99, 50), 750 - 400);
});
test('bad measurements never move anything', () => {
  assert.equal(cliShift(0, 15, 45, 50), 0);
  assert.equal(cliShift(400, 0, 45, 50), 0);
  assert.equal(cliShift(400, 15, 45, 0), 0);
});
