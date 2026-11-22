/**
 * Images in the chat (routes/media.ts): the opaque-id table and the allow-list the file behind an
 * id must satisfy before a byte of it is served.
 *
 * WHY AN ID TABLE. An image URL is pasted, bookmarked, logged by proxies and read over shoulders.
 * It must carry no credential and no path: `/api/media/<id>` names a row here, and the row names
 * the file. The id is 16 random bytes, minted once per (agent, path) and kept in
 * <data dir>/state/media-ids.json so a reload of yesterday's chat still resolves. Knowing an id
 * grants nothing by itself: the route re-checks the caller's agent scope AND this allow-list on
 * every request.
 *
 * THE ALLOW-LIST. A path is served only when it sits under one of the agent's roots: the
 * install's data dir and the agent's own working folder. Resolution is by realpath, and a path
 * with ANY symlink in it below the root is refused (a link inside the project dir pointing at
 * ~/.ssh is the attack). `..` never survives resolve(), and the containment check is on the
 * resolved form, so traversal is refused the same way. Everything else is "not found": the
 * response never says whether the file exists.
 *
 * ONLY IMAGES, BY CONTENT. The type comes from the first bytes, never from the extension, and only
 * PNG, JPEG, GIF, WebP, BMP and AVIF pass. SVG is refused (active content). A file over the cap is
 * refused.
 */
import { randomBytes } from 'node:crypto';
import { closeSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, realpathSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';

export const MEDIA_ID = /^[a-f0-9]{32}$/;

/** The largest image served: a screenshot is a few MB; anything bigger is not a chat thumbnail. */
export const MAX_IMAGE_BYTES = 25 * 1024 * 1024;

export interface MediaRow { agent: string; path: string; at: string }

/** The (agent, path) -> id table, persisted as one JSON file. */
export class MediaStore {
  private rows = new Map<string, MediaRow>();
  private byKey = new Map<string, string>();
  private loaded = false;

  constructor(private readonly file: string) {}

  private load(): void {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const raw = JSON.parse(readFileSync(this.file, 'utf-8')) as Record<string, MediaRow>;
      for (const [id, row] of Object.entries(raw)) {
        if (!MEDIA_ID.test(id) || !row || typeof row.path !== 'string' || typeof row.agent !== 'string') continue;
        this.rows.set(id, row);
        this.byKey.set(`${row.agent}\0${row.path}`, id);
      }
    } catch { /* no table yet, or unreadable: start empty; ids re-mint */ }
  }

  private save(): void {
    try {
      mkdirSync(dirname(this.file), { recursive: true });
      const tmp = `${this.file}.${process.pid}.tmp`;
      writeFileSync(tmp, JSON.stringify(Object.fromEntries(this.rows)), { mode: 0o600 });
      renameSync(tmp, this.file);
    } catch { /* best effort: the in-memory table still serves this process */ }
  }

  /** The id for this agent's reference to `path`: the existing one, or a fresh random one. */
  idFor(agent: string, path: string): string {
    this.load();
    const key = `${agent}\0${path}`;
    const have = this.byKey.get(key);
    if (have) return have;
    let id = randomBytes(16).toString('hex');
    while (this.rows.has(id)) id = randomBytes(16).toString('hex');
    this.rows.set(id, { agent, path, at: new Date().toISOString() });
    this.byKey.set(key, id);
    this.save();
    return id;
  }

  /** The row behind an id, or null. A malformed id is null without touching the table. */
  lookup(id: string): MediaRow | null {
    if (!MEDIA_ID.test(id)) return null;
    this.load();
    return this.rows.get(id) ?? null;
  }
}

let defaultStore: MediaStore | null = null;
/** The install's table, under <data dir>/state. */
export function mediaStore(dataDir: string): MediaStore {
  const file = join(dataDir, 'state', 'media-ids.json');
  if (!defaultStore || (defaultStore as unknown as { file: string }).file !== file) defaultStore = new MediaStore(file);
  return defaultStore;
}

