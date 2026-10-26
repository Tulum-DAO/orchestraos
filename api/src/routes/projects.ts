import { Router, type Request, type Response } from 'express';
import { getProjectsKnowledge, getRegistry, getAllAgentStates } from '../services/state-reader.js';
import { getDetectorStates, type DetectorStatus } from '../services/agent-status.js';
import { queryDb, execDb } from '../lib/db.js';
import { loadConfig } from '../lib/config.js';
import { actingAgent, tenantScope } from '../lib/principal.js';
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { listProjectFiles, resolveInside, readProjectFile, unfiled } from '../services/project-files.js';

const router = Router();

function getTenantScope(req: any) {
  return tenantScope(req);
}

/** A project's agents as the Projects page shows them: tier and reporting line from the registry's `agents`
 *  map (the flat lookup this replaced missed every seat, so all of them read "T3 stopped"), status from the
 *  same live detector the Agents page uses. */
export async function projectAgents(ids: string[], withMachine = false): Promise<any[]> {
  const registry = getRegistry() as Record<string, any> | null;
  const rows: Record<string, any> = registry?.agents ?? {};
  let detector = new Map<string, DetectorStatus>();
  try { detector = await getDetectorStates(); } catch { /* no scan yet: status unknown, never invented */ }
  const states = getAllAgentStates() as Record<string, any>;
  return ids.map((agentId) => {
    const reg = rows[agentId] || {};
    const d = detector.get(reg.tmux_session || agentId) || detector.get(agentId);
    const status = d?.state || (detector.size ? 'stopped' : 'unknown');
    return {
      id: agentId, name: reg.name || agentId, tier: reg.tier || '—', reports_to: reg.reports_to || null,
      status, activity: d?.activity || '', alive: !!d && !['stopped', 'offline_crashed', 'unknown'].includes(status),
      current_task: states[agentId]?.current_task || null, ...(withMachine ? { machine: reg.machine || null } : {}),
    };
  });
}

function nanoid(): string {
  return 'blocker_' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);
}

router.get('/', async (_req: Request, res: Response) => {
  try {
    const projects = getProjectsKnowledge();
    if (!projects) { res.json({ projects: [], total: 0 }); return; }

    const list: any[] = await Promise.all(Object.entries(projects).map(async ([slug, proj]: [string, any]) => {
      const agents = await projectAgents(proj.agents || []);
      const blockers = queryDb("SELECT COUNT(*) as count FROM project_blockers WHERE project_id = ? AND status = 'active'", [slug]);
      return {
        slug, name: proj.name, status: proj.status, priority: proj.priority, vertical: proj.vertical,
        summary: proj.summary, current_state: proj.current_state, pm: proj.pm || null, repo: proj.repo || null,
        team: proj.team || [], agents, active_agents: agents.filter((a: any) => a.alive).length,
        total_agents: agents.length, blocker_count: blockers.length ? blockers[0].count : 0,
      };
    }));

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

router.get('/:slug', async (req: Request, res: Response) => {
  try {
    const projects = getProjectsKnowledge();
    if (!projects) { res.status(404).json({ error: 'No projects data' }); return; }
    const slug = String(req.params.slug);
    const proj = projects[slug];
    if (!proj) { res.status(404).json({ error: `Project '${slug}' not found` }); return; }
    const agents = await projectAgents(proj.agents || [], true);
    const blockers = queryDb("SELECT * FROM project_blockers WHERE project_id = ? AND status = 'active' ORDER BY created_at DESC", [slug]);
    res.json({ slug, name: proj.name, status: proj.status, priority: proj.priority, vertical: proj.vertical, summary: proj.summary, current_state: proj.current_state, pm: proj.pm || null, repo: proj.repo || null, team: proj.team || [], agents, aliases: proj.aliases || [], blockers });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load project', detail: String(err) });
  }
});

// ── Files (the card's "View files") ──────────────────────────────────
// The project's folder: the card's own `folder`, else <data dir>/projects/<slug>.
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,39}$/;
function projectFolder(req: Request): { folder: string } | { status: number; error: string } {
  const slug = String(req.params.slug);
  if (!SLUG_RE.test(slug)) return { status: 404, error: 'No such project' };
  const scope = getTenantScope(req);
  if (!scope.isAdmin && scope.clientScope && slug !== scope.clientScope) return { status: 404, error: 'No such project' };
  const card = (getProjectsKnowledge() || {})[slug];
  if (!card) return { status: 404, error: `Project '${slug}' not found` };
  const data = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
  const folder = typeof card.folder === 'string' && card.folder ? card.folder : join(data, 'projects', slug);
  if (!existsSync(folder)) return { status: 404, error: 'This project has no folder yet' };
  return { folder };
}

router.get('/:slug/files', (req: Request, res: Response) => {
  const f = projectFolder(req);
  if ('error' in f) { res.status(f.status).json({ error: f.error }); return; }
  const { files, truncated } = listProjectFiles(f.folder);
  res.json({ folder: f.folder, files, total: files.length, truncated, unfiled: unfiled(files.map((x) => x.path)) });
});

router.get('/:slug/files/content', (req: Request, res: Response) => {
  const f = projectFolder(req);
  if ('error' in f) { res.status(f.status).json({ error: f.error }); return; }
  const rel = String(req.query.path || '');
  const abs = resolveInside(f.folder, rel);
  if (!abs) { res.status(404).json({ error: 'No such file in this project' }); return; }
  try {
    const content = readProjectFile(abs, rel);
    if ((content.kind === 'image' || content.kind === 'pdf') && req.query.raw === '1') {
      res.setHeader('Content-Type', content.mime);
      // an image (an SVG can carry script) renders inert; a PDF is left to the browser's own sandboxed viewer
      if (content.kind === 'image') res.setHeader('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'");
      res.setHeader('X-Content-Type-Options', 'nosniff');
      res.send(readFileSync(abs));
      return;
    }
    res.json({ path: rel, ...content });
  } catch (err) {
    res.status(500).json({ error: 'Could not read that file', detail: String(err) });
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
  const username = actingAgent(req) || loadConfig().operatorId;
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
