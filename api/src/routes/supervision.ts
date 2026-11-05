/**
 * supervision.ts — supervision levels, roles, standing grants and the safety gate on the web.
 *
 * Everything is the gateway's: GET/PUT /supervision[...], /supervision/roles[...], /grants[...],
 * /gate/mode, /gate/log and /gate/holds/{id}, forwarded with the server bearer (the attention.ts
 * pattern, so the browser never sees the token). This router adds only who is calling:
 * - a member (anyone but an unscoped admin) reads the supervision page for the agents they may
 *   see: the gateway gets `agents=<visible>`, and the API filters the agents and projects again by
 *   the card rule (lib/approval-scope), in case a gateway ever ignores the filter;
 * - a member may set a level, an override or an existing role on an agent they can see, and add,
 *   narrow or revoke agent and session grants for those agents;
 * - anything that changes more than one agent (the person's default, a project's default role,
 *   creating/editing/deleting a role, role/project/fleet grants, the gate mode and its log) is for
 *   an unscoped admin only;
 * - PATCH/DELETE of a grant are checked against the grant's scope as the gateway stores it, fetched
 *   first, so a member cannot touch someone else's grant by id.
 * Errors: no principal 401; out of scope 403 {ok:false, error:'not_allowed'}; gateway down 502;
 * the gateway's own 4xx bodies pass through unchanged.
 */
import { Router, type Request, type Response } from 'express';
import { readGatewayToken } from '../lib/gateway-token.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { principal as defaultPrincipal, installRole, type InstallRole, type Principal } from '../lib/principal.js';
import { canPrincipalSeeCard, isUnscopedAdmin } from '../lib/approval-scope.js';
import { defaultResolveTags } from '../lib/agent-scope.js';
import { getRegistry } from '../services/state-reader.js';

export interface SupervisionDeps {
  /** The gateway bearer. '' means unavailable: the gateway is never called without it. */
  token: () => string;
  gatewayUrl: () => string;
  /** One authed JSON round trip; returns the upstream status so it can be passed through. */
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  /** Who is calling (lib/principal): null = no identity, a 401 on every route here. */
  principal: (req: Request) => Principal | null;
  resolveTags: (id: string) => unknown;
  /** Every agent id the install knows (the registry). A member's `agents=` list is drawn from it. */
  agentIds: () => string[];
  /** The caller's role on the install (lib/principal installRole, the Tailscale login's role), as every other
   *  admin-only route reads it. Admin-only here = an unscoped principal AND an install admin: without an
   *  authenticating proxy every caller's principal is the unscoped operator, so the principal alone is no check. */
  role: (req: Request) => InstallRole;
}

/** A wide grant as a member sees it: what it allows, not who made it or why. */
export function redactWideGrant(g: any): any {
  if (!g || typeof g !== 'object') return g;
  const { said: _s, source_card: _c, proposed_by: _p, created_by: _b, ...rest } = g;
  return rest;
}

export function defaultSupervisionDeps(): SupervisionDeps {
  return {
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    principal: defaultPrincipal,
    role: (req) => installRole(req),
    resolveTags: defaultResolveTags,
    agentIds: () => {
      const registry = getRegistry() as { agents?: Record<string, unknown> } | null;
      return Object.keys(registry?.agents ?? {});
    },
  };
}

const UNREACHABLE = { ok: false, error: 'gateway unreachable' };
export const NOT_ALLOWED = { ok: false, error: 'not_allowed' };

/** The grant scopes a member may write, on an agent they can see. */
const MEMBER_GRANT_SCOPES = new Set(['agent', 'session']);
/** GET /grants query params passed through. */
const GRANT_PARAMS = ['agent', 'scope', 'state'] as const;
/** GET /gate/log query params passed through. */
const LOG_PARAMS = ['limit', 'agent'] as const;

