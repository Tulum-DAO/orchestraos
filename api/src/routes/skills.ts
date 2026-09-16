import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;
const CATALOG_FILE = join(ORCHESTRA, 'state', 'skills-catalog.json');

function loadCatalog() {
  if (!existsSync(CATALOG_FILE)) return { plugins: [], agent_skills: {} };
  try { return JSON.parse(readFileSync(CATALOG_FILE, 'utf-8')); } catch { return { plugins: [], agent_skills: {} }; }
}

function saveCatalog(data: any) {
  writeFileSync(CATALOG_FILE, JSON.stringify(data, null, 2));
}

// GET /api/skills — all plugins with their skills + agent assignments
router.get('/', (_req: Request, res: Response) => {
  const data = loadCatalog();
  const plugins = data.plugins || [];
  const agentSkills = data.agent_skills || {};

  // Flatten all skills across plugins
  const allSkills: any[] = [];
  for (const plugin of plugins) {
    for (const skill of (plugin.skills || [])) {
      allSkills.push({ ...skill, plugin_id: plugin.id, plugin_name: plugin.name });
    }
  }

  // Count installed skills per agent
  const agentCount = Object.keys(agentSkills).length;
  const totalSkills = allSkills.length;
  const totalPlugins = plugins.length;
  const installedPlugins = plugins.filter((p: any) => p.installed).length;

  res.json({
    plugins,
    agent_skills: agentSkills,
    all_skills: allSkills,
    total_plugins: totalPlugins,
    installed_plugins: installedPlugins,
    total_skills: totalSkills,
    agents_with_skills: agentCount
  });
});

// POST /api/skills/assign — assign a skill to an agent
router.post('/assign', (req: Request, res: Response) => {
  const { skill_id, agent_id } = req.body;
  const data = loadCatalog();
  if (!data.agent_skills) data.agent_skills = {};
  if (!data.agent_skills[agent_id]) data.agent_skills[agent_id] = [];
  if (!data.agent_skills[agent_id].includes(skill_id)) {
    data.agent_skills[agent_id].push(skill_id);
    saveCatalog(data);
  }
  res.json({ agent_id, skills: data.agent_skills[agent_id] });
});

// POST /api/skills/unassign — remove a skill from an agent
router.post('/unassign', (req: Request, res: Response) => {
  const { skill_id, agent_id } = req.body;
  const data = loadCatalog();
  if (!data.agent_skills?.[agent_id]) { res.status(404).json({ error: 'Agent not found' }); return; }
  data.agent_skills[agent_id] = data.agent_skills[agent_id].filter((s: string) => s !== skill_id);
  saveCatalog(data);
  res.json({ agent_id, skills: data.agent_skills[agent_id] });
});

// GET /api/skills/workflows — list all skill workflow files
router.get('/workflows', (_req: Request, res: Response) => {
  const skillsDir = join(ORCHESTRA, 'skills');
  if (!existsSync(skillsDir)) { res.json({ skills: [], total: 0 }); return; }

  const skills = readdirSync(skillsDir)
    .filter(f => f.endsWith('.md'))
    .map(f => {
      const content = readFileSync(join(skillsDir, f), 'utf-8');
      // Parse YAML frontmatter
      const frontmatterMatch = content.match(/^---\n([\s\S]*?)\n---/);
      const meta: Record<string, string> = {};
      if (frontmatterMatch) {
        for (const line of frontmatterMatch[1].split('\n')) {
          const [key, ...vals] = line.split(':');
          if (key && vals.length) meta[key.trim()] = vals.join(':').trim();
        }
      }
      return { file: f, ...meta, content_length: content.length };
    });

  res.json({ skills, total: skills.length });
});

// GET /api/skills/workflows/:name — get full content of a skill workflow
router.get('/workflows/:name', (req: Request, res: Response) => {
  const skillsDir = join(ORCHESTRA, 'skills');
  const name = req.params.name as string;
  const filename = name.endsWith('.md') ? name : `${name}.md`;
  const filepath = join(skillsDir, filename);
  if (!existsSync(filepath)) { res.status(404).json({ error: 'Skill not found' }); return; }
  const content = readFileSync(filepath, 'utf-8');
  res.json({ file: filename, content });
});

export default router;
