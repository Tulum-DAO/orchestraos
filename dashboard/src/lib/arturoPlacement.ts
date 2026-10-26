/**
 * Where the floating Arturo button sits (FloatingArturo): pinned to the screen's bottom-right corner, or just
 * above a bar docked at the bottom when that bar runs under the corner (the agent page's composer, the agents
 * pane's input), right-aligned, so the bar keeps its full width.
 */
export const BUTTON_MARGIN = 12;   // from the screen's right and bottom edges
export const BUTTON_GAP = 8;       // above a docked bar
export const BUTTON_SIZE = 44;     // the largest it gets (the phone touch size)

export interface Box { top: number; bottom: number; left: number; right: number; height: number }

/** The button's distance from the bottom of the screen. */
export function buttonBottom(vw: number, vh: number, bars: Box[]): number {
  const left = vw - BUTTON_MARGIN - BUTTON_SIZE, right = vw - BUTTON_MARGIN;
  let bottom = BUTTON_MARGIN;
  for (const b of bars) {
    if (b.height <= 0 || b.bottom <= 0 || b.top >= vh) continue;          // hidden or off screen
    if (b.right < left || b.left > right) continue;                        // not under the corner
    bottom = Math.max(bottom, Math.round(vh - b.top + BUTTON_GAP));
  }
  return bottom;
}
