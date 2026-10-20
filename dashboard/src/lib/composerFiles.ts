/**
 * composerFiles.ts — the chat composer's attachment list (ChatInput.tsx).
 *
 * The web chat box held ONE file: a second pick, drop or paste silently replaced the first,
 * while iOS, iPad, macOS and Quest all attach several at once. These are the pure rules for
 * holding a LIST, kept out of the component so they can be tested without a DOM.
 */

/** What the file picker offers. Video was missing even though POST /api/uploads already
 *  allows mp4/mov/m4v/webm (routes/uploads.ts ALLOWED_EXTS); keep the two lists in step. */
export const COMPOSER_ACCEPT = [
  '.png', '.jpg', '.jpeg', '.gif', '.webp',
  '.pdf', '.csv', '.txt', '.md', '.json', '.html', '.htm', '.vtt', '.srt',
  '.docx', '.xlsx', '.pptx', '.zip',
  '.m4a', '.mp3', '.wav',
  '.mp4', '.mov', '.m4v', '.webm',
  'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/pdf', 'text/csv',
  'application/json', 'text/html', 'audio/mpeg', 'audio/mp4',
  'video/mp4', 'video/quicktime', 'video/webm',
].join(',');

/** The fields a File has that identify it well enough to spot the same file picked twice. */
export interface FileLike { name: string; size: number; lastModified: number; type: string }

const fileKey = (f: FileLike) => `${f.name}\u0000${f.size}\u0000${f.lastModified}`;

/**
 * The incoming files that are not already attached, in the order given. Picking or dropping
 * the same file twice would otherwise upload it twice and mention it twice to the agent.
 * Duplicates inside one drop collapse too.
 */
export function newFiles<T extends FileLike>(attached: readonly T[], incoming: ArrayLike<T> | null | undefined): T[] {
  if (!incoming) return [];
  const seen = new Set(attached.map(fileKey));
  const out: T[] = [];
  for (const f of Array.from(incoming)) {
    const k = fileKey(f);
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(f);
  }
  return out;
}

/**
 * Should a paste attach the clipboard's files? Only when it carries files and NO text: a copied
 * screenshot or Finder file arrives that way, while copying from a document or spreadsheet often
 * brings a rendered image ALONGSIDE the text, and that paste is meant as text.
 */
export function pasteAttachesFiles(fileCount: number, clipText: string): boolean {
  return fileCount > 0 && !clipText;
}

/** The leading markers the inject path puts in front of the message, one per uploaded file,
 *  in the order attached. composerGate.retryText already carries a run of these on a retry. */
export function attachmentMarkers(uploaded: readonly { path: string; type: string }[], text: string): string {
  const markers = uploaded.map((u) => `[${u.type.startsWith('image/') ? 'IMAGE' : 'FILE'}: ${u.path}]`).join(' ');
  if (!markers) return text;
  return text ? `${markers} ${text}` : markers;
}
