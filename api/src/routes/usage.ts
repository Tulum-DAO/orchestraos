/**
 * Plan usage per provider for the web dashboard (scripts/usage_meter.py, via the gateway).
 *
 *   GET /api/usage -> gateway GET /usage   {ok, usage: [{provider, account, window, used_pct,
 *                                           resets_at, as_of, stale, source, reason}], as_of}
 *
 * A thin bearer-holding forwarder like away.ts: the browser never sees the gateway token. The plan
 * belongs to the operator, so only an admin principal gets it; anyone else (a client user behind
 * the sign-in proxy) gets the same 404 as an unknown route, never a hint that it exists.
 */
import { Router } from 'express';
import { readGatewayToken } from '../lib/gateway-token.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { tenantScope } from '../lib/principal.js';

export interface UsageDeps {
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  isAdmin: (req: any) => boolean;
}

export function defaultUsageDeps(): UsageDeps {
  return {
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    isAdmin: (req) => tenantScope(req).isAdmin,
  };
}

export function createUsageRouter(deps: UsageDeps = defaultUsageDeps()): Router {
  const router = Router();
  router.get('/usage', async (req, res) => {
    if (!deps.isAdmin(req)) { res.status(404).json({ ok: false, error: 'not found' }); return; }
    const token = deps.token();
    if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
    try {
      const { status, body } = await deps.fetchJson(`${deps.gatewayUrl()}/usage`, {
        method: 'GET', headers: { Authorization: `Bearer ${token}` },
      }, 10000);
      res.status(status).json(body);
    } catch (err: any) {
      res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err?.message });
    }
  });
  return router;
}

export default createUsageRouter();
