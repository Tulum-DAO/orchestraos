import { Router, type Request, type Response } from 'express';
import { writeFileSync, readFileSync, existsSync, mkdirSync, readdirSync, renameSync } from 'fs';
import { join } from 'path';
import { getAllTasks, getPendingContext, getCompletedReports } from '../services/state-reader.js';
import { logInteraction } from '../services/learning.js';
import { loadConfig } from '../lib/config.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const TASKS_DIR = join(ORCHESTRA, 'tasks');
const CLIENTS_DIR = join(ORCHESTRA, 'state', 'clients');

// Load client deliverables as tasks
function getClientDeliverables(): any[] {
  const tasks: any[] = [];
  try {
    if (!existsSync(CLIENTS_DIR)) return tasks;
    for (const slug of readdirSync(CLIENTS_DIR)) {
      const delFile = join(CLIENTS_DIR, slug, 'deliverables.json');
      if (!existsSync(delFile)) continue;
      try {
        const data = JSON.parse(readFileSync(delFile, 'utf-8'));
        const clientName = data.client || slug;
        for (const d of (data.deliverables || [])) {
          tasks.push({
            task_id: `${slug}:${d.id}`,
            description: d.name,
            status: d.status === 'complete' ? 'completed' : d.status === 'review' ? 'in_progress' : d.status || 'pending',
            source: 'client_deliverable',
            priority: 'high',
            routed_to: d.assigned_to || null,
            client: slug,
            client_name: clientName,
            due_date: d.due_date || null,
            completed_date: d.completed_date || null,
            created: data.created || new Date().toISOString(),
            updated: data.last_updated || new Date().toISOString(),
            notes: d.notes || '',
          });
        }
      } catch { /* skip bad files */ }
    }
  } catch { /* no clients dir */ }
  return tasks;
}

