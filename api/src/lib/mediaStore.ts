/**
 * Images in the chat (routes/media.ts): the opaque-id table, the per-agent allow-list, and the one
 * open() the bytes are read through.
 *
 * WHY AN ID TABLE. An image URL is pasted, bookmarked, logged by proxies and read over shoulders.
 * It must carry no credential and no path: `/api/media/<id>` names a row here, and the row names
 * the file. An id is 16 random bytes, minted ONLY when the transcript stamps a reference whose
 * file passed every check below at that moment (lib/mediaStore imageRefsFor): never for a path a
 * client supplied, never for a file that does not exist. The table is bounded (MAX_ROWS_PER_AGENT
 * per agent, least recently used first) and written to <data dir>/state/media-ids.json a moment
 * AFTER a change, off the request, so a chat poll never waits on the disk. A row the window being
 * stamped still shows is never the one evicted: above the cap the oldest images of THAT window go
 * without a thumbnail (their path stays in the words), and every id already shown stays the same
 * on every poll (stampImages, stampWindow). Only a path that can be shown counts toward the cap
 * (one that is not there, or not under the agent's roots, takes no place). Each row remembers when
 * the transcript last referenced its path (the item's time, `seen`): at the cap the row referenced
 * longest ago goes, and a path referenced EARLIER than every row it could replace gets no row, so
 * paging back through an older window never takes the newest window's ids. Knowing an id grants
 * nothing by itself: the route re-checks the caller's agent scope AND the allow-list on every request.
 *
 * THE ALLOW-LIST, PER AGENT. A path is served only under one of the AGENT'S roots: its working
 * folder (state/agents/<id>.json cwd) and its own output folders under the data dir
 * (state/shots/<id>, state/agents/<id>/). Never the data dir itself: state/uploads holds every
 * client's uploads, and an agent naming one of them must not hand it to another client's viewer.
 * So a working folder that IS the data dir, or encloses it (a seat started in $HOME), is not a
 * root at all, and neither is one inside the data dir's state/ tree (state/uploads, state/shots,
 * state/agents hold every client's and every agent's files; a seat's own folders there are roots
 * by name, never through its cwd).
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
 * ONLY IMAGES AND VIDEO, BY CONTENT (lib/mediaSniff.ts). PNG, JPEG, GIF, WebP, BMP and AVIF;
 * MP4/M4V, QuickTime and WebM, by magic bytes; SVG is refused (active content). Images up to
 * MAX_IMAGE_BYTES (25 MiB), video up to MAX_VIDEO_BYTES (256 MiB).
 */
