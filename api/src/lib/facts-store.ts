// Facts store — the dashboard/API facts file and the pure create path.
//
// Layout: $ORCHESTRA_DIR/facts/facts_db.json = { facts: FactRow[], last_updated }.
// services/arturo/facts_recall.py reads this same file (read-only) to build Arturo's
// FACTS block, so the row shape here is a contract: `fact` (text), `timestamp`,
// `verified_at`, optional `category` / `confidence` / `expiry_class`.
// Express-free so it can be unit-tested with `tsx --test`.
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync } from 'fs';
import { join, dirname } from 'path';

export interface FactRow {
  id: number;
  text?: string;
  fact?: string; // Legacy field name (recall reads fact || text)
  source?: string;
  category?: string;
  verified_at?: string;
  timestamp?: string;
  confidence?: number;
  [key: string]: any;
}

export interface FactsDb {
  facts: FactRow[];
  last_updated?: string | null;
}

export function factsDbPath(orchestraDir: string): string {
  return join(orchestraDir, 'facts', 'facts_db.json');
}

export function loadFactsDb(path: string): FactsDb {
  if (!existsSync(path)) {
    return { facts: [], last_updated: null };
  }
  try {
    const data = JSON.parse(readFileSync(path, 'utf-8'));
    if (!Array.isArray(data.facts)) data.facts = [];
    return data;
  } catch (err) {
    console.error(`Failed to parse facts_db.json: ${err}`);
    return { facts: [], last_updated: null };
  }
}

// Atomic write (tmp + rename) so a reader never sees a torn file.
export function writeFactsDb(path: string, data: FactsDb): void {
  data.last_updated = new Date().toISOString();
  mkdirSync(dirname(path), { recursive: true });
  const tmpPath = `${path}.tmp`;
  writeFileSync(tmpPath, JSON.stringify(data, null, 2));
  renameSync(tmpPath, path);
}

export function createFactRow(
  db: FactsDb,
  text: unknown,
  opts: { category?: string; source?: string } = {}
): FactRow {
  if (typeof text !== 'string' || !text.trim()) {
    throw new Error('text field required and must be a non-empty string');
  }
  const clean = text.trim();
  const maxId = (db.facts || []).reduce((m, f) => (typeof f.id === 'number' && f.id > m ? f.id : m), 0);
  const now = new Date().toISOString();
  const row: FactRow = {
    id: maxId + 1,
    fact: clean,
    text: clean,
    source: opts.source || 'dashboard',
    timestamp: now,
    verified_at: now,
  };
  if (opts.category && typeof opts.category === 'string') row.category = opts.category.trim();
  return row;
}
