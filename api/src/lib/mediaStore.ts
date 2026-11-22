/**
 * Images in the chat (routes/media.ts): the opaque-id table, the per-agent allow-list, and the one
 * open() the bytes are read through.
 *
 * WHY AN ID TABLE. An image URL is pasted, bookmarked, logged by proxies and read over shoulders.
 * It must carry no credential and no path: `/api/media/<id>` names a row here, and the row names
 * the file. An id is 16 random bytes, minted ONLY when the transcript stamps a reference whose
 * file passed every check below at that moment (lib/mediaStore imageRefsFor): never for a path a
 * client supplied, never for a file that does not exist. The table is bounded (MAX_ROWS_PER_AGENT,
 * oldest first) and written to <data dir>/state/media-ids.json a moment AFTER a change, off the
 * request, so a chat poll never waits on the disk. Knowing an id grants nothing by itself: the
 * route re-checks the caller's agent scope AND the allow-list on every request.
 *
 * THE ALLOW-LIST, PER AGENT. A path is served only under one of the AGENT'S roots: its working
 * folder (state/agents/<id>.json cwd) and its own output folders under the data dir
 * (state/shots/<id>, state/agents/<id>/). Never the data dir itself: state/uploads holds every
 * client's uploads, and an agent naming one of them must not hand it to another client's viewer.
 * Resolution is by realpath, and a path with ANY symlink in it below the root is refused (a link
 * inside the project dir pointing at ~/.ssh is the attack). `..` never survives resolve(), and the
 * containment check is on the resolved form, so traversal is refused the same way. A hard link is
 * refused too (nlink > 1). Everything else is "not found": the response never says whether the
 * file exists.
 *
 * ONE OPEN. The checks by path would be a TOCTOU if the bytes were then read by path again
 * (whoever writes in the project dir could swap the file for a link in between). So the file is
 * opened once with O_NOFOLLOW, the open descriptor is checked (its real path, fstat: a regular
 * file, one link, under the cap), the type is sniffed from it, and the route streams from that
 * same descriptor. There is no second lookup.
 *
 * ONLY IMAGES, BY CONTENT. PNG, JPEG, GIF, WebP, BMP and AVIF by magic bytes; SVG is refused
 * (active content).
 */
import { randomBytes } from 'node:crypto';
import { closeSync, constants, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readSync, readlinkSync, realpathSync, renameSync, writeFile, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import type { ImageRef } from './imageRefs.js';
import { imageName } from './imageRefs.js';

export const MEDIA_ID = /^[a-f0-9]{32}$/;

/** The largest image served: a screenshot is a few MB; anything bigger is not a chat thumbnail. */
export const MAX_IMAGE_BYTES = 25 * 1024 * 1024;

/** Rows kept per agent: a chat window never shows more; the oldest go first. */
export const MAX_ROWS_PER_AGENT = 500;

/** How long after a change the table is written (ms). One write for a burst of mints. */
const SAVE_DELAY_MS = 250;

export interface MediaRow { agent: string; path: string; at: string }

/** The (agent, path) -> id table, persisted as one JSON file, bounded per agent. */
export class MediaStore {
  private rows = new Map<string, MediaRow>();          // insertion order = age
  private byKey = new Map<string, string>();
  private perAgent = new Map<string, number>();
  private loaded = false;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly file: string, private readonly cap = MAX_ROWS_PER_AGENT) {}

  private load(): void {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const raw = JSON.parse(readFileSync(this.file, 'utf-8')) as Record<string, MediaRow>;
      for (const [id, row] of Object.entries(raw)) {
        if (!MEDIA_ID.test(id) || !row || typeof row.path !== 'string' || typeof row.agent !== 'string') continue;
        this.insert(id, row);
      }
    } catch { /* no table yet, or unreadable: start empty; ids re-mint */ }
  }

  private insert(id: string, row: MediaRow): void {
    this.rows.set(id, row);
    this.byKey.set(`${row.agent}\0${row.path}`, id);
    this.perAgent.set(row.agent, (this.perAgent.get(row.agent) ?? 0) + 1);
    // Over the cap: the agent's oldest row goes.
    while ((this.perAgent.get(row.agent) ?? 0) > this.cap) {
      for (const [oldId, old] of this.rows) {
        if (old.agent !== row.agent) continue;
        this.rows.delete(oldId);
        this.byKey.delete(`${old.agent}\0${old.path}`);
        this.perAgent.set(row.agent, (this.perAgent.get(row.agent) ?? 1) - 1);
        break;
      }
    }
  }

  private serialize(): string { return JSON.stringify(Object.fromEntries(this.rows)); }

  /** Write the table now (tests, shutdown). Best effort. */
  flush(): void {
    if (this.saveTimer) { clearTimeout(this.saveTimer); this.saveTimer = null; }
    try {
      mkdirSync(dirname(this.file), { recursive: true });
      const tmp = `${this.file}.${process.pid}.tmp`;
      writeFileSync(tmp, this.serialize(), { mode: 0o600 });
      renameSync(tmp, this.file);
    } catch { /* the in-memory table still serves this process */ }
  }

  private scheduleSave(): void {
    if (this.saveTimer) return;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      try { mkdirSync(dirname(this.file), { recursive: true }); } catch { /* below */ }
      const tmp = `${this.file}.${process.pid}.tmp`;
      writeFile(tmp, this.serialize(), { mode: 0o600 }, (err) => {
        if (err) return;
        try { renameSync(tmp, this.file); } catch { /* best effort */ }
      });
    }, SAVE_DELAY_MS);
    this.saveTimer.unref?.();
  }

  /** The id already minted for this agent's reference to `path`, or null. No fs, no mint. */
  has(agent: string, path: string): string | null {
    this.load();
    return this.byKey.get(`${agent}\0${path}`) ?? null;
  }

  /** The id for this agent's reference to `path`: the existing one, or a fresh random one. Callers
   *  mint only after the file passed the allow-list (imageRefsFor). */
  idFor(agent: string, path: string): string {
    const have = this.has(agent, path);
    if (have) return have;
    let id = randomBytes(16).toString('hex');
    while (this.rows.has(id)) id = randomBytes(16).toString('hex');
    this.insert(id, { agent, path, at: new Date().toISOString() });
    this.scheduleSave();
    return id;
  }

  /** The row behind an id, or null. A malformed id is null without touching the table. */
  lookup(id: string): MediaRow | null {
    if (!MEDIA_ID.test(id)) return null;
    this.load();
    return this.rows.get(id) ?? null;
  }

  /** Rows held for one agent (tests). */
  countFor(agent: string): number { this.load(); return this.perAgent.get(agent) ?? 0; }
}

