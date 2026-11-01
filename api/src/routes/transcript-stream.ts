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
 *   event: live_text  data: { agent_id, message_id, text, blocks[{ uuid, text }], done }
 *                         HEADLESS seats only (services/live-text.ts): the reply still being
 *                         written. Sent on change, after the beat's delta, so the frame that brings
 *                         a finished line also takes its words out of the live text; text '' clears.
 *                         No `id:` line, so it never moves the resume offset.
 *
 * A seat with no transcript and no tmux window for about 30 s (an agent with no pane, a retired seat whose page is
 * still open) stops being polled: its clients get one `: gone` comment and keep their connection (no reconnect
 * loop), and the next subscriber starts it again.
 *
 * Kept in its OWN router (like chat-transcript.ts) to avoid co-edit clobbering.
 * Mounted at /api/agents in server.ts.
 */
import { Router, type Request, type Response } from 'express';
import { TranscriptTailer } from '../services/transcript-tail.js';
import { resolveTranscriptPath, seatTmuxSession } from './chat-transcript.js';
import { paneGone } from '../lib/paneProbe.js';
import { agentScopeParam } from '../lib/agent-scope.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { dataDir } from '../lib/config.js';
import { headlessSeats } from '../services/status-stream.js';
import { LiveText, type LiveTextView } from '../services/live-text.js';
import { existsSync } from 'fs';
import { join } from 'path';

const router = Router();
// Every /:id route on this router is scoped to the caller's principal — the same rule GET /
// applies (lib/agent-scope.ts). Registered as a param handler so a /:id route added later is
// scoped without anyone remembering to; an out-of-scope id gets the same 404 as an unknown one.
router.param('id', agentScopeParam);

const TICK_MS = 1000;
const HEARTBEAT_MS = 25000;
const DEFAULT_LIMIT = 150;
const MAX_LIMIT = 500;
/** Live text is read more often than the transcript: words arrive many times a second. */
const LIVE_TICK_MS = 250;

// One shared tailer (+ one file-read per beat) per agent, however many
// clients are watching. Disposed when the last subscriber disconnects.
interface Live {
  tailer: TranscriptTailer; timer: NodeJS.Timeout | null; gone: Set<() => void>;
  liveText: LiveText | null; liveTimer: NodeJS.Timeout | null; liveSubs: Set<(v: LiveTextView) => void>;
  headless: boolean;
}
const live = new Map<string, Live>();

/** The seat's supervisor log, or null for an id that is not a safe path segment / no data dir. */
export function headlessEventsPath(agentId: string, root?: string): string | null {
  if (!isSafeAgentId(agentId)) return null;
  try { return join(root ?? dataDir(), 'state', 'headless', agentId, 'events.jsonl'); } catch { return null; }
}

/** Live text only for a seat switched to headless and not in Dev (a TUI then owns the session). */
export function liveTextOn(agentId: string, headless: Set<string> = headlessSeats(), root?: string): boolean {
  if (!headless.has(agentId) || !isSafeAgentId(agentId)) return false;
  try { return !existsSync(join(root ?? dataDir(), 'state', 'headless', agentId, 'dev')); } catch { return false; }
}

function liveStep(entry: Live): void {
  if (!entry.liveText) return;
  let v: LiveTextView | null = null;
  try { v = entry.liveText.step((u) => entry.tailer.hasItemUuid(u), entry.headless); } catch { v = null; }
  if (!v) return;
  for (const cb of entry.liveSubs) { try { cb(v); } catch { /* a closed client */ } }
}

function startBeat(agentId: string, entry: Live): void {
  entry.timer = setInterval(() => {
    let r: 'gone' | void = undefined;
    try { r = entry.tailer.tick(); } catch { /* keep the beat alive */ }
    if (entry.liveText) {
      try { entry.headless = liveTextOn(agentId); } catch { entry.headless = false; }
      liveStep(entry);                     // right after the delta: a landed line leaves the live text in the same beat
    }
    if (r === 'gone') {
      // Nothing left to tail and no window to come back to: stop asking. The clients stay connected.
      if (entry.timer) clearInterval(entry.timer);
      entry.timer = null;
      if (entry.liveTimer) clearInterval(entry.liveTimer);
      entry.liveTimer = null;
      for (const g of entry.gone) { try { g(); } catch { /* a closed client */ } }
    }
  }, TICK_MS);
  entry.timer.unref?.();
  if (entry.liveText && !entry.liveTimer) {
    entry.liveTimer = setInterval(() => liveStep(entry), LIVE_TICK_MS);
    entry.liveTimer.unref?.();
  }
}

function getTailer(agentId: string, limit: number): Live {
  const existing = live.get(agentId);
  if (existing) {
    if (existing.tailer.isStopped) { existing.tailer.restart(); startBeat(agentId, existing); }
    return existing;
  }
  const tmux = seatTmuxSession(agentId);
  const tailer = new TranscriptTailer({
    agentId,
    resolve: () => resolveTranscriptPath(agentId),
    limit,
    windowGone: () => !tmux || paneGone(tmux),
  });
  // Every seat gets the follower (cheap: a missing log reads as nothing), so a seat switched on
  // while its page is open starts streaming at the next beat; it only ever speaks while headless.
  const events = headlessEventsPath(agentId);
  const entry: Live = {
    tailer, timer: null, gone: new Set(),
    liveText: events ? new LiveText(events) : null, liveTimer: null, liveSubs: new Set(),
    headless: false,
  };
  try { entry.headless = liveTextOn(agentId); } catch { entry.headless = false; }
  startBeat(agentId, entry);
  live.set(agentId, entry);
  return entry;
}

function releaseTailer(agentId: string): void {
  const entry = live.get(agentId);
  if (entry && entry.tailer.subscriberCount === 0) {
    if (entry.timer) clearInterval(entry.timer);
    if (entry.liveTimer) clearInterval(entry.liveTimer);
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

  const entry = getTailer(agentId, limit);
  const tailer = entry.tailer;
  const unsubscribe = tailer.subscribe((ev) => {
    res.write(`event: ${ev.type}\nid: ${ev.id}\ndata: ${JSON.stringify(ev)}\n\n`);
  }, lastEventId);
  const onGone = () => { res.write(': gone\n\n'); };
  entry.gone.add(onGone);
  const onLive = (v: LiveTextView) => {
    res.write(`event: live_text\ndata: ${JSON.stringify({ agent_id: agentId, ...v })}\n\n`);
  };
  // A turn already under way when the page opens: its words so far, at once (the others hear
  // of a change from this step as usual; this client gets the state once, after it).
  liveStep(entry);
  entry.liveSubs.add(onLive);
  const now = entry.liveText?.current();
  if (entry.headless && now && now.text) onLive(now);

  const heartbeat = setInterval(() => { res.write(': ping\n\n'); }, HEARTBEAT_MS);
  heartbeat.unref?.();

  req.on('close', () => {
    clearInterval(heartbeat);
    entry.gone.delete(onGone);
    entry.liveSubs.delete(onLive);
    unsubscribe();
    releaseTailer(agentId);
  });
});

export default router;
