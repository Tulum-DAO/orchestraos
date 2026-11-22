/**
 * Media in the chat: what a file IS, by its first bytes (never by its name). The one rule for the
 * media route (lib/mediaStore.ts openMedia) and for a copy pulled from a device (lib/devicePull.ts).
 *
 * Images: PNG, JPEG, GIF, WebP, BMP, AVIF. SVG is refused (active content).
 * Video: MP4 / M4V and QuickTime (an ISO-BMFF `ftyp` box with a known MAJOR brand), and WebM (an
 * EBML header whose DocType is `webm`). Matroska that is not WebM, HEIC, audio-only MP4 (M4A) and
 * every other `ftyp` brand are refused: a browser's <video> may not play them, and a type the
 * route does not know is a type it does not serve. Pure, no fs.
 */

/** Bytes read from the start of a file to decide its type. */
export const SNIFF_BYTES = 64;

/** The largest image served: a screenshot is a few MB; anything bigger is not a chat thumbnail. */
export const MAX_IMAGE_BYTES = 25 * 1024 * 1024;

/** The largest video served: a screen recording of a few minutes at 1080p. A longer film is not a
 *  chat attachment, and a pull from a device (lib/devicePull.ts) holds no more than this either. */
export const MAX_VIDEO_BYTES = 256 * 1024 * 1024;

export type MediaKind = 'image' | 'video';

// ISO-BMFF major brands served as video. `qt  ` is QuickTime; the rest are the MP4 family.
const MP4_BRANDS = new Set(['isom', 'iso2', 'iso3', 'iso4', 'iso5', 'iso6', 'iso7', 'iso8', 'iso9', 'mp41', 'mp42', 'mp71', 'avc1', 'M4V ', 'M4VP', 'dash', 'MSNV', 'mmp4']);

/** The image type of a file by its first bytes, or null when it is not one of the served types. */
export function sniffImage(head: Buffer | Uint8Array): string | null {
  const b = Buffer.from(head);
  if (b.length >= 8 && b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 && b[4] === 0x0d && b[5] === 0x0a && b[6] === 0x1a && b[7] === 0x0a) return 'image/png';
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg';
  if (b.length >= 6 && (b.subarray(0, 6).toString('latin1') === 'GIF87a' || b.subarray(0, 6).toString('latin1') === 'GIF89a')) return 'image/gif';
  if (b.length >= 12 && b.subarray(0, 4).toString('latin1') === 'RIFF' && b.subarray(8, 12).toString('latin1') === 'WEBP') return 'image/webp';
  if (b.length >= 2 && b[0] === 0x42 && b[1] === 0x4d) return 'image/bmp';
  if (b.length >= 12 && b.subarray(4, 8).toString('latin1') === 'ftyp' && b.subarray(8, 12).toString('latin1') === 'avif') return 'image/avif';
  return null;
}

/** The video type of a file by its first bytes, or null. */
export function sniffVideo(head: Buffer | Uint8Array): string | null {
  const b = Buffer.from(head);
  if (b.length >= 12 && b.subarray(4, 8).toString('latin1') === 'ftyp') {
    const brand = b.subarray(8, 12).toString('latin1');
    if (brand === 'qt  ') return 'video/quicktime';
    if (MP4_BRANDS.has(brand)) return 'video/mp4';
    return null;
  }
  // EBML magic, then the DocType element (0x42 0x82) naming `webm` within the header we read.
  if (b.length >= 8 && b[0] === 0x1a && b[1] === 0x45 && b[2] === 0xdf && b[3] === 0xa3) {
    const at = b.indexOf(Buffer.from([0x42, 0x82]), 4);
    if (at < 0) return null;
    // Then the size (a vint, 1 to 8 bytes; 0x84 in practice) and the DocType itself.
    const k = b.subarray(at + 2, at + 2 + 12).toString('latin1').indexOf('webm');
    return k >= 1 && k <= 8 ? 'video/webm' : null;
  }
  return null;
}

/** An image or a video the chat serves, by its first bytes, or null. */
export function sniffMedia(head: Buffer | Uint8Array): { mime: string; kind: MediaKind } | null {
  const img = sniffImage(head);
  if (img) return { mime: img, kind: 'image' };
  const vid = sniffVideo(head);
  if (vid) return { mime: vid, kind: 'video' };
  return null;
}

/** The size cap for a kind. */
export function maxBytesFor(kind: MediaKind): number {
  return kind === 'video' ? MAX_VIDEO_BYTES : MAX_IMAGE_BYTES;
}
