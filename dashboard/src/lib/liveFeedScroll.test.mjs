/**
 * Pure logic tests for dashboard/src/lib/liveFeedScroll.ts.
 * Run: node --experimental-strip-types src/lib/liveFeedScroll.test.mjs
 */
import assert from 'node:assert';
import { isNearBottom } from './liveFeedScroll.ts';

// scrollHeight=1000, clientHeight=400 throughout — distance from bottom = 1000 - scrollTop - 400.
assert.strictEqual(isNearBottom(600, 1000, 400), true, 'scrolled exactly to the bottom is near-bottom');
assert.strictEqual(isNearBottom(580, 1000, 400), true, 'inside the default 48px threshold counts as near-bottom');
assert.strictEqual(isNearBottom(500, 1000, 400), false, 'scrolled well above the tail is not near-bottom');
assert.strictEqual(isNearBottom(0, 1000, 400), false, 'scrolled to the very top is not near-bottom');
assert.strictEqual(isNearBottom(590, 1000, 400, 20), true, 'custom threshold: within it is near-bottom');
assert.strictEqual(isNearBottom(590, 1000, 400, 5), false, 'custom threshold: outside a tighter one is not near-bottom');

// THE BOUNDARY (added during integration — build). Distance EXACTLY equal to the threshold
// must count as near-bottom. Without this, none of the six assertions above distinguishes
// `<= threshold` from `< threshold`: the cases sit at distance 0, 20 and 100, so an off-by-one
// on the comparison survives every one of them. Verified by mutation both ways.
assert.strictEqual(isNearBottom(552, 1000, 400), true, 'distance exactly at the 48px threshold is near-bottom');
assert.strictEqual(isNearBottom(551, 1000, 400), false, 'one px past the threshold is not');
assert.strictEqual(isNearBottom(580, 1000, 400, 20), true, 'exactly at a custom threshold is near-bottom');

console.log('PASS: liveFeedScroll.ts (9 assertions)');
