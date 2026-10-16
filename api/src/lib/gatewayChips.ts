/**
 * The gateway computes each agent's status chip ONCE (scripts/agent_chip.py, docs/AGENT_CHIP.md);
 * /api/agents attaches it by id so the dashboard draws exactly what iOS, the Watch and the Mac grid
 * draw. Never blocks a poll: it serves the last good answer (for at most CHIP_MAX_AGE_MS) and
 * refreshes in the background; a gateway that is down means rows carry no chip and the rail falls
 * back to its own fields.
 */
import { readGatewayToken } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';

export interface Chip {
  name: string;
  kind: 'agent' | 'service' | 'external';
  provider: string | null;
  model_label: string | null;
  /** null only for kind external: its liveness is not knowable here. */
  status: 'working' | 'needs_you' | 'idle' | 'down' | null;
  context_left_pct: number | null;
}

export interface ChipDeps {
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  now: () => number;
}

export const CHIP_TTL_MS = 5000;
/** Older than this, the last good answer is dropped: a chip that old is not what the seat is doing. */
export const CHIP_MAX_AGE_MS = 30000;
const STATUSES = new Set(['working', 'needs_you', 'idle', 'down']);

export function defaultChipDeps(): ChipDeps {
  return {
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      return { status: r.status, body: await r.json().catch(() => null) };
    },
    now: () => Date.now(),
  };
}

/** id -> chip from a gateway /agents body; rows without a well-formed chip are skipped. */
export function chipsFromAgents(body: any): Map<string, Chip> {
  const out = new Map<string, Chip>();
  for (const r of Array.isArray(body?.agents) ? body.agents : []) {
    const c = r?.chip;
    if (typeof r?.id !== 'string' || !c || typeof c !== 'object') continue;
    if (!(STATUSES.has(c.status) || (c.kind === 'external' && c.status === null))) continue;
    out.set(r.id, c as Chip);
  }
  return out;
}

export function createChipSource(deps: ChipDeps = defaultChipDeps()) {
  let chips = new Map<string, Chip>();
  let at = -Infinity;
  let inflight: Promise<void> | null = null;

  async function refresh(): Promise<void> {
    const token = deps.token();
    if (!token) return;
    try {
      const { status, body } = await deps.fetchJson(`${deps.gatewayUrl()}/agents`,
        { method: 'GET', headers: { Authorization: `Bearer ${token}` } }, 3000);
      if (status === 200) { chips = chipsFromAgents(body); at = deps.now(); }
    } catch { /* keep the last good answer */ }
  }

  /** The current chips; kicks a background refresh when older than CHIP_TTL_MS, and returns none
   * once the last good answer is older than CHIP_MAX_AGE_MS (the gateway has been down that long). */
  function current(): Map<string, Chip> {
    const age = deps.now() - at;
    if (age > CHIP_TTL_MS && !inflight) {
      inflight = refresh().finally(() => { inflight = null; });
    }
    return age > CHIP_MAX_AGE_MS ? new Map() : chips;
  }

  return { current, refresh };
}

export const gatewayChips = createChipSource();
