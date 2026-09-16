/**
 * northstars.ts — North Star + Key Results CRUD backed by SQLite (state/tasks.db).
 */

import { Router, type Request, type Response } from 'express';
import { queryDb, execDb } from '../lib/db.js';
import { loadConfig } from '../lib/config.js';

const router = Router();

function getTenantScope(req: any) {
  const role = (req.headers['x-orchestra-role'] as string) || 'admin';
  const clientScope = (req.headers['x-orchestra-client'] as string) || null;
  const username = (req.headers['x-orchestra-user'] as string) || loadConfig().operatorId;
  return { isAdmin: role === 'admin', clientScope: role === 'admin' ? null : clientScope, username };
}

function nanoid(prefix: string): string {
  return prefix + '_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

// ── North Stars ─────────────────────────────────────────────────

// GET /api/north-stars — list all (scoped by tenant)
router.get('/', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const tenantWhere = scope.isAdmin ? '' : ` AND ns.tenant_id = '${scope.clientScope || scope.username}'`;

  const scopeFilter = req.query.scope as string;
  const project = req.query.project as string;
  const client = req.query.client as string;
  const status = req.query.status as string;

  const conds: string[] = ['1=1' + tenantWhere];
  const params: any[] = [];

  if (scopeFilter) { conds.push('ns.scope = ?'); params.push(scopeFilter); }
  if (project) { conds.push('ns.project_id = ?'); params.push(project); }
  if (client) { conds.push('ns.client_id = ?'); params.push(client); }
  if (status) { conds.push('ns.status = ?'); params.push(status); }
  else { conds.push("ns.status != 'abandoned'"); }

  const w = conds.join(' AND ');
  const northStars = queryDb(`SELECT ns.* FROM north_stars ns WHERE ${w} ORDER BY ns.priority ASC, ns.created_at DESC`, params);

  // Attach key results to each
  for (const ns of northStars) {
    ns.key_results = queryDb(
      'SELECT * FROM key_results WHERE north_star_id = ? ORDER BY created_at',
      [ns.id]
    );
    const total = ns.key_results.length;
    const done = ns.key_results.filter((kr: any) => kr.status === 'completed').length;
    ns.kr_total = total;
    ns.kr_done = done;
    ns.kr_progress = total > 0 ? Math.round((done / total) * 100) : 0;

    // Count aligned tasks
    const taskCount = queryDb(
      "SELECT COUNT(*) as count FROM tasks WHERE north_star_id = ? AND status != 'cancelled'",
      [ns.id]
    );
    ns.aligned_tasks = taskCount.length ? taskCount[0].count : 0;
  }

  // For admin: group by tenant if requested
  if (scope.isAdmin && req.query.grouped === 'true') {
    const grouped: Record<string, any[]> = {};
    for (const ns of northStars) {
      const key = ns.tenant_id || loadConfig().operatorId;
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(ns);
    }
    res.json({ north_stars: northStars, grouped, total: northStars.length });
    return;
  }

  res.json({ north_stars: northStars, total: northStars.length });
});

// GET /api/north-stars/:id — single with key results + aligned tasks
router.get('/:id', (req: Request, res: Response) => {
  const ns = queryDb('SELECT * FROM north_stars WHERE id = ?', [req.params.id]);
  if (!ns.length) { res.status(404).json({ error: 'Not found' }); return; }

  const result = ns[0];
  result.key_results = queryDb(
    'SELECT * FROM key_results WHERE north_star_id = ? ORDER BY created_at',
    [req.params.id]
  );
  result.aligned_tasks = queryDb(
    "SELECT id, title, status, priority FROM tasks WHERE north_star_id = ? AND status != 'cancelled' ORDER BY priority, created_at DESC LIMIT 20",
    [req.params.id]
  );

  res.json(result);
});

