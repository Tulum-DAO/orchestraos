/**
 * tasks-v2.ts — Task CRUD backed by SQLite (state/tasks.db).
 */

import { Router, type Request, type Response } from 'express';
import { queryDb, execDb } from '../lib/db.js';

const router = Router();

function getTenantScope(req: any) {
  const role = (req.headers['x-orchestra-role'] as string) || 'admin';
  const clientScope = (req.headers['x-orchestra-client'] as string) || null;
  const username = (req.headers['x-orchestra-user'] as string) || 'admin';
  return { isAdmin: role === 'admin', clientScope: role === 'admin' ? null : clientScope, username };
}

function nanoid(): string {
  return 'task_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

// PROJECT_KW/PM_ROUTE are a title-keyword classifier example — populate with
// your own projects and clients. "orchestraos" (the harness itself) is the
// only entry with a real meaning out of the box; acme/northwind are a
// generic example pair to show the shape.
const PROJECT_KW: Record<string, string[]> = {
  orchestraos: ['orchestraos','dashboard','agent','telegram','jarvis','proxy','vps','deploy','tmux'],
  acme: ['acme'],
  northwind: ['northwind'],
};
const PRIO_KW: Record<string, string> = {
  asap:'critical',urgent:'critical',broken:'critical',important:'high',fix:'high',bug:'high','nice to have':'low',eventually:'low',minor:'low',
};
const PM_ROUTE: Record<string, string> = {
  orchestraos:'pm-infra',acme:'pm-products',northwind:'pm-clients',
};
// Which PROJECT_KW keys are "clients" (vs. internal products) — used to
// auto-fill t.client below.
const CLIENT_PROJECTS = ['northwind'];

function enrichTask(t: any): any {
  const title = (t.title || '').toLowerCase();
  if (!t.project_id) { for (const [p, kw] of Object.entries(PROJECT_KW)) { if (kw.some(k => title.includes(k))) { t.project_id = p; break; } } }
  if (!t.priority || t.priority === 'medium') { for (const [s, p] of Object.entries(PRIO_KW)) { if (title.includes(s)) { t.priority = p; break; } } }
  if (!t.client && t.project_id && CLIENT_PROJECTS.includes(t.project_id)) t.client = t.project_id;
  return t;
}

router.get('/', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const tw = scope.isAdmin ? '' : ' AND tenant_id = ?';
  const twParams = scope.isAdmin ? [] : [scope.clientScope || scope.username];
  const conds: string[] = ['1=1' + tw]; const params: any[] = [...twParams];

  if (req.query.status) { const s = (req.query.status as string).split(','); conds.push(`status IN (${s.map(()=>'?').join(',')})`); params.push(...s); }
  if (req.query.project) { conds.push('project_id = ?'); params.push(req.query.project); }
  if (req.query.client && req.query.client !== 'all') { conds.push('client = ?'); params.push(req.query.client); }
  if (req.query.assigned_to) { conds.push('assigned_to = ?'); params.push(req.query.assigned_to); }
  if (req.query.priority) { const p = (req.query.priority as string).split(','); conds.push(`priority IN (${p.map(()=>'?').join(',')})`); params.push(...p); }
  if (req.query.phase) { conds.push('phase_id = ?'); params.push(req.query.phase); }
  if (req.query.parent === 'null') conds.push('parent_id IS NULL');
  else if (req.query.parent) { conds.push('parent_id = ?'); params.push(req.query.parent); }
  if (req.query.cohort) { conds.push('cohort_id = ?'); params.push(req.query.cohort); }
  if (req.query.search) { conds.push('(title LIKE ? OR description LIKE ?)'); params.push(`%${req.query.search}%`, `%${req.query.search}%`); }

  const sort = (req.query.sort as string) || 'created_at';
  const dir = (req.query.dir as string) === 'asc' ? 'ASC' : 'DESC';
  const limit = Math.min(parseInt(req.query.limit as string) || 100, 500);
  const offset = parseInt(req.query.offset as string) || 0;

  const w = conds.join(' AND ');
  const tasks = queryDb(`SELECT * FROM tasks WHERE ${w} ORDER BY ${sort} ${dir} LIMIT ${limit} OFFSET ${offset}`, params);
  const counts = queryDb(`SELECT status, COUNT(*) as count FROM tasks WHERE 1=1${tw} AND parent_id IS NULL GROUP BY status`, twParams);
  const summary: Record<string, number> = {}; for (const c of counts) summary[c.status] = c.count;
  const clients = queryDb(`SELECT DISTINCT client FROM tasks WHERE client IS NOT NULL${tw}`, twParams).map((r: any) => r.client);
  const projects = queryDb(`SELECT DISTINCT project_id FROM tasks WHERE project_id IS NOT NULL${tw}`, twParams).map((r: any) => r.project_id);

  res.json({ tasks, total: tasks.length, summary, clients, projects });
});

