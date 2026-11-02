// Where the reaction UI goes (components/kit/Reactions.tsx): hover icons outside the bubble when there is room, popovers
// on screen, the phone hold's quick row above and menu below the lifted message.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  badgePlace, badgeReserve, barOffsetTop, barOutside, liftLayout, popoverAt,
  BADGE_HEIGHT, BADGE_OVERHANG, BAR_HEIGHT, BAR_WIDTH, CLEAR, EDGE, GAP, type Box,
} from './reactionLayout.ts';

const clip: Box = { left: 0, top: 0, width: 860, height: 800 };

test('hover icons go outside the bubble when the scroller has room on its outer side', () => {
  // a short agent reply on the left: room on its right
  assert.equal(barOutside('agent', { left: 16, top: 100, width: 300, height: 40 }, clip), true);
  // a full-width reply: no room on its right, so under it
  assert.equal(barOutside('agent', { left: 16, top: 100, width: 828, height: 400 }, clip), false);
  // the person's bubble on the right (85% at most): room on its left
  assert.equal(barOutside('user', { left: 860 - 16 - 400, top: 100, width: 400, height: 40 }, clip), true);
  assert.equal(barOutside('user', { left: 60, top: 100, width: 784, height: 40 }, clip), false);
  assert.equal(barOutside('user', { left: BAR_WIDTH + GAP, top: 100, width: 400, height: 40 }, clip), true);
});

test('a popover opens below what opened it, else above, never off screen', () => {
  const view = { width: 390, height: 844 };
  const size = { width: 300, height: 48 };
  assert.deepEqual(popoverAt({ left: 40, top: 100, width: 96, height: 28 }, size, view), { left: 40, top: 100 + 28 + GAP });
  // no room below: above
  assert.deepEqual(popoverAt({ left: 40, top: 800, width: 96, height: 28 }, size, view), { left: 40, top: 800 - GAP - 48 });
  // right-aligned to a right-side opener, then clamped to the left edge
  assert.equal(popoverAt({ left: 250, top: 100, width: 96, height: 28 }, size, view, { align: 'end' }).left, 250 + 96 - 300);
  assert.equal(popoverAt({ left: 300, top: 100, width: 96, height: 28 }, size, view, { align: 'end' }).left, 390 - EDGE - 300);
  assert.equal(popoverAt({ left: 10, top: 100, width: 96, height: 28 }, size, view, { align: 'end' }).left, EDGE);
  // prefer above when there is room
  assert.equal(popoverAt({ left: 40, top: 400, width: 96, height: 28 }, size, view, { prefer: 'above' }).top, 400 - GAP - 48);
});

test('the phone hold: quick row above the lifted message, menu below, all on screen', () => {
  const view = { width: 390, height: 844 };
  const l = liftLayout({ left: 100, top: 300, width: 260, height: 60 }, view, 52, 56);
  assert.deepEqual(l, { quickTop: 300 - GAP - 52, bubbleTop: 300, bubbleHeight: 60, menuTop: 300 + 60 + GAP, clipped: false });
  // held at the very top: moved down so the quick row fits
  assert.equal(liftLayout({ left: 0, top: 10, width: 260, height: 60 }, view, 52, 56).quickTop, EDGE);
  // held at the very bottom: moved up so the menu fits
  const b = liftLayout({ left: 0, top: 820, width: 260, height: 60 }, view, 52, 56);
  assert.equal(b.menuTop + 56, view.height - EDGE);
  // a message taller than the screen: clipped, all three still fit
  const t = liftLayout({ left: 0, top: -400, width: 260, height: 2000 }, view, 52, 56);
  assert.equal(t.clipped, true);
  assert.equal(t.quickTop, EDGE);
  assert.equal(t.menuTop + 56, view.height - EDGE);
});

