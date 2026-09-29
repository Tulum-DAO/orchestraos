/**
 * Pure helpers behind the 2D view's connection lines (spec §4). They live here rather than
 * in TopologyDiagram.tsx for one concrete reason: a .tsx file cannot be imported by this
 * repo's test idiom (node --experimental-strip-types erases types but does not transform
 * JSX), and eslint's react-refresh/only-export-components forbids exporting them from a
 * component file anyway. A plain .ts module is testable and lint-clean.
 */

/** Widest a line may get, in px. */
export const MAX_LINE_PX = 5;

/**
 * Line thickness for a pair's message count, relative to the busiest pair on screen.
 *
 * sqrt, not linear, and that is the whole point: with a busiest line near 150 and a real
 * but quiet line at 3, linear scaling renders both at 1px, so "quiet" and "dead" look
 * identical — the one distinction the operator actually needs at a glance. Caps at
 * MAX_LINE_PX so a single runaway conversation cannot swamp the diagram (spec §12,
 * "thickness caps"). A zero or unknown count is 1px, never 0: a line that vanishes is a
 * line you cannot click, and spec §2 says nothing important is hidden.
 */
export function lineWidthPx(count: number, busiest: number): number {
  if (!Number.isFinite(count) || count <= 0) return 1;
  if (!Number.isFinite(busiest) || busiest <= 0) return 1;
  const scaled = 1 + Math.round((MAX_LINE_PX - 1) * Math.sqrt(count / busiest));
  return Math.min(MAX_LINE_PX, Math.max(1, scaled));
}

/** Unordered pair key. A->B and B->A are one line, so they must hash the same. */
export function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

/**
 * Coarse "how long ago", for line tooltips.
 *
 * Ninth local copy of a relative-time formatter in this codebase (ApprovalCard, Inbox,
 * Activity, Overview, Approvals, Tasks, Questionnaires and ChatHistory each have one).
 * Deliberately not a refactor of those eight: they belong to other seats in a shared
 * checkout, and rewriting them mid-fleet is a bigger risk than one duplicated formatter.
 */
export function shortAgo(ts: string | null | undefined, now = Date.now()): string {
  if (!ts) return 'never';
  const then = new Date(ts).getTime();
  if (!Number.isFinite(then)) return 'never';
  const ms = now - then;
  if (ms < 60_000) return 'just now';      // covers clock skew / future stamps too
  const m = Math.floor(ms / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
}
