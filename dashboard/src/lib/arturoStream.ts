/**
 * arturoStream.ts — the browser half of /api/arturo/text/stream.
 *
 * SSE framing is simple until the network splits it: a frame can arrive in pieces, several can
 * arrive at once, and the last one can be half-written when a turn dies. So the parser holds a
 * buffer and only emits on a complete blank-line-terminated frame, which is what the
 * split-at-every-byte test pins.
 */
import { buildTextBody, arturoText, type ArturoContext, type ArturoReply } from './arturo';
import type { ToolCallEvent, ToolResultEvent } from './turnParts';

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
  reply_text?: string;
  tools_called?: string[];
  spawned?: string[];
  operator?: any;
  brain?: any;
  error?: string;
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
    // A refusal comes back as ordinary JSON (a bad model id, an empty message).
    const json = await res.json().catch(() => ({ ok: false, error: 'bad_response' }));
    cb.onError?.(json);
    return { ok: false, error: json.error || 'bad_response' };
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
  if (failed && !final) return { ok: false, error: failed.code || 'turn_failed' };
  if (!final) return { ok: false, error: 'incomplete' };
  // Everything turn.end says the turn DID — onboarding advances on tools_called, spawned and
  // operator, and a streamed turn used to report none of them.
  return {
    ok: true, reply_text: final.reply_text, tools_called: final.tools_called || [],
    spawned: final.spawned || [], operator: final.operator, brain: final.brain,
  };
}


/**
 * A turn that streams, with the whole-reply path as its fallback.
 *
 * The caller gets the same `ArturoReply` either way, so a surface can adopt streaming without
 * a second code path for "it didn't stream". Falls back when the install's API has no
 * /text/stream (an older box), or when the stream breaks before `turn.end` — a half-typed
 * reply is not an answer, so the turn is re-asked whole rather than left dangling.
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
  } = {},
): Promise<ArturoReply> {
  const body = buildTextBody(text, conversationId, ctx, opts.brain);
  let started = false;
  const res = await arturoTextStream(body, {
    // turn.start is the server saying it has the turn — a stricter "Sent" than an upload event.
    onStart: () => { started = true; opts.onSent?.(); },
    onDelta: (t) => opts.onDelta?.(t),
    onToolCall: (c) => opts.onToolCall?.(c),
    onToolResult: (r) => opts.onToolResult?.(r),
    onReset: () => opts.onReset?.(),
  }, opts.signal);

  if (res.ok) {
    return {
      ok: true, reply_text: res.reply_text || '', tools_called: res.tools_called || [],
      spawned: res.spawned || [], ...(res.operator !== undefined ? { operator: res.operator } : {}),
    } as ArturoReply;
  }
  if (opts.signal?.aborted) return { ok: false, error: 'aborted' } as ArturoReply;
  // The re-ask answers from the top: whatever the dead attempt showed (half a reply, tool
  // cards) is cleared first, or it sits beside the answer.
  opts.onReset?.();
  // Anything that failed BEFORE the first delta is safe to re-ask; a turn that failed after
  // partial text is re-asked too, and the caller clears what it had shown.
  const t0 = Date.now();
  const whole = await arturoText(text, conversationId, ctx, { onSent: opts.onSent, brain: opts.brain });
  if (!whole.ok) {
    reportTurnFailure('fallback_failed', { error: (whole as any).error, stream_error: res.error, started, ms: Date.now() - t0 });
  } else {
    reportTurnFailure('fell_back_ok', { stream_error: res.error, started, ms: Date.now() - t0 });
  }
  return whole;
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
