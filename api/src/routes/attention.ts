/**
 * attention.ts — the shared attention list on the web: what is waiting on the person, the Updates
 * record (done, failed, stuck, missed calls, on-behalf answers) and what was answered, plus seen
 * marks, a live-update stream and the timeout rules.
 *
 * Everything is the gateway's: GET /attention, POST /attention/seen, GET /events and
 * /attention/rules[/{id}], forwarded with the server bearer (the questionnaires.ts / away.ts
 * pattern, so the browser never sees the token and every surface reads one list). This router
 * adds only what the gateway cannot know, which is who is calling:
 * - a member (anyone but an unscoped admin) names the agents they may see as `agents=`, so the
 *   gateway narrows every tab BEFORE limits and counts; a member never gets a short page or the
 *   fleet's totals. The API then filters again by the same card rule (lib/approval-scope), in case
 *   a gateway ever ignores the filter;
 * - seen marks carry a surface the server picks (web, or chat when the web says so); a client
 *   cannot claim to be the phone or the watch, and ids it may not see are dropped first;
 * - the stream says only "something changed, refetch" with the attention rev; the gateway's
 *   global pending count is never passed on, so a scoped caller learns nothing from it;
 * - rules scoped to one agent may be written by anyone who can see that agent; person-wide rules
 *   (no scope_agent) only by an unscoped admin.
 */
import { Router, type Request, type Response } from 'express';
import { readGatewayToken } from '../lib/gateway-token.js';
import { gatewayUrl } from '../lib/gateway-url.js';
import { principal as defaultPrincipal, type Principal } from '../lib/principal.js';
import { canPrincipalSeeCard, isUnscopedAdmin } from '../lib/approval-scope.js';
import { defaultResolveTags } from '../lib/agent-scope.js';
import { getRegistry } from '../services/state-reader.js';

export interface AttentionStream {
  status: number;
  contentType: string;
  body: ReadableStream<Uint8Array> | null;
  json: () => Promise<any>;
}

export interface AttentionDeps {
  /** The gateway bearer. '' means unavailable: the gateway is never called without it. */
  token: () => string;
  gatewayUrl: () => string;
  /** One authed JSON round trip; returns the upstream status so it can be passed through. */
  fetchJson: (url: string, init: RequestInit, timeoutMs: number) => Promise<{ status: number; body: any }>;
  /** Open a GET stream (the gateway's SSE). The signal is aborted when the web client goes away.
   *  Its own seam because arturo's pipeStream is a POST with a body and a fixed timeout, and an
   *  SSE stream must stay open for as long as the page does. */
  pipeStream: (url: string, headers: Record<string, string>, signal: AbortSignal) => Promise<AttentionStream>;
  /** Who is calling (lib/principal): null = no identity, a 401 on every route here. */
  principal: (req: Request) => Principal | null;
  resolveTags: (id: string) => unknown;
  /** Every agent id the install knows (the registry). A member's `agents=` list is drawn from it. */
  agentIds: () => string[];
}

export function defaultAttentionDeps(): AttentionDeps {
  return {
    token: readGatewayToken,
    gatewayUrl: () => gatewayUrl(),
    fetchJson: async (url, init, timeoutMs) => {
      const r = await fetch(url, { ...init, signal: AbortSignal.timeout(timeoutMs) });
      const body = await r.json().catch(() => ({ ok: false, error: 'bad gateway json' }));
      return { status: r.status, body };
    },
    pipeStream: async (url, headers, signal) => {
      const r = await fetch(url, { method: 'GET', headers, signal });
      return { status: r.status, contentType: r.headers.get('content-type') || '', body: r.body,
        json: async () => r.json().catch(() => ({ ok: false, error: 'bad gateway json' })) };
    },
    principal: defaultPrincipal,
    resolveTags: defaultResolveTags,
    agentIds: () => {
      const registry = getRegistry() as { agents?: Record<string, unknown> } | null;
      return Object.keys(registry?.agents ?? {});
    },
  };
}

/** The query params the gateway's GET /attention takes from a client. `agents` is never one of them. */
const LIST_PARAMS = ['tab', 'limit_updates', 'limit_answered', 'since'] as const;

const UNREACHABLE = { ok: false, error: 'gateway unreachable' };
const FORBIDDEN = { ok: false, error: 'forbidden' };

/** The counts the gateway sends, over whatever items are left. */
export function countsOf(items: any[]): { waiting: number; waiting_blocking: number; updates_unread: number } {
  const waiting = items.filter((i) => i?.tab === 'waiting');
  return {
    waiting: waiting.length,
    waiting_blocking: waiting.filter((i) => i?.blocking === true).length,
    updates_unread: items.filter((i) => i?.tab === 'update' && i?.seen_at == null).length,
  };
}

