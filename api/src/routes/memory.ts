import { Router, type Request, type Response } from 'express';
import { existsSync, readFileSync, writeFileSync, readdirSync, mkdirSync, appendFileSync } from 'fs';
import { join, basename } from 'path';
import {
  getContextLayer,
  getGlobalFacts,
  getProjectFacts,
  getAllProjects,
  getAllHandoffs,
  getHandoff,
} from '../services/state-reader.js';
import { homedir } from 'os';
import { loadConfig } from '../lib/config.js';

const router = Router();

// GET /context — context_layer.json
router.get('/context', (_req: Request, res: Response) => {
  try {
    const data = getContextLayer();
    if (!data) {
      res.status(404).json({ error: 'Context layer not found' });
      return;
    }
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: 'Failed to load context', detail: String(err) });
  }
});

// GET /facts — global or ?project=X
router.get('/facts', (req: Request, res: Response) => {
  try {
    const project = req.query.project as string | undefined;
    const data = project ? getProjectFacts(project) : getGlobalFacts();
    if (!data) {
      res.status(404).json({ error: project ? `Facts not found for project '${project}'` : 'Global facts not found' });
      return;
    }
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: 'Failed to load facts', detail: String(err) });
  }
});

// GET /handoffs — all handoffs
router.get('/handoffs', (_req: Request, res: Response) => {
  try {
    const handoffs = getAllHandoffs();
    res.json({ handoffs });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load handoffs', detail: String(err) });
  }
});

// GET /projects — all projects with facts + handoff status
router.get('/projects', (_req: Request, res: Response) => {
  try {
    const projectNames = getAllProjects();
    const projects = projectNames.map((name) => {
      const facts = getProjectFacts(name);
      const handoff = getHandoff(name);
      return {
        name,
        has_facts: facts !== null,
        fact_count: facts && Array.isArray((facts as Record<string, unknown>).facts)
          ? ((facts as Record<string, unknown>).facts as unknown[]).length
          : 0,
        has_handoff: handoff !== null,
        handoff_length: handoff?.length ?? 0,
      };
    });
    res.json({ projects, total: projects.length });
  } catch (err) {
    res.status(500).json({ error: 'Failed to load projects', detail: String(err) });
  }
});

// PATCH /context — update context_layer.json fields
router.patch('/context', (req: Request, res: Response) => {
  try {
    const contextFile = join(process.env.OMNI_DIR!, 'global', 'context_layer.json');
    if (!existsSync(contextFile)) {
      res.status(404).json({ error: 'No context file' });
      return;
    }
    const current = JSON.parse(readFileSync(contextFile, 'utf-8'));
    if (req.body.top_of_mind !== undefined) current.top_of_mind = req.body.top_of_mind;
    current.last_updated = new Date().toISOString();
    writeFileSync(contextFile, JSON.stringify(current, null, 2));
    res.json(current);
  } catch (err) {
    res.status(500).json({ error: 'Failed to update context', detail: String(err) });
  }
});

