/**
 * Dismiss a pending card without answering it: the web's way to the gateway's POST /approvals/:id/discard,
 * which iOS already uses. The gateway keeps the card in history as 'discarded' and tells the asking agent
 * not to keep waiting on it, so the web adds no state change of its own here.
 *
 * Used for menus that are not on an agent's screen (an agent's own written decisions). A menu that IS on
 * screen is dismissed with Escape through /api/agents/:id/agent-key instead, so the pane and the card agree.
 */
import { readGatewayToken } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';

export interface DismissDeps {
  token: () => string;
  post: (url: string, token: string) => Promise<{ status: number; body: any }>;
  base: () => string;
}

export interface DismissResult { status: number; body: Record<string, unknown> }

export async function dismissCard(id: string, deps: DismissDeps): Promise<DismissResult> {
  const token = deps.token();
  if (!token) return { status: 502, body: { error: 'gateway token unavailable', id } };
  let r: { status: number; body: any };
  try {
    r = await deps.post(`${deps.base()}/approvals/${encodeURIComponent(id)}/discard`, token);
  } catch (err: any) {
    return { status: 502, body: { error: 'gateway unreachable', detail: err?.message, id } };
  }
  if (r.status === 404) return { status: 404, body: { error: `Approval item '${id}' not found`, id } };
  if (r.status !== 200 || !r.body?.ok) {
    return { status: 502, body: { error: r.body?.error || `gateway refused (${r.status})`, id } };
  }
  // applied=false: the card was answered or closed before the dismiss landed; nothing was changed.
  if (!r.body.applied) return { status: 409, body: { error: 'This card was already answered or closed.', id } };
  return { status: 200, body: { status: 'dismissed', id } };
}

export function defaultDismissDeps(): DismissDeps {
  return {
    token: readGatewayToken,
    base: () => gatewayUrl(),
    post: async (url, token) => {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: '{}',
        signal: AbortSignal.timeout(10000),
      });
      return { status: resp.status, body: await resp.json().catch(() => ({})) };
    },
  };
}
