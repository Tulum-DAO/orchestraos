/**
 * people.ts — People CRUD + connections + project links + activity.
 */

import { Router, type Request, type Response } from 'express';
import { queryDb, execDb } from '../lib/db.js';
import { goalsOpenForPerson, openGoalsForPerson } from '../services/goals-ledger.js';
import { loadConfig } from '../lib/config.js';

const router = Router();

// Parse a stored JSON-array TEXT column (aliases, company_slugs) into an array.
function jsonArray(v: unknown): string[] {
  if (Array.isArray(v)) return v as string[];
  if (typeof v !== 'string' || !v) return [];
  try { const a = JSON.parse(v); return Array.isArray(a) ? a : []; } catch { return []; }
}

function getTenantScope(req: any) {
  const role = (req.headers['x-orchestra-role'] as string) || 'admin';
  const clientScope = (req.headers['x-orchestra-client'] as string) || null;
  const username = (req.headers['x-orchestra-user'] as string) || loadConfig().operatorId;
  return { isAdmin: role === 'admin', clientScope: role === 'admin' ? null : clientScope, username };
}

function nanoid(prefix: string): string {
  return prefix + '_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

// ── List people ─────────────────────────────────────────────────

router.get('/', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const tw = scope.isAdmin ? '' : ` AND p.tenant_id = '${scope.clientScope || scope.username}'`;
  const conds: string[] = ['1=1' + tw];
  const params: any[] = [];

  if (req.query.relationship) { conds.push('p.relationship = ?'); params.push(req.query.relationship); }
  if (req.query.project) { conds.push("p.id IN (SELECT person_id FROM person_projects WHERE project_id = ?)"); params.push(req.query.project); }
  if (req.query.search) {
    conds.push("(p.name LIKE ? OR p.title LIKE ? OR p.company LIKE ? OR p.context LIKE ?)");
    const s = `%${req.query.search}%`;
    params.push(s, s, s, s);
  }

  const sort = (req.query.sort as string) || 'name';
  const dir = (req.query.dir as string) === 'desc' ? 'DESC' : 'ASC';
  const w = conds.join(' AND ');

  let people = queryDb(`SELECT p.* FROM people p WHERE ${w} ORDER BY p.${sort} ${dir}`, params);

  // CRM P0 additive: resolved company link filter (post-filter; company_slugs is
  // a JSON-array TEXT col, and the table is small). §4.2 company_slug filter.
  const companySlug = req.query.company_slug as string | undefined;
  if (companySlug) people = people.filter((p) => jsonArray(p.company_slugs).includes(companySlug));

  // Attach project links, connection count, pipeline/stage names, + CRM P0 spine joins.
  for (const p of people) {
    p.projects = queryDb('SELECT project_id, role FROM person_projects WHERE person_id = ?', [p.id]);
    const conns = queryDb(
      'SELECT COUNT(*) as count FROM person_connections WHERE person_a = ? OR person_b = ?',
      [p.id, p.id]
    );
    p.connection_count = conns.length ? conns[0].count : 0;
    // Pipeline info
    if (p.pipeline_id) {
      const pl = queryDb('SELECT name, color FROM pipelines WHERE id = ?', [p.pipeline_id]);
      p.pipeline_name = pl.length ? pl[0].name : null;
      p.pipeline_color = pl.length ? pl[0].color : null;
    }
    if (p.stage_id) {
      const st = queryDb('SELECT name FROM pipeline_stages WHERE id = ?', [p.stage_id]);
      p.stage_name = st.length ? st[0].name : null;
    }
    // CRM P0 spine (§4.1): resolved company slug(s), lead-vs-resolved flag, and
    // the open-goals roll-up joined from the ledger on the person id (0 until the
    // miner seeds the ledger — the join is live + safe meanwhile).
    p.company_slugs = jsonArray(p.company_slugs);
    p.resolved = p.resolved == null ? true : !!p.resolved;
    p.goals_open = goalsOpenForPerson(p.id);
  }

  // §4.2 has_goals filter (applied after the roll-up).
  if (req.query.has_goals === '1' || req.query.has_goals === 'true') {
    people = people.filter((p) => (p.goals_open || 0) > 0);
  }

  res.json({ people, total: people.length });
});

// ── Get single person with full detail ──────────────────────────

