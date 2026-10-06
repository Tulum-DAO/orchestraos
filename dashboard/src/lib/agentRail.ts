/**
 * agentRail.ts — which of the three rail sections an agent belongs in.
 *
 * Needs you / Working / Idle (PLAN_harness-ux-overhaul v3.2, C7). Pure and unit-tested, so the
 * grouping rules are pinned without a DOM — the same shape as lib/toolGroups.ts.
 *
 * SOURCE OF TRUTH is the /api/agents v2 detector `status` field plus `has_pending_menu`, the
 * one-truth feed guarded by dashboard/scripts/guard-one-truth.sh. Nothing here infers state from
 * a name, and nothing here treats a state file as liveness.
 */
import { normalizeAgentState, type LiveState } from './agentStatus';

export type RailSection = 'needsYou' | 'working' | 'idle';

export interface RailAgent {
  id: string;
  name?: string;
  status?: string;
  has_pending_menu?: boolean;
  unread?: number;
  provider?: string;
}

/**
 * A menu on screen OUTRANKS the detector state, because a menu is a question already asked and
 * the agent cannot proceed until it is answered — it is the most actionable thing the rail can
 * show. `waiting` (a permission prompt) and `stranded` (an unsent draft someone left at the
 * prompt) are the two detector states that mean a HUMAN is the blocker.
 */
const NEEDS_YOU: ReadonlySet<LiveState> = new Set<LiveState>(['waiting', 'stranded']);

/**
 * `stalled` lives in WORKING deliberately: it is the detector's 600s-quiet reclassification of a
 * working agent (a long deep turn), not a fault. Putting it in Needs you would cry for a human on
 * every slow test run, and the colour mapping already distinguishes it (amber, not red).
 */
const WORKING: ReadonlySet<LiveState> = new Set<LiveState>(['working', 'stalled']);

export function sectionFor(a: RailAgent): RailSection | undefined {
  if (a.has_pending_menu) return 'needsYou';
  const s = normalizeAgentState(a.status);
  if (NEEDS_YOU.has(s)) return 'needsYou';
  if (WORKING.has(s)) return 'working';
  if (s === 'idle') return 'idle';
  // stopped / crashed / offline / retired / unknown are NOT in the rail's three sections. An
  // absent or unrecognised state is deliberately NOT idle: "we do not know" must never render as
  // "alive at the prompt", which is the absent-evidence-passing-as-evidence failure.
  return undefined;
}

export interface RailGroups {
  needsYou: RailAgent[];
  working: RailAgent[];
  idle: RailAgent[];
  /** Everything the three sections do not claim — surfaced under a collapsed "Not running". */
  other: RailAgent[];
}

/**
 * Group for the rail, preserving input order within each section so the caller controls sort.
 * Every input lands in exactly one bucket, including `other`: nothing is dropped, so the rail can
 * never silently hide an agent that exists.
 */
export function groupForRail(agents: RailAgent[]): RailGroups {
  const out: RailGroups = { needsYou: [], working: [], idle: [], other: [] };
  for (const a of agents) {
    const s = sectionFor(a);
    if (s) out[s].push(a);
    else out.other.push(a);
  }
  return out;
}

/** The count that belongs on a "Needs you" badge: people, not machines, are the blocker. */
export function needsYouCount(agents: RailAgent[]): number {
  return groupForRail(agents).needsYou.length;
}

/** claude / codex / gemini / antigravity / service -> the two-letter rail badge. */
export const PROVIDER_ABBR: Record<string, string> = {
  claude: 'CL', codex: 'CX', gemini: 'GM', antigravity: 'AG', service: 'SV',
};

/** The badge is two letters with no legend anywhere, so it needs to say its own name. */
export const PROVIDER_LABEL: Record<string, string> = {
  claude: 'Claude', codex: 'Codex', gemini: 'Gemini', antigravity: 'Antigravity', service: 'Service',
};

/**
 * Should the runtime badge be shown at all?
 *
 * A column that prints the SAME two letters on every row tells the reader nothing and spends
 * a column of width saying it. The badge earns its place only when it distinguishes one row
 * from another, so it appears once at least two DISTINCT known runtimes are on screen.
 *
 * A row with no runtime is not a second value. 138 of 320 live rows carry none, so counting
 * "unknown" as variety would switch the badge on permanently by accident — which is the exact
 * failure being fixed.
 */
export function providersVary(agents: RailAgent[]): boolean {
  const seen = new Set<string>();
  for (const a of agents) {
    const p = a.provider?.toLowerCase();
    if (p && PROVIDER_ABBR[p]) seen.add(p);
    if (seen.size > 1) return true;
  }
  return false;
}
