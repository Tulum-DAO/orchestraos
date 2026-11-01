/**
 * The calls made on an agent's page (lib/agentCalls.ts), for the agent's own chat:
 *
 *   GET /api/agents/:id/calls                    -> {ok, calls: CallSummary[]}   newest first
 *   GET /api/agents/:id/calls/:callId            -> {ok, call: CallRecord}       turns as You / the agent, work anchors
 *   PUT /api/agents/:id/calls/:callId/rating     {rating: "up"|"down"|null} -> {ok, call_id, rating}
 *   POST /api/agents/:id/calls/handoff           {client_call_id?} -> {ok, call_id, matched, state} (handOff below)
 *
 * Read-only over the call journals (the voice side writes them); the thumbs are the only write. `:id` is scoped like
 * every /api/agents/:id route, and a call that is not this agent's is the same 404 as an unknown one.
 */
import { Router, type Request, type Response } from 'express';
import { agentScopeParam } from '../lib/agent-scope.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { principal } from '../lib/principal.js';
import { dataDir } from '../lib/config.js';
import { listAgentCalls, getAgentCall, setCallRating, CALL_ID_RE, CLIENT_CALL_ID_RE, findHandoffCall, handoffMessage,
  markHandoff, readHandoffs, type Rating } from '../lib/agentCalls.js';
import { defaultAgentSendDeps, type GatewayInjectResult, type MsgStoreSendResult } from './agent-send.js';
import { getRegistry } from '../services/state-reader.js';
import { readSeatNames } from '../lib/agentIdentity.js';
import { chatItemsFor } from './chat-transcript.js';

export interface AgentCallsDeps {
  dataDir: () => string;
  chatItems: (agentId: string) => any[];
  /** The hand-off's delivery: the same gateway path and durable fallback as POST /:id/send. */
  resolveSession?: (agentId: string) => string;
  gatewayInject?: (session: string, text: string) => Promise<GatewayInjectResult>;
  msgStoreSend?: (p: { fromAgent: string; toAgent: string; body: string; reason: string }) => Promise<MsgStoreSendResult>;
  /** The name the agent's lines carry in the hand-off. */
  agentName?: (agentId: string) => string;
  now?: () => number;
}

function realAgentName(id: string, dataDir: string): string {
  try { const n = readSeatNames(dataDir).get(id)?.name; if (n) return n; } catch { /* no names db */ }
  const row = (getRegistry() as any)?.agents?.[id] || {};
  return String(row.display_name || row.name || id);
}

