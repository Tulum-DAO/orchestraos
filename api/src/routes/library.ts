/**
 * library.ts — read routes over the library index (services/library-index.ts, parity 4g.1). The Library tab
 * (4g.3) and the project "made by" filter (4g.4) read these; the only write is a pin.
 *   GET /api/library/status    the backfill's time and the last pass
 *   GET /api/library/summary   per agent: made, mentioned, links (last `days`, default 14); shared/unattributed per project
 *   GET /api/library/usage     kept copies: bytes, copies, pins, too large, cleared, per agent and in all, with the caps
 *   GET /api/library/items     ?agent= &project= &made=1|0 &kind=file|media|link &action= &days= &limit= &offset= &retired=1 &pinned=1
 *   GET /api/library/items/:id one item (pinned, missing, moved_to, kept, keep as in the list)
 *   POST|DELETE /api/library/items/:id/pin   pin (never cleared) / unpin; answers with the item
 *   GET /api/library/projects/:slug/made-by   who made each of a project's files (made only; "unclear" with its
 *       candidates, "unattributed"), keyed by the path inside the project folder, with the makers and their counts
 *   GET /api/library/lookup    ?path=…(repeated, up to 20) &agent=   the library items behind paths a report names, keyed by
 *       the path as given: { item, openable } (made before mentioned, the agent's own first), or { project, rel } for a
 *       file in a project folder the index does not hold, else null
 *   GET /api/library/items/:id/content[?raw=1]   an indexed file's content, as the project files route gives it; when the
 *       file is gone, its kept copy (served_from "kept", header X-Library-Served-From: kept)
 * Retired agents' items are left out unless retired=1 (or the agent is asked for by name).
 * Each item has a `title` (its file's name, or a short description of its content) and a `subtitle` (the words a
 * Bash command came with, or null), shown under the title.
 */
import { Router, type Request, type Response } from 'express';
import { readFileSync } from 'fs';
import { libraryIndex } from '../services/library-index.js';
import { readProjectFile } from '../services/project-files.js';
import { tenantScope } from '../lib/principal.js';

const router = Router();
const num = (v: unknown) => { const n = Number(v); return Number.isFinite(n) && n > 0 ? n : undefined; };
const ITEM_ID = /^[0-9a-f]{24}$/;
const SLUG = /^[a-z0-9][a-z0-9-]{0,39}$/;
const str = (v: unknown) => (typeof v === 'string' && /^[\w.-]{1,80}$/.test(v) ? v : undefined);

router.get('/status', (_req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  res.json(ix.status());
});

router.get('/summary', (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  res.json(ix.summary(num(req.query.days) ?? 14, req.query.retired === '1'));
});

router.get('/usage', (_req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  res.json(ix.usage());
});

router.get('/items', (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  const made = req.query.made === '1' ? true : req.query.made === '0' ? false : undefined;
  const kind = ['file', 'media', 'link'].includes(String(req.query.kind)) ? String(req.query.kind) : undefined;
  const action = ['created', 'edited', 'uploaded', 'deployed', 'mentioned', 'posted'].includes(String(req.query.action)) ? String(req.query.action) : undefined;
  res.json({ items: ix.list({ agent: str(req.query.agent), project: str(req.query.project), made, kind, action,
                               days: num(req.query.days), limit: num(req.query.limit), offset: num(req.query.offset),
                               retired: req.query.retired === '1', pinned: req.query.pinned === '1' }) });
});

router.get('/projects/:slug/made-by', (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  const slug = String(req.params.slug);
  // the same scope as the project's own files: a client sees only its project
  const scope = tenantScope(req);
  if (!SLUG.test(slug) || (!scope.isAdmin && scope.clientScope && slug !== scope.clientScope)) { res.status(404).json({ error: 'No such project' }); return; }
  res.json(ix.madeBy(slug));
});

const MAX_LOOKUP = 20;
router.get('/lookup', (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  const raw = req.query.path;
  const paths = (Array.isArray(raw) ? raw : raw === undefined ? [] : [raw])
    .filter((p): p is string => typeof p === 'string' && p.length > 0 && p.length <= 1024);
  if (paths.length > MAX_LOOKUP) { res.status(400).json({ error: `At most ${MAX_LOOKUP} paths` }); return; }
  const scope = tenantScope(req);
  res.json({ files: ix.lookup([...new Set(paths)], str(req.query.agent), scope.isAdmin ? null : scope.clientScope) });
});

router.get('/items/:id', (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  const id = String(req.params.id);
  const it = ITEM_ID.test(id) ? ix.item(id) : null;
  if (!it) { res.status(404).json({ error: 'No such item in the library' }); return; }
  res.json(it);
});

const setPin = (on: boolean) => (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  const id = String(req.params.id);
  const it = ITEM_ID.test(id) ? ix.pin(id, on) : null;
  if (!it) { res.status(404).json({ error: 'No such item in the library' }); return; }
  res.json(it);
};
router.post('/items/:id/pin', setPin(true));
router.delete('/items/:id/pin', setPin(false));

router.get('/items/:id/content', (req: Request, res: Response) => {
  const ix = libraryIndex();
  if (!ix) { res.status(503).json({ error: 'The library index is not running' }); return; }
  const id = String(req.params.id);
  const f = ITEM_ID.test(id) ? ix.contentOf(id) : null;
  if (!f) { res.status(404).json({ error: 'No such file in the library' }); return; }
  try {
    // the kept copy is named by item id: its type comes from the original's name
    const content = readProjectFile(f.path, f.name);
    res.setHeader('X-Library-Served-From', f.kept ? 'kept' : 'original');
    if ((content.kind === 'image' || content.kind === 'pdf') && req.query.raw === '1') {
      res.setHeader('Content-Type', content.mime);
      // the same posture as the project files route: an image renders inert, a PDF goes to the browser's viewer
      if (content.kind === 'image') res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'");
      res.setHeader('X-Content-Type-Options', 'nosniff');
      res.send(readFileSync(f.path));
      return;
    }
    res.json({ path: f.original, title: f.title, served_from: f.kept ? 'kept' : 'original', ...content });
  } catch (err) {
    res.status(500).json({ error: 'Could not read that file', detail: String(err) });
  }
});

export default router;
