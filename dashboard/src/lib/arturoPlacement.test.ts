import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buttonBottom } from './arturoPlacement.ts';

const box = (top: number, bottom: number, left: number, right: number) => ({ top, bottom, left, right, height: bottom - top });

test('the button sits in the corner, or right above a bar docked under it', () => {
  assert.equal(buttonBottom(400, 800, []), 12);
  assert.equal(buttonBottom(400, 800, [box(730, 800, 0, 400)]), 78);             // full-width composer: above it
  assert.equal(buttonBottom(1400, 900, [box(820, 900, 300, 900)]), 12);          // a bar that ends left of the corner
  assert.equal(buttonBottom(400, 800, [box(730, 800, 0, 400), box(700, 800, 200, 400)]), 108);   // the taller one
  assert.equal(buttonBottom(400, 800, [{ top: 0, bottom: 0, left: 0, right: 0, height: 0 }]), 12); // hidden
});
