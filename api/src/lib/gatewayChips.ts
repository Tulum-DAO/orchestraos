/**
 * The gateway computes each agent's status chip ONCE (scripts/agent_chip.py, docs/AGENT_CHIP.md);
 * /api/agents attaches it by id so the dashboard draws exactly what iOS, the Watch and the Mac grid
 * draw. Never blocks a poll: it serves the last good answer and refreshes in the background, and a
 * gateway that is down just means rows carry no chip (the rail falls back to its own fields).
 */
import { readGatewayToken } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';

export interface Chip {
  name: string;
  provider: string | null;
  model_label: string | null;
  status: 'working' | 'needs_you' | 'idle' | 'down';
  context_left_pct: number | null;
}

export interface ChipDeps {
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  now: () => number;
}

export const CHIP_TTL_MS = 5000;
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
    if (typeof r?.id !== 'string' || !c || typeof c !== 'object' || !STATUSES.has(c.status)) continue;
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

  /** The current chips; kicks a background refresh when older than CHIP_TTL_MS. */
  function current(): Map<string, Chip> {
    if (deps.now() - at > CHIP_TTL_MS && !inflight) {
      inflight = refresh().finally(() => { inflight = null; });
    }
    return chips;
  }

  return { current, refresh };
}

export const gatewayChips = createChipSource();
