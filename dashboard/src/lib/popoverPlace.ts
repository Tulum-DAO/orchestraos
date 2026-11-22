/**
 * Where a floating popover (the model picker on desktop) sits next to the control that opened it.
 *
 * - `auto` (the agent composer's pill): under the control when there is room for the whole panel there or more room
 *   than above; else above it.
 * - `above` (Arturo's brain chip, inside his composer): directly above the control, shortened to the room there, and
 *   under it only when even MIN_ABOVE does not fit above and there is more room below.
 *
 * Left edge on the control's left edge; when that would run off the right of the window, right edge on the control's
 * right edge (so the panel still sits over the control); never closer than 8px to either window edge. The height is
 * capped to the room on the chosen side, so the panel never runs off the screen.
 *
 * No control at all: near the top, centred (the old fallback, kept for a picker opened from a menu with nothing to
 * anchor to).
 */
export interface AnchorRect { top: number; bottom: number; left: number; right: number }
export interface Viewport { width: number; height: number }
export interface PopoverPlace { left: number; top?: number; bottom?: number; maxHeight: number }
export type Placement = 'auto' | 'above';

const EDGE = 8;
const GAP = 6;
/** Under 'above', the panel stays above (shortened to fit) while it has at least this much room there. */
export const MIN_ABOVE = 260;

export function placePopover(anchor: AnchorRect | null, vp: Viewport, opts: { width: number; height: number; placement?: Placement }): PopoverPlace {
  const w = Math.min(opts.width, vp.width - EDGE * 2);
  const h = opts.height;
  if (!anchor) return { left: Math.max(EDGE, (vp.width - w) / 2), top: Math.round(vp.height * 0.12), maxHeight: h };
  const fitsLeftAligned = anchor.left + w <= vp.width - EDGE;
  const left = Math.max(EDGE, Math.min(vp.width - w - EDGE, fitsLeftAligned ? anchor.left : anchor.right - w));
  const roomBelow = vp.height - anchor.bottom - GAP - EDGE;
  const roomAbove = anchor.top - GAP - EDGE;
  const below = (opts.placement ?? 'auto') === 'above'
    ? roomAbove < Math.min(h, MIN_ABOVE) && roomBelow > roomAbove
    : roomBelow + GAP + EDGE >= h || roomBelow >= roomAbove;
  return below
    ? { left, top: anchor.bottom + GAP, maxHeight: Math.max(0, Math.min(h, roomBelow)) }
    : { left, bottom: vp.height - anchor.top + GAP, maxHeight: Math.max(0, Math.min(h, roomAbove)) };
}