router.get('/', (req: Request, res: Response) => {
  try {
    const apiTasks = getAllTasks();
    const clientTasks = getClientDeliverables();

    // Tag API tasks as source=api if not already tagged
    for (const t of apiTasks) {
      if (!t.source) t.source = 'api';
      if (!t.client) t.client = null;
    }

    // Merge — client deliverables + API tasks
    const allTasks = [...clientTasks, ...apiTasks];

    // Optional filter by client
    const clientFilter = req.query.client as string | undefined;
    const filtered = clientFilter && clientFilter !== 'all'
      ? allTasks.filter(t => t.client === clientFilter)
      : allTasks;

    const pendingContext = getPendingContext();
    const completedReports = getCompletedReports();

    const now = new Date();
    const todayStr = now.toISOString().slice(0, 10);

    let active = 0, pending = 0, blocked = 0, completed = 0, failed = 0, today = 0;

    for (const t of filtered) {
      const status = (t.status as string) || '';
      if (['active', 'running', 'in_progress', 'review'].includes(status)) active++;
      else if (['pending', 'queued', 'routing'].includes(status)) pending++;
      else if (['blocked', 'context_wait'].includes(status)) blocked++;
      else if (['completed', 'done', 'complete', 'reported'].includes(status)) completed++;
      else if (['failed', 'error'].includes(status)) failed++;

      const created = (t.created_at as string) || (t.created as string) || '';
      if (created.startsWith(todayStr)) today++;
    }

    // Extract unique clients for filter dropdown
    const clients = [...new Set(allTasks.map(t => t.client).filter(Boolean))].sort();

    res.json({
      tasks: filtered,
      total: filtered.length,
      summary: { today, active, pending, blocked, completed, failed },
      clients,
      pending_context: pendingContext,
      completed_reports: completedReports,
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load tasks', detail: String(err) });
  }
});

// POST /api/tasks/analyze — smart routing: analyze description, suggest client/priority/assignee/type
router.post('/analyze', (req: Request, res: Response) => {
  try {
    const { description } = req.body;
    if (!description) { res.status(400).json({ error: 'description required' }); return; }

    const desc = description.toLowerCase();

    // Load known clients from BOTH state/clients/ AND projects.json
    const clientSlugs: string[] = [];
    const clientNames: Record<string, string> = {};

    // Source 1: state/clients/ directory
    try {
      if (existsSync(CLIENTS_DIR)) {
        for (const slug of readdirSync(CLIENTS_DIR)) {
          const cf = join(CLIENTS_DIR, slug, 'client.json');
          if (existsSync(cf)) {
            const c = JSON.parse(readFileSync(cf, 'utf-8'));
            clientSlugs.push(slug);
            clientNames[slug] = c.name || slug;
          }
        }
      }
    } catch { /* */ }

    // Load projects for keyword matching
    let projects: Record<string, any> = {};
    try {
      const pf = join(ORCHESTRA, 'state', 'knowledge', 'projects.json');
      if (existsSync(pf)) projects = JSON.parse(readFileSync(pf, 'utf-8'));
    } catch { /* */ }

    // Source 2: projects.json client/partner entries
    for (const [pid, proj] of Object.entries(projects)) {
      if (clientSlugs.includes(pid)) continue; // already from clients dir
      const vert = (proj as any).vertical || '';
      const status = (proj as any).status || '';
      if (vert === 'client' || vert === 'partner' || status.startsWith('client')) {
        clientSlugs.push(pid);
        clientNames[pid] = (proj as any).name || pid;
      }
    }

    // Load registry for agent matching
    let agents: Record<string, any> = {};
    try {
      const rf = join(ORCHESTRA, 'registry.json');
      if (existsSync(rf)) agents = JSON.parse(readFileSync(rf, 'utf-8')).agents || {};
    } catch { /* */ }

    // --- Infer type ---
    let type = 'deliverable';
    if (/\b(fix|bug|broken|crash|error|fail)\b/.test(desc)) type = 'bug';
    else if (/\b(research|investigate|look into|find out|explore)\b/.test(desc)) type = 'question';
    else if (/\b(orchestraos|dashboard|infra|deploy|boot|proxy|staging)\b/.test(desc)) type = 'internal';

    // --- Infer client ---
    let client: string | null = null;
    for (const slug of clientSlugs) {
      const name = (clientNames[slug] || '').toLowerCase();
      if (desc.includes(slug) || desc.includes(name)) {
        client = slug;
        break;
      }
    }
    // Check project aliases
    if (!client) {
      for (const [pid, proj] of Object.entries(projects)) {
        const aliases = (proj.aliases || []).map((a: string) => a.toLowerCase());
        const pname = (proj.name || '').toLowerCase();
        if (desc.includes(pname) || aliases.some((a: string) => desc.includes(a))) {
          // Check if this project is a client
          if (proj.vertical === 'client' || proj.status === 'client_active') {
            client = pid;
          }
          break;
        }
      }
    }

    // --- Infer priority ---
    let priority = 'medium';
    if (/\b(urgent|asap|critical|immediately|now)\b/.test(desc)) priority = 'critical';
    else if (/\b(important|high priority|today|soon)\b/.test(desc)) priority = 'high';
    else if (/\b(low priority|whenever|backlog|nice to have)\b/.test(desc)) priority = 'low';

    // --- Infer assignee ---
    let assignee: string | null = null;
    if (client) {
      // Route to client PM
      assignee = `pm-${client}`;
      if (!agents[assignee]) {
        // Fall back to pm-clients
        assignee = 'pm-clients';
      }
    } else if (type === 'internal' || type === 'bug') {
      assignee = 'pm-infra';
    } else if (type === 'question') {
      assignee = 'gm';
    } else {
      assignee = 'pm-products';
    }

    // Check for specific agent mentions
    for (const aid of Object.keys(agents)) {
      if (desc.includes(aid)) {
        assignee = aid;
        break;
      }
    }

    res.json({
      type,
      client,
      client_name: client ? clientNames[client] || client : null,
      priority,
      assignee,
      assignee_name: assignee ? (agents[assignee]?.name || assignee) : null,
      available_clients: clientSlugs.map(s => ({ slug: s, name: clientNames[s] || s })),
      available_types: ['deliverable', 'internal', 'question', 'bug'],
    });
  } catch (err) {
    res.status(500).json({ error: 'Analysis failed', detail: String(err) });
  }
});

// POST /api/tasks — create a new task
router.post('/', (req: Request, res: Response) => {
  try {
    const { description, priority, assignee, type, client, due_date, source: reqSource } = req.body;
    if (!description) { res.status(400).json({ error: 'description required' }); return; }

    if (!existsSync(TASKS_DIR)) mkdirSync(TASKS_DIR, { recursive: true });

    const now = new Date();
    const taskId = `task_${now.toISOString().replace(/[-:T]/g, '').slice(0, 14)}_${Math.floor(Math.random() * 1000)}`;
    const task = {
      task_id: taskId,
      description,
      type: type || 'deliverable',
      source: reqSource || 'dashboard',
      priority: priority || 'medium',
      status: 'pending',
      routed_to: assignee || null,
      client: client || null,
      due_date: due_date || null,
      agents_spawned: [],
      agents_complete: [],
      agents_in_progress: [],
      context_questions: [],
      context_answers: [],
      pm_report: null,
      final_report_sent: false,
      notify_telegram: reqSource === 'telegram' || reqSource === 'jarvis',
      created: now.toISOString(),
      updated: now.toISOString(),
    };

    // --- Auto-bundle client context ---
    if (client) {
      const clientContext: any = {};

      // client.json
      const clientFile = join(CLIENTS_DIR, client, 'client.json');
      if (existsSync(clientFile)) {
        try { clientContext.client_record = JSON.parse(readFileSync(clientFile, 'utf-8')); } catch {}
      }

      // deliverables.json
      const delFile = join(CLIENTS_DIR, client, 'deliverables.json');
      if (existsSync(delFile)) {
        try { clientContext.deliverables = JSON.parse(readFileSync(delFile, 'utf-8')); } catch {}
      }

      // session-recap.md
      const recapFile = join(CLIENTS_DIR, client, 'session-recap.md');
      if (existsSync(recapFile)) {
        try { clientContext.session_recap_path = recapFile; } catch {}
      }

      // projects.json entry
      const projFile = join(ORCHESTRA, 'state', 'knowledge', 'projects.json');
      if (existsSync(projFile)) {
        try {
          const projs = JSON.parse(readFileSync(projFile, 'utf-8'));
          if (projs[client]) clientContext.project = projs[client];
        } catch {}
      }

      (task as any).client_context = clientContext;
    }

    // --- Also bundle project context for non-client tasks ---
    if (!client) {
      const projFile = join(ORCHESTRA, 'state', 'knowledge', 'projects.json');
      if (existsSync(projFile)) {
        try {
          const projs = JSON.parse(readFileSync(projFile, 'utf-8'));
          // Match project from description keywords
          const descLower = description.toLowerCase();
          for (const [pid, proj] of Object.entries(projs) as [string, any][]) {
            const aliases = (proj.aliases || []).map((a: string) => a.toLowerCase());
            if (descLower.includes(pid) || aliases.some((a: string) => descLower.includes(a))) {
              (task as any).project_context = { slug: pid, ...proj };
              break;
            }
          }
        } catch {}
      }
    }

    writeFileSync(join(TASKS_DIR, `${taskId}.json`), JSON.stringify(task, null, 2));

    // --- Route to agent inbox with full context ---
    const routeTo = (task as any).routed_to;
    if (routeTo) {
      const inboxDir = join(ORCHESTRA, 'queue', 'inbox', routeTo);
      mkdirSync(inboxDir, { recursive: true });

      const inboxMsg: any = {
        type: 'task',
        task_id: taskId,
        description,
        priority: (task as any).priority,
        client: client || null,
        due_date: (task as any).due_date,
        created: (task as any).created,
      };

      // Include context paths so the agent knows what to read
      if (client) {
        const clientDir = join(CLIENTS_DIR, client);
        inboxMsg.context_files = [];
        if (existsSync(join(clientDir, 'client.json'))) inboxMsg.context_files.push(join(clientDir, 'client.json'));
        if (existsSync(join(clientDir, 'deliverables.json'))) inboxMsg.context_files.push(join(clientDir, 'deliverables.json'));
        if (existsSync(join(clientDir, 'session-recap.md'))) inboxMsg.context_files.push(join(clientDir, 'session-recap.md'));
        inboxMsg.instruction = `Read all context_files before starting this task. Client: ${(task as any).client_context?.client_record?.name || client}`;
      }

      writeFileSync(
        join(inboxDir, `${taskId}.json`),
        JSON.stringify(inboxMsg, null, 2)
      );

      // Immediate dispatch: check if agent is alive, if not spawn it
      const { execFile } = require('child_process');
      const tmuxCheck = require('child_process').execFileSync;
      try {
        // Check if agent tmux session exists
        tmuxCheck('tmux', ['has-session', '-t', routeTo], { timeout: 3000 });
        // Agent is alive — inject the task directly for immediate execution
        const taskPrompt = `You have a new task (${taskId}): ${description}` +
          (client ? `. Client: ${client}.` : '') +
          (inboxMsg.context_files ? ` Read these files first: ${inboxMsg.context_files.join(', ')}` : '') +
          `. Priority: ${(task as any).priority}. Start immediately and update task status when done via: curl -X PATCH http://localhost:8888/api/tasks/${taskId} -H "Content-Type: application/json" -d '{"status":"completed"}'`;
        execFile('tmux', ['send-keys', '-t', routeTo, taskPrompt, 'Enter'], { timeout: 5000 }, () => {});

        // Mark task as in_progress
        (task as any).status = 'in_progress';
        writeFileSync(join(TASKS_DIR, `${taskId}.json`), JSON.stringify(task, null, 2));
      } catch {
        // Agent not running — try to spawn it
        try {
          const spawnScript = join(ORCHESTRA, 'spawn-agent.sh');
          execFile('bash', [spawnScript, routeTo], { timeout: 30000 }, () => {});
        } catch { /* spawn failed, task stays pending */ }
      }
    }

    // LEARNING: Log task creation
    const user = (req.headers['x-orchestra-user'] as string) || loadConfig().operatorId;
    logInteraction({
      channel: 'dashboard',
      userId: user,
      input: description.slice(0, 500),
      tool: 'create_task',
      toolArgs: { priority, client, assignee: routeTo },
      toolResult: 'success',
      response: `Task ${taskId} created, routed to ${routeTo || 'unassigned'}`,
    });

    res.json(task);
  } catch (err) {
    res.status(500).json({ error: 'Failed to create task', detail: String(err) });
  }
});

// PATCH /api/tasks/:id — update task status, priority, description, assignee
router.patch('/:id', (req: Request, res: Response) => {
  try {
    const taskId = req.params.id;
    const taskFile = join(TASKS_DIR, `${taskId}.json`);

    if (!existsSync(taskFile)) {
      res.status(404).json({ error: `Task '${taskId}' not found` });
      return;
    }

    const task = JSON.parse(readFileSync(taskFile, 'utf-8'));
    const allowed = ['status', 'priority', 'description', 'routed_to'];
    for (const key of allowed) {
      if (req.body[key] !== undefined) {
        task[key] = req.body[key];
      }
    }
    task.updated = new Date().toISOString();

    writeFileSync(taskFile, JSON.stringify(task, null, 2));
    res.json(task);
  } catch (err) {
    res.status(500).json({ error: 'Failed to update task', detail: String(err) });
  }
});

// DELETE /api/tasks/:id — archive a task
router.delete('/:id', (req: Request, res: Response) => {
  try {
    const taskId = req.params.id;
    const taskFile = join(TASKS_DIR, `${taskId}.json`);

    if (!existsSync(taskFile)) {
      res.status(404).json({ error: `Task '${taskId}' not found` });
      return;
    }

    const archiveDir = join(TASKS_DIR, 'archived');
    if (!existsSync(archiveDir)) mkdirSync(archiveDir, { recursive: true });

    renameSync(taskFile, join(archiveDir, `${taskId}.json`));
    res.json({ archived: true, task_id: taskId });
  } catch (err) {
    res.status(500).json({ error: 'Failed to archive task', detail: String(err) });
  }
});

export default router;
