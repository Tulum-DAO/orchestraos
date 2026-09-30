/**
 * arturoStream.ts — the browser half of /api/arturo/text/stream.
 *
 * SSE framing is simple until the network splits it: a frame can arrive in pieces, several can
 * arrive at once, and the last one can be half-written when a turn dies. So the parser holds a
 * buffer and only emits on a complete blank-line-terminated frame, which is what the
 * split-at-every-byte test pins.
 */
import { buildTextBody, arturoText, type ArturoContext, type ArturoReply } from './arturo';

export interface ArturoStreamEvent {
  event: 'turn.start' | 'text.delta' | 'thinking.delta' | 'turn.end' | 'error' | string;
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

export interface StreamCallbacks {
  onStart?: (data: any) => void;
  onDelta?: (text: string) => void;
  onEnd?: (data: any) => void;
  onError?: (data: any) => void;
}

/**
 * POST a turn and deliver its events as they arrive. Returns the final reply, so a caller can
 * await the turn exactly as it awaited `arturoText`.
 */
export async function arturoTextStream(
  body: Record<string, unknown>,
  cb: StreamCallbacks = {},
  signal?: AbortSignal,
): Promise<{ ok: boolean; reply_text?: string; error?: string }> {
  let res: Response;
  try {
    res = await fetch('/api/arturo/text/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal,
    });
  } catch (e) {
    cb.onError?.({ code: 'network' });
    return { ok: false, error: 'network' };
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
    else if (e.event === 'turn.end') { final = e.data; cb.onEnd?.(e.data); }
    else if (e.event === 'error') { failed = e.data; cb.onError?.(e.data); }
  });

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      feed(decoder.decode(value, { stream: true }));
    }
  } catch {
    if (!final) { cb.onError?.({ code: 'stream_broken' }); return { ok: false, error: 'stream_broken' }; }
  }
  if (failed && !final) return { ok: false, error: failed.code || 'turn_failed' };
  if (!final) return { ok: false, error: 'incomplete' };
  return { ok: true, reply_text: final.reply_text };
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
    signal?: AbortSignal;
  } = {},
): Promise<ArturoReply> {
  const body = buildTextBody(text, conversationId, ctx, opts.brain);
  let started = false;
  const res = await arturoTextStream(body, {
    // turn.start is the server saying it has the turn — a stricter "Sent" than an upload event.
    onStart: () => { started = true; opts.onSent?.(); },
    onDelta: (t) => opts.onDelta?.(t),
  }, opts.signal);

  if (res.ok) return { ok: true, reply_text: res.reply_text || '', tools_called: [], spawned: [] } as ArturoReply;
  if (opts.signal?.aborted) return { ok: false, error: 'aborted' } as ArturoReply;
  // Anything that failed BEFORE the first delta is safe to re-ask; a turn that failed after
  // partial text is re-asked too, and the caller clears what it had shown.
  void started;
  return arturoText(text, conversationId, ctx, { onSent: opts.onSent, brain: opts.brain });
}
