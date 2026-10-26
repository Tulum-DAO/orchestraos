/**
 * arturoUpload.ts — attachments for the Arturo composer.
 *
 * The operator, 2026-09-18: "I still cannot upload documents to this arturo agent by
 * clicking the plus button. That's its whole point."
 *
 * The server half already existed and worked: POST /api/uploads (multer, 100MB cap,
 * extension allowlist, sanitised display name) writes into the data dir's uploads/ and
 * returns the absolute path. The + button had simply never been connected to it.
 *
 * The part that makes this real rather than decorative: the turn carries the file's
 * ABSOLUTE PATH. Arturo's brain runs on this machine with tool access, so a path is
 * something it can actually open — a filename alone would look like an upload had
 * happened and then do nothing, which is the failure shape this project keeps refusing.
 */
import { isAttachmentFiller } from './arturoComposer.ts';
export interface Attachment { name: string; path: string; size: number;
  /** A thumbnail for the composer while it is waiting to send (an object URL of the picked image). */
  preview?: string }
export type UploadResult =
  | { ok: true; name: string; path: string; size: number; url?: string }
  | { ok: false; error: string };

export async function uploadAttachment(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append('file', file);                  // the field name routes/uploads.ts expects
  try {
    const res = await fetch('/api/uploads', { method: 'POST', body: form });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: json.error || `upload failed (HTTP ${res.status})` };
    return {
      ok: true,
      name: json.originalname || json.filename || file.name,
      path: json.path,
      size: typeof json.size === 'number' ? json.size : file.size,
      url: json.url,
    };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : 'upload failed' };
  }
}

/** What rides in front of the operator's message so the brain can open the file. */
export function attachmentPreamble(files?: Attachment[] | null): string {
  if (!files || files.length === 0) return '';
  const lines = files.map((f) => `- ${f.name}  (${f.path})`);
  const plural = files.length === 1 ? 'a file' : `${files.length} files`;
  return `[The operator attached ${plural} on this machine. You can read ${files.length === 1 ? 'it' : 'them'} directly at the path shown:\n${lines.join('\n')}]`;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|heic|heif|avif)$/i;
export function isImageName(name: string): boolean { return IMAGE_EXT.test(name || ''); }

/** Where the browser can load an uploaded file: the API serves the uploads directory at /uploads/<name>
 *  (nosniff, active content forced to download). Only a path inside that directory, by its base name. */
export function uploadUrl(path: string): string | null {
  const m = /\/state\/uploads\/([^/\s]+)$/.exec(path || '');
  return m ? `/uploads/${encodeURIComponent(m[1])}` : null;
}

const PREAMBLE_RE = /^\[The operator attached (?:a file|\d+ files) on this machine\. You can read (?:it|them) directly at the path shown:\n((?:- [^\n]*\n?)+)\]\n*/;

/** A stored message as the operator sent it: the attachment note for the brain taken off the front, and the
 *  attached images (by their upload URLs) so the message can show them again after a reload. */
export function splitAttachmentPreamble(text: string): { text: string; images: string[] } {
  const m = PREAMBLE_RE.exec(text || '');
  if (!m) return { text: text || '', images: [] };
  const images: string[] = [];
  for (const line of m[1].split('\n')) {
    const path = /\(([^()]+)\)\s*$/.exec(line)?.[1] || '';
    const url = isImageName(path) ? uploadUrl(path) : null;
    if (url) images.push(url);
  }
  const rest = text.slice(m[0].length);
  // attachments sent with nothing typed: the filler request was Arturo's to read, not the operator's words
  return { text: isAttachmentFiller(rest) ? '' : rest, images };
}

/** The images among attachments, by URL (for the message just sent). */
export function attachmentImages(files: Attachment[]): string[] {
  return files.filter((f) => isImageName(f.name) || isImageName(f.path)).map((f) => uploadUrl(f.path)).filter((u): u is string => !!u);
}

export function describeAttachment(f: { name: string; size: number }): string {
  const kb = f.size / 1024;
  const size = kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(kb))} KB`;
  return `${f.name} · ${size}`;
}
