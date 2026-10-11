/**
 * Phone Dev mode: the tmux pane keeps its full height when the keyboard opens (resizing it
 * would make the agent's TUI redraw on every keyboard flip), so the box that shows it gets
 * shorter than the terminal. Shift the terminal up just far enough that the cursor row (the
 * line being typed into) and the row under it (Claude Code's divider) sit at the bottom of the
 * visible box, right above the key bar.
 *
 * Returns how many px to move the terminal up. 0 when the cursor row is already visible.
 */
export function cliShift(boxHeight: number, rowHeight: number, cursorRow: number, rows: number, below = 1): number {
  if (!(boxHeight > 0) || !(rowHeight > 0) || rows <= 0) return 0;
  const total = rows * rowHeight;
  const overflow = Math.max(0, total - boxHeight);
  const lastShown = Math.min(Math.max(cursorRow, 0) + Math.max(below, 0), rows - 1);
  const cursorBottom = (lastShown + 1) * rowHeight;
  return Math.round(Math.min(overflow, Math.max(0, cursorBottom - boxHeight)));
}