router.get('/:id', (req: Request, res: Response) => {
  const people = queryDb('SELECT * FROM people WHERE id = ?', [String(req.params.id)]);
  if (!people.length) { res.status(404).json({ error: 'Not found' }); return; }

  const person = people[0];

  // Projects
  person.projects = queryDb('SELECT project_id, role FROM person_projects WHERE person_id = ?', [person.id]);

  // Connections (both directions)
  const rawConns = queryDb(
    `SELECT c.*,
       CASE WHEN c.person_a = ? THEN c.person_b ELSE c.person_a END as other_id
     FROM person_connections c
     WHERE c.person_a = ? OR c.person_b = ?`,
    [person.id, person.id, person.id]
  );
  person.connections = [];
  for (const c of rawConns) {
    const other = queryDb('SELECT id, name, title, company, relationship FROM people WHERE id = ?', [c.other_id]);
    if (other.length) {
      person.connections.push({
        ...c,
        other: other[0],
      });
    }
  }

  // Activity
  person.activity = queryDb(
    'SELECT * FROM person_activity WHERE person_id = ? ORDER BY created_at DESC LIMIT 20',
    [person.id]
  );

  // CRM P0 spine (§4.2): resolved company link(s) + open goals concerning this
  // person (from the ledger; empty until the miner seeds it).
  person.company_slugs = jsonArray(person.company_slugs);
  person.resolved = person.resolved == null ? true : !!person.resolved;
  person.goals = openGoalsForPerson(person.id);
  person.goals_open = person.goals.length;

  res.json(person);
});

// ── Create person ───────────────────────────────────────────────

