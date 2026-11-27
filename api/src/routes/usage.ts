/**
 * Plan usage per provider for the web dashboard (scripts/usage_meter.py, via the gateway), and the Claude cards'
 * "Use this account for all agents" (lib/accountSwitch.ts).
 *
 *   GET  /api/usage           -> gateway GET /usage   {ok, usage: [{provider, account, window, used_pct,
 *                                                     resets_at, as_of, stale, source, reason}], as_of}
 *   GET  /api/usage/accounts  {ok, accounts: null | {provider, shared, followers, terms_ack, terms_text,
 *                                                    accounts: [{id, name, where, agents, canUse, whyNot}]}}
 *                             ?provider=codex: the Codex cards (account_switch.py --provider codex --holders);
 *                             any provider other than claude/codex is a 400.
 *   POST /api/usage/account   {account, ack_terms?} -> agent-orchestra account_switch.py --shared <account>:
 *                             every agent on the shared ~/.claude moves to that login at its next model call.
 *                             {provider: "codex", account, ack_terms?} -> --provider codex --shared <account>: new
 *                             Codex sessions use that login; running ones keep theirs until their next start.
 *
 * A thin bearer-holding forwarder like away.ts for the reading: the browser never sees the gateway token. The plan
 * belongs to the operator, so only an admin principal gets any of it; anyone else (a client user behind the sign-in
 * proxy) gets the same 404 as an unknown route, never a hint that it exists.
 *
 * The write is refused when another site started it (lib/cross-site-write.ts: Sec-Fetch-Site cross-site/same-site,
 * or, from a browser that sends none, an Origin the CORS rule would not accept), and it takes only Content-Type
 * application/json (415), which a plain cross-site form cannot send. A Claude press never names a Codex login.
 */
import express, { Router, type Request } from 'express';
import { readGatewayToken } from '../lib/gateway-token.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { actingAgent, tenantScope } from '../lib/principal.js';
import { isCrossSiteWrite, isForeignOriginWrite } from '../lib/cross-site-write.js';
import { configuredOrigins, originDecision } from '../lib/cors-origin.js';
import { dataDir as configDataDir, loadConfig } from '../lib/config.js';
import {
  CODEX_TERMS_LINE, loadAccountsView, loadCodexAccountsView, LABEL_RE, recordSwitchBy, recordTermsAck, registryProvider, serialised,
  switchAllAgents, type AccountsView, type CodexAccountsView, type Provider, type RunCli, type SwitchAnswer, termsAcknowledged, TERMS_LINE,
} from '../lib/accountSwitch.js';

export interface UsageDeps {
  token: () => string;
  gatewayUrl: () => string;
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  isAdmin: (req: any) => boolean;
  /** The CORS layer's decision for an Origin (same-origin, or allowlisted). */
  originAllowed?: (origin: string, req: Request) => boolean;
  /** The data dir (registry, ledger, terms acknowledgement). */
  dataDir?: () => string;
  /** The Claude cards' state (lib/accountSwitch.ts loadAccountsView: account_switch.py --holders + the registry). */
  accounts?: (dataDir: string) => Promise<AccountsView | null>;
  /** Runs account_switch.py --shared <account> and words its answer. */
  switchAll?: (account: string, dataDir: string, names: Record<string, string>) => Promise<SwitchAnswer>;
  /** The Codex cards (loadCodexAccountsView: account_switch.py --provider codex --holders). */
  codexAccounts?: (dataDir: string) => Promise<CodexAccountsView | null>;
  /** Runs account_switch.py --provider codex --shared <account> and words its answer. */
  switchCodex?: (account: string, dataDir: string, names: Record<string, string>) => Promise<SwitchAnswer>;
  /** The CLI runner the default Codex reader/switch use (tests stub it). */
  run?: RunCli;
}