// POST /api/north-stars — create
router.post('/', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const { objective, scope: nsScope, project_id, client_id, priority, target_date, key_results } = req.body;

  if (!objective) { res.status(400).json({ error: 'objective required' }); return; }
  if (!nsScope || !['global', 'client', 'project'].includes(nsScope)) {
    res.status(400).json({ error: 'scope must be global, client, or project' }); return;
  }

  const id = nanoid('ns');
  const tenantId = scope.isAdmin ? (req.body.tenant_id || loadConfig().operatorId) : (scope.clientScope || scope.username);
  const now = new Date().toISOString();

  execDb(`INSERT INTO north_stars (id, tenant_id, scope, project_id, client_id, objective, priority, target_date, created_by, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [id, tenantId, nsScope, project_id || null, client_id || null,
     objective, priority || 'P1', target_date || null, scope.username, now, now]);

  // Create key results if provided
  if (key_results && Array.isArray(key_results)) {
    for (const kr of key_results) {
      if (!kr.description) continue;
      const krId = nanoid('kr');
      execDb(`INSERT INTO key_results (id, north_star_id, tenant_id, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)`,
        [krId, id, tenantId, kr.description, now, now]);
    }
  }

  const created = queryDb('SELECT * FROM north_stars WHERE id = ?', [id]);
  if (created.length) {
    created[0].key_results = queryDb('SELECT * FROM key_results WHERE north_star_id = ?', [id]);
  }
  res.json(created[0] || { id, created: true });
});

// PATCH /api/north-stars/:id — update
router.patch('/:id', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const existing = queryDb('SELECT * FROM north_stars WHERE id = ?', [req.params.id]);
  if (!existing.length) { res.status(404).json({ error: 'Not found' }); return; }

  const allowed = ['objective', 'status', 'priority', 'target_date', 'project_id', 'client_id', 'scope'];
  const sets: string[] = [];
  const params: any[] = [];

  for (const f of allowed) {
    if (f in req.body) { sets.push(`${f} = ?`); params.push(req.body[f]); }
  }

  if (req.body.status === 'achieved') {
    sets.push('last_progress = ?');
    params.push(new Date().toISOString());
  }

  if (!sets.length) { res.json({ updated: false }); return; }
  sets.push('updated_at = ?'); params.push(new Date().toISOString()); params.push(req.params.id);
  execDb(`UPDATE north_stars SET ${sets.join(', ')} WHERE id = ?`, params);

  const updated = queryDb('SELECT * FROM north_stars WHERE id = ?', [req.params.id]);
  if (updated.length) {
    updated[0].key_results = queryDb('SELECT * FROM key_results WHERE north_star_id = ?', [req.params.id]);
  }
  res.json(updated[0] || { updated: true });
});

// DELETE /api/north-stars/:id — soft delete (status → abandoned)
router.delete('/:id', (_req: Request, res: Response) => {
  execDb("UPDATE north_stars SET status = 'abandoned', updated_at = ? WHERE id = ?",
    [new Date().toISOString(), _req.params.id]);
  res.json({ deleted: true });
});

// ── Key Results ─────────────────────────────────────────────────

// POST /api/north-stars/:id/key-results — add key result
router.post('/:id/key-results', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const { description } = req.body;
  if (!description) { res.status(400).json({ error: 'description required' }); return; }

  const ns = queryDb('SELECT tenant_id FROM north_stars WHERE id = ?', [req.params.id]);
  if (!ns.length) { res.status(404).json({ error: 'North star not found' }); return; }

  const id = nanoid('kr');
  const now = new Date().toISOString();
  execDb(`INSERT INTO key_results (id, north_star_id, tenant_id, description, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?)`,
    [id, req.params.id, ns[0].tenant_id, description, now, now]);

  const created = queryDb('SELECT * FROM key_results WHERE id = ?', [id]);
  res.json(created[0] || { id, created: true });
});

// PATCH /api/north-stars/:nsId/key-results/:krId — update key result
router.patch('/:nsId/key-results/:krId', (req: Request, res: Response) => {
  const { status, description } = req.body;
  const sets: string[] = [];
  const params: any[] = [];

  if (status) { sets.push('status = ?'); params.push(status); }
  if (description) { sets.push('description = ?'); params.push(description); }

  if (status === 'completed') {
    sets.push('measured_at = ?');
    params.push(new Date().toISOString());
    // Update parent north star's last_progress
    execDb('UPDATE north_stars SET last_progress = ?, updated_at = ? WHERE id = ?',
      [new Date().toISOString(), new Date().toISOString(), req.params.nsId]);
  }

  if (!sets.length) { res.json({ updated: false }); return; }
  sets.push('updated_at = ?'); params.push(new Date().toISOString()); params.push(req.params.krId);
  execDb(`UPDATE key_results SET ${sets.join(', ')} WHERE id = ?`, params);

  const updated = queryDb('SELECT * FROM key_results WHERE id = ?', [req.params.krId]);
  res.json(updated[0] || { updated: true });
});

// DELETE /api/north-stars/:nsId/key-results/:krId
router.delete('/:nsId/key-results/:krId', (_req: Request, res: Response) => {
  execDb('DELETE FROM key_results WHERE id = ?', [_req.params.krId]);
  res.json({ deleted: true });
});

export default router;
