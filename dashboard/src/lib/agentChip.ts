/**
 * The gateway's status chip (scripts/agent_chip.py, docs/AGENT_CHIP.md), as the rail draws it.
 * Pure, so the rules are tested without a browser: unknown context shows nothing rather than 0% or
 * 100%, and "waiting on a card" counts as needing you.
 */
export interface AgentChip {
  name?: string;
  kind?: 'agent' | 'service' | 'external' | string;
  provider?: string | null;
  model_label?: string | null;
  status?: 'working' | 'needs_you' | 'idle' | 'down' | string;
  context_left_pct?: number | null;
}

export const LOW_CONTEXT_LEFT = 20;

export function asChip(v: unknown): AgentChip | undefined {
  if (!v || typeof v !== 'object') return undefined;
  const c = v as AgentChip;
  return typeof c.status === 'string' || (c.kind === 'external' && c.status === null) ? c : undefined;
}

export function chipNeedsYou(c: AgentChip | undefined): boolean {
  return c?.status === 'needs_you';
}

/** "92% left", or null when there is no reading. */
export function contextLeftLabel(c: AgentChip | undefined): string | null {
  const v = c?.context_left_pct;
  if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 100) return null;
  return `${Math.round(v)}% left`;
}

export function contextLeftLow(c: AgentChip | undefined): boolean {
  const v = c?.context_left_pct;
  return typeof v === 'number' && Number.isFinite(v) && v <= LOW_CONTEXT_LEFT;
}

/** The logo's tooltip: "Runs on Claude · Opus 5.5". */
export function providerTitle(providerLabel: string, c: AgentChip | undefined): string {
  return c?.model_label ? `Runs on ${providerLabel} · ${c.model_label}` : `Runs on ${providerLabel}`;
}

/**
 * The rail's state for a row, from the chip when there is one, so the dashboard's dot and section
 * say what every other surface says. The detector's finer state is kept where it is the same kind
 * (waiting vs stranded; crashed vs stopped vs offline), and the one-truth colours stay STATE_STYLE.
 */
export function railStatus(c: AgentChip | undefined, detector: string | undefined): string | undefined {
  if (!c) return detector;
  // External: not checked here. No live state is claimed; the detector's word (if any) stands.
  if (c.kind === 'external') return detector;
  switch (c.status) {
    case 'needs_you': return detector === 'waiting' || detector === 'stranded' ? detector : 'waiting';
    case 'working': return 'working';
    case 'idle': return 'idle';
    case 'down': return detector === 'stopped' || detector === 'offline' ? detector : 'crashed';
    default: return detector;
  }
}

/** The rail row's pending-menu flag: with a chip, the chip's status has already counted it. */
export function railPendingMenu(c: AgentChip | undefined, detectorFlag: boolean): boolean {
  return c ? false : detectorFlag;
}

/** What stands in for "N% left" on a row that is not an agent: services and external seats. */
export function kindLabel(c: AgentChip | undefined): string | null {
  if (c?.kind === 'service') return 'Service';
  if (c?.kind === 'external') return 'External';
  return null;
}
