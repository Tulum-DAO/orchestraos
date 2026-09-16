/**
 * E4 — same-second agent-status fan-out (status-oscillation-investigation.md).
 *
 * GET /api/status/stream (SSE) pushes a per-seat status DELTA the instant the
 * authoritative realtime file changes, so the app + GM reflect status changes
 * sub-second instead of on a 30s poll.
 *
 * SINGLE-SOURCE-OF-TRUTH GUARDRAIL (DEC-1787826639): telemetryd is the SOLE
 * writer of the v2-detector STATE lane. This service is READ-ONLY — it watches
 * telemetryd's authoritative ~/.orchestra/realtime/status.json and only
 * re-broadcasts its output faster. It NEVER writes status, never watches the
 * raw hook pane files (which would create a competing state source), and never
 * touches telemetryd. Pure additive delivery; the classifier is untouched.
 *
 * Wire protocol:
 *   event: status   data: { seat, status, ts, runtime }   — one per changed seat
 *   event: gone     data: { seat }                          — seat left the file
 */
import fs from 'fs';
import path from 'path';
import os from 'os';
import chokidar from 'chokidar';
import type { Response } from 'express';

const STATUS_FILE =
  process.env.REALTIME_STATUS_FILE ||
  path.join(os.homedir(), '.orchestra', 'realtime', 'status.json');

const clients = new Set<Response>();
// last broadcast status per seat, so we only push actual transitions.
const lastStatus = new Map<string, string>();
let watcher: ReturnType<typeof chokidar.watch> | null = null;

function readSeats(): Record<string, { status?: string; ts?: number; runtime?: string }> {
  try {
    const raw = fs.readFileSync(STATUS_FILE, 'utf-8');
    if (!raw.trim()) return {};
    const doc = JSON.parse(raw);
    const seats = doc && typeof doc === 'object' ? doc.seats : null;
    return seats && typeof seats === 'object' ? seats : {};
  } catch {
    // partial write / missing / malformed — skip this tick, keep last state
    return {};
  }
}

function send(payload: string): void {
  for (const client of clients) {
    try {
      client.write(payload);
    } catch {
      clients.delete(client);
    }
  }
}

function broadcastStatusDeltas(): void {
  const seats = readSeats();
  const seen = new Set<string>();
  for (const [seat, info] of Object.entries(seats)) {
    const status = info && typeof info.status === 'string' ? info.status : '';
    if (!status) continue;
    seen.add(seat);
    if (lastStatus.get(seat) === status) continue; // no transition
    lastStatus.set(seat, status);
    const delta = { seat, status, ts: info.ts ?? null, runtime: info.runtime ?? null };
    send(`event: status\ndata: ${JSON.stringify(delta)}\n\n`);
  }
  // seats that disappeared from the file
  for (const seat of Array.from(lastStatus.keys())) {
    if (!seen.has(seat)) {
      lastStatus.delete(seat);
      send(`event: gone\ndata: ${JSON.stringify({ seat })}\n\n`);
    }
  }
}

export function initStatusStream(): void {
  // seed lastStatus WITHOUT broadcasting (avoids a burst on every new client/boot)
  for (const [seat, info] of Object.entries(readSeats())) {
    if (info && typeof info.status === 'string' && info.status) lastStatus.set(seat, info.status);
  }
  watcher = chokidar.watch(STATUS_FILE, {
    persistent: true,
    usePolling: false,
    ignoreInitial: true,
  });
  watcher.on('change', broadcastStatusDeltas);
  watcher.on('add', broadcastStatusDeltas);
}

/** Attach an SSE client and immediately hand it the current full snapshot. */
export function addStatusSSEClient(res: Response): void {
  clients.add(res);
  // one-shot snapshot so a fresh subscriber is correct before the next change
  try {
    const seats = readSeats();
    const snapshot = Object.entries(seats)
      .filter(([, i]) => i && typeof i.status === 'string' && i.status)
      .map(([seat, i]) => ({ seat, status: i.status, ts: i.ts ?? null, runtime: i.runtime ?? null }));
    res.write(`event: snapshot\ndata: ${JSON.stringify({ seats: snapshot })}\n\n`);
  } catch {
    /* snapshot best-effort */
  }
  res.on('close', () => {
    clients.delete(res);
  });
}

export function getStatusSSEClientCount(): number {
  return clients.size;
}

export function stopStatusStream(): void {
  if (watcher) {
    watcher.close();
    watcher = null;
  }
}
