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
  /** /api/agents context_pct as the detector wrote it: "NN%" or '' (no reading). */
  context_pct?: string;
  tier?: string;
  reports_to?: string;
  /** The project it is in (projects.json, inherited up reports_to); null/absent for a T0 or an agent with no PM. */
  project?: { slug: string; label: string; name?: string } | null;
  /** Its short name ("dev"), and its full name outside its project's header ("Demo · dev"; its id if never renamed). */
  display_name?: string;
  full_name?: string;
  /** When it last sent or got a message (ISO), for the order of groups and rows. */
  last_message_at?: string | null;
}

/**
 * A menu on screen OUTRANKS the detector state, because a menu is a question already asked and
 * the agent cannot proceed until it is answered — it is the most actionable thing the rail can
 * show. `waiting` (a permission prompt), `stranded` (an unsent draft someone left at the
 * prompt) and `queued` (a message the CLI accepted and never ran) are the detector states that
 * mean a HUMAN is the blocker.
 */
const NEEDS_YOU: ReadonlySet<LiveState> = new Set<LiveState>(['waiting', 'stranded', 'queued']);

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

/**
 * The context-used figure for a rail row, or null when there is no reading.
 *
 * /api/agents carries the detector's context_pct: "NN%" for every runtime that has a reading
 * (claude from its own meter, codex from the rollout's token counts) and '' otherwise. Only that
 * shape, 0-100, is a reading; anything else is null so the row shows a dash. A blank must never
 * become "0%", which would claim an empty context nobody measured.
 */
export function contextLabel(raw: unknown): string | null {
  // Exactly the shape the detector writes (an integer then '%'); a bare number, a decimal or
  // any other text is not a reading from it (codex review, #436).
  if (typeof raw !== 'string') return null;
  const m = /^(\d{1,3})%$/.exec(raw);
  if (!m) return null;
  const n = Number(m[1]);
  return n <= 100 ? `${n}%` : null;
}

/** 80% and up is the band where seats rotate, so the figure is marked there. */
export function contextHigh(raw: unknown): boolean {
  const l = contextLabel(raw);
  return l !== null && parseInt(l, 10) >= 80;
}

/**
 * Only a row in one of the three running sections shows a context figure. A seat that is not
 * running has no live context: a number there is a stale reading shown as current, and a dash on
 * each of the ~300 "Not running" rows would be a column of noise.
 */
export function showsContext(a: RailAgent): boolean {
  return sectionFor(a) !== undefined;
}


// ---- project groups inside a section ------------------------------------------------------------------------

/** One entry in a section: an agent's own row (shown by its full name), or a project group of two or more of the
 *  section's agents from one project (shown under the project's label by their short names). */
export type RailItem =
  | { kind: 'row'; agent: RailAgent }
  | { kind: 'group'; project: { slug: string; label: string }; agents: RailAgent[] };

const time = (a: RailAgent) => (a.last_message_at ? Date.parse(a.last_message_at) || 0 : 0);
const label = (a: RailAgent) => (a.display_name || a.name || a.id).toLowerCase();

/** Inside a group: the PM first (the project's T1), then by short name: a stable order, so rows don't jump. */
export function groupOrder(a: RailAgent, b: RailAgent): number {
  const pa = a.tier === 'T1' ? 0 : 1, pb = b.tier === 'T1' ? 0 : 1;
  return pa - pb || label(a).localeCompare(label(b));
}

/** A section's entries: T0s first, then projects (a group of two or more, or one agent's own row) and then agents
 *  in no project, each part by most recently messaged (name breaks ties). A project appears once per section it
 *  has agents in, so at most once each in Needs you, Working and Idle. */
export function sectionItems(agents: RailAgent[]): RailItem[] {
  const t0: RailAgent[] = [], loose: RailAgent[] = [];
  const byProject = new Map<string, { project: { slug: string; label: string }; agents: RailAgent[] }>();
  for (const a of agents) {
    if (a.tier === 'T0') t0.push(a);
    else if (a.project?.slug) {
      const g = byProject.get(a.project.slug) || { project: { slug: a.project.slug, label: a.project.label }, agents: [] };
      g.agents.push(a);
      byProject.set(a.project.slug, g);
    } else loose.push(a);
  }
  const recent = (x: number, y: number, tie: number) => (y - x) || tie;
  const byRecent = (x: RailAgent, y: RailAgent) => recent(time(x), time(y), label(x).localeCompare(label(y)));
  const projectItems: { at: number; name: string; item: RailItem }[] = [...byProject.values()].map((g) => {
    const at = Math.max(...g.agents.map(time));
    const item: RailItem = g.agents.length === 1 ? { kind: 'row', agent: g.agents[0] }
      : { kind: 'group', project: g.project, agents: [...g.agents].sort(groupOrder) };
    return { at, name: g.project.label.toLowerCase(), item };
  });
  projectItems.sort((x, y) => recent(x.at, y.at, x.name.localeCompare(y.name)));
  return [
    ...[...t0].sort(byRecent).map((a): RailItem => ({ kind: 'row', agent: a })),
    ...projectItems.map((p) => p.item),
    ...[...loose].sort(byRecent).map((a): RailItem => ({ kind: 'row', agent: a })),
  ];
}

/** The name a row shows: its short name inside its project's group, its full name anywhere else. */
export function rowLabel(a: RailAgent, inGroup: boolean): string {
  return (inGroup ? a.display_name : a.full_name) || a.display_name || a.name || a.id;
}
