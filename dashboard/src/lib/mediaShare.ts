/**
 * Share for an image or a video in the chat's full-screen view (components/kit/ChatImages.tsx).
 *
 * The media URL (/api/media/<id>) only works for someone signed in to THIS install with a scope
 * that covers the agent: sharing or copying the link would hand the other person a 404 (or, worse,
 * teach people to paste authenticated URLs around). So Share sends the FILE: the bytes are fetched
 * from the authenticated route (the browser's own session, nothing in the URL) and handed to the
 * Web Share API as a File, when the browser says it can share files. Where it cannot (most
 * desktop browsers), Share downloads the file instead and the button's tooltip says so. It never
 * copies the link and never puts a credential in a URL. Pure logic with injected browser seams,
 * so the decision is tested without a DOM.
 */

export interface ShareNav {
  share?: (data: ShareData) => Promise<void>;
  canShare?: (data?: ShareData) => boolean;
}

export type MakeFile = (parts: BlobPart[], name: string, type: string) => File;
const defaultMakeFile: MakeFile = (parts, name, type) => new File(parts, name, { type });

/** The tooltip when the browser can share files, and when it cannot (Share downloads instead). */
export const SHARE_TITLE = 'Share';
export const SHARE_FALLBACK_TITLE = "This browser can't share files, so Share downloads it instead (the link needs your sign-in, so it isn't copied)";

/** Can this browser share a FILE of this type (not a URL)? Asked with an empty file of the same type. */
export function canShareFiles(nav: ShareNav | undefined, type = 'image/png', makeFile: MakeFile = defaultMakeFile): boolean {
  if (!nav || typeof nav.share !== 'function' || typeof nav.canShare !== 'function') return false;
  try { return nav.canShare({ files: [makeFile([], 'probe', type)] }) === true; } catch { return false; }
}

export type ShareOutcome = 'shared' | 'cancelled' | 'downloaded';

export interface ShareDeps {
  nav: ShareNav | undefined;
  /** The bytes, from the authenticated media route (same origin, the session's cookie). */
  fetchBlob: (url: string) => Promise<Blob>;
  /** Save the file the ordinary way (the route's ?download answer). */
  download: () => void;
  makeFile?: MakeFile;
  /** Bytes fetched ahead of the tap (an image the overlay already holds), when there are any. */
  ready?: Blob | null;
}

/**
 * Share one media file: as a File through the Web Share API when the browser can share it, else a
 * download. A person closing the share sheet is not a failure (nothing else happens); any other
 * refusal (the browser lost the tap, the file is too big for it) falls back to the download.
 */
export async function shareMedia(url: string, name: string, type: string, deps: ShareDeps): Promise<ShareOutcome> {
  const makeFile = deps.makeFile ?? defaultMakeFile;
  if (!canShareFiles(deps.nav, type, makeFile)) { deps.download(); return 'downloaded'; }
  let blob: Blob;
  try { blob = deps.ready ?? await deps.fetchBlob(url); } catch { deps.download(); return 'downloaded'; }
  const file = makeFile([blob], name, blob.type || type);
  const data: ShareData = { files: [file], title: name };
  try {
    if (!deps.nav!.canShare!(data)) { deps.download(); return 'downloaded'; }
    await deps.nav!.share!(data);
    return 'shared';
  } catch (e) {
    if ((e as { name?: string } | null)?.name === 'AbortError') return 'cancelled';
    deps.download();
    return 'downloaded';
  }
}

/** The media type a ref is shared as, from its name (the server's sniff decided what it IS; this only labels the File). */
export function mediaTypeFor(name: string, kind?: 'video'): string {
  const ext = name.slice(name.lastIndexOf('.') + 1).toLowerCase();
  const map: Record<string, string> = {
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', bmp: 'image/bmp', avif: 'image/avif',
    mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm',
  };
  return map[ext] ?? (kind === 'video' ? 'video/mp4' : 'image/png');
}
