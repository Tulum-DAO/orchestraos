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

/**
 * Partition a flat agent list into the shape the tree draws: one root, its leads, each lead's
 * workers, and everything else.
 *
 * THE INVARIANT THIS EXISTS FOR: every agent handed in comes out in exactly one bucket. The
 * old code picked buckets by tier — `find(a => a.tier === 'T0')` for the root, `filter(tier
 * === 'T2' || 'T3')` for workers, and an orphan bucket that only collected T2/T3 — so an
 * agent could match nothing and vanish. review found it live (2026-09-30): the fleet's ONLY
 * down agent, gm-g2, is also tier T0, so `find` returned the live gm and gm-g2 rendered
 * nowhere on the page. Filter to Down and the header read "showing 1 of 14" above a graph of
 * thirteen live boxes.
 *
 * That is a direct violation of the spec's first rule (§2, "Nothing important is hidden. A
 * down agent stays on screen, in red") and of its done-criterion (§15, "find any down agent
 * within two seconds"). So the fix is not "also handle a second T0" — it is to compute
 * `rest` by SUBTRACTION, which no tier value, missing parent or unexpected shape can slip
 * through. `partitionTopology` is pure so that invariant is testable rather than hoped for.
 */
export interface TopologyAgent { id: string; tier?: string; parent?: string }

export interface TopologyPartition<T extends TopologyAgent> {
  /** First T0, the tree's root. Undefined if the list has none. */
  root: T | undefined;
  /** T1s, drawn as the row under the root. */
  leads: T[];
  /** lead id -> its workers (T2/T3 whose parent is one of `leads`). */
  workersByLead: Record<string, T[]>;
  /** Everything the tree would not otherwise draw — extra roots, parentless workers, workers
   *  whose parent is not a lead, unknown tiers. Rendered, never dropped. */
  rest: T[];
}

export function partitionTopology<T extends TopologyAgent>(agents: T[]): TopologyPartition<T> {
  const root = agents.find((a) => a.tier === 'T0');
  const leads = agents.filter((a) => a.tier === 'T1');
  const leadIds = new Set(leads.map((l) => l.id));

  const workersByLead: Record<string, T[]> = {};
  const drawn = new Set<string>();
  if (root) drawn.add(root.id);
  for (const l of leads) drawn.add(l.id);

  for (const a of agents) {
    if (drawn.has(a.id)) continue;
    const isWorker = a.tier === 'T2' || a.tier === 'T3';
    if (isWorker && a.parent && leadIds.has(a.parent)) {
      (workersByLead[a.parent] ??= []).push(a);
      drawn.add(a.id);
    }
  }

  // By subtraction, deliberately: whatever the tree did not claim is rendered here.
  const rest = agents.filter((a) => !drawn.has(a.id));
  return { root, leads, workersByLead, rest };
}
