/**
 * Fetch-based SSE client for POST /v2/converse.
 *
 * Native EventSource only does GET and can't set headers, but the frozen
 * contract POSTs a JSON body and an X-Jarvis-Token header. So we POST via
 * fetch() and parse the SSE stream off the response body ourselves.
 *
 * SSE frame format we parse (per contract.ts envelope):
 *   event: <type>\n
 *   data: <json>\n
 *   \n
 */
import {
  JARVIS_TOKEN_HEADER,
  type ConverseStreamEvent,
  type ConverseRequest,
} from './contract';
import { CONVERSE_ENDPOINT, JARVIS_TOKEN } from './config';

export interface ConverseStreamOptions {
  endpoint?: string;
  token?: string;
  signal?: AbortSignal;
}

/**
 * Parse one SSE frame block (already split on the blank-line delimiter).
 * Yields any event on the /v2/converse stream — converse envelope events
 * (start/delta/done/error) AND tool-call lifecycle events (B2). The payload
 * is self-describing via its `type`, so the parser is type-agnostic.
 */
function parseFrame(block: string): ConverseStreamEvent | null {
  let dataLine = '';
  for (const raw of block.split('\n')) {
    const line = raw.replace(/\r$/, '');
    if (line.startsWith('data:')) {
      dataLine += line.slice(5).trimStart();
    }
    // `event:` and `id:`/comment lines are ignored — the type lives in the
    // JSON payload so the stream is self-describing regardless of framing.
  }
  if (!dataLine) return null;
  try {
    return JSON.parse(dataLine) as ConverseStreamEvent;
  } catch {
    return null;
  }
}

/**
 * Stream a converse turn. Async generator yielding ConverseEvents in order.
 * Throws on transport/auth failure (non-2xx) or abort.
 */
export async function* converseStream(
  request: ConverseRequest,
  opts: ConverseStreamOptions = {},
): AsyncGenerator<ConverseStreamEvent, void, unknown> {
  const endpoint = opts.endpoint ?? CONVERSE_ENDPOINT;
  const token = opts.token ?? JARVIS_TOKEN;

  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      [JARVIS_TOKEN_HEADER]: token,
    },
    body: JSON.stringify(request),
    signal: opts.signal,
  });

  if (res.status === 401 || res.status === 403) {
    throw new Error(`Auth rejected (${res.status})`);
  }
  if (!res.ok || !res.body) {
    throw new Error(`Converse failed (${res.status})`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Split complete frames on the SSE blank-line delimiter.
      let idx: number;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const evt = parseFrame(block);
        if (evt) yield evt;
      }
    }
    // Flush any trailing frame without a terminating blank line.
    const tail = parseFrame(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}
