/**
 * Message reactions (spec docs/superpowers/specs/2026-11-02-message-reactions-design.md, build step 1).
 *
 *   GET    /api/reactions?surface=&conv=          every reaction in one conversation, grouped by message ref:
 *                                                 { surface, conv, reactions: { <ref>: [{ emoji, reactor, at }] } }
 *   PUT    /api/reactions {surface, conv, ref, emoji}
 *                                                 set the caller's reaction; the SAME emoji again removes it.
 *                                                 -> { ok, surface, conv, ref, reactor, emoji|null, at }
 *   DELETE /api/reactions {surface, conv, ref}    (body or query) remove the caller's reaction -> { ok, removed, ... }
 *   GET    /api/reactions/stream?surface=&conv=   SSE `event: reaction` data { surface, conv, ref, reactor, emoji|null, at }
 *                                                 one channel for every surface (transcript-stream is per agent)
 *
 * The reactor is lib/reactor.ts: the principal, or an agent proven by X-Orchestra-Agent + X-Orchestra-Agent-Token.
 * Scope: an agent-chat conversation follows lib/agent-scope.ts (same 404 as the agent's own routes); Arturo threads
 * and calls are admin-only until build step 4 gives them a rule of their own. Untrusted mode (this box): the
 * operator is admin with '*', so both checks pass.
 */
import { Router, type Request, type Response } from 'express';
import { principal, type Principal } from '../lib/principal.js';
import { canPrincipalSeeAgent, defaultResolveTags } from '../lib/agent-scope.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { reactorOf, AGENT_TOKEN_INVALID } from '../lib/reactor.js';
import { reactionStore, SURFACES, type ReactionStore, type Surface, type ReactionChange } from '../lib/reactions-store.js';
import { publishReaction, onReaction } from '../services/reaction-bus.js';

const HEARTBEAT_MS = 25000;
const CONV = /^[\w.:-]{1,200}$/;
/** agent-chat: `<session>~<uuid>#<part>` as the server stamps it (lib/messageRef.ts). */
const AGENT_CHAT_REF = /^[^~\s]{1,200}~[^~\s]{1,300}#\d{1,6}$/;
/** arturo: the turn seq; call: the journal turn index. */
const INDEX_REF = /^\d{1,9}$/;
// One emoji: lib/emoji.ts (shared with the chat's wake-reply hiding and the wake's emoji-reply reaction).
export { isEmoji } from '../lib/emoji.js';
import { isEmoji } from '../lib/emoji.js';

export interface Target { surface: Surface; conv: string; ref: string }

/** surface + conv (+ ref when `needRef`) from a query or a body, or why it is not one. */
export function parseTarget(src: Record<string, unknown>, needRef: boolean): Target | { error: string } {
  const { surface, conv, ref } = src;
  if (typeof surface !== 'string' || !(SURFACES as readonly string[]).includes(surface)) {
    return { error: `surface must be one of ${SURFACES.join(', ')}` };
  }
  if (typeof conv !== 'string' || !CONV.test(conv) || (surface === 'agent-chat' && !isSafeAgentId(conv))) {
    return { error: 'conv is missing or not a valid id' };
  }
  if (!needRef) return { surface: surface as Surface, conv, ref: '' };
  const shape = surface === 'agent-chat' ? AGENT_CHAT_REF : INDEX_REF;
  if (typeof ref !== 'string' || !shape.test(ref)) return { error: 'ref is missing or not a message ref for this surface' };
  return { surface: surface as Surface, conv, ref };
}

function inScope(p: Principal, t: Target): boolean {
  if (t.surface === 'agent-chat') {
    return canPrincipalSeeAgent(p, { id: t.conv, tags: p.clientScope ? defaultResolveTags(t.conv) : undefined });
  }
  return p.role === 'admin';
}

/** The checked target, or null after answering 401 / 400 / 404. */
function target(req: Request, res: Response, src: Record<string, unknown>, needRef: boolean): Target | null {
  const p = principal(req);
  if (!p) { res.status(401).json({ error: 'unauthenticated' }); return null; }
  const t = parseTarget(src, needRef);
  if ('error' in t) { res.status(400).json({ error: t.error }); return null; }
  if (!inScope(p, t)) {
    res.status(404).json({ error: t.surface === 'agent-chat' ? `Agent '${t.conv}' not found` : 'not found' });
    return null;
  }
  return t;
}

const asRecord = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' ? (v as Record<string, unknown>) : {};

export interface ReactionsDeps { store: () => ReactionStore }

export function createReactionsRouter(deps: ReactionsDeps = { store: () => reactionStore() }): Router {
  const router = Router();

  router.get('/', (req: Request, res: Response) => {
    const t = target(req, res, asRecord(req.query), false);
    if (!t) return;
    try {
      res.json({ surface: t.surface, conv: t.conv, reactions: deps.store().list(t.surface, t.conv) });
    } catch (e) {
      res.status(500).json({ error: String(e).slice(0, 200) });
    }
  });

  router.put('/', (req: Request, res: Response) => {
    const body = asRecord(req.body);
    const t = target(req, res, body, true);
    if (!t) return;
    if (!isEmoji(body.emoji)) { res.status(400).json({ error: 'emoji must be a single emoji' }); return; }
    const reactor = reactorOf(req);
    if (!reactor) { res.status(401).json({ error: 'unauthenticated' }); return; }
    if (reactor === AGENT_TOKEN_INVALID) { res.status(401).json({ error: 'agent_token_invalid' }); return; }
    try {
      const change = deps.store().toggle({ ...t, reactor }, body.emoji);
      publishReaction(change);
      res.json({ ok: true, ...change });
    } catch (e) {
      res.status(500).json({ error: String(e).slice(0, 200) });
    }
  });

  router.delete('/', (req: Request, res: Response) => {
    const t = target(req, res, { ...asRecord(req.query), ...asRecord(req.body) }, true);
    if (!t) return;
    const reactor = reactorOf(req);
    if (!reactor) { res.status(401).json({ error: 'unauthenticated' }); return; }
    if (reactor === AGENT_TOKEN_INVALID) { res.status(401).json({ error: 'agent_token_invalid' }); return; }
    try {
      const change = deps.store().remove({ ...t, reactor });
      if (change) publishReaction(change);
      res.json({ ok: true, removed: !!change, surface: t.surface, conv: t.conv, ref: t.ref, reactor, emoji: null });
    } catch (e) {
      res.status(500).json({ error: String(e).slice(0, 200) });
    }
  });

  router.get('/stream', (req: Request, res: Response) => {
    const t = target(req, res, asRecord(req.query), false);
    if (!t) return;
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write('retry: 3000\n\n');
    const off = onReaction((c: ReactionChange) => {
      if (c.surface !== t.surface || c.conv !== t.conv) return;
      res.write(`event: reaction\ndata: ${JSON.stringify(c)}\n\n`);
    });
    const heartbeat = setInterval(() => { res.write(': ping\n\n'); }, HEARTBEAT_MS);
    heartbeat.unref?.();
    const cleanup = () => { clearInterval(heartbeat); off(); };
    res.on('close', cleanup);
    res.on('error', cleanup);
  });

  return router;
}

export default createReactionsRouter();
