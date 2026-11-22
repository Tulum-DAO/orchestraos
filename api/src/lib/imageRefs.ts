/**
 * Which image and video files an agent REFERENCED: the one detector for the chat
 * (routes/chat-transcript.ts stamps `images` onto items with it; a video rides in the same list
 * with `kind: 'video'`).
 *
 * Only what the agent itself wrote counts: an absolute path with a media extension in its own
 * reply, or the target of a tool call that WRITES a file (Write, write_file, ...). Nothing is read
 * from the filesystem here and nothing is guessed from a tool's OUTPUT: a Bash result that lists a
 * directory of screenshots is not the agent showing them. Pure, no DOM, no fs.
 *
 * SVG is deliberately not an image here: it is active content (scripts, external loads) and the
 * media route refuses it by magic bytes anyway (lib/mediaSniff.ts). The extension only decides
 * what is LOOKED at: whether a file is served, and as an image or a video, is its bytes.
 */

export const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|avif)$/i;
export const VIDEO_EXT = /\.(mp4|m4v|mov|webm)$/i;
/** Any extension the detector picks up. */
export const MEDIA_EXT = /\.(png|jpe?g|gif|webp|bmp|avif|mp4|m4v|mov|webm)$/i;

/** How many images one item may carry: a reply that lists a whole folder is not a gallery. */
export const MAX_IMAGES_PER_ITEM = 8;

export interface ImageRef {
  /** Opaque id the media route serves it under (lib/mediaStore.ts). */
  id: string;
  /** The path as the agent wrote it (shown as text in Dev mode; never part of a URL). */
  path: string;
  /** The file's basename, for the thumbnail's label and the download name. */
  name: string;
  /** 'video' for a video (by its bytes); absent for an image. */
  kind?: 'video';
}

// An absolute POSIX path: starts at `/`, runs until something that cannot be in a path the agent
// would write inline (whitespace, quotes, backticks, brackets, angle brackets, a trailing
// sentence mark). Characters inside are the usual filename set.
const PATH_RE = /(?<![\w./~-])\/(?:[\w.~+%@-]+\/)*[\w.~+%@-]+\.(?:png|jpe?g|gif|webp|bmp|avif|mp4|m4v|mov|webm)(?!\.?[\w/~+%@-])/gi;

/** Absolute image and video paths in `text`, in order, de-duplicated, at most MAX_IMAGES_PER_ITEM. */
export function findImagePaths(text: string): string[] {
  if (!text || !text.includes('/')) return [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const m of text.matchAll(PATH_RE)) {
    const p = m[0];
    // No traversal and no doubled separators: those are not paths an image lives at, and the
    // media route would refuse them (containment) anyway. Dropped here so no id is minted for them.
    if (p.includes('/../') || p.includes('//') || p.startsWith('/./')) continue;
    if (seen.has(p)) continue;
    seen.add(p);
    out.push(p);
    if (out.length >= MAX_IMAGES_PER_ITEM) break;
  }
  return out;
}

// Tools that create or overwrite a file at a named path (Claude Code, Codex, Gemini CLI, Antigravity
// names; lower-cased). Edits of an existing image are not "writing an image".
const WRITE_TOOLS = new Set(['write', 'write_file', 'create_file', 'write_to_file']);
const PATH_KEYS = ['file_path', 'path', 'TargetFile', 'AbsolutePath', 'target_file', 'filename'];

/** The image or video a WRITE tool call targets, as a list (empty for any other call or a non-image). */
export function imagePathsInToolUse(tool: string, input: Record<string, unknown> | null | undefined): string[] {
  if (!WRITE_TOOLS.has(String(tool || '').toLowerCase()) || !input) return [];
  for (const k of PATH_KEYS) {
    const v = input[k];
    if (typeof v === 'string' && v.startsWith('/') && MEDIA_EXT.test(v)) return findImagePaths(v);
  }
  return [];
}

/** The basename an image is labelled with (never a path). */
export function imageName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}
