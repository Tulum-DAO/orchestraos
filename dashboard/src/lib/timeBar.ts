/**
 * Pure helpers for the 2D view's time bar (spec §7). Separate .ts module for the usual two
 * reasons in this repo: a .test.mjs cannot import a .tsx, and eslint's
 * react-refresh/only-export-components forbids exporting non-components from one.
 */

/** Windows the picker offers. 72h is included because the 3D surface gm saw uses 72h, and
 *  two views offering different windows would be a fresh version of the same
 *  two-sources-disagreeing problem this feature exists to remove. */
export const WINDOW_OPTIONS = [1, 6, 24, 72] as const;

/**
 * How many minutes back `asof` is from `now`, clamped to [0, spanMinutes].
 *
 * Clamped rather than allowed to go negative or overrun: an asof from a machine whose clock
 * is ahead would otherwise push the slider past its own maximum, and a browser left open
 * overnight would drift an old asof past the span. Both render as an out-of-range slider,
 * which reads as a broken control rather than as the edge case it is. Live (`null`) is 0 —
 * the "now" end of the track.
 */
export function minutesAgo(asof: string | null, now: number, spanMinutes: number): number {
  if (!asof) return 0;
  const then = new Date(asof).getTime();
  if (!Number.isFinite(then)) return 0;
  return Math.max(0, Math.min(spanMinutes, Math.round((now - then) / 60_000)));
}

/** Slider position -> the asof it means. The track runs oldest-left to now-right, so the
 *  value is inverted. Landing on the right-hand end means LIVE, not "zero minutes ago" —
 *  a frozen asof equal to now would stop polling and silently go stale. */
export function asofFromSlider(sliderValue: number, now: number, spanMinutes: number): string | null {
  const back = spanMinutes - sliderValue;
  if (back <= 0) return null;
  return new Date(now - back * 60_000).toISOString();
}
