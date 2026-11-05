/**
 * One message to a seat through the watch gateway's POST /agent-message: the only path that delivers text to a seat
 * whatever it runs on. On a headless seat the gateway hands it to the seat's supervisor (its pane holds the
 * supervisor, not the CLI, so keys typed there reach no one); on a tmux seat it is the verified inject, which never
 * types over someone's draft and confirms the submit. A raw `tmux send-keys` does neither.
 *
 * `actor` is the X-Actor class:name the gateway attributes and gates on ("automation:<what>" for a machine sender, so a
 * protected session refuses it there, as the gateway intends); `source: 'mail'` says it is not a person's own words.
 */
import { gatewayTokenFile } from './gateway-token.js';
import { gatewayUrl } from './gateway-url.js';
import { readFileSync } from 'fs';

export interface GatewayMessageResult { status: number; ok: boolean; delivered: boolean; body: Record<string, unknown>; error?: string }

export interface GatewayMessageDeps {
  fetch: typeof fetch;
  token: () => string;
  url: () => string;
}

export const defaultGatewayMessageDeps: GatewayMessageDeps = {
  fetch: (...a) => fetch(...a),
  token: () => { try { return readFileSync(gatewayTokenFile(), 'utf-8').trim(); } catch { return ''; } },
  url: () => gatewayUrl(),
};

/** Never throws: no token, no answer or a bad answer comes back as ok:false with the reason. */
export async function gatewayAgentMessage(session: string, text: string,
  opts: { actor: string; source?: 'mail' | 'person'; force?: boolean; timeoutMs?: number },
  deps: GatewayMessageDeps = defaultGatewayMessageDeps): Promise<GatewayMessageResult> {
  const token = deps.token();
  if (!token) return { status: 0, ok: false, delivered: false, body: {}, error: 'gateway token unavailable' };
  try {
    const resp = await deps.fetch(`${deps.url()}/agent-message`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'X-Actor': opts.actor },
      body: JSON.stringify({ session, text, ...(opts.source ? { source: opts.source } : {}), ...(opts.force ? { force: true } : {}) }),
      signal: AbortSignal.timeout(opts.timeoutMs ?? 25000),
    });
    const body = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
    return { status: resp.status, ok: !!body.ok, delivered: !!body.delivered, body };
  } catch (err: any) {
    return { status: 0, ok: false, delivered: false, body: {}, error: err?.message || 'gateway unreachable' };
  }
}
