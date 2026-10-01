/**
 * 2D Agents View step 7 — travelling dots + ticker logic.
 *   node --experimental-strip-types dashboard/src/lib/fleetTraffic.test.mjs
 *
 * The assertion that matters most is the first-poll one. Without it, opening the page fires
 * a dot for every message in the backlog — motion that means nothing, which is the one thing
 * spec §2 rules out by name.
 */
import assert from 'node:assert';
import { newlyArrived, dotColor, tickerLabel, DOT_COLOR_UNKNOWN } from './fleetTraffic.ts';
import { pairKey } from './topologyLines.ts';

const m = (id, from, to, type) => ({ id, from_agent: from, to_agent: to, type, created_at: null });

// First poll: nothing is "new", even though there is plenty there.
assert.deepEqual(newlyArrived(new Set(), [m('a', 'gm', 'build', 'task')]), []);

// Second poll: only the ids we had not seen.
const seen = new Set(['a', 'b']);
const got = newlyArrived(seen, [m('c', 'gm', 'build', 'task'), m('b', 'gm', 'plan', 'reply'), m('a', 'x', 'y', 'task')]);
assert.deepEqual(got.map((x) => x.id), ['c']);

// Order is preserved (the caller hands us newest-first and the ticker relies on that).
const got2 = newlyArrived(new Set(['z']), [m('n2', 'a', 'b', 'task'), m('n1', 'c', 'd', 'reply')]);
assert.deepEqual(got2.map((x) => x.id), ['n2', 'n1']);

// Nothing new is empty, not undefined.
assert.deepEqual(newlyArrived(new Set(['a']), [m('a', 'gm', 'build', 'task')]), []);

// Colours per spec §4, case-insensitive, and an unknown type gets neutral rather than
// borrowing a colour that means something else.
assert.equal(dotColor('task'), 'bg-yellow-400');
assert.equal(dotColor('TASK'), 'bg-yellow-400');
assert.equal(dotColor('task_request'), 'bg-orange-400');
assert.equal(dotColor('reply'), 'bg-sky-400');
assert.equal(dotColor('escalate'), DOT_COLOR_UNKNOWN);
assert.equal(dotColor(null), DOT_COLOR_UNKNOWN);
assert.equal(dotColor(undefined), DOT_COLOR_UNKNOWN);

// Ticker label shape, including the underscore-to-space pass and the missing-type fallback.
assert.equal(tickerLabel(m('1', 'gm', 'telegram', 'reply')), 'gm → telegram · reply');
assert.equal(tickerLabel(m('2', 'build', 'gm', 'task_request')), 'build → gm · task request');
assert.equal(tickerLabel(m('3', 'build', 'gm', null)), 'build → gm · message');

// A dot has to land on the line its count is drawn on, so there is exactly one key function
// and fleetTraffic does not define a second one. Guard that: if anyone adds a pairKey to
// fleetTraffic later, this fails and they have to justify two implementations.
const traffic = await import('./fleetTraffic.ts');
assert.equal(traffic.pairKey, undefined, 'fleetTraffic must not define its own pairKey');
assert.equal(pairKey('gm', 'build'), pairKey('build', 'gm'));
assert.equal(pairKey('gm', 'build'), 'build|gm');

console.log('fleetTraffic: all assertions passed');
