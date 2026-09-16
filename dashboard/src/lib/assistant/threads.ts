/**
 * Saved-conversations client lib (Phase 1): thread list/replay fetchers.
 * The pure hydration reducer lives in thread-hydrate.ts (type-only, testable)
 * and is re-exported here for a single import surface.
 *
 * Built against the /v2/threads contract (Track A owns the real server; mock
 * serves it today).
 */
import {
  JARVIS_TOKEN_HEADER,
  type ThreadSummary,
  type ThreadDetail,
} from './contract';
import { CONVERSE_ENDPOINT, JARVIS_TOKEN } from './config';

export { hydrateTimeline } from './thread-hydrate';

/** Threads base = the converse endpoint with `/converse` swapped for `/threads`. */
function threadsBase(): string {
  return CONVERSE_ENDPOINT.replace(/\/converse$/, '/threads');
}

function authHeaders(token: string): HeadersInit {
  return { Accept: 'application/json', [JARVIS_TOKEN_HEADER]: token };
}

export interface ListThreadsOpts {
  q?: string;
  archived?: boolean;
  token?: string;
  signal?: AbortSignal;
}

/** GET /v2/threads — the rail list. */
export async function listThreads(opts: ListThreadsOpts = {}): Promise<ThreadSummary[]> {
  const token = opts.token ?? JARVIS_TOKEN;
  const url = new URL(threadsBase(), window.location.origin);
  if (opts.q) url.searchParams.set('q', opts.q);
  if (opts.archived) url.searchParams.set('archived', '1');
  const res = await fetch(url.pathname + url.search, { headers: authHeaders(token), signal: opts.signal });
  if (!res.ok) throw new Error(`listThreads failed (${res.status})`);
  const data = await res.json();
  return (data.threads ?? []) as ThreadSummary[];
}

/** GET /v2/threads/{thread} — full replay. */
export async function getThread(thread: string, opts: { token?: string; signal?: AbortSignal } = {}): Promise<ThreadDetail> {
  const token = opts.token ?? JARVIS_TOKEN;
  const res = await fetch(`${threadsBase()}/${encodeURIComponent(thread)}`, {
    headers: authHeaders(token),
    signal: opts.signal,
  });
  if (!res.ok) throw new Error(`getThread failed (${res.status})`);
  return (await res.json()) as ThreadDetail;
}