router.post('/', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const b = req.body;
  if (!b.name) { res.status(400).json({ error: 'name required' }); return; }

  const id = nanoid('person');
  const tenantId = scope.isAdmin ? (b.tenant_id || loadConfig().operatorId) : (scope.clientScope || scope.username);
  const now = new Date().toISOString();

  execDb(`INSERT INTO people (id, tenant_id, name, aliases, title, company, email, phone, linkedin, facebook, instagram, website, relationship, source, context, goals, beliefs, environment, situation, created_at, updated_at)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [id, tenantId, b.name, b.aliases ? JSON.stringify(b.aliases) : null,
     b.title || null, b.company || null, b.email || null, b.phone || null,
     b.linkedin || null, b.facebook || null, b.instagram || null, b.website || null,
     b.relationship || 'contact', b.source || 'dashboard', b.context || null,
     b.goals || null, b.beliefs || null, b.environment || null, b.situation || null,
     now, now]);

  // Link to projects if provided
  if (b.projects && Array.isArray(b.projects)) {
    for (const p of b.projects) {
      const projId = typeof p === 'string' ? p : p.project_id;
      const role = typeof p === 'string' ? null : p.role;
      execDb('INSERT OR IGNORE INTO person_projects (person_id, project_id, role) VALUES (?,?,?)', [id, projId, role]);
    }
  }

  execDb("INSERT INTO person_activity (person_id, type, summary, source, created_at) VALUES (?,'created',?,?,?)",
    [id, `Added ${b.name}`, scope.username, now]);

  const created = queryDb('SELECT * FROM people WHERE id = ?', [id]);
  res.json(created[0] || { id, created: true });
});

// ── Update person ───────────────────────────────────────────────

router.patch('/:id', (req: Request, res: Response) => {
  const existing = queryDb('SELECT * FROM people WHERE id = ?', [String(req.params.id)]);
  if (!existing.length) { res.status(404).json({ error: 'Not found' }); return; }

  const allowed = ['name','aliases','title','company','email','phone','linkedin','facebook','instagram',
    'website','relationship','context','goals','beliefs','environment','situation','last_contact','source',
    'pipeline_id','stage_id'];
  const sets: string[] = [];
  const params: any[] = [];

  for (const f of allowed) {
    if (f in req.body) {
      sets.push(`${f} = ?`);
      params.push(f === 'aliases' ? JSON.stringify(req.body[f]) : req.body[f]);
    }
  }
  if (!sets.length) { res.json({ updated: false }); return; }
  sets.push('updated_at = ?'); params.push(new Date().toISOString()); params.push(String(req.params.id));
  execDb(`UPDATE people SET ${sets.join(', ')} WHERE id = ?`, params);

  // Log stage transition
  if ('stage_id' in req.body && req.body.stage_id !== existing[0].stage_id) {
    const oldStage = queryDb('SELECT name FROM pipeline_stages WHERE id = ?', [existing[0].stage_id || '']);
    const newStage = queryDb('SELECT name FROM pipeline_stages WHERE id = ?', [req.body.stage_id || '']);
    const fromName = oldStage.length ? oldStage[0].name : 'none';
    const toName = newStage.length ? newStage[0].name : 'none';
    execDb("INSERT INTO person_activity (person_id, type, summary, source, created_at) VALUES (?,?,?,?,?)",
      [String(req.params.id), 'stage_change', `${fromName} → ${toName}`, 'dashboard', new Date().toISOString()]);
  }

  const updated = queryDb('SELECT * FROM people WHERE id = ?', [String(req.params.id)]);
  res.json(updated[0] || { updated: true });
});

// ── Delete person ───────────────────────────────────────────────

router.delete('/:id', (_req: Request, res: Response) => {
  execDb('DELETE FROM person_projects WHERE person_id = ?', [String(_req.params.id)]);
  execDb('DELETE FROM person_connections WHERE person_a = ? OR person_b = ?', [String(_req.params.id), String(_req.params.id)]);
  execDb('DELETE FROM person_activity WHERE person_id = ?', [String(_req.params.id)]);
  execDb('DELETE FROM people WHERE id = ?', [String(_req.params.id)]);
  res.json({ deleted: true });
});

// ── Connections ─────────────────────────────────────────────────

router.post('/:id/connections', (req: Request, res: Response) => {
  const { other_id, relationship, context } = req.body;
  if (!other_id || !relationship) { res.status(400).json({ error: 'other_id and relationship required' }); return; }
  const id = nanoid('conn');
  execDb(`INSERT INTO person_connections (id, tenant_id, person_a, person_b, relationship, context, created_at) VALUES (?,'operator',?,?,?,?,?)`,
    [id, String(req.params.id), other_id, relationship, context || null, new Date().toISOString()]);
  res.json({ id, created: true });
});

// ── Project links ───────────────────────────────────────────────

router.post('/:id/projects', (req: Request, res: Response) => {
  const { project_id, role } = req.body;
  if (!project_id) { res.status(400).json({ error: 'project_id required' }); return; }
  execDb('INSERT OR IGNORE INTO person_projects (person_id, project_id, role) VALUES (?,?,?)',
    [String(req.params.id), project_id, role || null]);
  res.json({ linked: true });
});

// ── Activity ────────────────────────────────────────────────────

router.post('/:id/activity', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const { type, summary } = req.body;
  if (!type || !summary) { res.status(400).json({ error: 'type and summary required' }); return; }
  execDb("INSERT INTO person_activity (person_id, type, summary, source, created_at) VALUES (?,?,?,?,?)",
    [String(req.params.id), type, summary, scope.username, new Date().toISOString()]);
  res.json({ logged: true });
});

// ── Pipelines ───────────────────────────────────────────────────

// GET /api/people/pipelines — list all pipelines with stages + person counts
router.get('/pipelines/all', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const tw = scope.isAdmin ? '' : ` AND tenant_id = '${scope.clientScope || scope.username}'`;

  const pipelines = queryDb(`SELECT * FROM pipelines WHERE 1=1${tw} ORDER BY sort_order`);
  for (const p of pipelines) {
    p.stages = queryDb('SELECT * FROM pipeline_stages WHERE pipeline_id = ? ORDER BY sort_order', [p.id]);
    // Count people per stage
    for (const s of p.stages) {
      const count = queryDb('SELECT COUNT(*) as count FROM people WHERE stage_id = ?', [s.id]);
      s.person_count = count.length ? count[0].count : 0;
    }
    const totalCount = queryDb('SELECT COUNT(*) as count FROM people WHERE pipeline_id = ?', [p.id]);
    p.person_count = totalCount.length ? totalCount[0].count : 0;
  }

  res.json({ pipelines, total: pipelines.length });
});

// GET /api/people/pipelines/:id/people — people in a pipeline, grouped by stage
router.get('/pipelines/:id/people', (req: Request, res: Response) => {
  const pipelineId = String(req.params.id);
  const stages = queryDb('SELECT * FROM pipeline_stages WHERE pipeline_id = ? ORDER BY sort_order', [pipelineId]);

  const columns: any[] = [];
  for (const stage of stages) {
    const people = queryDb(
      'SELECT * FROM people WHERE pipeline_id = ? AND stage_id = ? ORDER BY name',
      [pipelineId, stage.id]
    );
    // Attach minimal project info
    for (const p of people) {
      p.projects = queryDb('SELECT project_id, role FROM person_projects WHERE person_id = ?', [p.id]);
    }
    columns.push({ stage, people });
  }

  // Also get unassigned (in pipeline but no stage)
  const unassigned = queryDb(
    'SELECT * FROM people WHERE pipeline_id = ? AND (stage_id IS NULL OR stage_id = ?)',
    [pipelineId, '']
  );

  res.json({ pipeline_id: pipelineId, columns, unassigned });
});

// POST /api/people/pipelines — create new pipeline (tenant-customizable)
router.post('/pipelines/create', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const { name, stages, color } = req.body;
  if (!name) { res.status(400).json({ error: 'name required' }); return; }

  const id = nanoid('pipeline');
  const tenantId = scope.isAdmin ? (req.body.tenant_id || loadConfig().operatorId) : (scope.clientScope || scope.username);
  const now = new Date().toISOString();

  execDb('INSERT INTO pipelines (id, tenant_id, name, color, created_at) VALUES (?,?,?,?,?)',
    [id, tenantId, name, color || '#6b7280', now]);

  if (stages && Array.isArray(stages)) {
    for (let i = 0; i < stages.length; i++) {
      const sid = `${id}_s${i}`;
      execDb('INSERT INTO pipeline_stages (id, pipeline_id, tenant_id, name, sort_order, created_at) VALUES (?,?,?,?,?,?)',
        [sid, id, tenantId, stages[i], i, now]);
    }
  }

  res.json({ id, created: true });
});

export default router;
