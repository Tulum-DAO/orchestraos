/**
 * Images and video in the chat, served by opaque id.
 *
 *   GET /api/media/:id    the bytes behind an id (lib/mediaStore.ts), or 404; one byte range on request
 *
 * There is no way to ask for an id by path: ids are minted only while the server stamps a
 * transcript (an agent's chat, or Arturo's thread as the API relays it), for a file that passed
 * every check at that moment. A client never sends a path.
 *
 * Auth is the chat's: the caller must be able to see the agent the id was minted for
 * (lib/agent-scope.ts canPrincipalSeeAgent, the rule behind GET /api/agents/:id/transcript), and
 * an out-of-scope id gets the same 404 as an unknown one. The file must pass the per-agent
 * allow-list again (its working folder + its own output folders, realpath, no links, no
 * traversal), be an image or a video by its bytes (lib/mediaSniff.ts), and is streamed from the ONE
 * descriptor the checks ran on. Responses are `Cache-Control: private`: a shared cache must never
 * hold them.
 *
 * RANGES. A browser's <video> asks for `Range: bytes=...` to read the index at the end and to seek,
 * and will not seek at all without a 206. One range is answered with 206 + Content-Range, read
 * from the same descriptor (a positioned read, start..end), never past the size fstat gave. A range
 * past the end is 416, its Content-Range naming only the size. Several ranges, a malformed header,
 * another unit, or an If-Range (the route sends no validator to match it) get the whole file,
 * 200, as RFC 9110 allows. `Accept-Ranges: bytes` is on every answer.
 */
import { Router, type Request, type Response } from 'express';
import { closeSync, createReadStream } from 'node:fs';
import { dataDir } from '../lib/config.js';
import { principal } from '../lib/principal.js';
import { canPrincipalSeeAgent, defaultResolveTags } from '../lib/agent-scope.js';
import { agentMediaRoots, mediaStore, openMedia, MEDIA_ID, type MediaStore } from '../lib/mediaStore.js';

export interface MediaDeps {
  store: MediaStore;
  roots: (agentId: string) => string[];
  resolveTags: (agentId: string) => unknown;
}

const notFound = (res: Response) => { res.status(404).json({ error: 'not found' }); };

/** One byte range of a `size`-byte file from a Range header: the range, 'unsatisfiable' (416), or
 *  null (serve the whole file: no header, several ranges, another unit, or a malformed one). */
export function parseRange(header: unknown, size: number): { start: number; end: number } | 'unsatisfiable' | null {
  if (typeof header !== 'string') return null;
  const m = /^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$/i.exec(header);
  if (!m) return null;
  const [, a, b] = m;
  if (a === '' && b === '') return null;
  if (a === '') {
    const n = Number(b);
    if (!Number.isSafeInteger(n)) return null;
    if (n === 0 || size === 0) return 'unsatisfiable';
    return { start: Math.max(0, size - n), end: size - 1 };
  }
  const start = Number(a);
  const last = b === '' ? size - 1 : Number(b);
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(last)) return null;
  if (b !== '' && last < start) return null;   // a reversed range is invalid: the whole file
  if (start >= size) return 'unsatisfiable';
  return { start, end: Math.min(last, size - 1) };
}

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
    const r = openMedia(row.path, deps.roots(row.agent));
    if (!r.ok) { notFound(res); return; }
    // Only a header with no If-Range is a range request we answer partially (we send no validator to compare).
    const range = req.headers['if-range'] === undefined ? parseRange(req.headers.range, r.size) : null;
    res.setHeader('Accept-Ranges', 'bytes');
    res.setHeader('Cache-Control', 'private, max-age=300');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (range === 'unsatisfiable') {
      closeSync(r.fd);
      res.setHeader('Content-Range', `bytes */${r.size}`);
      res.status(416).end();
      return;
    }
    const start = range ? range.start : 0;
    const end = range ? range.end : r.size - 1;
    res.setHeader('Content-Type', r.mime);
    res.setHeader('Content-Length', String(end - start + 1));
    res.setHeader('Content-Disposition', `${req.query.download !== undefined ? 'attachment' : 'inline'}; filename="${safeName(row.ref ?? row.path)}"`);
    if (range) { res.status(206); res.setHeader('Content-Range', `bytes ${start}-${end}/${r.size}`); }
    if (req.method === 'HEAD') { closeSync(r.fd); res.end(); return; }
    // The bytes come from the descriptor the checks ran on: no second lookup by path.
    const stream = createReadStream('', { fd: r.fd, start, end, autoClose: true });
    stream.on('error', () => { if (!res.headersSent) notFound(res); else res.destroy(); });
    // A viewer that seeks drops the old request mid-stream: stop reading and release the descriptor.
    res.on('close', () => { if (!stream.destroyed) stream.destroy(); });
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
