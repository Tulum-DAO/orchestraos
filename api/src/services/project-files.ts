/**
 * A project's files, for the Projects page's "View files" (a user report: "we should be able to see
 * all of the files within the project that aren't .JSONL files").
 *
 * The folder is the project's own (<data dir>/projects/<slug>, or the card's `folder`). Everything is read
 * from inside it: a path that resolves outside it (`..`, an absolute path, a symlink out) is refused.
 * Transcript logs (*.jsonl) are left out, and so are tool directories nobody browses (.git, node_modules...).
 */
import { lstatSync, readdirSync, readFileSync, realpathSync, statSync } from 'fs';
import path from 'path';

export const SKIP_DIRS = new Set(['.git', 'node_modules', '.venv', 'venv', '__pycache__', '.next', 'dist', '.cache']);
export const MAX_FILES = 2000;
export const MAX_TEXT_BYTES = 512 * 1024;
const IMAGE_TYPES: Record<string, string> = {
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
  '.svg': 'image/svg+xml', '.bmp': 'image/bmp', '.avif': 'image/avif',
};

export interface ProjectFile { path: string; size: number; mtime: number }

// The project layout (orchestra_cli/projects.py LAYOUT + TOP_LEVEL_FILES: keep the two in step). A file outside
// it is "unfiled": the files page flags it so the PM tidies it.
export const LAYOUT_DIRS = new Set(['deliverables', 'site', 'research', 'assets', 'drafts']);
export const TOP_LEVEL_FILES = new Set(['PROJECT.md', 'README.md']);
export function unfiled(paths: string[]): string[] {
  return paths.filter((p) => (p.includes('/') ? !LAYOUT_DIRS.has(p.split('/')[0]) : !TOP_LEVEL_FILES.has(p)));
}

export function isListed(name: string): boolean {
  return !name.toLowerCase().endsWith('.jsonl');
}

/** Every file under `folder` (relative paths, sorted), without .jsonl files or skipped directories. */
export function listProjectFiles(folder: string): { files: ProjectFile[]; truncated: boolean } {
  const files: ProjectFile[] = [];
  let truncated = false;
  const walk = (dir: string, rel: string) => {
    let entries: import('fs').Dirent[];
    try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return; }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const e of entries) {
      if (files.length >= MAX_FILES) { truncated = true; return; }
      const abs = path.join(dir, e.name);
      const r = rel ? `${rel}/${e.name}` : e.name;
      if (e.isDirectory()) {
        if (!SKIP_DIRS.has(e.name)) walk(abs, r);
      } else if (e.isFile() && isListed(e.name)) {
        try { const st = statSync(abs); files.push({ path: r, size: st.size, mtime: st.mtimeMs }); } catch { /* gone */ }
      }
      // symlinks are not followed: a link out of the folder is not the project's file
    }
  };
  walk(folder, '');
  return { files, truncated };
}

/** The absolute path of `rel` inside `folder`, or null when it is not a listed file inside it. */
export function resolveInside(folder: string, rel: string): string | null {
  if (!rel || path.isAbsolute(rel) || rel.split(/[\\/]/).some((p) => p === '..')) return null;
  if (!isListed(rel) || rel.split('/').some((p) => SKIP_DIRS.has(p))) return null;
  try {
    const root = realpathSync(folder);
    const abs = path.join(root, rel);
    if (lstatSync(abs).isSymbolicLink()) return null;
    const real = realpathSync(abs);
    if (real !== root && !real.startsWith(root + path.sep)) return null;
    return statSync(real).isFile() ? real : null;
  } catch {
    return null;
  }
}

export type FileContent =
  | { kind: 'text'; text: string; truncated: boolean; size: number }
  | { kind: 'image'; mime: string; size: number }
  | { kind: 'pdf'; mime: string; size: number }
  | { kind: 'binary'; size: number };

export function imageType(rel: string): string | null {
  return IMAGE_TYPES[path.extname(rel).toLowerCase()] ?? null;
}

/** What the viewer shows for a file: its text (capped), or that it is an image / a binary. */
export function readProjectFile(abs: string, rel: string): FileContent {
  const size = statSync(abs).size;
  const mime = imageType(rel);
  if (mime) return { kind: 'image', mime, size };
  if (path.extname(rel).toLowerCase() === '.pdf') return { kind: 'pdf', mime: 'application/pdf', size };
  const buf = readFileSync(abs).subarray(0, MAX_TEXT_BYTES);
  if (buf.includes(0)) return { kind: 'binary', size };
  return { kind: 'text', text: buf.toString('utf8'), truncated: size > MAX_TEXT_BYTES, size };
}
