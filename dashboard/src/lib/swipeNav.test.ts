import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  SWIPE, dragOffset, swipeTouch, releaseVelocity, swipeAxis, swipeIntent, swipeStartOk, swipeStep, wheelDelta, wheelStart, wheelStep,
  type WheelState,
} from './swipeNav.ts';

const start = { x: 200, viewportWidth: 400, pointerType: 'touch', primary: true, inField: false, inHScroll: false };

test('a touch or pen gesture starts anywhere on the card but a text field, sideways-scrolling content or a screen edge', () => {
  assert.equal(swipeStartOk(start), true);
  assert.equal(swipeStartOk({ ...start, pointerType: 'pen' }), true);
  assert.equal(swipeStartOk({ ...start, pointerType: 'mouse' }), false);       // a mouse clicks and selects; no drag
  assert.equal(swipeStartOk({ ...start, primary: false }), false);             // a second finger (pinch) is not a swipe
  assert.equal(swipeStartOk({ ...start, inField: true }), false);              // the write-in box keeps its own touches
  assert.equal(swipeStartOk({ ...start, inHScroll: true }), false);            // a wide code block scrolls sideways
  assert.equal(swipeStartOk({ ...start, x: SWIPE.edge - 1 }), false);          // iOS Safari's back-swipe from the left edge
  assert.equal(swipeStartOk({ ...start, x: 400 - SWIPE.edge + 1 }), false);    // and its forward-swipe from the right
  assert.equal(swipeStartOk({ ...start, x: SWIPE.edge + 1 }), true);
});

test('the axis locks only once the finger has clearly moved, and sideways only when it is 1.5x the vertical', () => {
  assert.equal(swipeAxis(4, 2), null);                // still inside the slop: undecided
  assert.equal(swipeAxis(-30, 5), 'h');
  assert.equal(swipeAxis(30, 19), 'h');
  assert.equal(swipeAxis(30, 21), 'v');               // diagonal: the page scrolls, the card does not move
  assert.equal(swipeAxis(3, 40), 'v');
});

test('a swipe needs ~48px, or a fast flick, and must stay mostly sideways', () => {
  assert.equal(swipeIntent({ dx: -SWIPE.minDx, dy: 0, vx: 0 }), 'next');     // finger to the left: Next
  assert.equal(swipeIntent({ dx: SWIPE.minDx, dy: 10, vx: 0 }), 'back');     // to the right: Back
  assert.equal(swipeIntent({ dx: -(SWIPE.minDx - 1), dy: 0, vx: 0 }), null); // short and slow: nothing
  assert.equal(swipeIntent({ dx: -30, dy: 0, vx: -0.8 }), 'next');           // a short fast flick counts
  assert.equal(swipeIntent({ dx: -10, dy: 0, vx: -2 }), null);               // a twitch is never a flick
  assert.equal(swipeIntent({ dx: 30, dy: 0, vx: 0.8 }), 'back');
  assert.equal(swipeIntent({ dx: -30, dy: 0, vx: 0.8 }), null);              // flicked back against the drag: nothing
});

test('a vertical or diagonal gesture is ignored, however long', () => {
  assert.equal(swipeIntent({ dx: -100, dy: 80, vx: 0 }), null);
  assert.equal(swipeIntent({ dx: -60, dy: 41, vx: -1 }), null);
  assert.equal(swipeIntent({ dx: 5, dy: 300, vx: 0 }), null);
});

test('swipe does only what a button does: no Submit from the last question, no Back from the first', () => {
  assert.equal(swipeStep('next', 0, 3), 'next');
  assert.equal(swipeStep('next', 1, 3), 'next');
  assert.equal(swipeStep('next', 2, 3), null);        // last: Submit is always an explicit tap
  assert.equal(swipeStep('back', 2, 3), 'back');
  assert.equal(swipeStep('back', 0, 3), null);        // first: no Back button
  assert.equal(swipeStep('next', 0, 1), null);        // a card of one part never pages
  assert.equal(swipeStep('back', 0, 1), null);
  assert.equal(swipeStep(null, 1, 3), null);
});

test('the release velocity is read from the last moments of the drag', () => {
  assert.equal(releaseVelocity([]), 0);
  assert.equal(releaseVelocity([{ x: 0, y: 0, t: 0 }]), 0);
  // Slow at first, then fast: the recent window decides.
  const v = releaseVelocity([{ x: 0, y: 0, t: 0 }, { x: -5, y: 0, t: 300 }, { x: -45, y: 0, t: 350 }]);
  assert.ok(v < -0.5, `fast at the end: ${v}`);
  // A pause before lifting the finger is no flick.
  assert.equal(releaseVelocity([{ x: 0, y: 0, t: 0 }, { x: -60, y: 0, t: 50 }, { x: -60, y: 0, t: 400 }]), 0);
});

