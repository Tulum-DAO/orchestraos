import { test } from 'node:test';
import assert from 'node:assert/strict';
import { FOLLOW_TOLERANCE_PX, anchorShift, isFollowing, pickAnchor, type RowBox } from './threadKit/anchor.ts';

test('only a reader at the bottom follows; a reader up even a line is left where they are', () => {
  assert.equal(isFollowing({ scrollHeight: 2000, scrollTop: 1000, clientHeight: 1000 }), true);
  assert.equal(isFollowing({ scrollHeight: 2000, scrollTop: 999.4, clientHeight: 1000 }), true);    // sub-pixel rounding
  assert.equal(isFollowing({ scrollHeight: 2000, scrollTop: 1000 - FOLLOW_TOLERANCE_PX, clientHeight: 1000 }), true);
  assert.equal(isFollowing({ scrollHeight: 2000, scrollTop: 981, clientHeight: 1000 }), false);     // one line (19px) up
  assert.equal(isFollowing({ scrollHeight: 2000, scrollTop: 940, clientHeight: 1000 }), false);     // 60px up
  assert.equal(isFollowing({ scrollHeight: 2000, scrollTop: 0, clientHeight: 1000 }), false);
});

// Rows as the scroller lays them out, in viewport coordinates; the scroller's top edge is at y=100.
const layout = (heights: number[], scrollTop: number, top = 100): RowBox[] => {
  let y = top - scrollTop;
  return heights.map((hgt) => { const r = { top: y, bottom: y + hgt }; y += hgt; return r; });
};

test('the anchor is the first row still showing at the top of the scroller', () => {
  const rows = layout([50, 50, 50, 50], 70);            // row 0 is scrolled off; row 1 shows from y=80 to 130
  assert.equal(pickAnchor(rows, 100), 1);
  assert.equal(pickAnchor(layout([50, 50], 0), 100), 0);
  assert.equal(pickAnchor(layout([50, 50], 100), 100), -1);   // scrolled past everything
  assert.equal(pickAnchor([], 100), -1);
});

/** One change, then the shift the anchor asks for: the scroller's new scrollTop holds the anchor in place. */
function hold(before: number[], after: number[], scrollTop: number, anchorIndexAfter: (i: number) => number) {
  const view = 100;
  const rowsBefore = layout(before, scrollTop);
  const i = pickAnchor(rowsBefore, view);
  const saved = rowsBefore[i].top - view;
  const rowsAfter = layout(after, scrollTop);                 // the change lands; scrollTop has not moved
  const j = anchorIndexAfter(i);
  const shift = anchorShift(saved, rowsAfter[j].top - view);
  const held = layout(after, scrollTop + shift);
  return { shift, drift: held[j].top - rowsBefore[i].top };
}

test('rows added below the reader move nothing', () => {
  const r = hold([40, 40, 40, 40, 40], [40, 40, 40, 40, 40, 19, 19, 60], 70, (i) => i);
  assert.deepEqual([r.shift, r.drift], [0, 0]);
});

test('a row above the reader that grows (a reply still streaming, a card settling) is held to the pixel', () => {
  const r = hold([40, 40, 40, 40, 40], [59, 40, 40, 40, 40], 50, (i) => i);
  assert.deepEqual([r.shift, r.drift], [19, 0]);
});

test('rows added above the reader (paging back through history) hold the reader where they are', () => {
  const r = hold([40, 40, 40, 40], [30, 30, 30, 40, 40, 40, 40], 20, (i) => i + 3);
  assert.deepEqual([r.shift, r.drift], [90, 0]);
});

test('less than half a pixel is rounding, never a move', () => {
  assert.equal(anchorShift(12, 12.4), 0);
  assert.equal(anchorShift(12, 31), 19);
  assert.equal(anchorShift(12, -8), -20);
});