// ── Unified Memory Search ────────────────────────────────────────────
const HOME_DIR = process.env.HOME || homedir();
// Claude CLI mangles the cwd into its project-dir name by replacing "/" with "-".
const MANGLED_HOME = '-' + HOME_DIR.replace(/^\//, '').replace(/\//g, '-');
const AUTO_MEMORY_DIR = join(HOME_DIR, `.claude/projects/${MANGLED_HOME}/memory`);
const OMNI_DIR = process.env.OMNI_DIR || join(process.env.ORCHESTRA_DIR || loadConfig().dataDir, 'facts');

interface SearchResult {
  source: string;
  score: number;
  [key: string]: unknown;
}

function scoreMatch(haystack: string, query: string): number {
  const h = haystack.toLowerCase();
  const q = query.toLowerCase();
  if (h === q) return 100;
  // Exact phrase match
  if (h.includes(q)) {
    // Boost if it appears early or in a short field
    const pos = h.indexOf(q);
    const ratio = q.length / h.length;
    return 80 + ratio * 15 - Math.min(pos / 100, 10);
  }
  // Word-level partial: check if all query words appear
  const words = q.split(/\s+/).filter(Boolean);
  const matched = words.filter((w) => h.includes(w));
  if (matched.length === words.length) return 60 + (matched.length / words.length) * 15;
  if (matched.length > 0) return 20 + (matched.length / words.length) * 30;
  return 0;
}

function parseFrontmatter(content: string): { name: string; description: string; type: string; body: string } {
  const fm = { name: '', description: '', type: '', body: content };
  const match = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) return fm;
  fm.body = match[2];
  for (const line of match[1].split('\n')) {
    const kv = line.match(/^(\w+):\s*(.*)$/);
    if (!kv) continue;
    const [, key, val] = kv;
    if (key === 'name') fm.name = val.trim();
    else if (key === 'description') fm.description = val.trim();
    else if (key === 'type') fm.type = val.trim();
  }
  return fm;
}

function searchAutoMemory(query: string): SearchResult[] {
  const results: SearchResult[] = [];
  try {
    const files = readdirSync(AUTO_MEMORY_DIR).filter((f) => f.endsWith('.md') && f !== 'MEMORY.md');
    for (const file of files) {
      try {
        const raw = readFileSync(join(AUTO_MEMORY_DIR, file), 'utf-8');
        const fm = parseFrontmatter(raw);
        const searchable = `${file} ${fm.name} ${fm.description} ${fm.body}`;
        const score = scoreMatch(searchable, query);
        if (score > 0) {
          // Find snippet around match
          const qLower = query.toLowerCase();
          const bodyLower = fm.body.toLowerCase();
          const idx = bodyLower.indexOf(qLower);
          let snippet: string;
          if (idx >= 0) {
            const start = Math.max(0, idx - 40);
            snippet = fm.body.substring(start, start + 200).trim();
          } else {
            snippet = fm.body.substring(0, 200).trim();
          }
          results.push({
            source: 'auto-memory',
            file,
            name: fm.name,
            description: fm.description,
            type: fm.type,
            snippet,
            score,
          });
        }
      } catch { /* skip unreadable files */ }
    }
  } catch { /* dir doesn't exist */ }
  results.sort((a, b) => b.score - a.score);
  return results.slice(0, 5);
}

interface OmniFact {
  id?: number;
  category?: string;
  fact?: string;
  confidence?: number;
  tags?: string[];
  [key: string]: unknown;
}

function searchOmniFacts(query: string, project?: string): SearchResult[] {
  const results: SearchResult[] = [];
  const sources: Array<{ label: string; path: string }> = [
    { label: 'global', path: join(OMNI_DIR, 'global', 'facts_db.json') },
  ];
  if (project) {
    const safe = basename(project);
    sources.push({ label: project, path: join(OMNI_DIR, 'projects', safe, 'facts_db.json') });
  }
  for (const { label, path: filePath } of sources) {
    try {
      if (!existsSync(filePath)) continue;
      const data = JSON.parse(readFileSync(filePath, 'utf-8'));
      const facts: OmniFact[] = data.facts || [];
      for (const f of facts) {
        const searchable = `${f.fact || ''} ${f.category || ''} ${(f.tags || []).join(' ')}`;
        const score = scoreMatch(searchable, query);
        if (score > 0) {
          results.push({
            source: 'omni-facts',
            project: label,
            fact: f.fact || '',
            category: f.category || '',
            confidence: f.confidence ?? 0,
            score,
          });
        }
      }
    } catch { /* skip */ }
  }
  results.sort((a, b) => b.score - a.score);
  return results.slice(0, 10);
}

