/**
 * arturoStream.ts — the browser half of /api/arturo/text/stream.
 *
 * SSE framing is simple until the network splits it: a frame can arrive in pieces, several can
 * arrive at once, and the last one can be half-written when a turn dies. So the parser holds a
 * buffer and only emits on a complete blank-line-terminated frame, which is what the
 * split-at-every-byte test pins.
 */
import { buildTextBody, arturoText, isStarting, newTurnId, type ArturoContext, type ArturoReply } from './arturo';
import type { ToolCallEvent, ToolResultEvent } from './turnParts';
import { loadTurn, type TurnFate } from './arturoThreads';
import { isBusy } from './arturoResume';

export interface ArturoStreamEvent {
  event: 'turn.start' | 'text.delta' | 'thinking.delta' | 'tool.call' | 'tool.result'
    | 'turn.reset' | 'turn.end' | 'error' | string;
  data: any;
}

/** Returns feed(chunk): call it with each chunk of the response body. */
export function parseSseChunks(onEvent: (e: ArturoStreamEvent) => void): (chunk: string) => void {
  let buffer = '';
  return (chunk: string) => {
    buffer += chunk;
    for (;;) {
      const cut = buffer.indexOf('\n\n');
      if (cut < 0) return;                      // a partial frame waits for the rest
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let name = 'message';
      const dataLines: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith(':')) continue;      // a keep-alive comment is not an event
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        onEvent({ event: name, data: JSON.parse(dataLines.join('\n')) });
      } catch {
        // A frame we cannot read is dropped; the turn continues. Dying here would lose the
        // rest of a reply over one bad line.
      }
    }
  };
}

/**
 * Read a body, handing each chunk to `onChunk`, and give up if nothing arrives for
 * `idleMs`. The server sends a keep-alive comment every 10s, so a longer silence is a dead
 * connection — without this a turn whose socket died left the bubble spinning while the
 * answer sat finished on the server (found live, 2026-09-30).
 */
export async function readStream(
  body: ReadableStream<Uint8Array>,
  onChunk: (text: string) => void,
  idleMs = 45000,
): Promise<'done' | 'stalled' | 'broken'> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const idle = new Promise<'idle'>((resolve) => { timer = setTimeout(() => resolve('idle'), idleMs); });
      const next = reader.read().then((r) => r);
      const winner = await Promise.race([next, idle]);
      clearTimeout(timer);
      if (winner === 'idle') { void reader.cancel().catch(() => {}); return 'stalled'; }
      const { done, value } = winner as ReadableStreamReadResult<Uint8Array>;
      if (done) return 'done';
      if (value) onChunk(decoder.decode(value, { stream: true }));
    }
  } catch {
    return 'broken';
  }
}

/**
 * Report a failed turn to the server log, so a failure on the operator's own machine is visible
 * without them opening devtools. Found necessary live: a turn died in the operator's browser
 * (the streaming request never left it, and a fallback the server DID answer never came back)
 * while every reproduction on the box succeeded. sendBeacon survives the page being closed.
 */
export function reportTurnFailure(stage: string, detail: Record<string, unknown> = {}): void {
  try {
    const body = JSON.stringify({
      stage, ...detail,
      ua: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      at: new Date().toISOString(),
    });
    if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
      navigator.sendBeacon('/api/arturo/client-log', new Blob([body], { type: 'application/json' }));
    } else {
      void fetch('/api/arturo/client-log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true }).catch(() => {});
    }
  } catch { /* diagnostics must never break a turn */ }
  // eslint-disable-next-line no-console
  console.warn('[arturo] turn failed at', stage, detail);
}

export interface StreamCallbacks {
  onStart?: (data: any) => void;
  onDelta?: (text: string) => void;
  /** A tool is about to run: open its card. */
  onToolCall?: (call: ToolCallEvent) => void;
  /** That tool finished: close its card (paired by call_id). */
  onToolResult?: (result: ToolResultEvent) => void;
  /** The whole-reply path took the turn over: clear what this turn has shown so far. */
  onReset?: (data: any) => void;
  onEnd?: (data: any) => void;
  onError?: (data: any) => void;
}

