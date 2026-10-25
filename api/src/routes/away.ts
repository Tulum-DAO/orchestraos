/**
 * The "While you were away" card for the web dashboard (RFC 0002, docs/rfcs/0002-away-card.md).
 *
 *   GET  /api/away?anchor=action|screen  -> gateway GET /away
 *   POST /api/presence {client_id, label}  -> gateway POST /presence {"foreground": true, "surface": "web", ...}
 *
 * Thin bearer-holding forwarders: the browser never sees the gateway token, and the web dashboard carries no
 * device token, so presence is keyed on surface "web" + the browser's own client_id (webos-dev's option a).
 * Only foreground, surface, client_id and label are forwarded; "viewing" never rides this route, so a
 * presence report from here can never move park-idle's focus.
 */
import { Router } from 'express';
import { readGatewayToken } from '../lib/gateway-token.js';
import { gatewayUrl } from '../lib/gateway-url.js';

export interface AwayDeps {
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
}

export function defaultAwayDeps(): AwayDeps {
  return {
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
  };
}

const ANCHORS = new Set(['action', 'screen']);

function cut(v: unknown, n: number): string | undefined {
  if (typeof v !== 'string') return undefined;
  const s = v.trim().slice(0, n);
  return s || undefined;
}

export function createAwayRouter(deps: AwayDeps = defaultAwayDeps()): Router {
  const router = Router();

  async function forward(res: any, path: string, init: RequestInit) {
    const token = deps.token();
    if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
    try {
      const { status, body } = await deps.fetchJson(`${deps.gatewayUrl()}${path}`, {
        ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
      }, 10000);
      res.status(status).json(body);
    } catch (err: any) {
      res.status(502).json({ ok: false, error: 'gateway unreachable', detail: err?.message });
    }
  }

  router.get('/away', async (req, res) => {
    const anchor = String(req.query.anchor || 'action');
    if (!ANCHORS.has(anchor)) { res.status(400).json({ ok: false, error: 'anchor must be action or screen' }); return; }
    await forward(res, `/away?anchor=${anchor}`, { method: 'GET' });
  });

  router.post('/presence', async (req, res) => {
    const b = (req.body && typeof req.body === 'object') ? req.body : {};
    const body = { foreground: true, surface: 'web', client_id: cut(b.client_id, 64), label: cut(b.label, 64) };
    await forward(res, '/presence', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  });

  return router;
}

export default createAwayRouter();
