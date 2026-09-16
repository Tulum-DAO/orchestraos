import { Router, type Request, type Response } from 'express';
import { getProjectsKnowledge, getRegistry, getAllAgentStates } from '../services/state-reader.js';
import { queryDb, execDb } from '../lib/db.js';
import { loadConfig } from '../lib/config.js';

const router = Router();

function getTenantScope(req: any) {
  const role = (req.headers['x-orchestra-role'] as string) || 'admin';
  const clientScope = (req.headers['x-orchestra-client'] as string) || null;
  return { isAdmin: role === 'admin', clientScope: role === 'admin' ? null : clientScope };
}

function nanoid(): string {
  return 'blocker_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

router.get('/', (_req: Request, res: Response) => {
  try {
    const projects = getProjectsKnowledge();
    if (!projects) { res.json({ projects: [], total: 0 }); return; }
    const registry = getRegistry() as Record<string, any> | null;
    const agentStates = getAllAgentStates();

    const list: any[] = Object.entries(projects).map(([slug, proj]: [string, any]) => {
      const agents = (proj.agents || []).map((agentId: string) => {
        const regEntry = registry?.[agentId];
        const stateEntry = agentStates[agentId];
        return { id: agentId, name: regEntry?.name || agentId, alive: stateEntry?.alive === true, tier: regEntry?.tier || 'T3', current_task: stateEntry?.current_task || null };
      });
      const blockers = queryDb("SELECT COUNT(*) as count FROM project_blockers WHERE project_id = ? AND status = 'active'", [slug]);
      return {
        slug, name: proj.name, status: proj.status, priority: proj.priority, vertical: proj.vertical,
        summary: proj.summary, current_state: proj.current_state, pm: proj.pm || null, repo: proj.repo || null,
        team: proj.team || [], agents, active_agents: agents.filter((a: any) => a.alive).length,
        total_agents: agents.length, blocker_count: blockers.length ? blockers[0].count : 0,
      };
    });

    list.sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99));

    const scope = getTenantScope(_req);
    let visible = list;
    if (!scope.isAdmin && scope.clientScope) {
      visible = list.filter((p: any) => p.slug === scope.clientScope || p.client === scope.clientScope);
    }

    res.json({ projects: visible, total: visible.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load projects', detail: String(err) });
  }
});

router.get('/:slug', (req: Request, res: Response) => {
  try {
    const projects = getProjectsKnowledge();
    if (!projects) { res.status(404).json({ error: 'No projects data' }); return; }
    const slug = String(req.params.slug);
    const proj = projects[slug];
    if (!proj) { res.status(404).json({ error: `Project '${slug}' not found` }); return; }
    const registry = getRegistry() as Record<string, any> | null;
    const agentStates = getAllAgentStates();
    const agents = (proj.agents || []).map((agentId: string) => {
      const regEntry = registry?.[agentId]; const stateEntry = agentStates[agentId];
      return { id: agentId, name: regEntry?.name || agentId, alive: stateEntry?.alive === true, tier: regEntry?.tier || 'T3', current_task: stateEntry?.current_task || null, machine: regEntry?.machine || null };
    });
    const blockers = queryDb("SELECT * FROM project_blockers WHERE project_id = ? AND status = 'active' ORDER BY created_at DESC", [slug]);
    res.json({ slug, name: proj.name, status: proj.status, priority: proj.priority, vertical: proj.vertical, summary: proj.summary, current_state: proj.current_state, pm: proj.pm || null, repo: proj.repo || null, team: proj.team || [], agents, aliases: proj.aliases || [], blockers });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load project', detail: String(err) });
  }
});

// Blockers CRUD
router.get('/:slug/blockers', (req: Request, res: Response) => {
  const blockers = queryDb("SELECT * FROM project_blockers WHERE project_id = ? ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, created_at DESC", [String(req.params.slug)]);
  res.json({ blockers, total: blockers.length });
});

router.post('/:slug/blockers', (req: Request, res: Response) => {
  const { description, type, linked_id } = req.body;
  if (!description) { res.status(400).json({ error: 'description required' }); return; }
  const id = nanoid();
  const username = (req.headers['x-orchestra-user'] as string) || loadConfig().operatorId;
  execDb("INSERT INTO project_blockers (id, tenant_id, project_id, description, type, linked_id, created_by, created_at) VALUES (?,'operator',?,?,?,?,?,?)",
    [id, String(req.params.slug), description, type || 'general', linked_id || null, username, new Date().toISOString()]);
  const created = queryDb('SELECT * FROM project_blockers WHERE id = ?', [id]);
  res.json(created[0] || { id, created: true });
});

router.patch('/:slug/blockers/:id', (req: Request, res: Response) => {
  if (req.body.status === 'resolved') {
    execDb("UPDATE project_blockers SET status = 'resolved', resolved_at = ? WHERE id = ?", [new Date().toISOString(), String(req.params.id)]);
  }
  const updated = queryDb('SELECT * FROM project_blockers WHERE id = ?', [String(req.params.id)]);
  res.json(updated[0] || { updated: true });
});

export default router;
