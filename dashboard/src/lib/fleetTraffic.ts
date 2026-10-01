/**
 * Pure logic behind the 2D view's travelling dots and bottom ticker (spec §4, §3).
 *
 * The rule these exist to honour is §2: "Motion means something. If a dot moves, a message
 * is actually moving." So a dot is never decorative and never a loop — it is emitted for a
 * specific message id that was not in the previous poll, and it animates once. That makes
 * the diff below the whole feature, not a helper.
 *
 * Separate .ts module because a .test.mjs cannot import a .tsx, and eslint's
 * react-refresh/only-export-components forbids exporting non-components from one.
 */

export interface TrafficMessage {
  id: string;
  from_agent: string;
  to_agent: string;
  type: string | null;
  created_at: string | null;
}

/** Messages present now that were absent from the previous poll, newest first preserved.
 *  On the FIRST poll (no previous ids) this returns nothing: the whole backlog is not "new",
 *  and firing 50 dots on page load would be exactly the decorative motion §2 rules out. */
export function newlyArrived(previousIds: Set<string>, now: TrafficMessage[]): TrafficMessage[] {
  if (previousIds.size === 0) return [];
  return now.filter((m) => !previousIds.has(m.id));
}

/** Dot colour by message type (spec §4). Unknown/absent type is neutral rather than assigned
 *  a colour it does not own — a wrong colour is a wrong claim about what is moving. */
export const DOT_COLOR: Record<string, string> = {
  task: 'bg-yellow-400',
  task_request: 'bg-orange-400',
  reply: 'bg-sky-400',
};
export const DOT_COLOR_UNKNOWN = 'bg-neutral-400';

export function dotColor(type: string | null | undefined): string {
  if (!type) return DOT_COLOR_UNKNOWN;
  return DOT_COLOR[type.toLowerCase()] ?? DOT_COLOR_UNKNOWN;
}

/** "gm → telegram · reply". Type is shown raw-but-readable (underscores to spaces) rather
 *  than mapped to prettier names, so what the ticker says matches what the store contains. */
export function tickerLabel(m: TrafficMessage): string {
  const type = (m.type ?? 'message').replace(/_/g, ' ');
  return `${m.from_agent} → ${m.to_agent} · ${type}`;
}

// Deliberately NO pairKey here. A dot must land on the line its count is drawn on, so there
// must be exactly ONE key function — topologyLines.pairKey. Re-exporting it from this module
// would also break the repo's test harness: node --experimental-strip-types cannot resolve an
// extensionless relative import, which vite and tsc both accept. Consumers import it from
// topologyLines directly, and fleetTraffic.test.mjs asserts the two agree behaviourally.
