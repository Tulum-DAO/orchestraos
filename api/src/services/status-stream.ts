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
 *   event: step     data: { seat, text, ts }               — what the seat is doing now ('' = nothing)
 *
 * Steps come from a second realtime file, realtime/steps.json, beside status.json. Its ONE writer is
 * Arturo's service (services/arturo/live_steps.py, which also keeps it to one change per seat every half
 * second); this service only reads it and passes changes on, exactly as it does with status.
 */
import fs from 'fs';
import path from 'path';
import os from 'os';
import chokidar from 'chokidar';
import type { Response } from 'express';

const STATUS_FILE =
  process.env.REALTIME_STATUS_FILE ||
  path.join(os.homedir(), '.orchestra', 'realtime', 'status.json');

const STEPS_FILE =
  process.env.REALTIME_STEPS_FILE || path.join(path.dirname(STATUS_FILE), 'steps.json');
/** A step older than this is not "now": a new page is not handed it (the turn that wrote it is long over). */
export const STEP_FRESH_S = 60;

const clients = new Set<Response>();
// last broadcast status per seat, so we only push actual transitions.
const lastStatus = new Map<string, string>();
let watcher: ReturnType<typeof chokidar.watch> | null = null;
let stepWatcher: ReturnType<typeof chokidar.watch> | null = null;
// last step text sent per seat, so only changes go out.
const lastStep = new Map<string, string>();

export interface StepLine { seat: string; text: string; ts: number | null }

export function readSteps(file = STEPS_FILE): Record<string, { text?: string; ts?: number }> {
  try {
    const raw = fs.readFileSync(file, 'utf-8');
    if (!raw.trim()) return {};
    const doc = JSON.parse(raw);
    const seats = doc && typeof doc === 'object' ? doc.seats : null;
    return seats && typeof seats === 'object' ? seats : {};
  } catch {
    return {};                                   // partial write / missing: skip this tick
  }
}

/** The step lines that changed since `last` (which it updates). Pure over its inputs, for the test. */
export function stepDeltas(seats: Record<string, { text?: string; ts?: number }>, last: Map<string, string>): StepLine[] {
  const out: StepLine[] = [];
  for (const [seat, info] of Object.entries(seats)) {
    const text = info && typeof info.text === 'string' ? info.text.slice(0, 160) : '';
    if (last.get(seat) === text) continue;
    last.set(seat, text);
    out.push({ seat, text, ts: typeof info?.ts === 'number' ? info.ts : null });
  }
  return out;
}

/** What a new page is handed: only lines with words that are still current. */
export function freshSteps(seats: Record<string, { text?: string; ts?: number }>, nowS: number): StepLine[] {
  return Object.entries(seats)
    .filter(([, i]) => i && typeof i.text === 'string' && i.text && typeof i.ts === 'number' && nowS - i.ts <= STEP_FRESH_S)
    .map(([seat, i]) => ({ seat, text: (i.text as string).slice(0, 160), ts: i.ts as number }));
}

function broadcastSteps(): void {
  for (const d of stepDeltas(readSteps(), lastStep)) send(`event: step\ndata: ${JSON.stringify(d)}\n\n`);
}

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
  // steps: seeded the same way, then every change goes out
  stepDeltas(readSteps(), lastStep);
  stepWatcher = chokidar.watch(STEPS_FILE, { persistent: true, usePolling: false, ignoreInitial: true });
  stepWatcher.on('change', broadcastSteps);
  stepWatcher.on('add', broadcastSteps);
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
    for (const d of freshSteps(readSteps(), Date.now() / 1000)) res.write(`event: step\ndata: ${JSON.stringify(d)}\n\n`);
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
  if (stepWatcher) {
    void stepWatcher.close();
    stepWatcher = null;
  }
}
