/**
 * Images in the chat (the client half of api/src/lib/imageRefs.ts + routes/media.ts).
 *
 * An agent's item (and one of Arturo's turns, as the API relays his thread) arrives with `images`:
 * one opaque id per image file it referenced, minted by the API for a file it checked. The
 * thumbnail's URL is `/api/media/<id>`, nothing else: never the path, never a token; the client
 * never sends a path. Pure, no DOM.
 */
import type { ImageRef } from './transcript.gen';

export type { ImageRef } from './transcript.gen';

/** An image a message shows, as the server stamped it. */
export interface ChatImage { id: string; path: string; name: string }

export const MEDIA_ID = /^[a-f0-9]{32}$/;

/** The URL an image is fetched from. Only an id the server could have minted makes one. */
export function mediaUrl(id: string, download = false): string | null {
  if (!MEDIA_ID.test(id)) return null;
  return `/api/media/${id}${download ? '?download' : ''}`;
}

// The same grammar as the API's detector (kept in step by imageRefs.test.ts), for a surface that
// wants to know whether a line names an image at all; ids never come from here.
const PATH_RE = /(?<![\w./~-])\/(?:[\w.~+%@-]+\/)*[\w.~+%@-]+\.(?:png|jpe?g|gif|webp|bmp|avif)(?!\.?[\w/~+%@-])/gi;
export const MAX_IMAGES_PER_ITEM = 8;

export function findImagePaths(text: string): string[] {
  if (!text || !text.includes('/')) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const m of text.matchAll(PATH_RE)) {
    const p = m[0];
    if (p.includes('/../') || p.includes('//') || p.startsWith('/./')) continue;
    if (seen.has(p)) continue;
    seen.add(p);
    out.push(p);
    if (out.length >= MAX_IMAGES_PER_ITEM) break;
  }
  return out;
}

export function imageName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}

/** The server's stamped refs as the message's images; nothing when there are none. */
export function stampedImages(refs: ImageRef[] | undefined | null): ChatImage[] | undefined {
  if (!Array.isArray(refs) || refs.length === 0) return undefined;
  const out = refs.filter((r) => r && MEDIA_ID.test(String(r.id)) && typeof r.path === 'string')
    .map((r) => ({ id: r.id, path: r.path, name: r.name || imageName(r.path) }));
  return out.length ? out : undefined;
}

/** The thumbnail's fixed box (CSS px): the same before, during and after load, so nothing shifts. */
export const THUMB = { width: 224, height: 160 } as const;
