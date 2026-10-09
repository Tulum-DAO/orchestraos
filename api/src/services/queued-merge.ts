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
 *   B3 (live card): a self-bound row with NO batch_id yet, which the transcript proves was
 *       injected (exactly one queued item whose text EQUALS the row's body, and no other row
 *       with that body) -> a one-entry
 *       queued_batch at once, at the injected item's position. The batch id is stamped by
 *       the Stop drain, i.e. at the END of the agent's turn: without B3 the operator saw the
 *       raw bubble for the whole turn and the card only after it (and never, for a row the
 *       agent acked before its drain ran). Once the drain stamps it, B2 takes the row over:
 *       card -> card, never card -> bubble -> card.
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
import { boundQueuedBatches, earliestTurnMs } from './queued-bound.js';
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

// B2: acknowledged self-bound rows grouped by write-once metadata.batch_id. `claimed` receives the
// id of every row B2 renders, so B3 can take exactly the rest.
function b2Batches(db: DB, agentId: string, claimed: Set<string> = new Set()): any[] {
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
      claimed.add(String(r.id));
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

// B3: a body shorter than this is not distinctive enough to attribute (the same floor
// dropQueuedCommandsCoveredByBatches uses, so B3 never renders a card the drop would then fail
// to pair with its bubble).
const LIVE_MIN = 40;
const LIVE_LIMIT = 200;

const norm = (s: unknown) => String(s ?? '').replace(/\s+/g, ' ').trim();

// B3: self-bound rows not yet batched, rendered as a card the moment the log shows them injected.
// Bound by EXACT body equality (measured: the logged queued_command prompt IS the row body), and
// only when the match is unique on BOTH sides. Two rows with one body (a re-send of the same
// answer) or one body typed twice is AMBIGUOUS: no live card, and the drain's batch (B2) shows it
// at the end of the turn as before. A late card is a timing annoyance; a card naming the wrong
// sender, or two rows folded into one, is wrong data the operator cannot detect.
function b3LiveCards(db: DB, agentId: string, items: any[], floorMs: number | null, claimed: Set<string>): any[] {
  // No real turn in the window = nothing to anchor the search to: the unbounded scan cost 128 ms per
  // poll on a busy seat (#327 review). Such a window has no mid-turn message to card anyway.
  if (floorMs === null) return [];
  const queuedByText = new Map<string, any[]>();
  for (const it of items) {
    if (it?.kind !== 'text' || !it.queued) continue;
    const t = norm(it.text);
    if (t.length < LIVE_MIN) continue;
    queuedByText.set(t, [...(queuedByText.get(t) || []), it]);
  }
  if (!queuedByText.size) return [];
  try {
    const floorIso = new Date(floorMs - 60_000).toISOString();
    // Every row B2 did NOT render, batch_id or not: the drain stamps batch_id BEFORE the row is acked,
    // so "no batch_id" would leave a stamped-but-unacked row in neither lane (a bubble again).
    const rows = db.prepare(
      `SELECT id, from_agent, body, created_at FROM messages
        WHERE to_agent = ? AND type != 'held_message'
          AND created_at >= ?
        ORDER BY created_at DESC LIMIT ${LIVE_LIMIT}`).all(agentId, floorIso) as any[];
    const rowsByBody = new Map<string, any[]>();
    for (const r of rows) {
      if (claimed.has(String(r.id))) continue;
      const b = norm(r.body);
      if (b.length < LIVE_MIN || !queuedByText.has(b)) continue;   // not injected yet: no card
      rowsByBody.set(b, [...(rowsByBody.get(b) || []), r]);
    }
    const out: any[] = [];
    for (const [b, rs] of rowsByBody) {
      const qs = queuedByText.get(b) || [];
      if (rs.length !== 1 || qs.length !== 1) continue;          // ambiguous: leave it to the drain
      const r = rs[0];
      out.push({ kind: 'queued_batch', count: 1,
        entries: [{ agent: String(r.from_agent ?? ''), sent_ts: String(r.created_at ?? ''), body: String(r.body ?? '') }],
        ts: qs[0].ts ?? r.created_at, key: `msg:${r.id}` });
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
  // B1 (still-pending held turns) is CURRENT STATE and is never bounded: it is the only thing
  // on screen for an agent that has not started a turn yet, and dropping it would hide a
  // message the operator just sent. Only B2, which is history, is bounded.
  const floor = earliestTurnMs(items);
  // One read snapshot for all three lanes: a batch stamp or an ack landing between two queries
  // must not drop a row out of both B2 and B3 for a poll.
  let pending: any[] = [], batches: any[] = [], live: any[] = [];
  try {
    db.transaction(() => {
      pending = b1QueuedTurns(db, agentId);
      const claimed = new Set<string>();
      batches = boundQueuedBatches(b2Batches(db, agentId, claimed), floor);
      // B3 needs the transcript's own evidence of injection, so it can never show a row early.
      live = b3LiveCards(db, agentId, items, floor, claimed);
    })();
  } catch {
    return items;
  }
  const synthetic = [...pending, ...batches, ...live];
  if (!synthetic.length) return items;
  return interleaveByTs(items.concat(synthetic));
}
