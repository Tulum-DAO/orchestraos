import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, mkdirSync, appendFileSync } from 'fs';
import { join } from 'path';
import { scoreAgents } from '../services/scoring.js';
import { runDetectors } from '../services/detectors.js';

const router = Router();
const ORCHESTRA = process.env.ORCHESTRA_DIR!;

const DEFAULT_PROFILE = {
  user_id: '',
  display_name: '',
  tier: 'client',  // 'power_user' or 'client' — clients get plain English
  default_view: 'adaptive',
  coaching_rules: [],
  pinned_agents: [],
  dismissed_suggestions: [],
  implicit_facts: [],  // auto-extracted from conversations
  weights: {
    recency: 0.35,
    frequency: 0.25,
    active_context: 0.25,
    time_of_day: 0.10,
    sequence: 0.05
  }
};

function userDir(userId: string) {
  return join(ORCHESTRA, 'state', 'users', userId);
}

function ensureUserDir(userId: string) {
  const dir = userDir(userId);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  return dir;
}

function readJSON(path: string) {
  try { return JSON.parse(readFileSync(path, 'utf-8')); } catch { return null; }
}

function readJSONL(path: string): any[] {
  if (!existsSync(path)) return [];
  try {
    return readFileSync(path, 'utf-8').trim().split('\n')
      .filter(Boolean)
      .map(l => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  } catch { return []; }
}

// POST /:userId/event — append telemetry event to ui-events.jsonl
router.post('/:userId/event', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const dir = ensureUserDir(userId as string);
    const event = {
      ...req.body,
      user_id: userId,
      timestamp: new Date().toISOString()
    };
    appendFileSync(join(dir, 'ui-events.jsonl'), JSON.stringify(event) + '\n');
    res.json({ ok: true, event });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// GET /:userId/profile — return profile.json with sensible defaults
router.get('/:userId/profile', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const profilePath = join(userDir(userId as string), 'profile.json');
    const profile = readJSON(profilePath);
    if (profile) {
      res.json(profile);
    } else {
      res.json({ ...DEFAULT_PROFILE, user_id: userId });
    }
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// PATCH /:userId/profile — merge updates into profile.json
router.patch('/:userId/profile', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const dir = ensureUserDir(userId as string);
    const profilePath = join(dir, 'profile.json');
    const existing = readJSON(profilePath) || { ...DEFAULT_PROFILE, user_id: userId };
    const updated = { ...existing, ...req.body, user_id: userId };
    // Deep merge weights if provided
    if (req.body.weights && existing.weights) {
      updated.weights = { ...existing.weights, ...req.body.weights };
    }
    writeFileSync(profilePath, JSON.stringify(updated, null, 2));
    res.json(updated);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// GET /:userId/insights — return insights, filterable by ?status=pending|accepted|dismissed
router.get('/:userId/insights', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const insightsPath = join(userDir(userId as string), 'insights.jsonl');
    let insights = readJSONL(insightsPath);
    const status = req.query.status as string | undefined;
    if (status) {
      insights = insights.filter(i => i.status === status);
    }
    res.json({ insights, total: insights.length });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// POST /:userId/insights/:id/respond — accept or dismiss an insight
router.post('/:userId/insights/:id/respond', (req: Request, res: Response) => {
  try {
    const { userId, id } = req.params;
    const { action } = req.body; // 'accept' or 'dismiss'
    if (!action || !['accept', 'dismiss'].includes(action)) {
      res.status(400).json({ error: 'action must be "accept" or "dismiss"' });
      return;
    }

    const dir = ensureUserDir(userId as string);
    const insightsPath = join(dir, 'insights.jsonl');
    const insights = readJSONL(insightsPath);

    const insight = insights.find(i => i.id === id);
    if (!insight) {
      res.status(404).json({ error: 'Insight not found' });
      return;
    }

    // Update insight status
    insight.status = action === 'accept' ? 'accepted' : 'dismissed';
    insight.responded_at = new Date().toISOString();

    // Rewrite the full JSONL
    writeFileSync(insightsPath, insights.map(i => JSON.stringify(i)).join('\n') + '\n');

    // If accepted, add as coaching rule to profile
    if (action === 'accept') {
      const profilePath = join(dir, 'profile.json');
      const profile = readJSON(profilePath) || { ...DEFAULT_PROFILE, user_id: userId };
      if (!profile.coaching_rules) profile.coaching_rules = [];
      profile.coaching_rules.push({
        source_insight: id,
        rule: insight.suggestion || insight.message || insight.title,
        added_at: new Date().toISOString()
      });
      writeFileSync(profilePath, JSON.stringify(profile, null, 2));
    }

    res.json({ ok: true, insight });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// GET /:userId/agents — returns scored, ranked agent list
router.get('/:userId/agents', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const scores = scoreAgents(userId as string);
    // Fire detectors asynchronously (throttled to max once per 10 min)
    setTimeout(() => { try { runDetectors(userId as string); } catch {} }, 0);
    res.json(scores);
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// GET /:userId/assumptions — learned action patterns
router.get('/:userId/assumptions', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const assumptionsPath = join(userDir(userId as string), 'assumptions.json');
    const data = readJSON(assumptionsPath);
    if (!data || !data.patterns) {
      res.json({ assumptions: [], total: 0 });
      return;
    }
    const assumptions = Object.entries(data.patterns).map(([key, pattern]: [string, any]) => ({
      key,
      action_type: pattern.action_type,
      context: pattern.context_sample,
      can_assume: pattern.can_assume || false,
      assumed_action: pattern.assumed_action,
      total: pattern.total_count || 0,
      confirmed: pattern.confirmed_count || 0,
      denied: pattern.denied_count || 0,
    }));
    assumptions.sort((a: any, b: any) => b.total - a.total);
    res.json({ assumptions, total: assumptions.length });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// DELETE /:userId/assumptions/:key — reset a specific assumption
router.delete('/:userId/assumptions/:key', (req: Request, res: Response) => {
  try {
    const { userId, key } = req.params;
    const assumptionsPath = join(userDir(userId as string), 'assumptions.json');
    const data = readJSON(assumptionsPath);
    const k = key as string;
    if (data?.patterns?.[k]) {
      data.patterns[k].can_assume = false;
      data.patterns[k].assumed_action = null;
      data.patterns[k].confirmed_count = 0;
      writeFileSync(assumptionsPath, JSON.stringify(data, null, 2));
      res.json({ ok: true, reset: key });
    } else {
      res.status(404).json({ error: 'Assumption not found' });
    }
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

// GET /:userId/implicit-facts — auto-extracted facts from conversations
router.get('/:userId/implicit-facts', (req: Request, res: Response) => {
  try {
    const { userId } = req.params;
    const profilePath = join(userDir(userId as string), 'profile.json');
    const profile = readJSON(profilePath) || {};
    const facts = profile.implicit_facts || [];
    res.json({ facts, total: facts.length });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

export default router;
