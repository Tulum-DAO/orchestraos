/**
 * Images in the chat, served by opaque id.
 *
 *   GET  /api/media/:id            the image bytes behind an id (lib/mediaStore.ts), or 404
 *   POST /api/media/resolve        { agent, path } -> { id }: the id for a path an agent wrote, or 404.
 *                                  The seam for a surface whose messages the API does not build
 *                                  (Arturo's threads come from the gateway): the client resolves
 *                                  the paths it found in the reply, never more.
 *
 * Auth is the chat's: the caller must be able to see the agent the id was minted for
 * (lib/agent-scope.ts canPrincipalSeeAgent, the rule behind GET /api/agents/:id/transcript), and
 * an out-of-scope id gets the same 404 as an unknown one. The file must pass the allow-list
 * (data dir + the agent's working folder, realpath, no symlinks, no traversal) and be an image by
 * its bytes. Responses are `Cache-Control: private`: a shared cache must never hold them.
 */
import { Router, type Request, type Response } from 'express';
import { createReadStream } from 'node:fs';
import { dataDir } from '../lib/config.js';
import { principal } from '../lib/principal.js';
import { canPrincipalSeeAgent, defaultResolveTags } from '../lib/agent-scope.js';
import { agentMediaRoots, mediaStore, resolveImageFile, MEDIA_ID, type MediaStore } from '../lib/mediaStore.js';
import { findImagePaths } from '../lib/imageRefs.js';
import { isSafeAgentId } from '../lib/agentPaths.js';

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
    const r = resolveImageFile(row.path, deps.roots(row.agent));
    if (!r.ok) { notFound(res); return; }
    res.setHeader('Content-Type', r.mime);
    res.setHeader('Content-Length', String(r.size));
    res.setHeader('Cache-Control', 'private, max-age=300');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    res.setHeader('Content-Disposition', `${req.query.download !== undefined ? 'attachment' : 'inline'}; filename="${safeName(row.path)}"`);
    const stream = createReadStream(r.file);
    stream.on('error', () => { if (!res.headersSent) notFound(res); else res.destroy(); });
    stream.pipe(res);
  });

  router.post('/resolve', (req: Request, res: Response) => {
    const body = (req.body || {}) as { agent?: unknown; path?: unknown };
    const agent = typeof body.agent === 'string' ? body.agent : '';
    const path = typeof body.path === 'string' ? body.path : '';
    if (!isSafeAgentId(agent) || !path) { notFound(res); return; }
    if (!canPrincipalSeeAgent(principal(req), { id: agent, tags: deps.resolveTags(agent) })) { notFound(res); return; }
    // Only a path the detector would have found in a reply: the same grammar as the stamped items.
    if (findImagePaths(path)[0] !== path) { notFound(res); return; }
    const r = resolveImageFile(path, deps.roots(agent));
    if (!r.ok) { notFound(res); return; }
    res.json({ id: deps.store.idFor(agent, path) });
  });

  return router;
}

const router = createMediaRouter({
  store: mediaStore(dataDir()),
  roots: (agentId) => agentMediaRoots(dataDir(), agentId),
  resolveTags: defaultResolveTags,
});
export default router;
