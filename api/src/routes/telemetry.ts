/**
 * telemetry.ts — Build B: the telemetry query/stream READ API.
 *
 * Surfaces B1's derived per-agent status + court-scrub-gated transcript deltas
 * to the dashboard / iOS. READ-ONLY (no writes to WAL/registry/status source),
 * tenant-scoped, never scans /proc (consumes B1's daemon output via
 * telemetry-store). SSE now; the delta stream is built on the transport-agnostic
 * DeltaStreamController so a later WS transport drops in without a rewrite.
 *
 * INERT: mounted under the normal api start; NO cron/systemd wiring (that is the
 * last telemetry-v2 step). With the B1 daemon unwired, status.json / delta logs
 * are absent -> the status endpoints serve an empty stale fleet and the delta
 * stream fails CLOSED (block) — the safe default.
 *
 * Endpoints (mounted at /api/telemetry):
 *   GET /status                     fleet status (tenant-scoped)
 *   GET /status/:session            per-agent status (403 outside scope)
 *   GET /status/stream              SSE fleet status (<=1-2s tick)
 *   GET /transcript/:session/stream SSE court-gated deltas (<=1s tick)
 */
import { Router, type Request, type Response } from 'express';
import { readFleetStatus, readSeatStatus } from '../services/telemetry-store.js';
import { canAccessSession, scopeSeats } from '../services/telemetry-scope.js';
import { DeltaStreamController, type StreamFrame } from '../services/telemetry-stream.js';

const router = Router();

const STATUS_TICK_MS = 500;     // sub-second (the operator amend): matches the daemon FAST
                                // lane so the wire delivers status transitions <1s
const DELTA_TICK_MS = 1000;     // <= 1s transcript deltas
const HEARTBEAT_MS = 25000;

function emptyFleet() {
  return { schema: 'realtime-status/v1', ts: 0, seats: {}, stale: true };
}

// GET /status — fleet, tenant-scoped.
router.get('/status', (req: Request, res: Response) => {
  const snap = readFleetStatus();
  if (!snap) return res.json(emptyFleet());
  res.json({ schema: snap.schema, ts: snap.ts, seats: scopeSeats(req, snap.seats), stale: false });
});

// GET /status/stream — SSE fleet status, tenant-scoped.
// MUST be declared BEFORE the '/status/:session' param route: Express matches
// in declaration order, so with the param route first, '/status/stream' bound
// session='stream' -> 404 no-telemetry and the SSE stream was unreachable.
router.get('/status/stream', (req: Request, res: Response) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write('retry: 3000\n\n');

  let lastTs = -1;
  const push = () => {
    const snap = readFleetStatus();
    const ts = snap ? snap.ts : 0;
    if (ts === lastTs) return;              // only emit on change (<=1-2s)
    lastTs = ts;
    const payload = snap
      ? { schema: snap.schema, ts: snap.ts, seats: scopeSeats(req, snap.seats), stale: false }
      : emptyFleet();
    res.write(`event: status\ndata: ${JSON.stringify(payload)}\n\n`);
  };
  push();
  const timer = setInterval(push, STATUS_TICK_MS);
  timer.unref?.();
  const heartbeat = setInterval(() => res.write(': ping\n\n'), HEARTBEAT_MS);
  heartbeat.unref?.();
  req.on('close', () => { clearInterval(timer); clearInterval(heartbeat); });
});

// GET /status/:session — per-agent, 403 outside scope. Declared AFTER the
// literal '/status/stream' so it never captures 'stream' as a session id.
router.get('/status/:session', (req: Request, res: Response) => {
  const session = String(req.params.session);
  if (!canAccessSession(req, session)) return res.status(403).json({ error: 'forbidden' });
  const seat = readSeatStatus(session);
  if (!seat) return res.status(404).json({ error: 'no telemetry for session', session, stale: true });
  res.json(seat);
});

// GET /transcript/:session/stream — SSE court-gated deltas.
// BLOCKING-3: tenant 403 BEFORE any court-bridge call or log relay.
router.get('/transcript/:session/stream', async (req: Request, res: Response) => {
  const session = String(req.params.session);
  if (!canAccessSession(req, session)) return res.status(403).json({ error: 'forbidden' });

  const after = typeof req.query.after === 'string' ? parseInt(req.query.after, 10) : 0;
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write('retry: 3000\n\n');

  const controller = new DeltaStreamController(session, { afterSeq: Number.isFinite(after) ? after : 0 });
  const write = (f: StreamFrame) => {
    const ev = f.type === 'block' ? 'block' : 'delta';
    res.write(`event: ${ev}\ndata: ${JSON.stringify(f)}\n\n`);
  };

  try {
    const first = await controller.open();
    write(first);
  } catch {
    write({ type: 'block', reason: 'open-error' });   // fail-closed
  }

  const timer = setInterval(async () => {
    if (controller.isBlocked) return;
    try {
      const f = await controller.tick();
      if (f) write(f);
    } catch { /* keep the beat; never surface raw on error */ }
  }, DELTA_TICK_MS);
  timer.unref?.();
  const heartbeat = setInterval(() => res.write(': ping\n\n'), HEARTBEAT_MS);
  heartbeat.unref?.();
  req.on('close', () => { clearInterval(timer); clearInterval(heartbeat); });
});

export default router;
