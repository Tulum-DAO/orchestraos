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
 *   event: step       data: { agent_id, kind: 'tool'|'thinking'|'waiting'|'idle', tool?, summary?, since }
 *                         What the agent is doing right now (services/live-step.ts): a headless seat from its
 *                         supervisor log, a tmux seat from the pane probe (summary from its open tool call). Sent on
 *                         change and once on connect; `kind: null` = no step to show (stopped, restarting, in Dev).
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
import { resolveTranscriptPath, resolveSubagentTranscriptPath, SAFE_SUBAGENT_ID, seatTmuxSession } from './chat-transcript.js';
import { paneGone } from '../lib/paneProbe.js';
import { agentScopeParam } from '../lib/agent-scope.js';
import { isSafeAgentId } from '../lib/agentPaths.js';
import { dataDir } from '../lib/config.js';
import { headlessSeats } from '../services/status-stream.js';
import { LiveText, type LiveTextView } from '../services/live-text.js';
import { compactionNotices, compactionItem, noticeShown } from '../services/conversation-store.js';
import { readSeatSettings } from '../lib/seatSettings.js';
import { headlessStep, openToolUse, stepFromProbe, stepSig, type StepBody } from '../services/live-step.js';
import { getDetectorStates, type DetectorStatus } from '../services/agent-status.js';
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
  /** Compaction notices already announced: id -> what was sent (state, end, tokens), so a change is sent once. */
  notices: Map<number, string>; noticeSubs: Set<(item: Record<string, unknown>) => void>;
  /** The live step last sent, and who hears of a change. */
  step: StepBody | null; stepKey: string; stepSubs: Set<(s: StepBody | null) => void>;
}
const live = new Map<string, Live>();

/** Notices sent on a new connection: the latest few (older ones are in the poll route's pages). */
const NOTICES_ON_CONNECT = 20;

/** The seat's notices as `event: compaction` items, unless its show_compaction is off. */
export function visibleNotices(agentId: string): Record<string, unknown>[] {
  try {
    if (!readSeatSettings(dataDir(), agentId).show_compaction) return [];
    return compactionNotices(agentId).filter(noticeShown).map(compactionItem);
  } catch { return []; }
}

const noticeSig = (it: Record<string, unknown>) => `${it.state}|${it.finished_at}|${it.after_tokens}|${it.before_tokens}`;

/** A notice that started, finished or gained its numbers since the last beat goes to every client once. */
function noticeStep(agentId: string, entry: Live): void {
  for (const it of visibleNotices(agentId)) {
    const id = Number(String(it.id).slice(4));
    const sig = noticeSig(it);
    if (entry.notices.get(id) === sig) continue;
    entry.notices.set(id, sig);
    for (const cb of entry.noticeSubs) { try { cb(it); } catch { /* a closed client */ } }
  }
}

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

// The pane probe's latest verdicts (a stale-while-revalidate cache, services/agent-status.ts), kept here so a beat
// never waits on a scan.
let probe: Map<string, DetectorStatus> = new Map();
let probing = false;
function probeNow(): Map<string, DetectorStatus> {
  if (!probing) {
    probing = true;
    getDetectorStates().then((m) => { probe = m; }).catch(() => { /* keep the last */ }).finally(() => { probing = false; });
  }
  return probe;
}

/** The seat's step now: ONE source, chosen by whether it runs headless (and not in Dev). */
export function currentStep(agentId: string, entry: Pick<Live, 'headless' | 'tailer'>): StepBody | null {
  if (headlessSeats().has(agentId)) return entry.headless ? headlessStep(agentId, dataDir()) : null;
  const tmux = seatTmuxSession(agentId);
  const d = tmux ? probeNow().get(tmux) : undefined;
  return stepFromProbe(d, openToolUse(entry.tailer.recentItems()));
}

