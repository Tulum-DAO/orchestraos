import { Router, type Request, type Response } from 'express';
import { readFileSync, writeFileSync, existsSync, appendFileSync, mkdirSync } from 'fs';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';
import { loadFactsDb as loadStore, writeFactsDb as writeStore, createFactRow } from '../lib/facts-store.js';

const router = Router();

// Paths
// NOTE: the private tree keeps facts under a sibling ~/scripts/omni-context/global
// directory (a separate historical checkout). This extraction nests it under the
// configured data dir instead so a fresh install is one self-contained tree — see
// EXTRACTION_REPORT.md section 7d.
const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;
const OMNI_DIR = join(ORCHESTRA, 'facts');
const FACTS_DB_PATH = join(OMNI_DIR, 'facts_db.json');
const CONTEXT_LAYER_PATH = join(OMNI_DIR, 'context_layer.json');
const AUDIT_LOG_PATH = join(ORCHESTRA, 'logs/facts-edits.log');
const LOGS_DIR = join(ORCHESTRA, 'logs');

// Ensure logs directory exists
function ensureLogsDir() {
  if (!existsSync(LOGS_DIR)) {
    mkdirSync(LOGS_DIR, { recursive: true });
  }
}

// Append audit line to facts-edits.log
function auditWrite(action: string, factId: number, changes: Record<string, any>) {
  ensureLogsDir();
  const timestamp = new Date().toISOString();
  const user = 'dashboard'; // Could come from headers if needed
  const line = JSON.stringify({
    timestamp,
    user,
    action,
    fact_id: factId,
    changes,
  });
  appendFileSync(AUDIT_LOG_PATH, line + '\n');
}

// Parse ISO date string with fallback
function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  try {
    const dt = new Date(value);
    return isNaN(dt.getTime()) ? null : dt;
  } catch {
    return null;
  }
}

// Compute staleness: returns (is_stale, age_days) for a last-activity date.
// Uses FRESHNESS_BUDGET_DAYS from env or 14 as default.
function computeStaleness(
  asOf: string | null | undefined,
  now: Date = new Date(),
  budgetDays: number = 14
): { is_stale: boolean; age_days: number | null } {
  const dt = parseDate(asOf);
  if (!dt) {
    return { is_stale: true, age_days: null };
  }
  const ageDays = Math.floor((now.getTime() - dt.getTime()) / (1000 * 60 * 60 * 24));
  return { is_stale: ageDays > budgetDays, age_days: ageDays };
}

// Load facts_db.json (shared with the pure store lib; see lib/facts-store.ts)
function loadFactsDb(): any {
  return loadStore(FACTS_DB_PATH);
}

// Load context_layer.json
function loadContextLayer(): any {
  if (!existsSync(CONTEXT_LAYER_PATH)) {
    return { top_of_mind: '', last_updated: null };
  }
  try {
    return JSON.parse(readFileSync(CONTEXT_LAYER_PATH, 'utf-8'));
  } catch (err) {
    console.error(`Failed to parse context_layer.json: ${err}`);
    return { top_of_mind: '', last_updated: null };
  }
}

// Write facts_db.json atomically (temp file + rename) — lib handles mkdir + rename.
function writeFactsDb(data: any) {
  ensureLogsDir();
  try {
    writeStore(FACTS_DB_PATH, data);
  } catch (err) {
    throw new Error(`Failed to write facts_db.json: ${err}`);
  }
}

interface FactRow {
  id: number;
  text?: string;
  fact?: string; // Legacy field name
  source?: string;
  verified_at?: string;
  timestamp?: string; // When the fact was originally created/modified
  category?: string;
  confidence?: number;
  [key: string]: any;
}

interface FactsResponse {
  facts: Array<{
    id: number;
    text: string;
    source: 'session' | 'facts_db' | 'context_layer';
    verified_at: string | null;
    freshness: {
      age_days: number | null;
      stale: boolean;
      threshold_days: number;
    };
  }>;
}