test('the part follows the finger a little; past an end it only gives a little, then bounces back', () => {
  const free = dragOffset(-100, 0, 3, 300);
  assert.ok(free < 0 && free > -100, `follows, less than the finger: ${free}`);
  assert.ok(Math.abs(dragOffset(-1000, 0, 3, 300)) <= 300 * SWIPE.followMax);
  const resisted = dragOffset(-100, 2, 3, 300);      // last part, toward Submit
  assert.ok(resisted < 0 && Math.abs(resisted) < Math.abs(free) / 2, `resists: ${resisted}`);
  assert.ok(Math.abs(dragOffset(-1000, 2, 3, 300)) <= SWIPE.resistMax);
  assert.ok(Math.abs(dragOffset(100, 0, 3, 300)) <= SWIPE.resistMax);   // first part, toward Back
  assert.equal(dragOffset(-100, 0, 1, 300), 0);      // a single part never moves
});

function wheel(events: [number, number, number][], s: WheelState = wheelStart) {
  const fired: string[] = [];
  for (const [dx, dy, t] of events) {
    const r = wheelStep(s, { dx, dy, t });
    s = r.state;
    if (r.dir) fired.push(`${r.dir}@${t}`);
  }
  return { fired, s };
}

test('a trackpad swipe accumulates sideways wheel deltas past a threshold: left is Next, right is Back', () => {
  // Two fingers moving left scroll content right: deltaX > 0.
  assert.deepEqual(wheel([[10, 0, 0], [15, 1, 16], [20, 0, 32], [20, 0, 48]]).fired, ['next@48']);
  assert.deepEqual(wheel([[-30, 0, 0], [-30, 0, 16]]).fired, ['back@16']);
  assert.deepEqual(wheel([[10, 0, 0], [10, 0, 16]]).fired, []);          // too little
});

test('vertical scrolling never pages, and resets what a sideways drift had built up', () => {
  assert.deepEqual(wheel([[0, 40, 0], [5, 60, 16], [10, 30, 32], [3, 80, 48]]).fired, []);
  assert.deepEqual(wheel([[30, 0, 0], [5, 50, 16], [30, 0, 32]]).fired, []);
});

test('one swipe is one question: the momentum tail is absorbed, and a fresh gesture after a pause pages again', () => {
  const ev: [number, number, number][] = [];
  for (let t = 0; t <= 1200; t += 16) ev.push([Math.max(1, 40 - t / 30), 0, t]);   // one swipe plus its inertia
  const one = wheel(ev);
  assert.equal(one.fired.length, 1);
  const later = 1200 + SWIPE.wheelGap + 10;
  const two = wheel([[40, 0, later], [40, 0, later + 16]], one.s);
  assert.equal(two.fired.length, 1);
  // Even after a gap, not inside the cooldown of the last step.
  const quick = wheel([[60, 0, 0], [60, 0, SWIPE.wheelGap + 1]]);
  assert.equal(quick.fired.length, 1);
});

test('a gesture that pauses starts over', () => {
  assert.deepEqual(wheel([[30, 0, 0], [30, 0, SWIPE.wheelGap + 5]]).fired, []);
});

test('wheel deltas in lines or pages are read as pixels', () => {
  assert.equal(wheelDelta(10, 0, 800), 10);
  assert.equal(wheelDelta(3, 1, 800), 48);
  assert.equal(wheelDelta(1, 2, 800), 800);
});

test('with 2+ parts the area and EVERY part carry pan-y (each part is its own scroll container); one part, none', () => {
  assert.deepEqual(swipeTouch(2), { touchAction: 'pan-y pinch-zoom' });
  assert.deepEqual(swipeTouch(5), { touchAction: 'pan-y pinch-zoom' });
  assert.equal(swipeTouch(1), undefined);
  assert.equal(swipeTouch(0), undefined);
  const src = readFileSync(new URL('../components/chat/MultiPartCard.tsx', import.meta.url), 'utf8');
  assert.match(src, /const touch = swipeTouch\(parts\.length\)/);
  assert.match(src, /data-parts style=\{touch\}/);
  const part = src.slice(src.indexOf('data-part={i}'), src.indexOf('{render(p, i)}'));
  assert.ok(part.length > 0, 'the part div');
  assert.match(part, /overflow-y-auto/);                 // the reason: each part scrolls on its own
  assert.match(part, /style=\{\{ \.\.\.touch,/, 'every part div spreads the touch-action');
});

test('every question card pages by swipe, and the swipe area keeps vertical scroll for the page', () => {
  const src = readFileSync(new URL('../components/chat/MultiPartCard.tsx', import.meta.url), 'utf8');
  assert.equal((src.match(/useSwipeNav\(/g) || []).length, 2, 'the open card and the answered card');
  assert.match(src, /pan-y/);
  assert.match(src, /aria-live="polite"/);
});