import { randomBytes } from 'node:crypto';
import { closeSync, constants, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readSync, readlinkSync, realpathSync, renameSync, unlinkSync, writeFile, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import type { ImageRef } from './imageRefs.js';
import { imageName } from './imageRefs.js';
import { SNIFF_BYTES, maxBytesFor, sniffMedia, type MediaKind } from './mediaSniff.js';

export { MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, sniffImage, sniffVideo, sniffMedia } from './mediaSniff.js';

export const MEDIA_ID = /^[a-f0-9]{32}$/;

/** Rows kept per agent; the least recently used go first, never one the stamped window shows. */
export const MAX_ROWS_PER_AGENT = 500;

/** How long after a change the table is written (ms). One write for a burst of mints. */
const SAVE_DELAY_MS = 250;

/** One id: the agent it was minted for and the file it serves. `kind` is set for a video (absent: an
 *  image). `seen` is the time (ms) of the newest transcript item that referenced the path, when the
 *  stamp knew it. */
export interface MediaRow { agent: string; path: string; at: string; kind?: 'video'; seen?: number }

export interface MintOpts {
  kind?: MediaKind;
  /** The paths of the window being stamped: none of their rows is evicted to make room. When every
   *  row of the agent is one of them, nothing is minted (null). */
  protect?: ReadonlySet<string>;
  /** When the transcript referenced the path (ms, the item's time): at the cap, the row referenced longest
   *  ago goes, and a path referenced before every row it could replace gets none (null). */
  refAt?: number;
}

/** Rename the written temp file into place; when the rename fails, the temp file goes too (never left beside the table). */
export function renameOrDrop(tmp: string, file: string): void {
  try { renameSync(tmp, file); } catch (e) { try { unlinkSync(tmp); } catch { /* never there */ } throw e; }
}

/** One row's place in its agent's eviction order: `seen` first (none is -Infinity), then the use it was made on. */
interface Slot { id: string; seen: number; seq: number }

const before = (a: Slot, b: Slot): boolean => a.seen < b.seen || (a.seen === b.seen && a.seq < b.seq);

function heapPush(h: Slot[], s: Slot): void {
  h.push(s);
  for (let i = h.length - 1; i > 0;) {
    const p = (i - 1) >> 1;
    if (!before(h[i], h[p])) break;
    [h[i], h[p]] = [h[p], h[i]];
    i = p;
  }
}

function heapPop(h: Slot[]): Slot {
  const top = h[0];
  const last = h.pop()!;
  if (h.length) {
    h[0] = last;
    for (let i = 0; ;) {
      const l = 2 * i + 1;
      const r = l + 1;
      let m = i;
      if (l < h.length && before(h[l], h[m])) m = l;
      if (r < h.length && before(h[r], h[m])) m = r;
      if (m === i) break;
      [h[i], h[m]] = [h[m], h[i]];
      i = m;
    }
  }
  return top;
}

function heapify(xs: Slot[]): Slot[] {
  const h: Slot[] = [];
  for (const s of xs) heapPush(h, s);
  return h;
}

/** The (agent, path) -> id table, persisted as one JSON file, bounded per agent. */
export class MediaStore {
  private rows = new Map<string, MediaRow>();          // insertion order = least recently used first
  private byKey = new Map<string, string>();
  private perAgent = new Map<string, number>();
  /** Per agent, its rows as a min-heap by (`seen`, use order): the top is the row to evict. A slot is
   *  live only while `live` still points at it (a use or a newer `seen` pushes a fresh slot; the old one
   *  is dropped when it surfaces), so a pick is O(log n) however many agents share the table. */
  private heaps = new Map<string, Slot[]>();
  private live = new Map<string, Slot>();
  private useSeq = 0;
  /** The window evict last found no room for, per agent: the same window asks again at no cost until a row
   *  of that agent comes or goes. */
  private noRoom = new Map<string, ReadonlySet<string>>();
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
        const clean: MediaRow = { agent: row.agent, path: row.path, at: String(row.at ?? '') };
        if (row.kind === 'video') clean.kind = 'video';
        if (typeof row.seen === 'number' && Number.isFinite(row.seen)) clean.seen = row.seen;
        if ((this.perAgent.get(clean.agent) ?? 0) >= this.cap && !this.evict(clean.agent)) continue;
        this.insert(id, clean);
      }
    } catch { /* no table yet, or unreadable: start empty; ids re-mint */ }
  }

  private insert(id: string, row: MediaRow): void {
    this.rows.set(id, row);
    this.byKey.set(`${row.agent}\0${row.path}`, id);
    this.perAgent.set(row.agent, (this.perAgent.get(row.agent) ?? 0) + 1);
    this.noRoom.delete(row.agent);
    this.slot(id, row);
  }

  /** A fresh heap slot for the row as it is now (its `seen`, this use): the one evict honours from here on. */
  private slot(id: string, row: MediaRow): void {
    const s: Slot = { id, seen: row.seen ?? -Infinity, seq: ++this.useSeq };
    this.live.set(id, s);
    let heap = this.heaps.get(row.agent);
    if (!heap) { heap = []; this.heaps.set(row.agent, heap); }
    heapPush(heap, s);
    // Stale slots pile up with every use; past twice the rows, keep only the live ones.
    if (heap.length > 2 * (this.perAgent.get(row.agent) ?? 0) + 32) this.heaps.set(row.agent, heapify(heap.filter((x) => this.live.get(x.id) === x)));
  }

  /** Drop the agent's row the window being stamped does not show that the transcript referenced longest
   *  ago (`seen`; a row with none counts as oldest; a tie goes to the least recently used). When `refAt`
   *  is given and that row was referenced AFTER it, nothing goes (the newer row stays). */
  private evict(agent: string, protect?: ReadonlySet<string>, refAt?: number): boolean {
    if (protect && this.noRoom.get(agent) === protect) return false;
    const heap = this.heaps.get(agent);
    if (!heap) return false;
    const held: Slot[] = [];
    let pick: Slot | null = null;
    while (heap.length) {
      const top = heap[0];
      if (this.live.get(top.id) !== top) { heapPop(heap); continue; }
      if (protect && protect.has(this.rows.get(top.id)!.path)) { held.push(heapPop(heap)); continue; }
      pick = top;
      break;
    }
    const go = pick !== null && !(refAt !== undefined && pick.seen > refAt);
    if (go) {
      heapPop(heap);
      const old = this.rows.get(pick!.id)!;
      this.live.delete(pick!.id);
      this.rows.delete(pick!.id);
      this.byKey.delete(`${old.agent}\0${old.path}`);
      this.perAgent.set(agent, (this.perAgent.get(agent) ?? 1) - 1);
      this.noRoom.delete(agent);
    } else if (protect && pick === null) {
      this.noRoom.set(agent, protect);
    }
    for (const s of held) heapPush(heap, s);
    return go;
  }

  /** A use: the row moves to the young end (LRU). In memory only; the next save keeps the order. */
  private touch(id: string): MediaRow | null {
    const row = this.rows.get(id);
    if (!row) return null;
    this.rows.delete(id);
    this.rows.set(id, row);
    this.slot(id, row);
    return row;
  }

  private serialize(): string { return JSON.stringify(Object.fromEntries(this.rows)); }

  /** Write the table now (tests, shutdown). Best effort. */
  flush(): void {
    if (this.saveTimer) { clearTimeout(this.saveTimer); this.saveTimer = null; }
    try {
      mkdirSync(dirname(this.file), { recursive: true });
      const tmp = `${this.file}.${process.pid}.tmp`;
      writeFileSync(tmp, this.serialize(), { mode: 0o600 });
      renameOrDrop(tmp, this.file);
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
        try { renameOrDrop(tmp, this.file); } catch { /* best effort */ }
      });
    }, SAVE_DELAY_MS);
    this.saveTimer.unref?.();
  }

  /** The id already minted for this agent's reference to `path`, or null. No fs, no mint. A hit is a use (LRU),
   *  and a later `refAt` moves its `seen` on (the path was referenced again, more recently). */
  has(agent: string, path: string, refAt?: number): string | null {
    this.load();
    const id = this.byKey.get(`${agent}\0${path}`) ?? null;
    if (id) {
      const row = this.touch(id);
      if (row && refAt !== undefined && !(row.seen !== undefined && row.seen >= refAt)) { row.seen = refAt; this.slot(id, row); this.scheduleSave(); }
    }
    return id;
  }

  /** The id for this agent's reference to `path`: the existing one, or a fresh random one (the agent's
   *  least recently used row goes when it is at the cap). Callers mint only after the file passed the
   *  allow-list (imageRefsFor). */
  idFor(agent: string, path: string, opts: Omit<MintOpts, 'protect'> = {}): string {
    return this.mint(agent, path, opts)!;
  }

  /** idFor, keeping every row `opts.protect` shows: null when the agent's rows are all in it (at the cap). */
  mint(agent: string, path: string, opts: MintOpts = {}): string | null {
    const have = this.has(agent, path, opts.refAt);
    if (have) return have;
    if ((this.perAgent.get(agent) ?? 0) >= this.cap && !this.evict(agent, opts.protect, opts.refAt)) return null;
    let id = randomBytes(16).toString('hex');
    while (this.rows.has(id)) id = randomBytes(16).toString('hex');
    const row: MediaRow = { agent, path, at: new Date().toISOString() };
    if (opts.kind === 'video') row.kind = 'video';
    if (opts.refAt !== undefined && Number.isFinite(opts.refAt)) row.seen = opts.refAt;
    this.insert(id, row);
    this.scheduleSave();
    return id;
  }

  /** The row behind an id, or null. A malformed id is null without touching the table. A hit is a use (LRU). */
  lookup(id: string): MediaRow | null {
    if (!MEDIA_ID.test(id)) return null;
    this.load();
    return this.touch(id);
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

export type Opened = { ok: true; fd: number; file: string; size: number; mime: string; kind: MediaKind } | { ok: false; why: Refusal };
export type Resolved = { ok: true; file: string; size: number; mime: string; kind: MediaKind } | { ok: false; why: Refusal };

const O_FLAGS = constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK;

/**
 * Open the file a media reference may be served from, or say why not. `roots` are the agent's
 * allowed directories (each may itself be reached through a symlink, e.g. a mounted home: the
 * root is realpath'd first). Below the root nothing may be a symlink or a hard link. The type is
 * the file's bytes (lib/mediaSniff.ts); the size cap is that kind's (MAX_IMAGE_BYTES for an image,
 * MAX_VIDEO_BYTES for a video) unless `maxBytes` overrides it for every kind. On success the
 * caller OWNS the descriptor (stream from it, then close it). A non-media file is 'not-an-image'.
 */
export function openMedia(path: string, roots: readonly string[], maxBytes?: number): Opened {
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
      const head = Buffer.alloc(SNIFF_BYTES);
      const got = readSync(fd, head, 0, SNIFF_BYTES, 0);
      const media = sniffMedia(head.subarray(0, got));
      if (!media) { closeSync(fd); return { ok: false, why: 'not-an-image' }; }
      if (st.size > (maxBytes ?? maxBytesFor(media.kind))) { closeSync(fd); return { ok: false, why: 'too-big' }; }
      return { ok: true, fd, file: wanted, size: st.size, mime: media.mime, kind: media.kind };
    } catch {
      try { closeSync(fd); } catch { /* already closed */ }
      return { ok: false, why: 'not-a-file' };
    }
  }
  return { ok: false, why: 'outside-roots' };
}