let defaultStore: MediaStore | null = null;
/** The install's table, under <data dir>/state. */
export function mediaStore(dataDir: string): MediaStore {
  const file = join(dataDir, 'state', 'media-ids.json');
  if (!defaultStore || (defaultStore as unknown as { file: string }).file !== file) defaultStore = new MediaStore(file);
  return defaultStore;
}

// ---- the allow-list -------------------------------------------------------------------------

/** Is `path` (as written) under `root`, by string alone: no fs. */
export function underRoot(path: string, root: string): boolean {
  if (!path.startsWith('/')) return false;
  const r = resolve(root);
  const p = resolve(path);
  return p !== r && p.startsWith(r.endsWith(sep) ? r : r + sep);
}

export type Refusal = 'not-absolute' | 'outside-roots' | 'symlink' | 'hardlink' | 'not-a-file' | 'too-big' | 'not-an-image';

export type Opened = { ok: true; fd: number; file: string; size: number; mime: string } | { ok: false; why: Refusal };
export type Resolved = { ok: true; file: string; size: number; mime: string } | { ok: false; why: Refusal };

const O_FLAGS = constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK;

/**
 * Open the file an image reference may be served from, or say why not. `roots` are the agent's
 * allowed directories (each may itself be reached through a symlink, e.g. a mounted home: the
 * root is realpath'd first). Below the root nothing may be a symlink or a hard link. On success
 * the caller OWNS the descriptor (stream from it, then close it).
 */