export function createAttentionRouter(deps: AttentionDeps = defaultAttentionDeps()): Router {
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

  /** The agents a member may see, or null for an unscoped admin (no filter at all). The registry's
   *  ids plus an explicit allowlist's own entries, each checked by the card rule, so an allowlisted
   *  agent missing from the registry is still named. */
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
    canPrincipalSeeCard(p, typeof agent === 'string' ? agent : null, deps.resolveTags);

  /** May this principal write a rule scoped to `scopeAgent`? Person-wide (none) is admin-only. */
  function mayWriteScope(p: Principal, scopeAgent: unknown): boolean {
    if (isUnscopedAdmin(p)) return true;
    if (typeof scopeAgent !== 'string' || !scopeAgent) return false;
    return sees(p, scopeAgent);
  }

  function who(req: Request, res: Response): Principal | null {
    const p = deps.principal(req);
    if (!p) res.status(401).json({ ok: false, error: 'unauthenticated' });
    return p;
  }

  // GET /api/attention — the list. The client's own agents= is never forwarded: for a member the
  // server writes it, for an unscoped admin there is none.
  router.get('/', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const qs = new URLSearchParams();
    for (const k of LIST_PARAMS) {
      const v = req.query[k];
      const s = Array.isArray(v) ? v[0] : v;
      if (typeof s === 'string' && s !== '') qs.set(k, s);
    }
    const agents = visibleAgents(p);
    if (agents && agents.length === 0) {
      // Never send an empty agents= (a gateway could read it as "no filter"): nothing is visible.
      res.json({ ok: true, items: [], counts: countsOf([]) });
      return;
    }
    if (agents) qs.set('agents', agents.join(','));
    const q = qs.toString();
    const g = await gateway(`/attention${q ? `?${q}` : ''}`, { method: 'GET' });
    if (g.status < 200 || g.status >= 300 || !agents || !Array.isArray(g.body?.items)) {
      res.status(g.status).json(g.body);
      return;
    }
    // Defence in depth: the gateway filtered already; anything it let through out of scope goes,
    // and then the counts it sent describe items the caller does not get, so they are recomputed.
    const items = g.body.items.filter((i: any) => sees(p, i?.agent));
    if (items.length === g.body.items.length) { res.status(g.status).json(g.body); return; }
    res.status(g.status).json({ ...g.body, items, counts: countsOf(items) });
  });

  // POST /api/attention/seen — {ids, via?}. The surface is the server's: web, or chat when the web
  // says the mark came from a chat thread. A member's ids are checked against the items they can
  // see (the same scoped list GET / serves, at the gateway's largest limits); the rest are dropped
  // and reported as unknown, exactly as the gateway reports an id that does not exist.
  router.post('/seen', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const ids = req.body?.ids;
    if (!Array.isArray(ids) || !ids.every((x) => typeof x === 'string' && x)) {
      res.status(400).json({ ok: false, error: 'ids must be a list of item ids' });
      return;
    }
    const surface = req.body?.via === 'chat' ? 'chat' : 'web';
    let keep: string[] = ids;
    const agents = visibleAgents(p);
    if (agents) {
      let visible = new Set<string>();
      if (agents.length) {
        const qs = new URLSearchParams({ tab: 'waiting,update,answered', limit_updates: '500',
          limit_answered: '200', agents: agents.join(',') });
        const g = await gateway(`/attention?${qs}`, { method: 'GET' });
        if (g.status < 200 || g.status >= 300) { res.status(g.status).json(g.body); return; }
        visible = new Set((Array.isArray(g.body?.items) ? g.body.items : [])
          .filter((i: any) => sees(p, i?.agent)).map((i: any) => String(i.id)));
      }
      keep = ids.filter((id) => visible.has(id));
    }
    const dropped = ids.filter((id) => !keep.includes(id)).map((id) => ({ id, why: 'unknown_id' }));
    if (!keep.length) { res.json({ ok: true, seen: 0, accepted_cards: [], skipped: dropped }); return; }
    const g = await gateway('/attention/seen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: keep, surface }),
    });
    if (dropped.length && g.status >= 200 && g.status < 300 && g.body && typeof g.body === 'object') {
      res.status(g.status).json({ ...g.body, skipped: [...(Array.isArray(g.body.skipped) ? g.body.skipped : []), ...dropped] });
      return;
    }
    res.status(g.status).json(g.body);
  });

  // GET /api/attention/stream — the gateway's /events SSE, re-emitted as "changed" + attention_rev
  // only. Keepalive comments pass through so proxies keep the connection open. The client going
  // away aborts the upstream read, so an open tab never leaks a gateway stream.
  router.get('/stream', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const token = deps.token();
    if (!token) { res.status(502).json({ ok: false, error: 'gateway token unavailable' }); return; }
    const ctl = new AbortController();
    let closed = false;
    res.on('close', () => { closed = true; ctl.abort(); });
    try {
      const up = await deps.pipeStream(`${deps.gatewayUrl()}/events`, {
        Authorization: `Bearer ${token}`, Accept: 'text/event-stream',
      }, ctl.signal);
      if (up.status !== 200 || !up.contentType.includes('text/event-stream')) {
        // A refusal is ordinary JSON: the client never opened a stream.
        res.status(up.status).json(await up.json());
        return;
      }
      res.status(200);
      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('X-Accel-Buffering', 'no');
      res.setHeader('Connection', 'keep-alive');
      res.flushHeaders?.();
      if (!up.body) { res.end(); return; }
      const reader = up.body.getReader();
      res.on('close', () => { void reader.cancel().catch(() => {}); });
      if (closed) void reader.cancel().catch(() => {});
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done || closed) break;
        buf += decoder.decode(value, { stream: true });
        // One SSE event ends at a blank line; a chunk may hold half of one.
        let at: RegExpExecArray | null;
        while ((at = /\r?\n\r?\n/.exec(buf))) {
          const block = buf.slice(0, at.index);
          buf = buf.slice(at.index + at[0].length);
          const out = relayBlock(block);
          if (out) res.write(out);
        }
      }
      res.end();
    } catch {
      if (res.headersSent) { res.end(); return; }
      if (!closed) res.status(502).json(UNREACHABLE);
    }
  });

  // ---- timeout rules: /api/attention/rules[/:id] -> the gateway's /attention/rules[/{id}] ----

  /** The person's rules as the gateway stores them, for checking a stored rule's scope. */
  async function storedRule(id: string): Promise<{ status: number; body: any; rule: any | null }> {
    const g = await gateway('/attention/rules', { method: 'GET' });
    const rules = Array.isArray(g.body?.rules) ? g.body.rules : [];
    return { ...g, rule: rules.find((r: any) => r?.id === id) ?? null };
  }

  const json = (body: unknown): RequestInit => ({
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}),
  });

  // A member reads the rules for agents they can see, plus the person-wide ones (read-only to them,
  // shown so the Settings page can say what applies to their agents).
  router.get('/rules', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const g = await gateway('/attention/rules', { method: 'GET' });
    if (isUnscopedAdmin(p) || g.status < 200 || g.status >= 300 || !Array.isArray(g.body?.rules)) {
      res.status(g.status).json(g.body);
      return;
    }
    const rules = g.body.rules.filter((r: any) => r?.scope_agent == null || r.scope_agent === '' || sees(p, r.scope_agent));
    res.status(g.status).json({ ...g.body, rules });
  });

  router.post('/rules', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    if (!mayWriteScope(p, req.body?.scope_agent)) { res.status(403).json(FORBIDDEN); return; }
    const g = await gateway('/attention/rules', { method: 'POST', ...json(req.body) });
    res.status(g.status).json(g.body);
  });

  router.put('/rules/:id', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const id = String(req.params.id);
    if (!isUnscopedAdmin(p)) {
      // Both scopes count: the one the rule has now (else a member could move someone else's
      // rule onto their own agent) and the one the body would give it.
      if (req.body && Object.prototype.hasOwnProperty.call(req.body, 'scope_agent')
        && !mayWriteScope(p, req.body.scope_agent)) { res.status(403).json(FORBIDDEN); return; }
      const s = await storedRule(id);
      if (s.status < 200 || s.status >= 300) { res.status(s.status).json(s.body); return; }
      if (s.rule && !mayWriteScope(p, s.rule.scope_agent)) { res.status(403).json(FORBIDDEN); return; }
    }
    const g = await gateway(`/attention/rules/${encodeURIComponent(id)}`, { method: 'PUT', ...json(req.body) });
    res.status(g.status).json(g.body);
  });

  router.delete('/rules/:id', async (req: Request, res: Response) => {
    const p = who(req, res);
    if (!p) return;
    const id = String(req.params.id);
    if (!isUnscopedAdmin(p)) {
      const s = await storedRule(id);
      if (s.status < 200 || s.status >= 300) { res.status(s.status).json(s.body); return; }
      if (s.rule && !mayWriteScope(p, s.rule.scope_agent)) { res.status(403).json(FORBIDDEN); return; }
    }
    const g = await gateway(`/attention/rules/${encodeURIComponent(id)}`, { method: 'DELETE' });
    res.status(g.status).json(g.body);
  });

  return router;
}

/**
 * One upstream SSE event, re-emitted. A comment (": keepalive") passes through; a data event with
 * changed:true becomes {changed, attention_rev, ts} and nothing else; anything else is dropped.
 */
export function relayBlock(block: string): string | null {
  const lines = block.split(/\r?\n/);
  if (lines.length && lines.every((l) => l.startsWith(':'))) return `${lines.join('\n')}\n\n`;
  const data = lines.filter((l) => l.startsWith('data:')).map((l) => l.slice(5).replace(/^ /, '')).join('\n');
  if (!data) return null;
  let ev: any;
  try { ev = JSON.parse(data); } catch { return null; }
  if (!ev || ev.changed !== true) return null;
  const out = {
    changed: true,
    attention_rev: typeof ev.attention_rev === 'number' ? ev.attention_rev : null,
    ts: typeof ev.ts === 'number' ? ev.ts : null,
  };
  return `data: ${JSON.stringify(out)}\n\n`;
}
