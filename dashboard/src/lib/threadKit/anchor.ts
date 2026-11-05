/**
 * anchor.ts — the rule that keeps a thread still under its reader while it changes (pure; the hook that applies it
 * is lib/useStickToBottom.ts).
 *
 *  - At the bottom (within FOLLOW_TOLERANCE_PX, which only absorbs sub-pixel rounding), the thread follows what
 *    arrives. A reader who has scrolled up at all, even a line, is not following: a looser "near the bottom" pulled
 *    such a reader down by every streamed line.
 *  - Not following, the reader's anchor is the first row still showing at the top of the scroller and how far below
 *    the scroller's top edge it sits. After any change (rows added below or above, a row that grows or settles, an
 *    image that loads), the scroller moves by however far that row moved, so what the reader is looking at stays
 *    exactly put. Rows added above (paging back through history) are held the same way.
 */

/** Sub-pixel rounding only: a phone's fractional scroll positions never read as "scrolled up". */
export const FOLLOW_TOLERANCE_PX = 4;

export interface ScrollMetrics { scrollHeight: number; scrollTop: number; clientHeight: number }

/** Whether the thread follows what arrives: the reader is at the bottom. */
export function isFollowing(m: ScrollMetrics, tolerance = FOLLOW_TOLERANCE_PX): boolean {
  return m.scrollHeight - m.scrollTop - m.clientHeight <= tolerance;
}

export interface RowBox { top: number; bottom: number }

/** The reader's anchor among the rows (in order, in viewport coordinates): the first whose bottom is below the
 *  scroller's top edge, i.e. the first row still showing. -1 when there is none. */
export function pickAnchor(rows: RowBox[], viewTop: number): number {
  return rows.findIndex((r) => r.bottom > viewTop + 0.5);
}

/** How far to move the scroller so the anchor is back where the reader left it: its offset below the scroller's top
 *  edge then, against now. Less than half a pixel is no move (rounding). */
export function anchorShift(savedOffset: number, nowOffset: number): number {
  const d = nowOffset - savedOffset;
  return Math.abs(d) < 0.5 ? 0 : d;
}
