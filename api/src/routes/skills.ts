import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;
const CATALOG_FILE = join(ORCHESTRA, 'state', 'skills-catalog.json');
const CLAUDE_SKILLS_DIR = join(homedir(), '.claude', 'skills');
const CLAUDE_PLUGINS_DIR = join(homedir(), '.claude', 'plugins');

function parseFrontmatter(content: string): Record<string, string> {
  const meta: Record<string, string> = {};
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return meta;
  const lines = match[1].split('\n');
  for (let i = 0; i < lines.length; i++) {
    const [key, ...vals] = lines[i].split(':');
    if (!key || key.startsWith(' ') || !vals.length) continue;
    const val = vals.join(':').trim();
    if (val === '>' || val === '>-' || val === '|' || val === '|-') {
      // YAML block scalar: fold the indented lines that follow into one string.
      const folded: string[] = [];
      while (i + 1 < lines.length && (lines[i + 1].startsWith(' ') || lines[i + 1] === '')) {
        i++;
        if (lines[i].trim()) folded.push(lines[i].trim());
      }
      meta[key.trim()] = folded.join(' ');
    } else {
      meta[key.trim()] = val.replace(/^["']|["']$/g, '');
    }
  }
  return meta;
}

// Every SKILL.md directly under `dir` (one level — matches how both
// ~/.claude/skills and a plugin's own skills/ folder are laid out).
function listSkillsIn(dir: string): any[] {
  const out: any[] = [];
  try {
    for (const name of readdirSync(dir)) {
      const skillFile = join(dir, name, 'SKILL.md');
      if (!statSync(join(dir, name)).isDirectory() || !existsSync(skillFile)) continue;
      const content = readFileSync(skillFile, 'utf-8');
      const meta = parseFrontmatter(content);
      out.push({
        id: name, name: meta.name || name, description: meta.description || '', version: meta.version || null,
        // gstack's own build stamps every skill it generates with this marker
        // (SKILL.md.tmpl -> SKILL.md) — a structural fact, unlike description
        // text which is free-form (quoted, YAML-folded, or missing the "(gstack)"
        // tag entirely) and so too unreliable to group by.
        gstack: content.includes('AUTO-GENERATED from SKILL.md.tmpl'),
      });
    }
  } catch { /* dir absent — fine, just zero */ }
  return out;
}

// Only the sub-agent registry (state/skills-catalog.json) is persisted — which
// skill is assigned to which agent. What skills actually EXIST is never cached
// to a file (that's how this page ended up stuck at zero: nothing regenerated
// it). It's discovered fresh on every request instead, from the two real,
// authoritative sources on disk:
//   - ~/.claude/plugins/{known_marketplaces,installed_plugins}.json — actual
//     Claude Code plugin installs (marketplace + version, currently a couple).
//   - ~/.claude/skills/*/SKILL.md — loose, non-plugin skills (the gstack
//     family lives here: ~55 directories, each a deployed skill with its own
//     frontmatter). Grouped into a synthetic "gstack" plugin (its description
//     is tagged "(gstack)") plus an "other-skills" bucket for the rest, so the
//     genuinely-installed plugins above aren't confused with these.
function discoverPlugins(): any[] {
  const plugins: any[] = [];

  try {
    const installed = JSON.parse(
      readFileSync(join(CLAUDE_PLUGINS_DIR, 'installed_plugins.json'), 'utf-8'));
    const marketplaces = JSON.parse(
      readFileSync(join(CLAUDE_PLUGINS_DIR, 'known_marketplaces.json'), 'utf-8'));
    for (const [key, versions] of Object.entries(installed.plugins || {})) {
      const [name, marketplace] = key.split('@');
      const latest = (versions as any[])[0];
      plugins.push({
        id: key, name, marketplace, installed: true,
        version: latest?.version, source: 'claude-plugin',
        marketplace_repo: marketplaces[marketplace]?.source?.repo || null,
        skills: latest?.installPath ? listSkillsIn(join(latest.installPath, 'skills')) : [],
      });
    }
  } catch { /* no plugins installed / manifest absent — fine, just zero */ }

  const gstack: any[] = [];
  const other: any[] = [];
  for (const skill of listSkillsIn(CLAUDE_SKILLS_DIR)) {
    (skill.gstack ? gstack : other).push(skill);
  }

  if (gstack.length) plugins.push({ id: 'gstack', name: 'gstack', marketplace: 'local',
    installed: true, source: 'skills-dir', skills: gstack });
  if (other.length) plugins.push({ id: 'other-skills', name: 'Other Skills', marketplace: 'local',
    installed: true, source: 'skills-dir', skills: other });

  return plugins;
}

function loadCatalog() {
  const agent_skills = (() => {
    if (!existsSync(CATALOG_FILE)) return {};
    try { return JSON.parse(readFileSync(CATALOG_FILE, 'utf-8')).agent_skills || {}; }
    catch { return {}; }
  })();
  return { plugins: discoverPlugins(), agent_skills };
}

function saveCatalog(data: any) {
  // Only agent_skills is durable state; plugins/skills are always rediscovered.
  writeFileSync(CATALOG_FILE, JSON.stringify({ agent_skills: data.agent_skills || {} }, null, 2));
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