/** openMedia under its first name (an image or a video: the bytes decide). */
export const openImage = openMedia;

/** The same checks as openMedia, for a caller that only wants the verdict (stamping). */
export function resolveImageFile(path: string, roots: readonly string[], maxBytes?: number): Resolved {
  const r = openMedia(path, roots, maxBytes);
  if (!r.ok) return r;
  try { closeSync(r.fd); } catch { /* fine */ }
  return { ok: true, file: r.file, size: r.size, mime: r.mime, kind: r.kind };
}

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;

/** Is `dir` the data dir or one of its ancestors (by the path as written, and by real path)? */
export function enclosesDataDir(dir: string, dataDir: string): boolean {
  const within = (x: string, root: string) => x === root || x.startsWith(root.endsWith(sep) ? root : root + sep);
  const c = resolve(dir);
  const d = resolve(dataDir);
  if (within(d, c)) return true;
  try { if (within(realpathSync(d), realpathSync(c))) return true; } catch { /* a folder that is not there encloses nothing real */ }
  return false;
}

/** Is `dir` inside the data dir's state/ tree (state itself or below it), by the path as written and by real path? */
export function insideStateTree(dir: string, dataDir: string): boolean {
  const within = (x: string, root: string) => x === root || x.startsWith(root.endsWith(sep) ? root : root + sep);
  const state = join(resolve(dataDir), 'state');
  if (within(resolve(dir), state)) return true;
  try { if (within(realpathSync(dir), realpathSync(state))) return true; } catch { /* a folder that is not there holds nothing real */ }
  return false;
}

