/**
 * 2D Agents View step 9 — time-bar maths.
 *   node --experimental-strip-types dashboard/src/lib/timeBar.test.mjs
 *
 * The assertions that matter are the clamps and the live sentinel. An unclamped value puts
 * the slider outside its own range (reads as a broken control), and a "0 minutes ago" asof
 * instead of null would freeze polling on a moment that is instantly stale.
 */
import assert from 'node:assert';
import { minutesAgo, asofFromSlider, WINDOW_OPTIONS } from './timeBar.ts';

const NOW = Date.parse('2026-09-30T12:00:00Z');
const SPAN = 72 * 60;

// Live is the now-end of the track.
assert.equal(minutesAgo(null, NOW, SPAN), 0);
assert.equal(minutesAgo('nonsense', NOW, SPAN), 0);

assert.equal(minutesAgo('2026-09-30T11:30:00Z', NOW, SPAN), 30);
assert.equal(minutesAgo('2026-09-29T12:00:00Z', NOW, SPAN), 24 * 60);

// Clamped at the far end: a browser left open for a week must not overrun the track.
assert.equal(minutesAgo('2026-09-01T12:00:00Z', NOW, SPAN), SPAN);
// Clamped at the near end: a future asof (clock skew between machines is normal here)
// must not go negative.
assert.equal(minutesAgo('2026-09-30T13:00:00Z', NOW, SPAN), 0);

// Slider -> asof. Right-hand end is LIVE (null), not a frozen "now".
assert.equal(asofFromSlider(SPAN, NOW, SPAN), null);
assert.equal(asofFromSlider(SPAN + 5, NOW, SPAN), null, 'overshoot still means live');
assert.equal(asofFromSlider(SPAN - 60, NOW, SPAN), new Date(NOW - 60 * 60_000).toISOString());
assert.equal(asofFromSlider(0, NOW, SPAN), new Date(NOW - SPAN * 60_000).toISOString());

// Round trip: a position turns into an asof that maps back to the same position.
for (const v of [0, 100, SPAN - 5, SPAN]) {
  const iso = asofFromSlider(v, NOW, SPAN);
  assert.equal(SPAN - minutesAgo(iso, NOW, SPAN), v === SPAN ? SPAN : v, `round trip failed at ${v}`);
}

assert.deepEqual([...WINDOW_OPTIONS], [1, 6, 24, 72]);

console.log('timeBar: all assertions passed');