// ---- the allow-list -------------------------------------------------------------------------

/** Is `path` (as written) under `root`, by string alone: no fs. The stamp step uses it so no id is
 *  minted for a path the route could never serve. */
export function underRoot(path: string, root: string): boolean {
  if (!path.startsWith('/')) return false;
  const r = resolve(root);
  const p = resolve(path);
  return p !== r && p.startsWith(r.endsWith(sep) ? r : r + sep);
}

export type Refusal = 'not-absolute' | 'outside-roots' | 'symlink' | 'not-a-file' | 'too-big' | 'not-an-image';

export type Resolved = { ok: true; file: string; size: number; mime: string } | { ok: false; why: Refusal };

/**
 * The file an image reference may be served from, or why not. `roots` are the agent's allowed
 * directories (each may itself be reached through a symlink, e.g. a mounted home: the root is
 * realpath'd first). Below the root nothing may be a symlink.
 */
export function resolveImageFile(path: string, roots: readonly string[], maxBytes = MAX_IMAGE_BYTES): Resolved {
  if (typeof path !== 'string' || !path.startsWith('/')) return { ok: false, why: 'not-absolute' };
  const wanted = resolve(path);   // collapses `..` and `.`; the containment check is on this form
  for (const root of roots) {
    if (!root || !underRoot(wanted, root)) continue;
    let realRoot: string;
    try { realRoot = realpathSync(root); } catch { continue; }
    const rel = relative(resolve(root), wanted);
    const expected = join(realRoot, rel);
    let real: string;
    try { real = realpathSync(wanted); } catch { return { ok: false, why: 'not-a-file' }; }
    // A symlink anywhere below the root changes the real path; the file itself is checked too so a
    // link whose target happens to sit inside the root is still refused (links are not served).
    if (real !== expected) return { ok: false, why: 'symlink' };
    let st;
    try { st = lstatSync(wanted); } catch { return { ok: false, why: 'not-a-file' }; }
    if (st.isSymbolicLink()) return { ok: false, why: 'symlink' };
    if (!st.isFile()) return { ok: false, why: 'not-a-file' };
    if (st.size > maxBytes) return { ok: false, why: 'too-big' };
    const mime = sniffImage(readHead(wanted, 16));
    if (!mime) return { ok: false, why: 'not-an-image' };
    return { ok: true, file: wanted, size: st.size, mime };
  }
  return { ok: false, why: 'outside-roots' };
}

function readHead(file: string, n: number): Buffer {
  const fd = openSync(file, 'r');
  try {
    const buf = Buffer.alloc(n);
    const got = readSync(fd, buf, 0, n, 0);
    return buf.subarray(0, got);
  } finally { closeSync(fd); }
}

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

/**
 * The directories an agent's images may come from: the install's data dir and the agent's working
 * folder (state/agents/<id>.json cwd, else state/agent-sessions.json). Unknown agent: the data dir
 * alone. The agent id is not used to build a path unless it is a plain name.
 */
export function agentMediaRoots(dataDir: string, agentId: string): string[] {
  const roots = [resolve(dataDir)];
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(agentId)) return roots;
  const cwd = agentCwd(dataDir, agentId);
  if (cwd && cwd.startsWith('/') && cwd !== '/') roots.push(resolve(cwd));
  return roots;
}

function agentCwd(dataDir: string, agentId: string): string | null {
  const stateFile = join(dataDir, 'state', 'agents', `${agentId}.json`);
  try {
    if (existsSync(stateFile)) {
      const st = JSON.parse(readFileSync(stateFile, 'utf-8'));
      if (typeof st?.cwd === 'string' && st.cwd) return st.cwd;
    }
  } catch { /* fall through */ }
  try {
    const sessions = JSON.parse(readFileSync(join(dataDir, 'state', 'agent-sessions.json'), 'utf-8'));
    const s = sessions?.[agentId];
    if (typeof s?.cwd === 'string' && s.cwd) return s.cwd;
  } catch { /* none */ }
  return null;
}