/**
 * The directories ONE agent's media may come from: its working folder (state/agents/<id>.json
 * cwd, else state/agent-sessions.json) and its own output folders under the data dir,
 * state/shots/<id> and state/agents/<id>/. Never the data dir itself (state/uploads is every
 * client's): a working folder that is the data dir or encloses it is dropped, and so is one inside
 * the state/ tree (state, state/uploads, state/shots, another agent's state/agents/<x>: every one
 * of them holds another client's or another agent's files). A path-shaped id builds no path; an unknown agent has only the data-dir folders named for it
 * (which do not exist, so nothing is served).
 */
export function agentMediaRoots(dataDir: string, agentId: string): string[] {
  if (!SAFE_ID.test(agentId)) return [];
  const d = resolve(dataDir);
  const roots = [join(d, 'state', 'shots', agentId), join(d, 'state', 'agents', agentId)];
  const cwd = agentCwd(dataDir, agentId);
  if (cwd && cwd.startsWith('/') && cwd !== '/' && !enclosesDataDir(cwd, d) && !insideStateTree(cwd, d)) roots.unshift(resolve(cwd));
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

export interface RefsDeps {
  store: MediaStore;
  roots: (agentId: string) => string[];
  now?: () => number;
}

/** A path that failed the checks is not looked at again for this long (ms): a reply naming a file
 *  that is not there yet is re-checked on a later poll, not on every poll. */
const MISS_TTL_MS = 5_000;
/** The miss cache's bound: past it, expired keys go, then the oldest, down to three quarters of it
 *  (one sweep pays for a quarter of the bound's inserts, not one). */
export const MAX_MISSES = 4096;
const misses = new Map<string, number>();
let fileChecks = 0;

function rememberMiss(key: string, until: number, now: number): void {
  misses.delete(key);
  if (misses.size >= MAX_MISSES) {
    for (const [k, t] of misses) if (t <= now) misses.delete(k);
    const keep = Math.floor(MAX_MISSES * 0.75);
    if (misses.size > keep) for (const k of misses.keys()) { if (misses.size <= keep) break; misses.delete(k); }
  }
  misses.set(key, until);
}

/**
 * The paths of a whole window that may carry media: the newest `cap` distinct ones, in the order
 * the window has them, by the words alone (no fs). The install's stamper uses stampWindow, which
 * counts only paths that can be shown; this is the fallback for an injected stamper.
 */
export function windowPaths(perItem: readonly (readonly string[])[], cap = MAX_ROWS_PER_AGENT): Set<string> {
  const keep = new Set<string>();
  for (let i = perItem.length - 1; i >= 0 && keep.size < cap; i--) {
    const ps = perItem[i];
    for (let j = ps.length - 1; j >= 0 && keep.size < cap; j--) keep.add(ps[j]);
  }
  return keep;
}

/** What one check of a path found: the id already held, or a file that passed every check now (its kind). */
type Checked = { id: string; kind?: MediaKind } | { id: null; kind: MediaKind };

/** A window (stampWindow): the paths the table keeps, when the transcript last referenced each (ms), and
 *  what the one check of each found, so stamping its items touches the disk no more. */
export class MediaWindow extends Set<string> {
  readonly refAt = new Map<string, number>();
  readonly checked = new Map<string, Checked>();
}

/** An item's time as ms (an ISO string, or a number in seconds or ms), or undefined. */
export function itemTime(ts: unknown): number | undefined {
  if (typeof ts === 'number' && Number.isFinite(ts)) return ts < 1e12 ? ts * 1000 : ts;
  if (typeof ts === 'string' && ts) { const t = Date.parse(ts); return Number.isNaN(t) ? undefined : t; }
  return undefined;
}

/** One check of a path: a recent miss is a miss with no fs; a held id needs no fs; else (when `fs` allows)
 *  under the roots and every check of the file now (one open). A refAt moves a held row's `seen` on. */
function check(agentId: string, p: string, roots: readonly string[], deps: RefsDeps, now: number, refAt?: number, fs = true): Checked | null {
  const key = `${agentId}\0${p}`;
  const until = misses.get(key);
  if (until !== undefined && until > now) return null;
  const have = deps.store.has(agentId, p, refAt);
  if (have) return { id: have, kind: deps.store.lookup(have)?.kind };
  if (!fs) return null;
  if (until !== undefined) misses.delete(key);
  if (!roots.some((r) => underRoot(p, r))) return null;
  fileChecks++;
  const r = resolveImageFile(p, roots);
  if (!r.ok) { rememberMiss(key, now + MISS_TTL_MS, now); return null; }
  return { id: null, kind: r.kind };
}

/** How many distinct paths under the roots one window checks on disk, newest first, at most (in units of
 *  the cap): a window naming thousands of files that are not there costs a bounded walk (each a miss-cache
 *  hit after the first poll), never a check of all of them. A path outside the roots, or one the table
 *  already holds, is answered without the disk and takes none of this budget. */
const WINDOW_SCAN_FACTOR = 2;

/**
 * The window the table keeps for one agent, read newest first: the newest `cap` distinct paths that
 * can be SHOWN (already held, or under the agent's roots and passing every check now). A path that is
 * not there, or outside the roots, takes no place, so a run of missing files never crowds out real
 * ones. The disk is asked about at most WINDOW_SCAN_FACTOR x cap distinct paths under the roots (a
 * path outside them is never a file check, so a thousand of them in prose cost no budget); past that,
 * a path the table already holds an id for is still kept (an image once shown never loses its thumbnail
 * to newer paths that cannot be shown), and a new one waits for a later window. Each kept path carries
 * the time of the newest item naming it (`times[i]`, itemTime) and what its one check found.
 */
export function stampWindow(agentId: string, perItem: readonly (readonly string[])[], deps: RefsDeps, times: readonly unknown[] = [], cap = MAX_ROWS_PER_AGENT): MediaWindow {
  const keep = new MediaWindow();
  const roots = deps.roots(agentId);
  const now = deps.now ? deps.now() : Date.now();
  const seen = new Set<string>();
  const scan = cap * WINDOW_SCAN_FACTOR;
  let looked = 0;
  for (let i = perItem.length - 1; i >= 0 && keep.size < cap; i--) {
    const ps = perItem[i];
    const at = itemTime(times[i]);
    for (let j = ps.length - 1; j >= 0 && keep.size < cap; j--) {
      const p = ps[j];
      if (seen.has(p)) continue;
      seen.add(p);
      const fs = looked < scan && roots.some((r) => underRoot(p, r));
      if (fs) looked++;
      const c = check(agentId, p, roots, deps, now, at, fs);
      if (!c) continue;
      keep.add(p);
      keep.checked.set(p, c);
      if (at !== undefined) keep.refAt.set(p, at);
    }
  }
  return keep;
}

/**
 * The refs to stamp for the paths an agent wrote: an id for each path that is under the agent's
 * roots AND passes every check right now (exists, a regular file, no links, an image or a video by
 * its bytes, under that kind's cap). An id already minted is reused without touching the disk.
 * Nothing else gets one. `window` (stampWindow, or windowPaths, of the whole window being stamped)
 * is what the table keeps: a path outside it is skipped, and no row a path inside it holds is
 * evicted. A MediaWindow already checked each of its paths (once per stamp) and says when each was
 * referenced (MintOpts.refAt).
 */
export function imageRefsFor(agentId: string, paths: readonly string[], deps: RefsDeps, window?: ReadonlySet<string>): ImageRef[] {
  const out: ImageRef[] = [];
  if (!paths.length) return out;
  const win = window instanceof MediaWindow ? window : null;
  const roots = win ? [] : deps.roots(agentId);
  const now = deps.now ? deps.now() : Date.now();
  const ref = (id: string, p: string, kind?: MediaKind): ImageRef =>
    (kind === 'video' ? { id, path: p, name: imageName(p), kind: 'video' } : { id, path: p, name: imageName(p) });
  for (const p of paths) {
    if (window && !window.has(p)) continue;
    const refAt = win?.refAt.get(p);
    const c = win ? win.checked.get(p) ?? null : check(agentId, p, roots, deps, now);
    if (!c) continue;
    if (c.id) { out.push(ref(c.id, p, c.kind)); continue; }
    const id = deps.store.mint(agentId, p, { kind: c.kind, protect: window, refAt });
    if (id) out.push(ref(id, p, c.kind));
  }
  return out;
}

/** Tests only: forget the miss cache. */
export function _resetMisses(): void { misses.clear(); }
/** Tests only: how many keys the miss cache holds. */
export function _missCount(): number { return misses.size; }
/** Tests only: how many times a file has been opened and checked by the stamper (resolveImageFile), ever. */
export function _fileChecks(): number { return fileChecks; }

/** A stamper: the refs for one item's paths, inside the window being stamped. One that also has `window`
 *  builds that window itself (stampWindow: only paths it can show count, with their times); else the
 *  caller uses windowPaths. */
export type RefsFor = ((agentId: string, paths: string[], window?: ReadonlySet<string>) => ImageRef[]) & {
  window?: (agentId: string, perItem: readonly (readonly string[])[], times?: readonly unknown[]) => ReadonlySet<string>;
};

/** A stamper over these deps, window included. */
export function stamperFor(deps: RefsDeps): RefsFor {
  const f: RefsFor = (agentId, paths, window) => imageRefsFor(agentId, paths, deps, window);
  f.window = (agentId, perItem, times) => stampWindow(agentId, perItem, deps, times);
  return f;
}

/** The window for `perItem` from this stamper: its own when it has one, else windowPaths. */
export function windowFor(refsFor: RefsFor, agentId: string, perItem: readonly (readonly string[])[], times?: readonly unknown[]): ReadonlySet<string> {
  return refsFor.window ? refsFor.window(agentId, perItem, times) : windowPaths(perItem);
}

/** The install's stamper: its table and each agent's own roots. */
export function defaultImageRefsFor(dataDir: string): RefsFor {
  return stamperFor({ store: mediaStore(dataDir), roots: (a) => agentMediaRoots(dataDir, a) });
}