export function createSupervisionRouter(deps: SupervisionDeps = defaultSupervisionDeps()): Router {
  const router = Router();

  async function gateway(path: string, init: RequestInit): Promise<{ status: number; body: any }> {
    const token = deps.token();
    if (!token) return { status: 502, body: { ok: false, error: 'gateway token unavailable' } };
    try {
      return await deps.fetchJson(`${deps.gatewayUrl()}${path}`, {
        ...init, headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
      }, 10000);
    } catch {
      return { status: 502, body: UNREACHABLE };
    }
  }

  const ok = (status: number) => status >= 200 && status < 300;
  const send = (res: Response, g: { status: number; body: any }) => { res.status(g.status).json(g.body); };
  const json = (method: string, body: unknown): RequestInit => ({
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
  });

  /** The agents a member may see, or null for an unscoped admin (no filter at all). */
  function visibleAgents(p: Principal): string[] | null {
    if (isUnscopedAdmin(p)) return null;
    const candidates = new Set(deps.agentIds());
    if (!p.clientScope && p.allowedAgents !== '*') {
      const listed = Array.isArray(p.allowedAgents) ? p.allowedAgents : String(p.allowedAgents).split(',');
      for (const id of listed) if (String(id).trim()) candidates.add(String(id).trim());
    }
    return [...candidates].filter((id) => canPrincipalSeeCard(p, id, deps.resolveTags)).sort();
  }

  const sees = (p: Principal, agent: unknown) =>
    typeof agent === 'string' && agent !== '' && canPrincipalSeeCard(p, agent, deps.resolveTags);

  function who(req: Request, res: Response): Principal | null {
    const p = deps.principal(req);
    if (!p) res.status(401).json({ ok: false, error: 'unauthenticated' });
    return p;
  }

  /** An unscoped principal that is also an admin of the install (see SupervisionDeps.role). */
  const isAdmin = (req: Request, p: Principal | null): boolean => isUnscopedAdmin(p) && deps.role(req) === 'admin';

  /** The principal, if it is an admin (isAdmin); otherwise the response is already a 401/403. */
  function admin(req: Request, res: Response): Principal | null {
    const p = who(req, res);
    if (!p) return null;
    if (!isAdmin(req, p)) { res.status(403).json(NOT_ALLOWED); return null; }
    return p;
  }

  /** The agent a session grant belongs to, when the grant or body names one. A session id alone
   *  names no agent, so a member's session grant without one is refused (fail closed). */
  const sessionAgent = (g: any): unknown => g?.agent ?? null;

  /** May this principal write (or see, for a member's listing) a grant of this shape? */
  function mayWriteGrant(req: Request, p: Principal, g: any): boolean {
    if (isAdmin(req, p)) return true;
    const scope = g?.scope;
    if (!MEMBER_GRANT_SCOPES.has(scope)) return false;
    return scope === 'agent' ? sees(p, g?.scope_ref) : sees(p, sessionAgent(g));
  }

  /** A grant as the gateway stores it (any state), for checking its scope before a write. */
  async function storedGrant(id: string): Promise<{ status: number; body: any; grant: any | null }> {
    const g = await gateway('/grants?state=all', { method: 'GET' });
    const grants = Array.isArray(g.body?.grants) ? g.body.grants : [];
    return { ...g, grant: grants.find((x: any) => x?.id === id) ?? null };
  }

  function query(req: Request, keys: readonly string[]): URLSearchParams {
    const qs = new URLSearchParams();
    for (const k of keys) {
      const v = req.query[k];
      const s = Array.isArray(v) ? v[0] : v;
      if (typeof s === 'string' && s !== '') qs.set(k, s);
    }
    return qs;
  }

  // ---- GET /api/supervision ------------------------------------------------------------------
  // The client's own agents= is never forwarded: for a member the server writes it, for an
  // unscoped admin there is none.
  router.get('/', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const agents = visibleAgents(p);
    // An empty agents= could read as "no filter" upstream: a member who sees nobody asks without
    // one and the API's own filter below removes every agent and project.
    const qs = agents && agents.length ? `?${new URLSearchParams({ agents: agents.join(',') })}` : '';
    const g = await gateway(`/supervision${qs}`, { method: 'GET' });
    if (!agents || !ok(g.status) || !g.body || typeof g.body !== 'object') { send(res, g); return; }
    const out = { ...g.body };
    if (Array.isArray(out.agents)) out.agents = out.agents.filter((a: any) => sees(p, a?.agent));
    if (Array.isArray(out.projects)) {
      out.projects = out.projects
        .map((pr: any) => ({ ...pr, agents: Array.isArray(pr?.agents) ? pr.agents.filter((a: any) => sees(p, a)) : [] }))
        .filter((pr: any) => pr.agents.length > 0);
    }
    res.status(g.status).json(out);
  });

  // ---- the person's default, an agent, a project ----------------------------------------------
  router.put('/default', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    send(res, await gateway('/supervision/default', json('PUT', req.body)));
  });

  router.put('/agents/:id', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const id = String(req.params.id);
    // Any member who sees the agent may assign it an existing role (the gateway checks it exists);
    // editing the role itself is a /roles write, admin-only.
    if (!isUnscopedAdmin(p) && !sees(p, id)) { res.status(403).json(NOT_ALLOWED); return; }
    send(res, await gateway(`/supervision/agents/${encodeURIComponent(id)}`, json('PUT', req.body)));
  });

  router.put('/projects/:key', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    const key = String(req.params.key); // express has decoded it; re-encode for the gateway path
    send(res, await gateway(`/supervision/projects/${encodeURIComponent(key)}`, json('PUT', req.body)));
  });

  // ---- roles: reads for anyone, writes for an unscoped admin ----------------------------------
  router.get('/roles', async (req: Request, res: Response) => {
    if (!who(req, res)) return;
    send(res, await gateway('/supervision/roles', { method: 'GET' }));
  });

  router.post('/roles', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    send(res, await gateway('/supervision/roles', json('POST', req.body)));
  });

  router.put('/roles/:id', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    send(res, await gateway(`/supervision/roles/${encodeURIComponent(String(req.params.id))}`, json('PUT', req.body)));
  });

  router.delete('/roles/:id', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    send(res, await gateway(`/supervision/roles/${encodeURIComponent(String(req.params.id))}`, { method: 'DELETE' }));
  });

  router.post('/roles/:id/duplicate', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    send(res, await gateway(`/supervision/roles/${encodeURIComponent(String(req.params.id))}/duplicate`, json('POST', req.body)));
  });

  // ---- grants ----------------------------------------------------------------------------------
  // A member sees the agent and session grants of agents they can see, plus the fleet, role and
  // project grants (read-only to them, listed so Settings can say what applies).
  router.get('/grants', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const qs = query(req, GRANT_PARAMS);
    const member = !isAdmin(req, p);
    if (member && qs.has('agent') && !sees(p, qs.get('agent'))) { res.status(403).json(NOT_ALLOWED); return; }
    const q = qs.toString();
    const g = await gateway(`/grants${q ? `?${q}` : ''}`, { method: 'GET' });
    if (!member || !ok(g.status) || !Array.isArray(g.body?.grants)) { send(res, g); return; }
    // Wide grants (fleet, role, project) are listed so Settings can say what applies, but without their
    // provenance: who made or proposed them, the card they came from and the words quoted on it.
    const grants = g.body.grants
      .filter((x: any) => !MEMBER_GRANT_SCOPES.has(x?.scope) || mayWriteGrant(req, p, x))
      .map((x: any) => (MEMBER_GRANT_SCOPES.has(x?.scope) ? x : redactWideGrant(x)));
    res.status(g.status).json({ ...g.body, grants });
  });

  router.post('/grants', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    if (!mayWriteGrant(req, p, req.body)) { res.status(403).json(NOT_ALLOWED); return; }
    send(res, await gateway('/grants', json('POST', req.body)));
  });

  /** PATCH and DELETE: the stored grant's scope decides (fetched first), never the body's. */
  async function grantWrite(req: Request, res: Response, init: RequestInit): Promise<void> {
    const p = who(req, res);
    if (!p) return;
    const id = String(req.params.id);
    if (!isAdmin(req, p)) {
      const s = await storedGrant(id);
      if (!ok(s.status)) { send(res, s); return; }
      if (!s.grant) { res.status(404).json({ ok: false, error: 'not_found' }); return; }
      if (!mayWriteGrant(req, p, s.grant)) { res.status(403).json(NOT_ALLOWED); return; }
    }
    send(res, await gateway(`/grants/${encodeURIComponent(id)}`, init));
  }

  router.patch('/grants/:id', (req: Request, res: Response) => grantWrite(req, res, json('PATCH', req.body)));
  router.delete('/grants/:id', (req: Request, res: Response) => grantWrite(req, res, { method: 'DELETE' }));

  // ---- the gate ----------------------------------------------------------------------------------
  router.put('/gate/mode', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    send(res, await gateway('/gate/mode', json('PUT', req.body)));
  });

  router.get('/gate/log', async (req: Request, res: Response) => {
    if (!admin(req, res)) return;
    const q = query(req, LOG_PARAMS).toString();
    send(res, await gateway(`/gate/log${q ? `?${q}` : ''}`, { method: 'GET' }));
  });

  // One hold, for a card's "why" line: a member only for an agent they can see.
  router.get('/gate/holds/:id', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const g = await gateway(`/gate/holds/${encodeURIComponent(String(req.params.id))}`, { method: 'GET' });
    if (!isUnscopedAdmin(p) && ok(g.status) && !sees(p, g.body?.agent)) { res.status(403).json(NOT_ALLOWED); return; }
    send(res, g);
  });

  return router;
}
