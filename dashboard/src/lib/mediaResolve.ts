/**
 * The id for an image path an agent wrote, from POST /api/media/resolve (routes/media.ts): the
 * seam for messages the API did not stamp (Arturo's threads). One request per (agent, path), the
 * answer kept for the page's life; a refusal is kept too (null), so a missing file is asked once.
 */
import { post } from './api';

const cache = new Map<string, Promise<string | null>>();

export function resolveMediaId(agent: string, path: string, fetcher: (a: string, p: string) => Promise<string | null> = viaApi): Promise<string | null> {
  const key = `${agent}\0${path}`;
  let p = cache.get(key);
  if (!p) {
    p = fetcher(agent, path).catch(() => null);
    cache.set(key, p);
  }
  return p;
}

async function viaApi(agent: string, path: string): Promise<string | null> {
  const r = await post<{ id?: string }>('/media/resolve', { agent, path });
  return typeof r?.id === 'string' ? r.id : null;
}

/** Tests only. */
export function _resetMediaResolveCache(): void { cache.clear(); }