function stepBeat(agentId: string, entry: Live): void {
  let s: StepBody | null = null;
  try { s = currentStep(agentId, entry); } catch { s = null; }
  const key = stepSig(s);
  if (key === entry.stepKey) return;
  entry.step = s; entry.stepKey = key;
  for (const cb of entry.stepSubs) { try { cb(s); } catch { /* a closed client */ } }
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
    noticeStep(agentId, entry);
    if (entry.liveText) {
      try { entry.headless = liveTextOn(agentId); } catch { entry.headless = false; }
      liveStep(entry);                     // right after the delta: a landed line leaves the live text in the same beat
    }
    stepBeat(agentId, entry);
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
    entry.liveTimer = setInterval(() => { liveStep(entry); if (entry.headless) stepBeat(agentId, entry); }, LIVE_TICK_MS);
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
    headless: false, notices: new Map(), noticeSubs: new Set(),
    step: null, stepKey: '', stepSubs: new Set(),
  };
  // Notices that exist when the stream starts are already known: a client gets them on connect, not again per beat.
  for (const it of visibleNotices(agentId)) entry.notices.set(Number(String(it.id).slice(4)), noticeSig(it));
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

  // The compaction notice: the latest ones now, then each start or finish as it happens (same object as the poll
  // route's `compaction` item; it updates in place by id). Never part of any model's context.
  const onNotice = (it: Record<string, unknown>) => { res.write(`event: compaction\ndata: ${JSON.stringify(it)}\n\n`); };
  noticeStep(agentId, entry);
  for (const it of visibleNotices(agentId).slice(-NOTICES_ON_CONNECT)) onNotice(it);
  entry.noticeSubs.add(onNotice);

  // The live step: what it is doing now, at once, then each change.
  const onStep = (st: StepBody | null) => {
    res.write(`event: step\ndata: ${JSON.stringify(st ? { agent_id: agentId, ...st } : { agent_id: agentId, kind: null })}\n\n`);
  };
  stepBeat(agentId, entry);
  onStep(entry.step);
  entry.stepSubs.add(onStep);

  const heartbeat = setInterval(() => { res.write(': ping\n\n'); }, HEARTBEAT_MS);
  heartbeat.unref?.();

  req.on('close', () => {
    clearInterval(heartbeat);
    entry.gone.delete(onGone);
    entry.liveSubs.delete(onLive);
    entry.noticeSubs.delete(onNotice);
    entry.stepSubs.delete(onStep);
    unsubscribe();
    releaseTailer(agentId);
  });
});

// ---- One subagent's own log: GET /:id/subagents/:aid/transcript/stream (the agent-tree drill-in) ----
// The same snapshot/delta protocol as the seat stream, and nothing else: no live text, no step, no compaction
// notices (those are the seat's; the view is read-only). Its own map, keyed `sub:<id>:<aid>`, so no seat id can
// ever name a subagent's tailer. Scoped like the seat stream (router.param('id') above). A subagent with no
// file yet streams an empty snapshot and fills in when its log appears.
export const subagentTailerKey = (agentId: string, subagentId: string): string => `sub:${agentId}:${subagentId}`;
const subLive = new Map<string, { tailer: TranscriptTailer; timer: NodeJS.Timeout }>();

export function liveSubagentKeys(): string[] { return [...subLive.keys()]; }

export function handleSubagentStream(req: Request, res: Response): void {
  const agentId = String(req.params.id);
  const subagentId = String(req.params.aid);
  if (!isSafeAgentId(agentId) || !SAFE_SUBAGENT_ID.test(subagentId)) {
    res.status(400).json({ error: 'bad agent or subagent id' });
    return;
  }
  const key = subagentTailerKey(agentId, subagentId);
  const limit = Math.min(parseInt(String(req.query.limit)) || DEFAULT_LIMIT, MAX_LIMIT);
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

  let entry = subLive.get(key);
  if (!entry) {
    const tailer = new TranscriptTailer({
      agentId, limit, keepSidechain: true,
      resolve: () => { try { return resolveSubagentTranscriptPath(agentId, subagentId); } catch { return { path: null, sid: null }; } },
    });
    const timer = setInterval(() => { try { tailer.tick(); } catch { /* keep the beat alive */ } }, TICK_MS);
    timer.unref?.();
    entry = { tailer, timer };
    subLive.set(key, entry);
  }
  const mine = entry;
  const unsubscribe = mine.tailer.subscribe((ev) => {
    res.write(`event: ${ev.type}\nid: ${ev.id}\ndata: ${JSON.stringify(ev)}\n\n`);
  }, lastEventId);
  const heartbeat = setInterval(() => { res.write(': ping\n\n'); }, HEARTBEAT_MS);
  heartbeat.unref?.();
  req.on('close', () => {
    clearInterval(heartbeat);
    unsubscribe();
    if (mine.tailer.subscriberCount === 0 && subLive.get(key) === mine) {
      clearInterval(mine.timer);
      subLive.delete(key);
    }
  });
}

router.get('/:id/subagents/:aid/transcript/stream', handleSubagentStream);

export default router;
