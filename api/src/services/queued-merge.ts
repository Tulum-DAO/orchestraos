/**
 * queued-merge — msg_store merge for queued-native-render (P1, DEC-1789392107605493,
 * CONSENSUS_REACHED @5b530acf). Owner: orchestraos-queue-render-dev.
 *
 * Merges two classes of msg_store rows into the transcript items[] so queued
 * phone/watch messages stop "disappearing":
 *
 *   B1 (native queued user turns): still-pending held_message rows (deliver_raw
 *       phone/watch lane) -> synthetic {kind:text, role:user, queued:true}.
 *       DEDUP is keyed ONLY on the row's own authoritative delivered_at: a row
 *       whose delivered_at is set has already been injected as a raw-body JSONL
 *       user turn, so its synthetic is dropped (no body-match heuristic — that
 *       would silently drop a legitimately-distinct message, violating D2/W1).
 *       Residual: delivered_at stamps at inject slightly before the JSONL flush,
 *       so a resolving message can blink out for ~one poll then reappear as the
 *       real turn — brief, self-healing, never data loss.
 *
 *   B2 (processed-batch div): acknowledged self-bound rows carrying a write-once
 *       metadata.batch_id (stamped by the Stop-hook drain) -> ONE queued_batch
 *       node per batch, entries NEWEST->OLDEST.
 *
 * READ-ONLY, and its own connection (not the write-handle singleton): opens a
 * readonly better-sqlite3 handle on MSG_DB_PATH || <ORCHESTRA>/state/tasks.db.
 * WAL allows concurrent readers; a reader never blocks the writer. Every DB
 * access is best-effort (fail-open to the un-merged items) — a transcript must
 * never fail because the queue read hiccuped.
 *
 * IMPORTANT (SSE-safety, §3e/A1): this merge is called ONLY from the poll route
 * (normalizeTranscript(..., includeQueued=true)); the SSE tailer never enables
 * it, so its ephemeral B1 synthetics never enter the monotonic-append delta.
 */
import Database, { type Database as DB } from 'better-sqlite3';
import { join } from 'path';
import { loadConfig } from '../lib/config.js';

const ORCHESTRA = process.env.ORCHESTRA_DIR || loadConfig().dataDir;

let cached: { path: string; db: DB } | null = null;

function dbPath(): string {
  return process.env.MSG_DB_PATH || join(ORCHESTRA, 'state', 'tasks.db');
}

// Lazy, cached, READONLY connection. Re-opens if MSG_DB_PATH changed (tests).
function conn(): DB | null {
  const path = dbPath();
  if (cached && cached.path === path) return cached.db;
  try {
    if (cached) { try { cached.db.close(); } catch { /* ignore */ } }
    const db = new Database(path, { readonly: true, fileMustExist: true });
    db.pragma('busy_timeout = 2000');
    cached = { path, db };
    return db;
  } catch {
    cached = null;
    return null;
  }
}

interface BatchEntry { agent: string; sent_ts: string; body: string }

// B1: still-pending held phone/watch messages for this agent.
function b1QueuedTurns(db: DB, agentId: string): any[] {
  try {
    const rows = db.prepare(
      `SELECT id, from_agent, body, created_at FROM messages
        WHERE to_agent = ? AND type = 'held_message'
          AND delivered_at IS NULL
          AND status IN ('pending', 'processing')
        ORDER BY created_at ASC`).all(agentId) as any[];
    return rows.map((r) => ({
      kind: 'text',
      role: 'user',
      text: String(r.body ?? ''),
      ts: r.created_at,
      uuid: `queued:${r.id}`,
      queued: true,
    }));
  } catch {
    return [];
  }
}

// B2: acknowledged self-bound rows grouped by write-once metadata.batch_id.
function b2Batches(db: DB, agentId: string): any[] {
  try {
    const rows = db.prepare(
      `SELECT id, from_agent, body, created_at, metadata, acknowledged_at FROM messages
        WHERE to_agent = ? AND metadata LIKE '%"batch_id"%'
          AND (acknowledged_at IS NOT NULL OR status IN ('acknowledged', 'archived'))
        ORDER BY created_at DESC`).all(agentId) as any[];
    const batches = new Map<string, { entries: BatchEntry[]; ts: string }>();
    for (const r of rows) {
      let md: any = {};
      try { md = r.metadata ? JSON.parse(r.metadata) : {}; } catch { md = {}; }
      const bid = md && typeof md.batch_id === 'string' ? md.batch_id : null;
      if (!bid) continue;
      const b = batches.get(bid) || { entries: [], ts: '' };
      // rows arrive newest->oldest (created_at DESC) -> entries stay newest-first
      b.entries.push({ agent: String(r.from_agent ?? ''), sent_ts: String(r.created_at ?? ''), body: String(r.body ?? '') });
      const cand = String(r.acknowledged_at || md.processed_ts || r.created_at || '');
      if (!b.ts || cand > b.ts) b.ts = cand;
      batches.set(bid, b);
    }
    const out: any[] = [];
    for (const [bid, b] of batches) {
      out.push({ kind: 'queued_batch', count: b.entries.length, entries: b.entries, ts: b.ts, key: `batch:${bid}` });
    }
    return out;
  } catch {
    return [];
  }
}

// Stable interleave of synthetics into the JSONL items by ts (carry-forward the
// last valid ts so an undated JSONL item stays anchored to its neighbour; ties
// keep original order via index).
function interleaveByTs(items: any[]): any[] {
  const decorated = items.map((it, i) => ({ it, i, t: 0 }));
  let last = 0;
  for (const d of decorated) {
    const n = Date.parse(String((d.it as any)?.ts ?? ''));
    if (!Number.isNaN(n)) last = n;
    d.t = Number.isNaN(n) ? last : n;
  }
  decorated.sort((a, b) => (a.t - b.t) || (a.i - b.i));
  return decorated.map((d) => d.it);
}

/**
 * Return a NEW array = items + synthetic B1/B2 nodes, stable-sorted by ts.
 * Fail-open: on any error, returns the original items unchanged.
 */
export function mergeQueuedItems(items: any[], agentId: string): any[] {
  if (!agentId) return items;
  const db = conn();
  if (!db) return items;
  const synthetic = [...b1QueuedTurns(db, agentId), ...b2Batches(db, agentId)];
  if (!synthetic.length) return items;
  return interleaveByTs(items.concat(synthetic));
}
