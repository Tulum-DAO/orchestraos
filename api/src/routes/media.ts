/**
 * Images in the chat, served by opaque id.
 *
 *   GET /api/media/:id    the image bytes behind an id (lib/mediaStore.ts), or 404
 *
 * There is no way to ask for an id by path: ids are minted only while the server stamps a
 * transcript (an agent's chat, or Arturo's thread as the API relays it), for a file that passed
 * every check at that moment. A client never sends a path.
 *
 * Auth is the chat's: the caller must be able to see the agent the id was minted for
 * (lib/agent-scope.ts canPrincipalSeeAgent, the rule behind GET /api/agents/:id/transcript), and
 * an out-of-scope id gets the same 404 as an unknown one. The file must pass the per-agent
 * allow-list again (its working folder + its own output folders, realpath, no links, no
 * traversal), be an image by its bytes, and is streamed from the ONE descriptor the checks ran on.
 * Responses are `Cache-Control: private`: a shared cache must never hold them.
 */
import { Router, type Request, type Response } from 'express';
import { closeSync, createReadStream } from 'node:fs';
import { dataDir } from '../lib/config.js';
import { principal } from '../lib/principal.js';
import { canPrincipalSeeAgent, defaultResolveTags } from '../lib/agent-scope.js';
import { agentMediaRoots, mediaStore, openImage, MEDIA_ID, type MediaStore } from '../lib/mediaStore.js';

export interface MediaDeps {
  store: MediaStore;
  roots: (agentId: string) => string[];
  resolveTags: (agentId: string) => unknown;
}

const notFound = (res: Response) => { res.status(404).json({ error: 'not found' }); };

/** The basename for Content-Disposition: ASCII, no quotes or control characters, never a path. */
function safeName(path: string): string {
  const base = path.slice(path.lastIndexOf('/') + 1);
  // eslint-disable-next-line no-control-regex
  const clean = base.replace(/[^\x20-\x7e]/g, '').replace(/["\\;]/g, '_').trim();
  return clean || 'image';
}

export function createMediaRouter(deps: MediaDeps): Router {
  const router = Router();

  router.get('/:id', (req: Request, res: Response) => {
    const id = String(req.params.id || '');
    if (!MEDIA_ID.test(id)) { notFound(res); return; }
    const row = deps.store.lookup(id);
    if (!row) { notFound(res); return; }
    if (!canPrincipalSeeAgent(principal(req), { id: row.agent, tags: deps.resolveTags(row.agent) })) { notFound(res); return; }
    const r = openImage(row.path, deps.roots(row.agent));
    if (!r.ok) { notFound(res); return; }
    res.setHeader('Content-Type', r.mime);
    res.setHeader('Content-Length', String(r.size));
    res.setHeader('Cache-Control', 'private, max-age=300');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Disposition', `${req.query.download !== undefined ? 'attachment' : 'inline'}; filename="${safeName(row.path)}"`);
    if (req.method === 'HEAD') { closeSync(r.fd); res.end(); return; }
    // The bytes come from the descriptor the checks ran on: no second lookup by path.
    const stream = createReadStream('', { fd: r.fd, start: 0, end: r.size - 1, autoClose: true });
    stream.on('error', () => { if (!res.headersSent) notFound(res); else res.destroy(); });
    stream.pipe(res);
  });

  return router;
}

const router = createMediaRouter({
  store: mediaStore(dataDir()),
  roots: (agentId) => agentMediaRoots(dataDir(), agentId),
  resolveTags: defaultResolveTags,
});
export default router;