test('badges overlap the border: desktop straddles the TOP inner corner (agent right, person left)', () => {
  const view = { width: 860, height: 800 };
  const size = { width: 44, height: BADGE_HEIGHT };
  const agent = badgePlace('agent', 'desktop', { left: 16, top: 100, width: 300, height: 60 }, size, view);
  assert.equal(agent.edge, 'top');
  assert.equal(agent.top, 100 - BADGE_HEIGHT / 2);                       // centre line on the border: it overlaps
  assert.equal(agent.left + size.width, 16 + 300 + BADGE_OVERHANG);      // sticks out past the inner (right) edge
  assert.equal(agent.clamped, false);
  const user = badgePlace('user', 'desktop', { left: 460, top: 100, width: 380, height: 60 }, size, view);
  assert.equal(user.edge, 'top');
  assert.equal(user.left, 460 - BADGE_OVERHANG);                         // inner = left edge of the person's bubble
  assert.equal(user.top, agent.top);
});

test('badges overlap the border: a phone straddles the BOTTOM border at the inner corner', () => {
  const view = { width: 390, height: 844 };
  const size = { width: 44, height: BADGE_HEIGHT };
  const agent = badgePlace('agent', 'phone', { left: 12, top: 200, width: 200, height: 60 }, size, view);
  assert.equal(agent.edge, 'bottom');
  assert.equal(agent.top, 260 - BADGE_HEIGHT / 2);
  assert.equal(agent.left + size.width, 212 + BADGE_OVERHANG);
  const user = badgePlace('user', 'phone', { left: 150, top: 200, width: 228, height: 60 }, size, view);
  assert.equal(user.edge, 'bottom');
  assert.equal(user.left, 150 - BADGE_OVERHANG);
});

test('a bubble whose inner edge is at the viewport edge: the badge is clamped inside, never clipped', () => {
  const view = { width: 390, height: 844 };
  const size = { width: 44, height: BADGE_HEIGHT };
  // a full-width agent reply: its inner (right) edge is 6px from the screen edge
  const full = badgePlace('agent', 'phone', { left: 6, top: 200, width: 378, height: 120 }, size, view);
  assert.equal(full.clamped, true);
  assert.equal(full.left + size.width, view.width - CLEAR);
  assert.equal(full.top, 320 - BADGE_HEIGHT / 2);                        // still on the border
  // a full-width person's bubble: inner (left) edge 5px from the screen edge
  const u = badgePlace('user', 'desktop', { left: 5, top: 50, width: 380, height: 40 }, size, view);
  assert.equal(u.clamped, true);
  assert.equal(u.left, CLEAR);
});

test('room is reserved with spacing: top padding on desktop, bottom padding on a phone, none without badges', () => {
  assert.deepEqual(badgeReserve('desktop', true), { top: BADGE_HEIGHT / 2 + 4, bottom: 0 });
  assert.deepEqual(badgeReserve('phone', true), { top: 0, bottom: BADGE_HEIGHT / 2 + 4 });
  assert.deepEqual(badgeReserve('desktop', false), { top: 0, bottom: 0 });
  assert.deepEqual(badgeReserve('phone', false), { top: 0, bottom: 0 });
  assert.ok(badgeReserve('desktop', true).top >= BADGE_HEIGHT / 2);     // at least the part that hangs out
});

test('the desktop hover icons are vertically centred on the bubble\'s outer side, clear of the top-inner badge', () => {
  const clip: Box = { left: 0, top: 0, width: 860, height: 800 };
  const a: Box = { left: 16, top: 100, width: 300, height: 80 };
  assert.equal(barOffsetTop('agent', a, clip), (80 - BAR_HEIGHT) / 2);
  assert.equal(barOffsetTop('agent', { ...a, height: 20 }, clip), 0);   // a short bubble: never above it
  // the bar's top edge sits well below the badge's bottom edge (which hangs BADGE_HEIGHT/2 below the bubble's top)
  assert.ok((barOffsetTop('agent', a, clip) ?? 0) >= BADGE_HEIGHT / 2);
  // no room outside: under the bubble instead (the CSS places it), so no offset
  assert.equal(barOffsetTop('agent', { left: 16, top: 100, width: 828, height: 80 }, clip), null);
  assert.equal(barOffsetTop('user', { left: 460, top: 100, width: 380, height: 80 }, clip, BAR_HEIGHT, BAR_WIDTH), (80 - BAR_HEIGHT) / 2);
});
