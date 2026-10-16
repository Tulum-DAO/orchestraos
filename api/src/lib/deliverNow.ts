/**
 * "Deliver now" for the dashboard: what is waiting for an agent, and releasing it early
 * (scripts/deliver_now.py; gateway GET /held and POST /deliver-now). Thin forwarders with the
 * gateway bearer; the agent-scope check is router.param('id') in routes/agents.ts, as for every
 * other /:id route.
 */
import { readGatewayToken } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';

export interface DeliverNowDeps {
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
}

export function defaultDeliverNowDeps(): DeliverNowDeps {
  return {
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      return { status: r.status, body: await r.json().catch(() => ({ ok: false, error: 'bad gateway json' })) };
    },
  };
}

/** The tmux session behind an agent id, as the inject route resolves it; null when unknown. */
export function sessionFor(registry: any, id: string): string | null {
  const agent = registry?.agents?.[id];
  if (agent) return (agent.tmux_session as string) || id;
  if (id.startsWith('unregistered:')) return id.slice('unregistered:'.length);
  return null;
}

export async function held(deps: DeliverNowDeps, session: string) {
  const token = deps.token();
  if (!token) return { status: 502, body: { ok: false, error: 'gateway token unavailable' } };
  return deps.fetchJson(`${deps.gatewayUrl()}/held?session=${encodeURIComponent(session)}`,
    { method: 'GET', headers: { Authorization: `Bearer ${token}` } }, 10000);
}

export async function deliverNow(deps: DeliverNowDeps, session: string) {
  const token = deps.token();
  if (!token) return { status: 502, body: { ok: false, error: 'gateway token unavailable' } };
  return deps.fetchJson(`${deps.gatewayUrl()}/deliver-now`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ session }),
  }, 10000);
}
