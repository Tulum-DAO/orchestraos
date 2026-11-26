/**
 * Dismiss a pending card without answering it: the web's way to the gateway's POST /approvals/:id/discard,
 * which iOS already uses. The gateway keeps the card in history as 'discarded' and tells the asking agent
 * not to keep waiting on it, so the web adds no state change of its own here.
 *
 * Every card kind takes it (approval, task for you, menu; operator order 2026-11-25). A menu that IS on an
 * agent's screen is dismissed with Escape through /api/agents/:id/agent-key instead, so the pane and the card agree.
 */
import { readGatewayToken } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';

export interface DismissDeps {
  token: () => string;
  /** `body` is the JSON the gateway reads (where / why); '{}' when the caller said nothing. */
  post: (url: string, token: string, body?: string) => Promise<{ status: number; body: any }>;
  base: () => string;
}

export interface DismissResult { status: number; body: Record<string, unknown> }

/**
 * Where and why a card was dismissed, as the API tells the gateway. `surface` is the API's own word (`web`, this
 * route's edge; no client hint is read); `reason` is the only field a client supplies. WHO dismissed is never sent: the
 * gateway derives it from the bearer it authenticated (R7d), exactly as it does for answered_by, so no client can
 * record that the operator dismissed a card, or that a web dismiss came from the watch.
 */
export interface DismissFields { surface?: 'web'; reason?: string }

export async function dismissCard(id: string, deps: DismissDeps, fields: DismissFields = {}): Promise<DismissResult> {
  const token = deps.token();
  if (!token) return { status: 502, body: { error: 'gateway token unavailable', id } };
  let r: { status: number; body: any };
  try {
    r = await deps.post(`${deps.base()}/approvals/${encodeURIComponent(id)}/discard`, token, JSON.stringify(fields));
  } catch (err: any) {
    return { status: 502, body: { error: 'gateway unreachable', detail: err?.message, id } };
  }
  if (r.status === 404) return { status: 404, body: { error: `Approval item '${id}' not found`, id } };
  // The gateway's own refusal (a menu that is on the agent's screen: "dismiss it on the pane"): its words, its why
  // and the agent reach the client as they are. 423, never 409: the web reads 409 as "already answered or closed".
  if (r.status === 423) {
    return { status: 423, body: { error: r.body?.error || 'This card cannot be dismissed here.', id,
                                  ...(r.body?.why ? { why: r.body.why } : {}), ...(r.body?.agent ? { agent: r.body.agent } : {}) } };
  }
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
    post: async (url, token, body = '{}') => {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body,
        signal: AbortSignal.timeout(10000),
      });
      return { status: resp.status, body: await resp.json().catch(() => ({})) };
    },
  };
}
