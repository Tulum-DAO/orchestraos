/**
 * Pure auto-scroll/follow logic for a docked live feed (2D Agents View §5).
 * Kept out of the .tsx so it is testable — a .test.mjs cannot import JSX.
 */

/** True when the scroll position is within `thresholdPx` of the bottom —
 * the signal for "still following the tail" vs. "reader scrolled up to read".
 * A feed that keeps auto-scrolling while the reader is up reading old lines
 * is worse than one that never auto-scrolls, so this is the single source
 * of truth for whether new items should pull the view down. */
export function isNearBottom(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  thresholdPx = 48
): boolean {
  return scrollHeight - scrollTop - clientHeight <= thresholdPx;
}
