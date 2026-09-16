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
