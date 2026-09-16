/**
 * F1 — live transcript streaming (SPEC_transcript-ecosystem §4).
 *
 * GET /api/agents/:id/transcript/stream  (SSE)
 *
 * Wire protocol (all payloads are F0 v2 contract shapes, grammar_version=2):
 *   event: snapshot   data: { agent_id, session_id, grammar_version,
 *                             items[last N], render_items[], reset? }
 *   event: delta      data: { agent_id, session_id, grammar_version,
 *                             items[only the new ones] }
 *   id: <session_id>:<offset>   — offset = item count in the FULL transcript;
 *                                 browsers replay it as Last-Event-ID on
 *                                 reconnect, so a resume gets a delta of the
 *                                 missed tail instead of a full refetch.
 *
 * ADDITIVE lane: GET /:id/transcript (the poll path) is untouched and remains
 * the client fallback. Live agent STATE stays the v2-detector lane — never
 * folded into this stream (locked congruence guardrail, DEC-1787826639).
 *
 * Kept in its OWN router (like chat-transcript.ts) to avoid co-edit clobbering.
 * Mounted at /api/agents in server.ts.
 */
import { Router, type Request, type Response } from 'express';
import { TranscriptTailer } from '../services/transcript-tail.js';
import { resolveTranscriptPath } from './chat-transcript.js';

const router = Router();

const TICK_MS = 1000;
const HEARTBEAT_MS = 25000;
const DEFAULT_LIMIT = 150;
const MAX_LIMIT = 500;

// One shared tailer (+ one file-read per beat) per agent, however many
// clients are watching. Disposed when the last subscriber disconnects.
const live = new Map<string, { tailer: TranscriptTailer; timer: NodeJS.Timeout }>();

function getTailer(agentId: string, limit: number): TranscriptTailer {
  const existing = live.get(agentId);
  if (existing) return existing.tailer;
  const tailer = new TranscriptTailer({
    agentId,
    resolve: () => resolveTranscriptPath(agentId),
    limit,
  });
  const timer = setInterval(() => {
    try { tailer.tick(); } catch { /* keep the beat alive */ }
  }, TICK_MS);
  timer.unref?.();
  live.set(agentId, { tailer, timer });
  return tailer;
}

function releaseTailer(agentId: string): void {
  const entry = live.get(agentId);
  if (entry && entry.tailer.subscriberCount === 0) {
    clearInterval(entry.timer);
    live.delete(agentId);
  }
}

router.get('/:id/transcript/stream', (req: Request, res: Response) => {
  const agentId = String(req.params.id);
  const limit = Math.min(parseInt(String(req.query.limit)) || DEFAULT_LIMIT, MAX_LIMIT);
  // EventSource sends Last-Event-ID only on auto-reconnect; `after` covers an
  // explicit first-connect resume (e.g. a client that kept its offset).
  const lastEventId =
    (req.headers['last-event-id'] as string | undefined) ||
    (typeof req.query.after === 'string' ? req.query.after : undefined);

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write('retry: 3000\n\n');

  const tailer = getTailer(agentId, limit);
  const unsubscribe = tailer.subscribe((ev) => {
    res.write(`event: ${ev.type}\nid: ${ev.id}\ndata: ${JSON.stringify(ev)}\n\n`);
  }, lastEventId);

  const heartbeat = setInterval(() => { res.write(': ping\n\n'); }, HEARTBEAT_MS);
  heartbeat.unref?.();

  req.on('close', () => {
    clearInterval(heartbeat);
    unsubscribe();
    releaseTailer(agentId);
  });
});

export default router;
