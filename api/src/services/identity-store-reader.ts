// DEC-1788554471 — the DB-first READER seam for the dashboard under the identity
// -store cutover. registry.json is a git-tracked PROJECTION of
// state/orchestra-registry.db, shared by ~40 sessions on different branches, so a
// foreign `git checkout` can wipe a brand-new agent from it — the dashboard then
// mints a phantom `unregistered:<name>` chip (a dead second chip whose click exits
// the pane) beside the live one. The DB never flaps. Under cutover the dashboard
// reads the canonical live head DB-first so a registered live agent renders as ONE
// chip whose click targets the current live head.
//
// The dashboard needs only the canonical live-head per root (tmux_session / status /
// generation) — it never walks succession — so the typed canonical<->generations
// JOIN is the correct, sufficient source here (the router's resolver needs the
// source_records session docs for succeeded_by; the chip list does not).
import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import { loadConfig } from '../lib/config.js';

const ORCHESTRA_DIR = process.env.ORCHESTRA_DIR || loadConfig().dataDir;

export interface CanonicalAgent {
  root: string;
  tmux_session: string;
  status: string;
  generation: number | null;
  session_id: string | null;
  model: string | null;
  tier: string | null;
  machine: string | null;
  runtime: string | null;
  cwd: string | null;
}

function dbPath(orch: string = ORCHESTRA_DIR): string {
  return path.join(orch, 'state', 'orchestra-registry.db');
}

// Cheap inline flag/env probe — mirrors registry-update._cutover_registry_write and
// message-router.load_agent_meta. No DB touch; safe to call on every request.
export function isCutoverActive(orch: string = ORCHESTRA_DIR): boolean {
  if (process.env.IDENTITY_STORE_CUTOVER === '1') return true;
  try {
    return fs.existsSync(path.join(orch, 'state', 'identity-store-cutover.flag'));
  } catch {
    return false;
  }
}

// Read the canonical live head per lineage, READ-ONLY. Returns null when the DB is
// absent/unreadable (caller falls back to registry.json). Short-lived per-request
// connection: a long-lived readonly handle would pin the WAL and defeat the
// projector's checkpoint. Never writes / never checkpoints.
export function getCanonicalAgents(orch: string = ORCHESTRA_DIR): Record<string, CanonicalAgent> | null {
  const p = dbPath(orch);
  let db: Database.Database | null = null;
  try {
    db = new Database(p, { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 5000');
    const rows = db.prepare(
      `SELECT c.root AS root, c.tmux_session AS tmux_session, c.status AS status,
              g.generation AS generation, g.session_id AS session_id, g.model AS model,
              l.tier AS tier, l.machine AS machine, l.runtime AS runtime, l.cwd AS cwd
       FROM canonical c
       JOIN generations g ON g.id = c.generation_id
       JOIN lineages l ON l.root = c.root`
    ).all() as CanonicalAgent[];
    const out: Record<string, CanonicalAgent> = {};
    for (const r of rows) out[r.root] = r;
    return out;
  } catch {
    return null; // absent/unreadable => caller uses the flat registry
  } finally {
    try { db?.close(); } catch { /* best-effort */ }
  }
}

// Resolve one agent id -> its current live-head tmux session under cutover, or null
// if the id is not a canonical root (caller keeps its existing behavior).
export function canonicalTmuxSession(id: string, orch: string = ORCHESTRA_DIR): string | null {
  const canon = getCanonicalAgents(orch);
  return canon?.[id]?.tmux_session ?? null;
}

/** One past (or current) generation of a lineage, for the Agents page's history list. */
export interface GenerationRow {
  generation: number;
  model: string | null;
  spawned_at: string | null;
  promoted_at: string | null;
  retired_at: string | null;
  note: string | null;
  current?: boolean;          // this is the lineage's canonical head
  /** A PRE-ALLOCATED SLOT: the row exists but the seat never ran — no promotion, no
   *  retirement, and it is not the canonical head. Rotation mints these for an incoming
   *  green before it is promoted. It is not history, and it is not counted as a generation. */
  pending?: boolean;
}

/**
 * What separates a real generation from a slot that was minted and never used. A row counts
 * if it was ever promoted, or was retired, or is the lineage's current head — anything else
 * names a seat that has not run. 84 rows fleet-wide are slots; counting them made gm read
 * "55 generations" including a "gen 3" that never existed.
 */
const REAL_GENERATION = `(g.promoted_at IS NOT NULL OR g.retired_at IS NOT NULL OR g.id = c.generation_id)`;

// Read-only, short-lived handle per call, same discipline as getCanonicalAgents: a long-lived
// handle would pin the WAL. Both return null when the DB is absent or unreadable, and the
// caller simply shows no history — never an error.

/** Generation count per root, in one query. Used to badge cards that have a history. */
export function getGenerationCounts(orch: string = ORCHESTRA_DIR): Record<string, number> | null {
  let db: Database.Database | null = null;
  try {
    db = new Database(dbPath(orch), { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 5000');
    // Joined to canonical so a live head always counts even if its promotion was not stamped.
    const rows = db.prepare(
      `SELECT g.root AS root, COUNT(*) AS n
         FROM generations g LEFT JOIN canonical c ON c.root = g.root
        WHERE ${REAL_GENERATION}
        GROUP BY g.root`,
    ).all() as { root: string; n: number }[];
    const out: Record<string, number> = {};
    for (const r of rows) out[r.root] = r.n;
    return out;
  } catch {
    return null;
  } finally {
    try { db?.close(); } catch { /* best-effort */ }
  }
}

/** Every generation of one root, newest first. */
export function getGenerations(root: string, orch: string = ORCHESTRA_DIR): GenerationRow[] | null {
  let db: Database.Database | null = null;
  try {
    db = new Database(dbPath(orch), { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 5000');
    // "current" comes from canonical, and order is by TIME, not by generation number: numbers
    // reset (gm's live head is generation 2 while its history runs to 87), so "highest number"
    // is not "current" and sorting by number would put a retired generation first.
    const rows = db.prepare(
      `SELECT g.generation, g.model, g.spawned_at, g.promoted_at, g.retired_at, g.note,
              (g.id = c.generation_id) AS current,
              (NOT ${REAL_GENERATION}) AS pending
       FROM generations g LEFT JOIN canonical c ON c.root = g.root
       WHERE g.root = ?
       ORDER BY current DESC, COALESCE(g.promoted_at, g.spawned_at) DESC, g.id DESC`,
    ).all(root) as (Omit<GenerationRow, 'current' | 'pending'> & {
      current: number | null; pending: number | null;
    })[];
    return rows.map((r) => ({ ...r, current: r.current === 1, pending: r.pending === 1 }));
  } catch {
    return null;
  } finally {
    try { db?.close(); } catch { /* best-effort */ }
  }
}