/** What a finished streamed turn reports — the same facts /text returns. */
export interface StreamOutcome {
  ok: boolean;
  /** The HTTP status of a refusal (no stream opened), e.g. 409 when the conversation is mid-turn. */
  status?: number;
  reply_text?: string;
  tools_called?: string[];
  spawned?: string[];
  operator?: any;
  brain?: any;
  /** What the turn's tools left for the page (a choice card, a pairing card), and on an onboarding
   *  turn whether onboarding is now done. */
  choices?: any;
  pair_card?: any;
  paired?: any;
  onboarding?: any;
  error?: string;
  replayed?: boolean;
  /** Set when the server reported the failure itself (an `error` event): its turn is over. */
  reported?: boolean;
}

/** What a failure body says the turn already did: kept so the page can say so (describeTurnError). */
function pickFailure(json: any): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of ['tools_called', 'provider', 'model', 'reason', 'detail', 'field']) if (json?.[k] !== undefined) out[k] = json[k];
  return out;
}

/**
 * POST a turn and deliver its events as they arrive. Returns the final reply, so a caller can
 * await the turn exactly as it awaited `arturoText`.
 */
export async function arturoTextStream(
  body: Record<string, unknown>,
  cb: StreamCallbacks = {},
  signal?: AbortSignal,
): Promise<StreamOutcome> {
  let res: Response;
  // A deadline on the request itself, not only on its reads: the server sends turn.start the
  // moment it has the turn, so no response head within 20s is a stuck request, and without this
  // a request that never got going hung the turn with nothing to say why.
  const headDeadline = new AbortController();
  const headTimer = setTimeout(() => headDeadline.abort(), 20000);
  const onOuterAbort = () => headDeadline.abort();
  signal?.addEventListener('abort', onOuterAbort);
  const t0 = Date.now();
  try {
    res = await fetch('/api/arturo/text/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: headDeadline.signal,
    });
  } catch (e) {
    const timedOut = headDeadline.signal.aborted && !signal?.aborted;
    reportTurnFailure(timedOut ? 'stream_no_response_head' : 'stream_fetch_rejected', {
      error: String((e as Error)?.message || e).slice(0, 200), ms: Date.now() - t0,
    });
    cb.onError?.({ code: timedOut ? 'stream_timeout' : 'network' });
    return { ok: false, error: timedOut ? 'stream_timeout' : 'network' };
  } finally {
    clearTimeout(headTimer);
    signal?.removeEventListener('abort', onOuterAbort);
  }
  const ctype = res.headers.get('content-type') || '';
  if (!ctype.includes('text/event-stream')) {
    const json = await res.json().catch(() => ({ ok: false, error: 'bad_response' }));
    // A send the server has already answered comes back whole, as JSON: nothing ran again
    // (DEC-1791518421640932). It is this send's answer, cards included.
    if (res.ok && json.ok) { cb.onEnd?.(json); return { ...json, ok: true }; }
    // A refusal comes back as ordinary JSON (a bad model id, an empty message).
    cb.onError?.(json);
    return { ok: false, status: res.status, error: json.error || 'bad_response', ...pickFailure(json) };
  }
  if (!res.body) { cb.onError?.({ code: 'no_body' }); return { ok: false, error: 'no_body' }; }

  let final: any = null;
  let failed: any = null;
  const feed = parseSseChunks((e) => {
    if (e.event === 'turn.start') cb.onStart?.(e.data);
    else if (e.event === 'text.delta') cb.onDelta?.(e.data.text || '');
    else if (e.event === 'tool.call') cb.onToolCall?.(e.data);
    else if (e.event === 'tool.result') cb.onToolResult?.(e.data);
    else if (e.event === 'turn.reset') cb.onReset?.(e.data);
    else if (e.event === 'turn.end') { final = e.data; cb.onEnd?.(e.data); }
    else if (e.event === 'error') { failed = e.data; cb.onError?.(e.data); }
  });

  const outcome = await readStream(res.body, feed, 45000);
  if (outcome !== 'done' && !final) {
    // Nothing usable arrived and the connection is gone: say so, so the caller can re-ask
    // rather than leave a bubble spinning.
    reportTurnFailure(outcome === 'stalled' ? 'stream_stalled' : 'stream_broken', { ms: Date.now() - t0 });
    cb.onError?.({ code: outcome === 'stalled' ? 'stream_stalled' : 'stream_broken' });
    return { ok: false, error: outcome === 'stalled' ? 'stream_stalled' : 'stream_broken' };
  }
  if (failed && !final) return { ok: false, error: failed.code || 'turn_failed', reported: true, ...pickFailure(failed) };
  if (!final) return { ok: false, error: 'incomplete' };
  // Everything turn.end says the turn DID — onboarding advances on tools_called, spawned and
  // operator, and a streamed turn used to report none of them.
  return {
    ok: true, reply_text: final.reply_text, tools_called: final.tools_called || [],
    spawned: final.spawned || [], operator: final.operator, brain: final.brain,
    choices: final.choices, pair_card: final.pair_card, paired: final.paired, onboarding: final.onboarding,
  };
}