function searchOmniHandoffs(query: string): SearchResult[] {
  const results: SearchResult[] = [];
  const projectsDir = join(OMNI_DIR, 'projects');
  try {
    const projects = readdirSync(projectsDir, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name);
    for (const proj of projects) {
      try {
        const handoffPath = join(projectsDir, proj, 'handoff.md');
        if (!existsSync(handoffPath)) continue;
        const content = readFileSync(handoffPath, 'utf-8');
        const score = scoreMatch(content, query);
        if (score > 0) {
          const qLower = query.toLowerCase();
          const contentLower = content.toLowerCase();
          const idx = contentLower.indexOf(qLower);
          let snippet: string;
          if (idx >= 0) {
            const start = Math.max(0, idx - 40);
            snippet = content.substring(start, start + 200).trim();
          } else {
            snippet = content.substring(0, 200).trim();
          }
          results.push({
            source: 'omni-handoff',
            project: proj,
            snippet,
            score,
          });
        }
      } catch { /* skip */ }
    }
  } catch { /* dir doesn't exist */ }
  results.sort((a, b) => b.score - a.score);
  return results.slice(0, 5);
}

// POST /handoff — write a session handoff for a project
router.post('/handoff', (req: Request, res: Response) => {
  try {
    const { project, summary, files_changed, next_steps } = req.body as {
      project?: string;
      summary?: string;
      files_changed?: string[];
      next_steps?: string[];
    };

    if (!project || !summary) {
      res.status(400).json({ error: 'Both "project" and "summary" are required' });
      return;
    }

    const safe = basename(project);
    const projectDir = join(OMNI_DIR, 'projects', safe);
    const handoffFile = join(projectDir, 'handoff.md');
    const eventsFile = join(projectDir, 'recent_events.jsonl');
    const timestamp = new Date().toISOString();

    // Ensure project directory exists
    if (!existsSync(projectDir)) {
      mkdirSync(projectDir, { recursive: true });
    }

    // Build handoff.md
    let content = `# ${safe} — Session Handoff\n\n`;
    content += `**Updated:** ${timestamp}\n`;
    content += `**Session:** ${summary}\n\n`;
    content += `## What was done\n${summary}\n\n`;

    if (files_changed && files_changed.length > 0) {
      content += `## Key files changed\n`;
      for (const f of files_changed) {
        content += `- \`${f}\`\n`;
      }
      content += '\n';
    }

    if (next_steps && next_steps.length > 0) {
      content += `## Next steps\n`;
      for (const step of next_steps) {
        content += `- ${step}\n`;
      }
      content += '\n';
    }

    writeFileSync(handoffFile, content);

    // Append to recent_events.jsonl
    const event = {
      timestamp,
      type: 'handoff',
      summary,
      files_changed: files_changed || [],
      next_steps: next_steps || [],
    };
    appendFileSync(eventsFile, JSON.stringify(event) + '\n');

    res.json({ ok: true, project: safe, handoff_file: handoffFile, timestamp });
  } catch (err) {
    res.status(500).json({ error: 'Failed to write handoff', detail: String(err) });
  }
});

// GET /search — unified memory search across all non-episodic stores
router.get('/search', (req: Request, res: Response) => {
  try {
    const q = (req.query.q as string || '').trim();
    if (!q) {
      res.status(400).json({ error: 'Query parameter "q" is required' });
      return;
    }
    const project = req.query.project as string | undefined;

    const autoMemoryResults = searchAutoMemory(q);
    const factsResults = searchOmniFacts(q, project);
    const handoffResults = searchOmniHandoffs(q);

    // Merge and sort by score descending
    const combined: SearchResult[] = [
      ...autoMemoryResults,
      ...factsResults,
      ...handoffResults,
    ];
    combined.sort((a, b) => b.score - a.score);

    res.json({
      query: q,
      project: project || null,
      total: combined.length,
      results: combined,
    });
  } catch (err) {
    res.status(500).json({ error: 'Search failed', detail: String(err) });
  }
});

export default router;
