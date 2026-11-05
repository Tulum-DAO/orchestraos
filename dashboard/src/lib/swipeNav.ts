/**
 * swipeNav.ts — paging a question card (components/chat/MultiPartCard.tsx) by swipe: a finger or pen dragged
 * sideways, or a two-finger sideways swipe on a trackpad (wheel deltaX). Swipe left is Next, swipe right is Back,
 * and swipe can never do what the buttons cannot (lib/multiPart.ts cardFooter): no Back from the first question, and
 * never Submit, so on the last question a swipe left only gives a little and bounces back (Submit is always a tap).
 * Next by swipe skips an unanswered question, exactly as the Next button does.
 *
 * Only a clearly sideways gesture counts: the finger must travel ~48px (or flick fast) and at least 1.5x as far
 * sideways as up or down; anything else is the page scrolling (the swipe area is `touch-action: pan-y`, so the
 * browser keeps vertical scroll). A gesture is not a swipe when it starts in a text field (the write-in box), on
 * content that scrolls sideways, within 20px of the screen's left or right edge (iOS Safari's back/forward swipe), or
 * with a mouse (a mouse clicks and selects text). A trackpad swipe is one step per gesture: its deltas add up past a
 * threshold, and the momentum tail that follows is absorbed until the wheel goes quiet and a short cooldown passes.
 *
 * Pure, so tested here (swipeNav.test.ts); hooks/useSwipeNav.ts wires it to the card.
 */

export type SwipeDir = 'next' | 'back';

export const SWIPE = {
  /** Travel that makes a swipe. */
  minDx: 48,
  /** Sideways must beat vertical by this much, during the drag and at release. */
  dominance: 1.5,
  /** Movement before the gesture picks an axis. */
  slop: 10,
  /** A flick: this fast (px/ms) at release, over at least flickMinDx. */
  flickV: 0.5,
  flickMinDx: 24,
  /** The window, ms, the release velocity is read over. */
  velocityWindow: 100,
  /** Gestures starting this close to the screen's side edges are the browser's. */
  edge: 20,
  /** How much the part follows the finger, and at most (a share of the card's width). */
  follow: 0.4,
  followMax: 0.35,
  /** Past an end (last question leftwards, first rightwards): a little give, at most resistMax px. */
  resist: 0.12,
  resistMax: 24,
  /** Trackpad: sideways wheel travel that makes a step. */
  wheelThreshold: 50,
  /** A pause this long (ms) between wheel events ends a gesture. */
  wheelGap: 150,
  /** No second step within this long (ms) of the last. */
  wheelCooldown: 400,
} as const;

export interface SwipeStart {
  x: number;
  viewportWidth: number;
  pointerType: string;
  primary: boolean;
  /** Started in an input, textarea, select or editable text. */
  inField: boolean;
  /** Started on content that scrolls sideways. */
  inHScroll: boolean;
}

/** Whether a pointer going down may start a swipe. */
export function swipeStartOk(s: SwipeStart): boolean {
  if (s.pointerType !== 'touch' && s.pointerType !== 'pen') return false;
  if (!s.primary || s.inField || s.inHScroll) return false;
  return s.x >= SWIPE.edge && s.x <= s.viewportWidth - SWIPE.edge;
}

const sideways = (dx: number, dy: number) => Math.abs(dx) > SWIPE.dominance * Math.abs(dy);

/** The axis a drag has taken: 'h' (the card's), 'v' (the page's: the gesture is dropped), or null while it is
 *  still inside the slop. */
export function swipeAxis(dx: number, dy: number): 'h' | 'v' | null {
  if (Math.abs(dx) < SWIPE.slop && Math.abs(dy) < SWIPE.slop) return null;
  return sideways(dx, dy) ? 'h' : 'v';
}

export interface Sample { x: number; y: number; t: number }

/** Sideways velocity (px/ms) over the drag's last moments: from the oldest sample inside the window (or the one
 *  just before it) to the last. A finger that stopped before lifting has none. */