/** ?provider / body.provider: absent = claude; anything but claude/codex is null (a 400). */
function providerOf(v: unknown): Provider | null {
  if (v === undefined) return 'claude';
  return v === 'claude' || v === 'codex' ? v : null;
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
    originAllowed: defaultOriginAllowed,
    dataDir: configDataDir,
    accounts: (d) => loadAccountsView({ dataDir: d }),
    switchAll: (account, d, names) => switchAllAgents(account, { dataDir: d, names }),
  };
}

/** Same inputs as server.ts's CORS rule: x-forwarded-host only from a loopback peer (the dashboard proxy). */
export function defaultOriginAllowed(origin: string, req: Request): boolean {
  const peer = req.socket?.remoteAddress || '';
  const peerIsLocal = peer === '127.0.0.1' || peer === '::1' || peer === '::ffff:127.0.0.1';
  let allowlist: string[];
  try { allowlist = configuredOrigins(loadConfig()); }       // the same list server.ts's CORS layer uses
  catch { allowlist = configuredOrigins({ dashboardHost: '127.0.0.1', dashboardPort: 0, publicHost: '' }); }
  return originDecision({
    origin,
    forwardedHost: peerIsLocal ? String(req.headers['x-forwarded-host'] || '') || undefined : undefined,
    hostHeader: req.headers.host,
    allowlist,
  }).allow;
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

  const dd = () => (deps.dataDir || configDataDir)();
  const view = (d: string) => (deps.accounts || ((x: string) => loadAccountsView({ dataDir: x })))(d);
  const codexView = (d: string) => (deps.codexAccounts || ((x: string) => loadCodexAccountsView({ dataDir: x, run: deps.run })))(d);
  const viewOf = (p: Provider, d: string): Promise<AccountsView | CodexAccountsView | null> => (p === 'codex' ? codexView(d) : view(d));
  // The cards are read by the Usage page, the sidebar line and each agent header: one --holders run per 10 s at most,
  // per provider.
  const cache = new Map<Provider, { at: number; d: string; v: AccountsView | CodexAccountsView | null }>();
  router.get('/usage/accounts', async (req, res) => {
    if (!deps.isAdmin(req)) { res.status(404).json({ ok: false, error: 'not found' }); return; }
    const p = providerOf(req.query.provider);
    if (!p) { res.status(400).json({ ok: false, error: 'unknown provider' }); return; }
    try {
      const d = dd();
      let c = cache.get(p);
      if (!c || c.d !== d || Date.now() - c.at > 10_000) { c = { at: Date.now(), d, v: await viewOf(p, d) }; cache.set(p, c); }
      res.json({ ok: true, accounts: c.v });
    } catch (err: any) {
      res.status(500).json({ ok: false, error: 'accounts unavailable' });
    }
  });

  const json = express.json({ limit: '4kb' });
  router.post('/usage/account', json, async (req: Request, res: any) => {
    if (!deps.isAdmin(req)) { res.status(404).json({ ok: false, error: 'not found' }); return; }
    const site = req.headers['sec-fetch-site'];
    const origin = typeof req.headers.origin === 'string' ? req.headers.origin : undefined;
    const allowed = deps.originAllowed || defaultOriginAllowed;
    if (isCrossSiteWrite(req.method, site) || isForeignOriginWrite(req.method, site, origin, (o) => allowed(o, req))) {
      res.status(403).json({ ok: false, error: 'cross-site write refused' }); return;
    }
    if (!req.is('application/json')) { res.status(415).json({ ok: false, error: 'Content-Type must be application/json' }); return; }
    const b = req.body && typeof req.body === 'object' ? req.body : {};
    const provider = providerOf(b.provider);
    if (!provider) { res.status(400).json({ ok: false, error: 'unknown provider' }); return; }
    const account = b.account;
    if (typeof account !== 'string' || !LABEL_RE.test(account)) {
      res.status(400).json({ ok: false, error: 'account (an account name) is required' }); return;
    }
    const d = dd();
    if (provider === 'codex') return pressCodex(req, res, b, account, d);
    const refuse = async (reason: string, message: string, extra: Record<string, unknown> = {}) =>
      res.status(409).json({ ok: false, status: 'refused', reason, account, message, ...extra, accounts: await view(d).catch(() => null) });
    const v = await view(d).catch(() => null);
    if (!v) return refuse('single-account', 'There is only one Claude account, so there is nothing to choose.');
    const card = v.accounts.find((a) => a.id === account);
    if (!card) {
      const prov = registryProvider(d, account);
      if (prov && prov !== 'claude') return refuse('not-claude', 'Only Claude accounts can be chosen here. Codex stays on its own login.');
      return refuse('unknown-account', `No enrolled Claude account is called ${account}.`);
    }
    if (!termsAcknowledged(d)) {
      if (b.ack_terms !== true) return refuse('terms-not-acknowledged', TERMS_LINE, { terms_text: TERMS_LINE });
      recordTermsAck(d, 'dashboard');
    }
    const names = Object.fromEntries(v.accounts.map((a) => [a.id, a.name]));
    const run = deps.switchAll || ((a: string, x: string, n: Record<string, string>) => switchAllAgents(a, { dataDir: x, names: n }));
    try {
      const out = await serialised(() => run(account, d, names));
      cache.delete('claude');                                 // the next read shows where the logins are now
      if (out.body.status === 'switched') {
        try { recordSwitchBy(d, account, `${actingAgent(req) || 'operator'} (dashboard)`); } catch { /* the top line says "by hand" */ }
      }
      res.status(out.http).json({ ...out.body, accounts: await view(d).catch(() => null) });
    } catch (err: any) {
      res.status(502).json({ ok: false, status: 'unknown', reason: 'switch-error', account,
        message: 'The account switch failed to run. The cards re-read where each login is.', accounts: await view(d).catch(() => null) });
    }
  });

  /** The Codex card's press: the same flow as Claude's, on the Codex login and its own terms acknowledgement. */
  async function pressCodex(req: Request, res: any, b: any, account: string, d: string) {
    const refuse = async (reason: string, message: string, extra: Record<string, unknown> = {}) =>
      res.status(409).json({ ok: false, status: 'refused', reason, account, message, ...extra, accounts: await codexView(d).catch(() => null) });
    const v = await codexView(d).catch(() => null);
    if (!v) return refuse('single-account', 'There is only one Codex account, so there is nothing to choose.');
    if (!v.accounts.some((a) => a.id === account)) return refuse('unknown-account', `No Codex account is called ${account}.`);
    if (!termsAcknowledged(d, 'codex')) {
      if (b.ack_terms !== true) return refuse('terms-not-acknowledged', CODEX_TERMS_LINE, { terms_text: CODEX_TERMS_LINE });
      recordTermsAck(d, 'dashboard', new Date(), 'codex');
    }
    const names = Object.fromEntries(v.accounts.map((a) => [a.id, a.name]));
    const run = deps.switchCodex
      || ((a: string, x: string, n: Record<string, string>) => switchAllAgents(a, { dataDir: x, names: n, run: deps.run, provider: 'codex' }));
    try {
      const out = await serialised(() => run(account, d, names));
      cache.delete('codex');
      if (out.body.status === 'switched') {
        try { recordSwitchBy(d, account, `${actingAgent(req) || 'operator'} (dashboard)`, new Date(), 'codex'); } catch { /* "by hand" */ }
      }
      res.status(out.http).json({ ...out.body, accounts: await codexView(d).catch(() => null) });
    } catch (err: any) {
      res.status(502).json({ ok: false, status: 'unknown', reason: 'switch-error', account,
        message: 'The account switch failed to run. The cards re-read where each login is.', accounts: await codexView(d).catch(() => null) });
    }
  }
  return router;
}

export default createUsageRouter();