// GET /api/facts — list merged facts with freshness
router.get('/', (_req: Request, res: Response) => {
  try {
    const now = new Date();
    const budgetDaysStr = process.env.CONTEXT_FRESHNESS_BUDGET_DAYS || '14';
    const budgetDays = parseInt(budgetDaysStr, 10);

    const factsDb = loadFactsDb();
    const contextLayer = loadContextLayer();

    const result: FactsResponse = { facts: [] };

    // Session facts from context_layer.top_of_mind (if it exists and is non-empty)
    if (contextLayer.top_of_mind && typeof contextLayer.top_of_mind === 'string') {
      const topOfMind = contextLayer.top_of_mind.split('\n').filter((line: string) => line.trim());
      topOfMind.forEach((text: string, idx: number) => {
        if (text.trim()) {
          const staleness = computeStaleness(contextLayer.last_updated, now, budgetDays);
          result.facts.push({
            id: 900000 + idx, // Synthetic IDs for session facts
            text: text.trim(),
            source: 'context_layer',
            verified_at: contextLayer.last_updated || null,
            freshness: {
              age_days: staleness.age_days,
              stale: staleness.is_stale,
              threshold_days: budgetDays,
            },
          });
        }
      });
    }

    // Facts from facts_db.json (use 'fact' or 'text' field)
    (factsDb.facts || []).forEach((row: FactRow) => {
      const text = row.fact || row.text || '';
      if (!text) return;

      // verified_at comes from row, or fall back to timestamp
      const verifiedAt = row.verified_at || row.timestamp;
      const staleness = computeStaleness(verifiedAt, now, budgetDays);

      result.facts.push({
        id: row.id || Math.random(), // Use fact ID if available
        text,
        source: 'facts_db',
        verified_at: verifiedAt || null,
        freshness: {
          age_days: staleness.age_days,
          stale: staleness.is_stale,
          threshold_days: budgetDays,
        },
      });
    });

    res.json(result);
  } catch (err: any) {
    console.error('GET /api/facts error:', err);
    res.status(503).json({
      error: 'facts_store_unavailable',
      reason: `Failed to read facts store: ${(err as Error).message}`,
    });
  }
});

// POST /api/facts — create a fact. This is the gate's "write a fact": the row lands in
// facts/facts_db.json, which services/arturo/facts_recall.py reads on Arturo's next turn.
router.post('/', (req: Request, res: Response) => {
  try {
    const { text, category } = req.body || {};
    const factsDb = loadFactsDb();
    let row;
    try {
      row = createFactRow(factsDb, text, { category, source: 'dashboard' });
    } catch (err: any) {
      res.status(400).json({ error: (err as Error).message });
      return;
    }
    factsDb.facts.push(row);
    writeFactsDb(factsDb);
    auditWrite('create', row.id, { text: row.fact, category: row.category || null });
    res.status(201).json({ id: row.id, text: row.fact, source: row.source, verified_at: row.verified_at });
  } catch (err: any) {
    console.error('POST /api/facts error:', err);
    res.status(503).json({
      error: 'facts_store_unavailable',
      reason: `Failed to write facts store: ${(err as Error).message}`,
    });
  }
});

// PATCH /api/facts/:id — edit a fact's text
router.patch('/:id', (req: Request, res: Response) => {
  try {
    const factId = parseInt(String(req.params.id), 10);
    const { text } = req.body;

    if (!text || typeof text !== 'string') {
      res.status(400).json({ error: 'text field required and must be a string' });
      return;
    }

    const factsDb = loadFactsDb();
    const fact = factsDb.facts.find((f: FactRow) => f.id === factId);

    if (!fact) {
      res.status(404).json({ error: `fact ${factId} not found` });
      return;
    }

    const oldText = fact.fact || fact.text || '';
    fact.fact = text; // Update fact field
    if (fact.text) fact.text = text; // Also update text if it exists
    fact.verified_at = new Date().toISOString(); // Update verified timestamp

    writeFactsDb(factsDb);
    auditWrite('edit', factId, { old_text: oldText, new_text: text });

    res.json({ id: factId, text, verified_at: fact.verified_at });
  } catch (err: any) {
    console.error(`PATCH /api/facts/:id error:`, err);
    res.status(503).json({
      error: 'facts_store_unavailable',
      reason: `Failed to write facts store: ${(err as Error).message}`,
    });
  }
});

// POST /api/facts/:id/fresh — bump verified_at to now
router.post('/:id/fresh', (req: Request, res: Response) => {
  try {
    const factId = parseInt(String(req.params.id), 10);

    const factsDb = loadFactsDb();
    const fact = factsDb.facts.find((f: FactRow) => f.id === factId);

    if (!fact) {
      res.status(404).json({ error: `fact ${factId} not found` });
      return;
    }

    fact.verified_at = new Date().toISOString();

    writeFactsDb(factsDb);
    auditWrite('fresh', factId, { verified_at: fact.verified_at });

    res.json({ id: factId, verified_at: fact.verified_at });
  } catch (err: any) {
    console.error(`POST /api/facts/:id/fresh error:`, err);
    res.status(503).json({
      error: 'facts_store_unavailable',
      reason: `Failed to write facts store: ${(err as Error).message}`,
    });
  }
});

export default router;