export function releaseVelocity(samples: Sample[]): number {
  if (samples.length < 2) return 0;
  const last = samples[samples.length - 1];
  let i = samples.length - 1;
  while (i > 0 && samples[i - 1].t >= last.t - SWIPE.velocityWindow) i--;
  if (i === samples.length - 1) i--;
  const from = samples[i];
  const dt = last.t - from.t;
  return dt > 0 ? (last.x - from.x) / dt : 0;
}

/** What a finished drag asks for, before the card's rules (swipeStep). Finger to the left = Next. */
export function swipeIntent(g: { dx: number; dy: number; vx: number }): SwipeDir | null {
  if (!sideways(g.dx, g.dy)) return null;
  const far = Math.abs(g.dx) >= SWIPE.minDx;
  const flick = Math.abs(g.vx) >= SWIPE.flickV && Math.abs(g.dx) >= SWIPE.flickMinDx && Math.sign(g.vx) === Math.sign(g.dx);
  if (!far && !flick) return null;
  return g.dx < 0 ? 'next' : 'back';
}

/** The card's rules on a swipe, the same as its buttons: Next only where it leads to another question (never
 *  Submit), Back only past the first. A card of one part never pages. */
export function swipeStep(dir: SwipeDir | null, at: number, count: number): SwipeDir | null {
  if (!dir || count < 2) return null;
  if (dir === 'next') return at < count - 1 ? 'next' : null;
  return at > 0 ? 'back' : null;
}

/** How far (px) the part on show follows a drag of dx on a card `width` wide: a share of the finger, capped; toward
 *  an end it cannot go past, only a little give. */
export function dragOffset(dx: number, at: number, count: number, width: number): number {
  if (count < 2 || !dx) return 0;
  const open = swipeStep(dx < 0 ? 'next' : 'back', at, count) !== null;
  const cap = open ? width * SWIPE.followMax : SWIPE.resistMax;
  const v = Math.min(cap, Math.abs(dx) * (open ? SWIPE.follow : SWIPE.resist));
  return dx < 0 ? -v : v;
}

/** The touch-action a swipeable card needs on its parts area AND on every part: each part scrolls vertically on its
 *  own (overflow-y-auto), and a browser applies touch-action only from the touched element up to the nearest scroll
 *  container, so a rule on the area alone is ignored inside a part and the browser takes the sideways pan
 *  (pointercancel, no swipe). Vertical scroll and zoom stay the browser's. None on a card of one part. */
export function swipeTouch(count: number): { touchAction: 'pan-y pinch-zoom' } | undefined {
  return count > 1 ? { touchAction: 'pan-y pinch-zoom' } : undefined;
}

/** A wheel event's delta in pixels (deltaMode 1 = lines, 2 = pages). */
export function wheelDelta(delta: number, mode: number, page: number): number {
  return mode === 1 ? delta * 16 : mode === 2 ? delta * page : delta;
}

export interface WheelState {
  /** Sideways travel in this gesture so far. */
  acc: number;
  lastT: number;
  /** When this gesture stepped (it then absorbs the rest of itself), else null. */
  firedAt: number | null;
}

export const wheelStart: WheelState = { acc: 0, lastT: -Infinity, firedAt: null };

/** One wheel event. Two fingers moving left scroll the content right (deltaX > 0): Next. Vertical-dominant events
 *  reset the sideways travel. After a step, the gesture's momentum tail is absorbed until the wheel has been quiet
 *  for wheelGap and wheelCooldown has passed since the step. */
export function wheelStep(s: WheelState, e: { dx: number; dy: number; t: number }): { state: WheelState; dir: SwipeDir | null } {
  const quiet = e.t - s.lastT >= SWIPE.wheelGap;
  if (s.firedAt !== null && !(quiet && e.t - s.firedAt >= SWIPE.wheelCooldown)) {
    return { state: { ...s, acc: 0, lastT: e.t }, dir: null };
  }
  const base = quiet || s.firedAt !== null ? 0 : s.acc;
  const acc = sideways(e.dx, e.dy) ? base + e.dx : 0;
  if (Math.abs(acc) >= SWIPE.wheelThreshold) {
    return { state: { acc: 0, lastT: e.t, firedAt: e.t }, dir: acc > 0 ? 'next' : 'back' };
  }
  return { state: { acc, lastT: e.t, firedAt: null }, dir: null };
}
