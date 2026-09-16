/**
 * goals-ledger.ts — read-only join source for the CRM's goals_open roll-up
 * (CRM P0, spec 2026-08-11-crm-pipeline-split §4.1/§4.2). The goals ledger
 * (state/goals/ledger.json) carries resolved entity linkage on every goal:
 *   entities.companies[].slug, entities.people[].id
 * We build per-key open-goal counts + open-goal lists so a person/company row
 * can show how many open goals concern it.
 *
 * NB (verified 2026-08-11): the ledger is currently EMPTY (0 goals) — the miner
 * (pocket-agent lineage) that populates it is the operator-gated + not built. So every
 * count is 0 until then; the join is built + safe. The goal-status enum isn't
 * observable yet, so "open" is defined defensively (see OPEN below) and should
 * be reconciled against the real shape when the miner ships.
 *
 * mtime-cached: the ledger is a small JSON file; re-read only when it changes.
 */
import { readFileSync, statSync } from 'fs';
import { join } from 'path';

const ORCH = process.env.ORCHESTRA_DIR || join(process.env.HOME!, 'scripts/agent-orchestra');
const LEDGER = join(ORCH, 'state', 'goals', 'ledger.json');

// Defensive "open" definition until the miner's real status enum is observable:
// anything NOT in a terminal state counts as open.
const CLOSED = new Set(['done', 'complete', 'completed', 'cancelled', 'canceled', 'archived', 'dropped']);

export interface LedgerGoal {
  id?: string;
  title?: string;
  status?: string;
  entities?: { companies?: Array<{ slug?: string; name?: string; resolved?: boolean }>;
               people?: Array<{ id?: string; name?: string; resolved?: boolean }> };
}

interface Index {
  byPerson: Map<string, LedgerGoal[]>;
  byCompany: Map<string, LedgerGoal[]>;
}

let cache: { mtimeMs: number; idx: Index } | null = null;
const EMPTY: Index = { byPerson: new Map(), byCompany: new Map() };

function isOpen(g: LedgerGoal): boolean {
  return !CLOSED.has(String(g.status || '').toLowerCase());
}

function build(): Index {
  let mtimeMs = 0;
  try { mtimeMs = statSync(LEDGER).mtimeMs; } catch { return EMPTY; }
  if (cache && cache.mtimeMs === mtimeMs) return cache.idx;
  const idx: Index = { byPerson: new Map(), byCompany: new Map() };
  try {
    const raw = JSON.parse(readFileSync(LEDGER, 'utf-8'));
    const goals: LedgerGoal[] = Array.isArray(raw) ? raw
      : Array.isArray(raw?.goals) ? raw.goals
      : (raw && typeof raw === 'object') ? Object.values(raw).filter((v) => v && typeof v === 'object') as LedgerGoal[]
      : [];
    for (const g of goals) {
      if (!g || !isOpen(g)) continue;
      for (const p of g.entities?.people || []) {
        if (!p?.id) continue;
        (idx.byPerson.get(p.id) || idx.byPerson.set(p.id, []).get(p.id)!).push(g);
      }
      for (const c of g.entities?.companies || []) {
        if (!c?.slug) continue;
        (idx.byCompany.get(c.slug) || idx.byCompany.set(c.slug, []).get(c.slug)!).push(g);
      }
    }
  } catch { return cache?.idx || EMPTY; }
  cache = { mtimeMs, idx };
  return idx;
}

export function goalsOpenForPerson(personId: string): number {
  return build().byPerson.get(personId)?.length || 0;
}
export function goalsOpenForCompany(slug: string): number {
  return build().byCompany.get(slug)?.length || 0;
}
/** Open-goal summaries concerning a person (for the /:id detail block). */
export function openGoalsForPerson(personId: string): Array<{ id?: string; title?: string; status?: string }> {
  return (build().byPerson.get(personId) || []).map((g) => ({ id: g.id, title: g.title, status: g.status }));
}
