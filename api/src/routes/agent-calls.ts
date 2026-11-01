/**
 * The calls made on an agent's page (lib/agentCalls.ts), for the agent's own chat:
 *
 *   GET /api/agents/:id/calls                    -> {ok, calls: CallSummary[]}   newest first
 *   GET /api/agents/:id/calls/:callId            -> {ok, call: CallRecord}       turns as You / the agent, work anchors
 *   PUT /api/agents/:id/calls/:callId/rating     {rating: "up"|"down"|null} -> {ok, call_id, rating}
 *
 * Read-only over the call journals (the voice side writes them); the thumbs are the only write. `:id` is scoped like
 * every /api/agents/:id route, and a call that is not this agent's is the same 404 as an unknown one.
 */
import { Router, type Request, type Response } from 'express';
import { agentScopeParam } from '../lib/agent-scope.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { principal } from '../lib/principal.js';
import { dataDir } from '../lib/config.js';
import { listAgentCalls, getAgentCall, setCallRating, CALL_ID_RE, type Rating } from '../lib/agentCalls.js';
import { chatItemsFor } from './chat-transcript.js';

export interface AgentCallsDeps { dataDir: () => string; chatItems: (agentId: string) => any[] }

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

  return router;
}

export default createAgentCallsRouter({ dataDir: () => dataDir(), chatItems: (id) => chatItemsFor(id) });