/**
 * One operator send, start to finish: the ONE send path for the home and the pill (DEC-1791518421640932).
 *
 * The send gets a turn id once, and every attempt carries it, so the server runs it at most once and a
 * re-ask of a send that already landed is answered from the server's record, never run again. When an
 * attempt's outcome is unclear (busy, a hop still starting, a timeout, a dropped stream), the page asks the
 * server what became of THIS send (by id) before doing anything else:
 *   done    -> its answer, cards included (a dropped onboarding turn still advances the page);
 *   running -> keep waiting;
 *   lost    -> it may have run: never re-sent for the operator (MAY_HAVE_RUN_TEXT);
 *   unknown -> nothing of it ran: a turn that had started stops and says so (NOT_ANSWERED_TEXT, item 3);
 *              one that never reached the server is sent again, with the same id.
 * `stream: false` sends whole over /text (the pill); otherwise the first attempt streams.
 */
export async function arturoTurn(
  text: string,
  conversationId: string,
  ctx?: ArturoContext | null,
  opts: {
    onSent?: () => void;
    brain?: { provider: string; model: string };
    onDelta?: (text: string) => void;
    onToolCall?: (call: ToolCallEvent) => void;
    onToolResult?: (result: ToolResultEvent) => void;
    /** Clear what this turn has shown: the server's fallback is taking over, or ours is. */
    onReset?: () => void;
    signal?: AbortSignal;
    /** This send's id; minted here when absent. Pass one to keep it across the caller's own retries. */
    turnId?: string;
    /** false = whole replies over /text only (no streaming). */
    stream?: boolean;
    /** The stack is still starting (G15): show it, wait for health, resolve whether it came up. */
    onStarting?: () => Promise<boolean>;
  } = {},
): Promise<ArturoReply> {
  const turnId = opts.turnId || newTurnId();
  const deadline = Date.now() + SEND_MAX_MS;
  let streamNext = opts.stream !== false;
  let reasked = false;
  for (let attempt = 0; ; attempt++) {
    let started = false;
    let res: StreamOutcome | ArturoReply;
    if (streamNext) {
      res = await arturoTextStream(buildTextBody(text, conversationId, ctx, opts.brain, turnId), {
        // turn.start is the server saying it has the turn — a stricter "Sent" than an upload event.
        onStart: () => { started = true; opts.onSent?.(); },
        onDelta: (t) => opts.onDelta?.(t),
        onToolCall: (c) => opts.onToolCall?.(c),
        onToolResult: (r) => opts.onToolResult?.(r),
        onReset: () => opts.onReset?.(),
      }, opts.signal);
      streamNext = false;          // any re-ask is whole: a second stream would only re-show what the first did
    } else {
      res = await arturoText(text, conversationId, ctx, { onSent: opts.onSent, brain: opts.brain, turnId });
    }
    if (res.ok) return asReply(res);
    if (opts.signal?.aborted) return { ok: false, error: 'aborted' } as ArturoReply;
    const busy = isBusy(res);
    const reported = !!(res as StreamOutcome).reported;
    const dropped = DROPPED.has(res.error || '');
    const unclear = busy || reported || dropped || isUnclear(res);
    if (!unclear) return asReply(res);           // a refusal or a brain's own error: the server's final word
    // Whatever this attempt showed goes before the answer (or the next attempt) arrives.
    opts.onReset?.();
    const fate = await settleTurn(conversationId, turnId, opts.signal, deadline, busy ? 1 : UNKNOWN_POLLS);
    if (fate.state === 'aborted') return { ok: false, error: 'aborted' } as ArturoReply;
    if (fate.state === 'done') {
      if (!fate.result) return { ok: false, error: 'not_answered_here' } as ArturoReply;
      const { status, ...body } = fate.result;
      return (status === undefined || status === 200) && body.ok !== false
        ? asReply({ ...body, ok: true })
        : { ...body, ok: false, status } as ArturoReply;
    }
    if (fate.state === 'lost' || fate.state === 'timeout') {
      reportTurnFailure('turn_unresolved', { state: fate.state, error: res.error });
      return { ok: false, error: 'may_have_run' } as ArturoReply;
    }
    // unknown: the server holds nothing of this send, so nothing of it ran.
    if (started && !reported) {
      reportTurnFailure('turn_not_answered', { error: res.error });
      return { ok: false, error: 'not_answered' } as ArturoReply;
    }
    if (reported) {
      // The server ended its turn with an error and ran nothing: ask once more, whole (the old fallback).
      if (reasked) return asReply(res);
      reasked = true;
    } else if (!busy && isStarting(res as ArturoReply) && opts.onStarting) {
      if (!(await opts.onStarting())) return asReply(res);
    } else {
      await pause(READ_BACK_EVERY_MS, opts.signal);
    }
    if (Date.now() >= deadline || (!busy && attempt >= MAX_RESENDS)) return asReply(res);
  }
}