router.get('/:id', (req: Request, res: Response) => {
  const tasks = queryDb('SELECT * FROM tasks WHERE id = ?', [req.params.id]);
  if (!tasks.length) { res.status(404).json({ error: 'Not found' }); return; }
  const activity = queryDb('SELECT * FROM task_activity WHERE task_id = ? ORDER BY created_at DESC LIMIT 50', [req.params.id]);
  const subtasks = queryDb('SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at', [req.params.id]);
  res.json({ ...tasks[0], activity, subtasks });
});

router.post('/', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  let task = { ...req.body };
  if (!task.title) { res.status(400).json({ error: 'title required' }); return; }
  if (task.parent_id) {
    const parent = queryDb('SELECT parent_id FROM tasks WHERE id = ?', [task.parent_id]);
    if (parent.length && parent[0].parent_id) { res.status(400).json({ error: 'Max 2 levels' }); return; }
  }
  const id = task.id || nanoid();
  const tenantId = scope.isAdmin ? (task.tenant_id || 'admin') : (scope.clientScope || scope.username);
  const createdBy = task.created_by || scope.username;
  task = enrichTask(task);
  const routedTo = task.assigned_to || (task.project_id && PM_ROUTE[task.project_id]) || null;
  const now = new Date().toISOString();

  execDb(`INSERT INTO tasks (id,tenant_id,title,description,status,priority,project_id,phase_id,parent_id,north_star_id,cohort_id,assigned_to,created_by,source,routed_to,client,tags,due_date,blocked_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [id, tenantId, task.title, task.description||null, task.status||'pending', task.priority||'medium',
     task.project_id||null, task.phase_id||null, task.parent_id||null, task.north_star_id||null, task.cohort_id||null,
     task.assigned_to||routedTo, createdBy, task.source||'dashboard', routedTo,
     task.client||null, task.tags?JSON.stringify(task.tags):null, task.due_date||null,
     task.blocked_by?JSON.stringify(task.blocked_by):null, now, now]);

  execDb("INSERT INTO task_activity (task_id,actor,action,to_value) VALUES (?,'system','created',?)", [id, task.title]);
  const created = queryDb('SELECT * FROM tasks WHERE id = ?', [id]);
  res.json(created[0] || { id, created: true });
});

router.patch('/:id', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const existing = queryDb('SELECT * FROM tasks WHERE id = ?', [req.params.id]);
  if (!existing.length) { res.status(404).json({ error: 'Not found' }); return; }
  const task = existing[0]; const u = req.body;
  const sets: string[] = []; const params: any[] = [];
  const allowed = ['title','description','status','priority','type','project_id','phase_id','assigned_to','routed_to','client','tags','due_date','blocked_by','deployment_state','north_star_id','cohort_id','tokens_used','time_spent_ms'];

  for (const f of allowed) {
    if (f in u && u[f] !== task[f]) {
      execDb("INSERT INTO task_activity (task_id,actor,action,from_value,to_value) VALUES (?,?,?,?,?)",
        [req.params.id, scope.username, f+'_changed', String(task[f]||''), String(u[f]||'')]);
      sets.push(`${f} = ?`); params.push((f==='tags'||f==='blocked_by') ? JSON.stringify(u[f]) : u[f]);
    }
  }
  if (u.status && u.status !== task.status) {
    if (u.status === 'in_progress' && !task.started_at) { sets.push('started_at = ?'); params.push(new Date().toISOString()); }
    if (u.status === 'completed') { sets.push('completed_at = ?'); params.push(new Date().toISOString()); }
  }
  if (!sets.length) { res.json({ updated: false }); return; }
  sets.push('updated_at = ?'); params.push(new Date().toISOString()); params.push(req.params.id);
  execDb(`UPDATE tasks SET ${sets.join(', ')} WHERE id = ?`, params);

  // Notify creator when task completes
  if (u.status === 'completed' && task.created_by) {
    try {
      const { mkdirSync: mkd, writeFileSync: wf, existsSync: ex } = require('fs');
      const { join: jn } = require('path');
      const oDir = process.env.ORCHESTRA_DIR || jn(process.env.HOME, 'scripts/agent-orchestra');
      const inboxDir = jn(oDir, 'queue', 'inbox', task.created_by);
      if (!ex(inboxDir)) mkd(inboxDir, { recursive: true });
      wf(jn(inboxDir, `${Date.now()}_task_completed.json`), JSON.stringify({
        type: 'task_completed', task_id: task.id, title: task.title,
        completed_by: scope.username, timestamp: new Date().toISOString(),
      }, null, 2));
    } catch { /* non-critical */ }
  }

  // Cohort completion: notify parent when ALL tasks in cohort are done
  if (u.status === 'completed' && task.cohort_id) {
    const c = queryDb("SELECT COUNT(*) as total, SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as done FROM tasks WHERE cohort_id=?", [task.cohort_id]);
    if (c.length && c[0].total === c[0].done) {
      execDb("INSERT INTO task_activity (task_id,actor,action,comment) VALUES (?,'system','cohort_complete',?)",
        [req.params.id, `All ${c[0].total} tasks in cohort ${task.cohort_id} complete`]);
      // Notify the routed_to PM/agent that spawned the cohort
      try {
        const cohortTasks = queryDb("SELECT DISTINCT routed_to FROM tasks WHERE cohort_id=? AND routed_to IS NOT NULL", [task.cohort_id]);
        const { mkdirSync: mkd, writeFileSync: wf, existsSync: ex } = require('fs');
        const { join: jn } = require('path');
        const oDir = process.env.ORCHESTRA_DIR || jn(process.env.HOME, 'scripts/agent-orchestra');
        for (const ct of cohortTasks) {
          const inboxDir = jn(oDir, 'queue', 'inbox', ct.routed_to);
          if (!ex(inboxDir)) mkd(inboxDir, { recursive: true });
          wf(jn(inboxDir, `${Date.now()}_cohort_completed.json`), JSON.stringify({
            type: 'cohort_completed', cohort_id: task.cohort_id,
            total_tasks: c[0].total, message: `All ${c[0].total} tasks complete`,
            timestamp: new Date().toISOString(),
          }, null, 2));
        }
      } catch { /* non-critical */ }
    }
  }
  const updated = queryDb('SELECT * FROM tasks WHERE id = ?', [req.params.id]);
  res.json(updated[0] || { updated: true });
});

router.delete('/:id', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  execDb("UPDATE tasks SET status='cancelled',updated_at=? WHERE id=?", [new Date().toISOString(), req.params.id]);
  execDb("INSERT INTO task_activity (task_id,actor,action) VALUES (?,?,'cancelled')", [req.params.id, scope.username]);
  res.json({ deleted: true });
});

router.post('/:id/enrich', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  const fields = ['project_id','priority','assigned_to','client','tags','phase_id','north_star_id'];
  const sets: string[] = []; const params: any[] = [];
  for (const f of fields) { if (f in req.body) { sets.push(`${f}=?`); params.push(f==='tags'?JSON.stringify(req.body[f]):req.body[f]); } }
  if (!sets.length) { res.json({ enriched: false }); return; }
  sets.push('updated_at=?'); params.push(new Date().toISOString()); params.push(req.params.id);
  execDb(`UPDATE tasks SET ${sets.join(',')} WHERE id=?`, params);
  execDb("INSERT INTO task_activity (task_id,actor,action,comment) VALUES (?,?,'enriched',?)",
    [req.params.id, scope.username, `Fields: ${Object.keys(req.body).join(',')}`]);
  const updated = queryDb('SELECT * FROM tasks WHERE id=?', [req.params.id]);
  res.json(updated[0] || { enriched: true });
});

router.post('/:id/comment', (req: Request, res: Response) => {
  const scope = getTenantScope(req);
  if (!req.body.comment) { res.status(400).json({ error: 'comment required' }); return; }
  execDb("INSERT INTO task_activity (task_id,actor,action,comment) VALUES (?,?,'comment',?)",
    [req.params.id, scope.username, req.body.comment]);
  res.json({ commented: true });
});

export default router;