export function openImage(path: string, roots: readonly string[], maxBytes = MAX_IMAGE_BYTES): Opened {
  if (typeof path !== 'string' || !path.startsWith('/')) return { ok: false, why: 'not-absolute' };
  const wanted = resolve(path);   // collapses `..` and `.`; the containment check is on this form
  for (const root of roots) {
    if (!root || !underRoot(wanted, root)) continue;
    let realRoot: string;
    try { realRoot = realpathSync(root); } catch { continue; }
    const expected = join(realRoot, relative(resolve(root), wanted));
    // By path first: a symlink anywhere below the root changes the real path.
    let real: string;
    try { real = realpathSync(wanted); } catch { return { ok: false, why: 'not-a-file' }; }
    if (real !== expected) return { ok: false, why: 'symlink' };
    // Then ONE open, following no link at the last step; everything after this is on the descriptor.
    let fd: number;
    try { fd = openSync(wanted, O_FLAGS); } catch (e: any) {
      return { ok: false, why: e?.code === 'ELOOP' ? 'symlink' : 'not-a-file' };
    }
    try {
      // The descriptor's own path (Linux): refuses a directory component swapped for a link between the
      // realpath above and the open. Where /proc is absent the realpath check stands alone.
      try {
        const opened = readlinkSync(`/proc/self/fd/${fd}`);
        if (opened !== expected) { closeSync(fd); return { ok: false, why: 'symlink' }; }
      } catch (e: any) { if (e?.code !== 'ENOENT') { closeSync(fd); return { ok: false, why: 'not-a-file' }; } }
      const st = fstatSync(fd);
      if (!st.isFile()) { closeSync(fd); return { ok: false, why: 'not-a-file' }; }
      if (st.nlink > 1) { closeSync(fd); return { ok: false, why: 'hardlink' }; }
      if (st.size > maxBytes) { closeSync(fd); return { ok: false, why: 'too-big' }; }
      const head = Buffer.alloc(16);
      const got = readSync(fd, head, 0, 16, 0);
      const mime = sniffImage(head.subarray(0, got));
      if (!mime) { closeSync(fd); return { ok: false, why: 'not-an-image' }; }
      return { ok: true, fd, file: wanted, size: st.size, mime };
    } catch {
      try { closeSync(fd); } catch { /* already closed */ }
      return { ok: false, why: 'not-a-file' };
    }
  }
  return { ok: false, why: 'outside-roots' };
}

/** The same checks as openImage, for a caller that only wants the verdict (stamping). */
export function resolveImageFile(path: string, roots: readonly string[], maxBytes = MAX_IMAGE_BYTES): Resolved {
  const r = openImage(path, roots, maxBytes);
  if (!r.ok) return r;
  try { closeSync(r.fd); } catch { /* fine */ }
  return { ok: true, file: r.file, size: r.size, mime: r.mime };
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

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

/**
 * The directories ONE agent's images may come from: its working folder (state/agents/<id>.json
 * cwd, else state/agent-sessions.json) and its own output folders under the data dir,
 * state/shots/<id> and state/agents/<id>/. Never the data dir itself (state/uploads is every
 * client's). A path-shaped id builds no path; an unknown agent has only the data-dir folders
 * named for it (which do not exist, so nothing is served).
 */
export function agentMediaRoots(dataDir: string, agentId: string): string[] {
  if (!SAFE_ID.test(agentId)) return [];
  const d = resolve(dataDir);
  const roots = [join(d, 'state', 'shots', agentId), join(d, 'state', 'agents', agentId)];
  const cwd = agentCwd(dataDir, agentId);
  if (cwd && cwd.startsWith('/') && cwd !== '/') roots.unshift(resolve(cwd));
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

// ---- stamping -------------------------------------------------------------------------------

export interface RefsDeps { store: MediaStore; roots: (agentId: string) => string[]; now?: () => number }

/** A path that failed the checks is not looked at again for this long (ms): a reply naming a file
 *  that is not there yet is re-checked on a later poll, not on every poll. */
const MISS_TTL_MS = 5_000;
const misses = new Map<string, number>();

/**
 * The refs to stamp for the paths an agent wrote: an id for each path that is under the agent's
 * roots AND passes every check right now (exists, a regular file, no links, an image by its bytes,
 * under the cap). An id already minted is reused without touching the disk. Nothing else gets one.
 */
export function imageRefsFor(agentId: string, paths: readonly string[], deps: RefsDeps): ImageRef[] {
  const out: ImageRef[] = [];
  if (!paths.length) return out;
  const roots = deps.roots(agentId);
  const now = deps.now ? deps.now() : Date.now();
  for (const p of paths) {
    const have = deps.store.has(agentId, p);
    if (have) { out.push({ id: have, path: p, name: imageName(p) }); continue; }
    if (!roots.some((r) => underRoot(p, r))) continue;
    const key = `${agentId}\0${p}`;
    const until = misses.get(key);
    if (until !== undefined && until > now) continue;
    if (!resolveImageFile(p, roots).ok) { misses.set(key, now + MISS_TTL_MS); continue; }
    misses.delete(key);
    out.push({ id: deps.store.idFor(agentId, p), path: p, name: imageName(p) });
  }
  return out;
}

/** Tests only: forget the miss cache. */
export function _resetMisses(): void { misses.clear(); }

/** The install's stamper: its table and each agent's own roots. */
export function defaultImageRefsFor(dataDir: string): (agentId: string, paths: readonly string[]) => ImageRef[] {
  return (agentId, paths) => imageRefsFor(agentId, paths, { store: mediaStore(dataDir), roots: (a) => agentMediaRoots(dataDir, a) });
}