function asReply(r: StreamOutcome | ArturoReply): ArturoReply {
  const { reported: _r, ...rest } = r as StreamOutcome;
  return { ...rest, ...(rest.ok ? { reply_text: rest.reply_text || '', tools_called: rest.tools_called || [], spawned: rest.spawned || [] } : {}) } as ArturoReply;
}

/** Unclear = the send may or may not have reached the server: a hop still starting (502-504 from a hop, not a
 *  brain's own error), a timeout, or the network. Only the server can say what happened to it. */
function isUnclear(r: { ok: boolean; status?: number; error?: string }): boolean {
  if (BRAIN_ERROR_RE.test(String(r.error || ''))) return false;
  return isStarting(r as ArturoReply) || r.error === 'stream_timeout' || r.error === 'stream_no_body' || r.error === 'no_body';
}

const BRAIN_ERROR_RE = /^(brain_|empty_response|provider_unavailable|unknown_model|bad_brain|turn_lost|turn_id_conflict|turn_mark_unavailable|bad_turn_id)/;
const DROPPED = new Set(['stream_broken', 'stream_stalled', 'incomplete']);
const READ_BACK_EVERY_MS = 2000;
const UNKNOWN_POLLS = 3;                     // "unknown" is final only when seen 3 times, 2 s apart (v4)
const SEND_MAX_MS = 5 * 60 * 1000;           // past any full tool turn
const MAX_RESENDS = 3;

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((ok) => {
    if (signal?.aborted) { ok(); return; }
    const t = setTimeout(ok, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); ok(); }, { once: true });
  });
}

type Settled = TurnFate | { state: 'timeout' } | { state: 'aborted' };

/** Ask the server what became of this send until it is no longer running. Never sends anything. */
async function settleTurn(conversationId: string, turnId: string, signal: AbortSignal | undefined,
                          deadline: number, unknownPolls: number): Promise<Settled> {
  let unknowns = 0;
  for (;;) {
    if (signal?.aborted) return { state: 'aborted' };
    const fate = await loadTurn(conversationId, turnId);
    if (signal?.aborted) return { state: 'aborted' };       // nit 8: an abort mid-read wins over its answer
    if (fate) {
      if (fate.state === 'done' || fate.state === 'lost') return fate;
      if (fate.state === 'unknown') { if (++unknowns >= unknownPolls) return fate; } else unknowns = 0;
    }                                                          // null: the read failed (a hop starting): wait
    if (Date.now() >= deadline) return { state: 'timeout' };
    await pause(READ_BACK_EVERY_MS, signal);
  }
}


/**
 * Start this conversation's warm CLI before the operator sends anything, so the first turn
 * does not pay for the process start (~0.6-1.2s, measured). Fire-and-forget: a prewarm that
 * fails only means the first turn starts the process itself, exactly as it did before.
 */
export function arturoPrewarm(conversationId: string, brain?: { provider: string; model: string }): void {
  if (!conversationId) return;
  const body: Record<string, unknown> = { conversation_id: conversationId };
  if (brain) body.brain = brain;
  try {
    void fetch('/api/arturo/text/prewarm', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).catch(() => {});
  } catch { /* no fetch — nothing to warm, nothing lost */ }
}
