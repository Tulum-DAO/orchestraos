/**
 * reactionLayout.ts — where the reaction UI goes on screen (components/kit/Reactions.tsx). Pure: boxes in, positions out,
 * so the placement rules are tested without a browser.
 *
 *   - Desktop hover icons sit OUTSIDE the bubble on its outer side (right of the agent's reply, left of the person's)
 *     when the thread's scroller has room for them there, vertically centred on the bubble (so they never meet the
 *     badge on the inner top corner); otherwise just under the bubble, on its outer edge.
 *   - Badges OVERLAP the bubble's border: desktop straddles the TOP border at the INNER corner
 *     (iMessage tapback: agent bubble top-right, person's bubble top-left); a phone straddles the BOTTOM border at the
 *     inner corner (the one nearest the screen's middle). Room is made with spacing, not by moving the badge inside:
 *     a row with badges reserves the overhang as top padding (desktop) or bottom padding (phone), and a bubble whose
 *     inner edge is within CLEAR of the viewport edge has its badge clamped sideways so it never clips.
 *   - A popover (the quick row, the full picker, who reacted) opens next to what opened it and stays on screen:
 *     below it when it fits, else above; never past an edge (8px margin).
 *   - Phone hold: the held message is lifted where it is, the quick row above it and the menu below; a message too
 *     tall for both is clipped and moved so all three fit.
 */
export interface Box { left: number; top: number; width: number; height: number }
export interface Size { width: number; height: number }

export const EDGE = 8;
export const GAP = 8;
/** The hover icons' width: three 28px buttons, their gaps and padding. */
export const BAR_WIDTH = 96;
/** ...and its height. */
export const BAR_HEIGHT = 32;

export const boxOf = (r: { left: number; top: number; width: number; height: number }): Box =>
  ({ left: r.left, top: r.top, width: r.width, height: r.height });

/** Whether the hover icons fit outside the bubble on its outer side, inside the scroller's box. */
export function barOutside(side: 'user' | 'agent', anchor: Box, clip: Box, barWidth = BAR_WIDTH): boolean {
  const room = side === 'agent' ? clip.left + clip.width - (anchor.left + anchor.width) : anchor.left - clip.left;
  return room >= barWidth + GAP;
}

/** Vertical offset (from the bubble's top) of the hover icons on the bubble's outer side: centred on the bubble, never
 *  above it. Centred keeps them clear of the top-inner badge. Under the bubble (not outside): null, the CSS puts them
 *  at the bubble's bottom outer edge. */
export function barOffsetTop(side: 'user' | 'agent', anchor: Box, clip: Box, barHeight = BAR_HEIGHT, barWidth = BAR_WIDTH): number | null {
  if (!barOutside(side, anchor, clip, barWidth)) return null;
  return Math.max(0, Math.round((anchor.height - barHeight) / 2));
}

export type Device = 'desktop' | 'phone';
export const BADGE_HEIGHT = 22;
/** How far a badge sticks out past the bubble's inner edge (the corner it straddles). */
export const BADGE_OVERHANG = 8;
/** Closest a badge may come to the viewport's side edge. */
export const CLEAR = 4;
/** A bubble whose inner edge is this near the viewport edge gets its badge clamped. */
export const NEAR_EDGE = 12;

/** The room a row with badges must reserve for them: half the badge hangs outside the bubble, plus a gap. Desktop above
 *  the bubble (top padding), phone below it (bottom padding). No badges: nothing. */
export function badgeReserve(device: Device, hasBadges: boolean, height = BADGE_HEIGHT): { top: number; bottom: number } {
  const room = hasBadges ? Math.ceil(height / 2) + 4 : 0;
  return device === 'desktop' ? { top: room, bottom: 0 } : { top: 0, bottom: room };
}

export interface BadgePlace { edge: 'top' | 'bottom'; left: number; top: number; clamped: boolean }

/** Where a badge row of `size` goes, in the same coordinates as `anchor` (the bubble): its centre line ON the bubble's
 *  border (top on desktop, bottom on a phone), at the inner corner (right for the agent, left for the person), sticking
 *  BADGE_OVERHANG past the inner edge. Kept inside `view` (CLEAR from its sides): when the bubble's inner edge is within
 *  NEAR_EDGE of the viewport edge the badge is pulled in, so it overlaps the corner without clipping. */
export function badgePlace(side: 'user' | 'agent', device: Device, anchor: Box, size: Size, view: Size): BadgePlace {
  const edge = device === 'desktop' ? 'top' : 'bottom';
  const top = (edge === 'top' ? anchor.top : anchor.top + anchor.height) - Math.round(size.height / 2);
  const inner = side === 'agent' ? anchor.left + anchor.width : anchor.left;
  const want = side === 'agent' ? inner + BADGE_OVERHANG - size.width : inner - BADGE_OVERHANG;
  const left = clamp(want, CLEAR, view.width - CLEAR - size.width);
  const near = side === 'agent' ? view.width - inner < NEAR_EDGE : inner < NEAR_EDGE;
  return { edge, left, top, clamped: left !== want || near };
}

/** A popover of `size` next to `from`: below it (or above when `prefer` says so) if it fits, else the other way;
 *  its outer edge lined up with `from`'s (`align` 'start' = left edges, 'end' = right edges); clamped to the screen. */
export function popoverAt(from: Box, size: Size, view: Size, opts: { prefer?: 'below' | 'above'; align?: 'start' | 'end' } = {}): { left: number; top: number } {
  const below = from.top + from.height + GAP;
  const above = from.top - GAP - size.height;
  const fitsBelow = below + size.height <= view.height - EDGE;
  const fitsAbove = above >= EDGE;
  const top = (opts.prefer ?? 'below') === 'below'
    ? (fitsBelow || !fitsAbove ? below : above)
    : (fitsAbove || !fitsBelow ? above : below);
  const left = (opts.align ?? 'start') === 'start' ? from.left : from.left + from.width - size.width;
  return {
    left: clamp(left, EDGE, view.width - EDGE - size.width),
    top: clamp(top, EDGE, view.height - EDGE - size.height),
  };
}

export interface LiftLayout { quickTop: number; bubbleTop: number; bubbleHeight: number; menuTop: number; clipped: boolean }

/** The phone hold: the quick row above the lifted message, the menu below it, all on screen. */
export function liftLayout(bubble: Box, view: Size, quickHeight: number, menuHeight: number): LiftLayout {
  const room = view.height - 2 * EDGE - quickHeight - menuHeight - 2 * GAP;
  const bubbleHeight = Math.max(0, Math.min(bubble.height, room));
  const minTop = EDGE + quickHeight + GAP;
  const maxTop = view.height - EDGE - menuHeight - GAP - bubbleHeight;
  const bubbleTop = clamp(bubble.top, minTop, Math.max(minTop, maxTop));
  return {
    quickTop: bubbleTop - GAP - quickHeight,
    bubbleTop,
    bubbleHeight,
    menuTop: bubbleTop + bubbleHeight + GAP,
    clipped: bubbleHeight < bubble.height,
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), Math.max(lo, hi));
}