export function createAgentCallsRouter(deps: AgentCallsDeps): Router {
  const router = Router();
  router.param('id', agentScopeParam);

  router.get('/:id/calls', (req: Request, res: Response) => {
    const id = String(req.params.id);
    if (!isSafeAgentId(id)) { res.status(400).json({ ok: false, error: 'bad agent id' }); return; }
    res.json({ ok: true, agent: id, calls: listAgentCalls(deps.dataDir(), id) });
  });

  router.get('/:id/calls/:callId', (req: Request, res: Response) => {
    const id = String(req.params.id);
    const callId = String(req.params.callId);
    if (!isSafeAgentId(id) || !CALL_ID_RE.test(callId)) { res.status(400).json({ ok: false, error: 'bad id' }); return; }
    const call = getAgentCall(deps.dataDir(), id, callId, deps.chatItems(id));
    if (!call) { res.status(404).json({ ok: false, error: 'call not found' }); return; }
    res.json({ ok: true, call });
  });

  router.put('/:id/calls/:callId/rating', (req: Request, res: Response) => {
    const id = String(req.params.id);
    const callId = String(req.params.callId);
    if (!isSafeAgentId(id) || !CALL_ID_RE.test(callId)) { res.status(400).json({ ok: false, error: 'bad id' }); return; }
    const raw = (req.body || {}).rating;
    if (!(raw === 'up' || raw === 'down' || raw === null)) {
      res.status(400).json({ ok: false, error: 'rating must be "up", "down" or null' });
      return;
    }
    const who = principal(req)?.username ?? null;
    if (!setCallRating(deps.dataDir(), id, callId, raw as Rating | null, who)) {
      res.status(404).json({ ok: false, error: 'call not found' });
      return;
    }
    res.json({ ok: true, call_id: callId, rating: raw });
  });

  // The call, handed to the agent as its own words (lib/agentCalls.ts handoffMessage): once per call. Arturo's own
  // calls are his thread's, never handed off. Delivery as POST /:id/send delivers: the gateway, else durable mail.
  router.post('/:id/calls/handoff', async (req: Request, res: Response) => {
    const id = String(req.params.id);
    if (!isSafeAgentId(id)) { res.status(400).json({ ok: false, error: 'bad agent id' }); return; }
    if (id === 'arturo') { res.status(400).json({ ok: false, error: 'not_for_arturo' }); return; }
    const raw = (req.body || {}).client_call_id;
    if (raw !== undefined && raw !== null && !(typeof raw === 'string' && CLIENT_CALL_ID_RE.test(raw))) {
      res.status(400).json({ ok: false, error: 'bad client_call_id' });
      return;
    }
    const dir = deps.dataDir();
    const hit = findHandoffCall(dir, id, raw ?? null, deps.now ? deps.now() : Date.now());
    if (!hit) { res.status(404).json({ ok: false, error: 'no_recent_call' }); return; }
    const { call, doc, matched } = hit;
    const done = readHandoffs(dir)[call.call_id];
    if (done) { res.json({ ok: true, call_id: call.call_id, matched, already: true, state: done.state }); return; }
    const who = principal(req)?.username ?? null;
    if (!who) { res.status(401).json({ ok: false, error: 'unauthenticated' }); return; }
    // Recorded before the send (no await between the check and this): a second request is a no-op from here on.
    markHandoff(dir, call.call_id, { agent: id, at: new Date().toISOString(), by: who, state: 'sending' });
    const text = handoffMessage(call, doc, (deps.agentName ?? ((a) => realAgentName(a, dir)))(id));
    const session = (deps.resolveSession ?? defaultAgentSendDeps.resolveSession)(id);
    const gw = await (deps.gatewayInject ?? ((s, t) => defaultAgentSendDeps.gatewayInject(s, t)))(session, text);
    const record = (state: string) => markHandoff(dir, call.call_id, { agent: id, at: new Date().toISOString(), by: who, state });
    if (gw.ok && (gw.delivered || gw.held)) {
      const state = gw.delivered ? 'delivered' : 'held';
      record(state);
      res.json({ ok: true, call_id: call.call_id, matched, state });
      return;
    }
    // Typed but not confirmed: it may well be there, so it is not sent again (that could hand it off twice).
    if ((gw.httpStatus === 502 && gw.reason === 'unverified_submit') || gw.timedOut) {
      record('unknown');
      res.json({ ok: true, call_id: call.call_id, matched, state: 'unknown' });
      return;
    }
    const write = await (deps.msgStoreSend ?? defaultAgentSendDeps.msgStoreSend)({
      fromAgent: who, toAgent: session, body: text, reason: `voice call hand-off (${gw.reason || gw.state || gw.error || 'not delivered'})`,
    });
    if (write.sent) {
      record('queued');
      res.json({ ok: true, call_id: call.call_id, matched, state: 'queued' });
      return;
    }
    // Neither path took it: forget the hand-off so a retry can send it.
    markHandoff(dir, call.call_id, null);
    console.error('[calls] hand-off not delivered:', write.error || gw.error || gw.reason || 'unknown');
    res.status(502).json({ ok: false, call_id: call.call_id, error: 'not_delivered' });
  });

  return router;
}

export default createAgentCallsRouter({ dataDir: () => dataDir(), chatItems: (id) => chatItemsFor(id) });
