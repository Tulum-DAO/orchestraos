/**
 * Images in the chat (the client half of api/src/lib/imageRefs.ts + routes/media.ts).
 *
 * An agent's item arrives with `images`: one opaque id per image file it referenced, minted by the
 * API. The thumbnail's URL is `/api/media/<id>`, nothing else: never the path, never a token. For a
 * surface whose messages the API does not build (Arturo's threads come from the gateway) the same
 * detector runs here and the ids are asked for one by one (lib/mediaResolve.ts). Pure, no DOM.
 */
import type { ImageRef } from './transcript.gen';

export type { ImageRef } from './transcript.gen';

/** An image a message shows: stamped by the server (`id` set) or found here and not yet resolved (`id` empty). */
export interface ChatImage { id: string; path: string; name: string }

export const MEDIA_ID = /^[a-f0-9]{32}$/;

/** The URL an image is fetched from. Only an id the server could have minted makes one. */
export function mediaUrl(id: string, download = false): string | null {
  if (!MEDIA_ID.test(id)) return null;
  return `/api/media/${id}${download ? '?download' : ''}`;
}

// The same grammar as the API's detector: an absolute POSIX path ending in an image extension,
// ending where a sentence would.
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

/** Images found in a reply the server did not stamp (Arturo): no id yet. */
export function unresolvedImages(text: string): ChatImage[] {
  return findImagePaths(text).map((path) => ({ id: '', path, name: imageName(path) }));
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
