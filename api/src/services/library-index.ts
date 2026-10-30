/**
 * library-index.ts — the library's index (parity 4g.1): what each agent made, mentioned and posted, kept in
 * <data dir>/state/library.db, owned by the API.
 *
 * Kept current without rescanning: each agent's log is read forward from a saved position (the start of its last
 * turn, so a turn still being written is read again whole next time; every write is an upsert, so reading a turn
 * twice changes nothing). A pass runs every 60 s; the first start backfills the last 14 days. Logs are found the way
 * the chat finds them (chat-transcript.ts: the agent's current session, then each predecessor), so ~/.claude is only
 * ever read through the API's own reader.
 *
 * Rules: services/library-attribution.ts. On top of them, the project-folder pass: a file that changed inside a
 * project folder while exactly one agent had a tool call running (each call's own window, tool_use to tool_result,
 * or to a background command's finished notice) is that agent's; while several did, "unclear" (Unclear who made it),
 * naming them; while none did, "unattributed". A file an agent's own log already shows it making is theirs, whatever
 * the windows say. Arturo's conversations (threads.db, read only) add the links he posted.
 *
 * Kept copies (4g.2): a file an agent made in scratch or /tmp is copied, while it still exists, to
 * <data dir>/library/<agent>/<item id><ext>, so the Library can still open it after the scratch space is cleaned.
 * Named by item id, not by content: an item has one copy, replaced when its file changes, so a cap counts what the
 * operator sees. Project files and uploads are not copied (they already live in kept folders); mentioned, unclear
 * and unattributed files are not either (not this agent's work, or not known to be). Over 25 MB an item is not
 * kept ("too_large"). Over the per-agent or whole-library cap, the oldest unpinned copies are cleared first; a
 * pinned item is never cleared. Each pass also marks files that are gone (missing; the kept copy is served), and
 * links a move: the same agent has a newer item with the same name and size.
 *
 * Titles: an item's title is its file's name, or a short description of its content when one exists (a markdown
 * note's first heading, read here; an image's alt text from the reply that showed it). The words a Bash command
 * came with (its description) are the subtitle, under the title, never the title. Rows indexed before this rule are
 * retitled once on open.
 */
import Database, { type Database as DB } from 'better-sqlite3';
import { createHash } from 'crypto';
import { chmodSync, closeSync, copyFileSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, readSync, realpathSync, renameSync, statSync, unlinkSync } from 'fs';
import { basename, extname, join, relative, sep } from 'path';
import { resolveTranscriptPath, resolvePreviousTranscript } from '../routes/chat-transcript.js';
import { parseClaudeLines, itemsFromEvents, ownersOf, kindOf, linksInText, isScratch, ignored, markdownTitle, subtitleFor, resolveIn, type LibraryItem, type Stat, type AgentWindow } from './library-attribution.js';

const DAY = 86_400_000;
export const BACKFILL_DAYS = 14;
const MAX_CHAIN = 25;                 // sessions followed back per agent
const MAX_PROJECT_FILES = 20_000;     // per project folder per pass
const SKIP_DIRS = new Set(['.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache', 'dist', 'build', '.next', '.turbo']);
const MB = 1024 * 1024;

/** Kept copies' limits, in bytes. */
export interface KeepLimits { enabled: boolean; itemMax: number; agentCap: number; totalCap: number }
export const KEEP_DEFAULTS: KeepLimits = { enabled: true, itemMax: 25 * MB, agentCap: 500 * MB, totalCap: 2048 * MB };

/** The limits from the environment (sizes in MB; ORCHESTRA_LIBRARY_KEEP=0 turns copying off). */
export function keepLimitsFromEnv(env: NodeJS.ProcessEnv = process.env): KeepLimits {
  const mb = (v: string | undefined, d: number) => { const n = Number(v); return v && Number.isFinite(n) && n >= 0 ? n * MB : d; };
  return { enabled: env.ORCHESTRA_LIBRARY_KEEP !== '0',
           itemMax: mb(env.ORCHESTRA_LIBRARY_ITEM_MAX_MB, KEEP_DEFAULTS.itemMax),
           agentCap: mb(env.ORCHESTRA_LIBRARY_AGENT_CAP_MB, KEEP_DEFAULTS.agentCap),
           totalCap: mb(env.ORCHESTRA_LIBRARY_TOTAL_CAP_MB, KEEP_DEFAULTS.totalCap) };
}

export interface IndexOptions {
  dataDir: string;
  /** The agents to index, with the folder each works in. */
  agents: () => { id: string; cwd?: string }[];
  /** An agent's log files, newest first (default: the chat's own resolution). */
  logsOf?: (agentId: string, cutoffMs: number) => { path: string; session: string | null }[];
  stat?: Stat;
  now?: () => number;
  /** What counts as scratch space (tests run in a temp folder, which the real rule calls scratch). */
  scratch?: (p: string) => boolean;
  /** The files in a folder a command named as its output (two levels, a few hundred at most). */
  list?: (dir: string) => string[];
  /** Kept copies' limits (default KEEP_DEFAULTS). */
  keep?: Partial<KeepLimits>;
}

export function listOutputDir(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string, depth: number) => {
    let ents;
    try { ents = readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of ents) {
      if (out.length >= 500) return;
      if (e.isFile()) out.push(join(d, e.name));
      else if (e.isDirectory() && depth < 2 && !SKIP_DIRS.has(e.name)) walk(join(d, e.name), depth + 1);
    }
  };
  walk(dir, 0);
  return out;
}

export const realStat: Stat = (p) => {
  try { const s = statSync(p); return { mtimeMs: s.mtimeMs, birthtimeMs: s.birthtimeMs, size: s.size, isFile: s.isFile() }; } catch { return null; }
};

/** The chat's resolution: the current session's log, then each predecessor's, while they are recent. */
export function chatLogsOf(agentId: string, cutoffMs: number): { path: string; session: string | null }[] {
  const out: { path: string; session: string | null }[] = [];
  const cur = resolveTranscriptPath(agentId);
  if (!cur?.path) return out;
  out.push({ path: cur.path, session: cur.sid });
  let sid = cur.sid;
  const seen = new Set<string>([cur.path]);
  for (let i = 0; i < MAX_CHAIN && sid; i++) {
    const prev = resolvePreviousTranscript(agentId, sid);
    if (!prev || seen.has(prev.path)) break;
    let m = 0;
    try { m = statSync(prev.path).mtimeMs; } catch { break; }
    if (m < cutoffMs) break;
    seen.add(prev.path);
    const from = String((prev.divider as { from_session?: string }).from_session || '') || null;
    out.push({ path: prev.path, session: from });
    sid = from;
  }
  return out;
}

export function openLibraryDb(dataDir: string): DB {
  const db = new Database(join(dataDir, 'state', 'library.db'));
  db.pragma('journal_mode = WAL');
  db.exec(`
    create table if not exists items (
      id text primary key, agent text not null default '', agents text not null default '', attribution text not null default 'agent',
      project text, kind text not null, action text not null, made integer not null, title text not null,
      path text, url text, source text not null, session text, turn text, at text, size integer,
      first_seen text not null, updated text not null);
    create index if not exists items_agent on items(agent, made, at);
    create index if not exists items_project on items(project, at);
    create table if not exists cursors (path text primary key, agent text not null, session text, offset integer not null, ino integer, size integer);
    create table if not exists windows (turn text primary key, agent text not null, session text, start integer not null, end integer not null, cwd text);
    create index if not exists windows_agent on windows(agent, start);
    create table if not exists calls (id text primary key, agent text not null, session text, tool text, start integer not null, end integer not null, cwd text);
    create index if not exists calls_time on calls(start, end);
    create table if not exists meta (key text primary key, value text);
  `);
  // read_only came after the first index: add it to an index made before.
  const cols = (db.prepare('pragma table_info(calls)').all() as { name: string }[]).map((c) => c.name);
  if (!cols.includes('read_only')) db.exec('alter table calls add column read_only integer not null default 0');
  // Kept copies, pins and missing/moved came with 4g.2: add them to an index made before.
  const itemCols = new Set((db.prepare('pragma table_info(items)').all() as { name: string }[]).map((c) => c.name));
  for (const [c, def] of [['pinned', 'integer not null default 0'], ['missing', 'integer not null default 0'], ['missing_since', 'text'],
                          ['moved_to', 'text'], ['keep', 'text'], ['kept_path', 'text'], ['kept_size', 'integer'], ['kept_at', 'text'],
                          ['kept_mtime', 'real'], ['subtitle', 'text']] as const) {
    if (!itemCols.has(c)) db.exec(`alter table items add column ${c} ${def}`);
  }
  return db;
}

const MARKDOWN = /\.(md|markdown|mdx)$/i;
const HEAD_BYTES = 16 * 1024;

/** A markdown note's first heading (or front matter title), read from the start of the file; else null. */
export function contentTitle(path: string): string | null {
  if (!MARKDOWN.test(path) || ignored(path)) return null;
  let fd: number | null = null;
  try {
    fd = openSync(path, 'r');
    const buf = Buffer.alloc(HEAD_BYTES);
    const n = readSync(fd, buf, 0, HEAD_BYTES, 0);
    return markdownTitle(buf.subarray(0, n).toString('utf-8')) ?? null;
  } catch { return null; } finally { if (fd !== null) closeSync(fd); }
}

const idOf = (...parts: (string | undefined | null)[]) => createHash('sha1').update(parts.map((p) => p ?? '').join('\u0000')).digest('hex').slice(0, 24);

export function projectOf(dataDir: string, path: string | undefined, cwd?: string): string | null {
  const root = join(dataDir, 'projects') + sep;
  for (const p of [path, cwd]) {
    if (p && p.startsWith(root)) return p.slice(root.length).split(sep)[0] || null;
  }
  return null;
}

const ITEM_COLS = `id, agent, agents, attribution, project, kind, action, made, title, subtitle, path, url, source, at, size,
  pinned, missing, missing_since, moved_to, keep, kept_path is not null as kept, kept_size`;
interface ItemRow {
  id: string; agent: string; agents: string; attribution: string; project: string | null; kind: string; action: string; made: number;
  title: string; subtitle: string | null; path: string | null; url: string | null; source: string; at: string | null; size: number | null;
  pinned: number; missing: number; missing_since: string | null; moved_to: string | null; keep: string | null; kept: number; kept_size: number | null;
}
/** The new flags as booleans in the JSON (made stays 0/1, as the dashboard already reads it). */
const shape = (r: ItemRow) => ({ ...r, pinned: !!r.pinned, missing: !!r.missing, kept: !!r.kept });
export type LibraryItemRow = ReturnType<typeof shape>;
interface KeptCounts { bytes: number; items: number; pinned: number; too_large: number; cleared: number }
const KEEP_COLS = 'id, agent, attribution, kind, made, path, pinned, keep, kept_mtime';
interface KeepRow { id: string; agent: string; attribution: string; kind: string; made: number; path: string; pinned: number; keep: string | null; kept_mtime: number | null }
const SAFE_AGENT = /^[\w.-]{1,80}$/;

export class LibraryIndex {
  readonly db: DB;
  readonly keep: KeepLimits;
  private readonly o: Required<Omit<IndexOptions, 'logsOf' | 'keep'>> & { logsOf: NonNullable<IndexOptions['logsOf']> };
  constructor(opts: IndexOptions, db?: DB) {
    const { keep, ...rest } = opts;
    this.keep = { ...KEEP_DEFAULTS, ...keep };
    this.o = { stat: realStat, now: Date.now, logsOf: chatLogsOf, scratch: isScratch, list: listOutputDir, ...rest };
    this.db = db ?? openLibraryDb(opts.dataDir);
    try { this.retitle(); } catch { /* the old titles stay until each item is read again */ }
  }

  /** Once: rows indexed before titles were names (a Bash description was the title) get the name or content
   *  description as their title, and the description as the subtitle. */
  private retitle() {
    if (this.meta('titles') === '2') return;
    const rows = this.db.prepare(`select id, kind, source, title, path from items where path is not null and kind != 'link'`).all() as
      { id: string; kind: string; source: string; title: string; path: string }[];
    const set = this.db.prepare('update items set title = ?, subtitle = coalesce(subtitle, ?) where id = ?');
    this.db.transaction(() => {
      for (const r of rows) {
        const sub = r.source === 'bash' ? subtitleFor(r.path, r.title) : undefined;
        set.run(contentTitle(r.path) ?? basename(r.path), sub ?? null, r.id);
      }
      this.setMeta('titles', '2');
    })();
  }

  private meta(key: string): string | null {
    return (this.db.prepare('select value from meta where key = ?').get(key) as { value: string } | undefined)?.value ?? null;
  }
  private setMeta(key: string, value: string) { this.db.prepare('insert into meta(key, value) values(?, ?) on conflict(key) do update set value = excluded.value').run(key, value); }

  /** Store one item. A file made outranks the same file mentioned; a known agent outranks shared/unattributed. */
  upsert(it: LibraryItem & { attribution?: string; agents?: string; project?: string | null }) {
    const now = new Date(this.o.now()).toISOString();
    if (it.kind !== 'link' && it.path) it = { ...it, title: contentTitle(it.path) ?? it.title };
    const key = it.kind === 'link' ? idOf(it.agent, 'link', it.url) : idOf(it.attribution && it.attribution !== 'agent' ? '' : it.agent, 'file', it.path);
    const prev = this.db.prepare('select made, action from items where id = ?').get(key) as { made: number; action: string } | undefined;
    if (prev && prev.made && !it.made) return;                         // made stays made
    if (prev && prev.action === 'created' && it.action === 'edited') it = { ...it, action: 'created' };
    // An agent's own log showing it made a file settles who made it: no row from the folder pass's timing (shared,
    // unattributed, or another agent's) stays for it.
    let carryPin = false;
    if (it.made && it.path && it.source !== 'project-window' && it.agent) {
      // a pin the operator put on the row being replaced stays with the file
      carryPin = !!this.db.prepare(`select 1 from items where path = ? and source = 'project-window' and id != ? and pinned = 1`).get(it.path, key);
      this.db.prepare(`delete from items where path = ? and source = 'project-window' and id != ?`).run(it.path, key);
    }
    // a later read without a description (a Write after the command) keeps the one it had
    this.db.prepare(`insert into items(id, agent, agents, attribution, project, kind, action, made, title, subtitle, path, url, source, session, turn, at, size, first_seen, updated)
      values(@id, @agent, @agents, @attribution, @project, @kind, @action, @made, @title, @subtitle, @path, @url, @source, @session, @turn, @at, @size, @now, @now)
      on conflict(id) do update set agent=excluded.agent, agents=excluded.agents, attribution=excluded.attribution, project=excluded.project,
        kind=excluded.kind, action=excluded.action, made=excluded.made, title=excluded.title, subtitle=coalesce(excluded.subtitle, items.subtitle),
        source=excluded.source, session=excluded.session,
        turn=excluded.turn, at=max(coalesce(items.at, ''), coalesce(excluded.at, '')), size=excluded.size, updated=excluded.updated`)
      .run({ id: key, agent: it.attribution && it.attribution !== 'agent' ? '' : it.agent, agents: it.agents ?? it.agent, attribution: it.attribution ?? 'agent',
             project: it.project ?? null, kind: it.kind, action: it.action, made: it.made ? 1 : 0, title: it.title, subtitle: it.subtitle ?? null, path: it.path ?? null, url: it.url ?? null,
             source: it.source, session: it.session ?? null, turn: it.turn ?? null, at: it.at ?? null, size: it.size ?? null, now });
    if (carryPin) this.db.prepare('update items set pinned = 1 where id = ?').run(key);
  }

  /** Read one log forward from its cursor: items and turn windows. */
  indexLog(agent: string, cwd: string | undefined, path: string, session: string | null) {
    let st;
    try { st = statSync(path); } catch { return; }
    const cur = this.db.prepare('select offset, ino, size from cursors where path = ?').get(path) as { offset: number; ino: number; size: number } | undefined;
    if (cur && cur.ino === st.ino && cur.size === st.size) return;     // nothing new since the last read
    const from = cur && cur.ino === st.ino && st.size >= cur.offset ? cur.offset : 0;
    const len = st.size - from;
    if (len <= 0) return;
    const buf = Buffer.alloc(len);
    const fd = openSync(path, 'r');
    try { readSync(fd, buf, 0, len, from); } finally { closeSync(fd); }
    const text = buf.toString('utf-8');
    const end = text.lastIndexOf('\n');
    if (end < 0) return;                                               // no complete line yet
    const lines = text.slice(0, end).split('\n');
    const { events, turns, calls } = parseClaudeLines(lines, session ?? undefined);
    const items = itemsFromEvents(agent, events, this.o.stat, session ?? undefined, this.o.scratch, this.o.list);
    const tx = this.db.transaction(() => {
      for (const it of items) this.upsert({ ...it, project: projectOf(this.o.dataDir, it.path, cwd) });
      for (const c of calls) {
        this.db.prepare(`insert into calls(id, agent, session, tool, start, end, cwd, read_only) values(?, ?, ?, ?, ?, ?, ?, ?)
          on conflict(id) do update set end=max(calls.end, excluded.end), read_only=excluded.read_only`)
          .run(c.id, agent, session, c.tool, c.start, c.end, c.cwd ?? cwd ?? null, c.readOnly ? 1 : 0);
      }
      for (const t of turns) {
        this.db.prepare(`insert into windows(turn, agent, session, start, end, cwd) values(?, ?, ?, ?, ?, ?)
          on conflict(turn) do update set start=min(windows.start, excluded.start), end=max(windows.end, excluded.end)`)
          .run(t.turn, agent, session, t.start, t.end, t.cwd ?? cwd ?? null);
      }
      // Next time, start again at this read's last turn: it may still be growing.
      let next = from + Buffer.byteLength(text.slice(0, end + 1), 'utf-8');
      const lastTurn = turns[turns.length - 1];
      if (lastTurn) {
        const idx = lines.findIndex((l) => l.includes(`"uuid":"${lastTurn.turn}"`));
        if (idx > 0) next = from + Buffer.byteLength(lines.slice(0, idx).join('\n') + '\n', 'utf-8');
      }
      this.db.prepare(`insert into cursors(path, agent, session, offset, ino, size) values(?, ?, ?, ?, ?, ?)
        on conflict(path) do update set offset=excluded.offset, ino=excluded.ino, size=excluded.size`).run(path, agent, session, next, st.ino, st.size);
    });
    tx();
  }

  /** Files that changed in project folders since `sinceMs`, attributed by who was working there then. */
  projectPass(sinceMs: number) {
    const root = join(this.o.dataDir, 'projects');
    let slugs: string[] = [];
    try { slugs = readdirSync(root, { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name); } catch { return; }
    for (const slug of slugs) {
      const dir = join(root, slug);
      // Every agent's running calls: an agent working elsewhere can still write here by its full path.
      const windows = this.db.prepare(`select agent, start, end, cwd, read_only from calls where end >= ?`).all(sinceMs - DAY) as AgentWindow[];
      const files: string[] = [];
      const walk = (d: string) => {
        if (files.length >= MAX_PROJECT_FILES) return;
        let ents;
        try { ents = readdirSync(d, { withFileTypes: true }); } catch { return; }
        for (const e of ents) {
          if (files.length >= MAX_PROJECT_FILES) return;
          if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) walk(join(d, e.name)); }
          else if (e.isFile()) files.push(join(d, e.name));
        }
      };
      walk(dir);
      const explicit = this.db.prepare(`select 1 from items where path = ? and made = 1 and attribution = 'agent' limit 1`);
      const tx = this.db.transaction(() => {
        for (const f of files) {
          const st = this.o.stat(f);
          if (!st || st.mtimeMs < sinceMs || ignored(f)) continue;
          if (explicit.get(f)) continue;                               // its own log says who made it
          const own = ownersOf(st.mtimeMs, windows);
          const created = st.birthtimeMs && st.birthtimeMs > 0 ? Math.abs(st.birthtimeMs - st.mtimeMs) < 60_000 : true;
          this.upsert({ agent: own.agents[0] ?? '', agents: own.agents.join(','), attribution: own.attribution, project: slug,
                        kind: kindOf(f), action: created ? 'created' : 'edited', made: true, title: basename(f), path: f,
                        source: 'project-window', at: new Date(st.mtimeMs).toISOString(), size: st.size });
        }
      });
      tx();
    }
  }

  /** Arturo's conversations: the links he posted (threads.db, read only). */
  arturoPass(sinceMs: number) {
    const p = join(this.o.dataDir, 'state', 'arturo', 'threads.db');
    if (!existsSync(p)) return;
    let adb: DB | null = null;
    try {
      adb = new Database(p, { readonly: true, fileMustExist: true });
      const rows = adb.prepare(`select thread_id, seq, content, ts from turns where role = 'assistant' and ts >= ?`).all(sinceMs / 1000) as
        { thread_id: string; seq: number; content: string; ts: number }[];
      const tx = this.db.transaction(() => {
        for (const r of rows) for (const l of linksInText(r.content || '')) {
          this.upsert({ agent: 'arturo', kind: 'link', action: 'posted', made: false, title: l.label || l.url.replace(/^https?:\/\//, '').slice(0, 120),
                        url: l.url, source: 'link', session: r.thread_id, turn: String(r.seq), at: new Date(r.ts * 1000).toISOString() });
        }
      });
      tx();
    } catch { /* his store is busy or older: next pass */ } finally { adb?.close(); }
  }

  /** One pass: every agent's logs forward, then the project folders and Arturo. */
  pass(days = BACKFILL_DAYS): { ms: number; logs: number } {
    const t0 = Date.now();
    const cutoff = this.o.now() - days * DAY;
    let logs = 0;
    for (const a of this.o.agents()) {
      let files: { path: string; session: string | null }[] = [];
      try { files = this.o.logsOf(a.id, cutoff); } catch { files = []; }
      for (const f of files.slice().reverse()) {                       // oldest first: windows and items in order
        try { this.indexLog(a.id, a.cwd, f.path, f.session); logs++; } catch { /* one bad log never stops the pass */ }
      }
    }
    const lastProject = Number(this.meta('project_pass') || 0);
    const since = lastProject ? lastProject - 5 * 60_000 : cutoff;
    this.projectPass(Math.max(since, cutoff));
    this.setMeta('project_pass', String(this.o.now()));
    this.arturoPass(Math.max(Number(this.meta('arturo_pass') || 0) - 5 * 60_000, cutoff));
    this.setMeta('arturo_pass', String(this.o.now()));
    try { this.keepPass(); } catch { /* a copy that fails is tried again next pass */ }
    this.missingPass();
    return { ms: Date.now() - t0, logs };
  }

  /** The first start: the last 14 days, timed. Later starts only read forward. */
  backfillIfNeeded(): { ms: number; logs: number } | null {
    if (this.meta('backfill_done')) return null;
    const r = this.pass(BACKFILL_DAYS);
    this.setMeta('backfill_done', new Date(this.o.now()).toISOString());
    this.setMeta('backfill_ms', String(r.ms));
    this.setMeta('backfill_logs', String(r.logs));
    return r;
  }

  status() {
    return { backfill_done: this.meta('backfill_done'), backfill_ms: Number(this.meta('backfill_ms') || 0) || null,
             backfill_logs: Number(this.meta('backfill_logs') || 0) || null, last_pass: this.meta('project_pass') };
  }

  /** Agents shown by default: the registry's, and Arturo. Retired agents' items stay in the index, shown on request. */
  private current(): Set<string> { return new Set(['arturo', ...this.o.agents().map((a) => a.id)]); }

  /** Counts per agent over the last `days`: made, mentioned, links; and unclear/unattributed per project. */
  summary(days = BACKFILL_DAYS, retired = false) {
    const since = new Date(this.o.now() - days * DAY).toISOString();
    const cur = this.current();
    const agents = this.db.prepare(`select agent,
        sum(case when made = 1 and kind != 'link' then 1 else 0 end) made,
        sum(case when action = 'mentioned' then 1 else 0 end) mentioned,
        sum(case when kind = 'link' then 1 else 0 end) links
      from items where attribution = 'agent' and agent != '' and coalesce(at, first_seen) >= ? group by agent order by agent`).all(since)
      .filter((r: any) => retired || cur.has(r.agent));
    const elsewhere = this.db.prepare(`select project, attribution, count(*) n from items where attribution != 'agent' and coalesce(at, first_seen) >= ?
      group by project, attribution order by project`).all(since);
    return { days, agents, shared_or_unattributed: elsewhere };
  }

  list(q: { agent?: string; project?: string; made?: boolean; kind?: string; days?: number; limit?: number; offset?: number; retired?: boolean; action?: string; pinned?: boolean }) {
    const where: string[] = []; const args: unknown[] = [];
    if (q.agent) { where.push('agent = ?'); args.push(q.agent); }
    if (q.project) { where.push('project = ?'); args.push(q.project); }
    if (q.made !== undefined) { where.push('made = ?'); args.push(q.made ? 1 : 0); }
    if (q.kind) { where.push('kind = ?'); args.push(q.kind); }
    if (q.action) { where.push('action = ?'); args.push(q.action); }
    if (!q.retired && !q.agent) {
      const cur = [...this.current()];
      where.push(`(agent = '' or agent in (${cur.map(() => '?').join(',')}))`); args.push(...cur);
    }
    if (q.days) { where.push('coalesce(at, first_seen) >= ?'); args.push(new Date(this.o.now() - q.days * DAY).toISOString()); }
    if (q.pinned) where.push('pinned = 1');
    const sql = `select ${ITEM_COLS} from items
      ${where.length ? `where ${where.join(' and ')}` : ''} order by coalesce(at, first_seen) desc limit ? offset ?`;
    return (this.db.prepare(sql).all(...args, Math.min(q.limit ?? 50, 500), q.offset ?? 0) as ItemRow[]).map(shape);
  }

  /** Who made each of a project's files (4g.4, the project Files "made by" filter), keyed by the path inside the
   *  project folder. Only made rows count (a file an agent only showed or named never makes it the maker). Agents
   *  whose own logs show them making a file are its makers (several when several did); else the folder pass's
   *  answer: unclear, naming the candidates, or unattributed. Retired agents are named too (they did make it),
   *  marked retired in `makers`. Gone files are left out. */
  madeBy(project: string) {
    const dir = join(this.o.dataDir, 'projects', project);
    const rows = this.db.prepare(`select agent, agents, attribution, path, at from items
      where project = ? and made = 1 and kind != 'link' and path is not null and missing = 0 limit ?`).all(project, MAX_PROJECT_FILES * 2) as
      { agent: string; agents: string; attribution: string; path: string; at: string | null }[];
    type Entry = { attribution: 'agent' | 'unclear' | 'unattributed'; agents: string[]; at: string | null };
    const files: Record<string, Entry> = {};
    const rank = { agent: 3, unclear: 2, unattributed: 1 } as const;
    for (const r of rows) {
      const rel = relative(dir, r.path);
      if (!rel || rel.startsWith('..') || rel.startsWith(sep)) continue;
      const attribution = (r.attribution === 'agent' && r.agent ? 'agent' : r.attribution === 'unclear' ? 'unclear' : 'unattributed') as Entry['attribution'];
      const agents = attribution === 'agent' ? [r.agent] : String(r.agents || '').split(',').map((a) => a.trim()).filter(Boolean);
      const prev = files[rel];
      if (!prev || rank[attribution] > rank[prev.attribution]) { files[rel] = { attribution, agents: attribution === 'unattributed' ? [] : agents, at: r.at }; continue; }
      if (rank[attribution] < rank[prev.attribution]) continue;
      if (attribution !== 'unattributed') prev.agents = [...new Set([...prev.agents, ...agents])].sort();
      if (r.at && (!prev.at || r.at > prev.at)) prev.at = r.at;
    }
    const cur = this.current();
    const count = new Map<string, number>();
    let unclear = 0, unattributed = 0;
    for (const e of Object.values(files)) {
      if (e.attribution === 'agent') for (const a of e.agents) count.set(a, (count.get(a) ?? 0) + 1);
      else if (e.attribution === 'unclear') unclear++;
      else unattributed++;
    }
    const makers = [...count].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .map(([agent, n]) => ({ agent, files: n, retired: !cur.has(agent) }));
    return { project, files, makers, unclear, unattributed };
  }

  /** The library items behind paths a report names (4g.5, result cards link their files), keyed by the path as
   *  given. For each: what the index holds for that file, preferring a row that made it (the named agent's first),
   *  then one that only mentioned it; a gone file is still given (its kept copy may be served; `openable` says
   *  whether anything can be). No item but a file inside a project folder: that project and its path, so it opens
   *  as a project file. Else null. `onlyProject`: a client sees its own project's files only. A refused path (a secret, ~/.ssh, the install's records) is always null. */
  lookup(paths: string[], agent?: string, onlyProject?: string | null) {
    const out: Record<string, { item: LibraryItemRow | null; openable: boolean; project?: string; rel?: string } | null> = {};
    const rows = this.db.prepare(`select ${ITEM_COLS} from items where path = ? and kind != 'link'
      order by made desc, (attribution = 'agent' and agent = ?) desc, attribution = 'agent' desc, missing asc, coalesce(at, first_seen) desc limit 1`);
    const root = join(this.o.dataDir, 'projects') + sep;
    for (const given of paths) {
      const p = resolveIn(given);
      if (!p || ignored(p)) { out[given] = null; continue; }
      const r = rows.get(p, agent ?? '') as ItemRow | undefined;
      if (r && onlyProject && r.project !== onlyProject) { out[given] = null; continue; }
      if (r) { const it = shape(r); out[given] = { item: it, openable: !it.missing || it.kept }; continue; }
      const st = this.o.stat(p);
      if (p.startsWith(root) && st?.isFile) {
        const [project, ...rest] = p.slice(root.length).split(sep);
        if (project && rest.length && (!onlyProject || project === onlyProject)) { out[given] = { item: null, openable: true, project, rel: rest.join('/') }; continue; }
      }
      out[given] = null;
    }
    return out;
  }

  /** One item, as the list gives it. */
  item(id: string) {
    const r = this.db.prepare(`select ${ITEM_COLS} from items where id = ?`).get(id) as ItemRow | undefined;
    return r ? shape(r) : null;
  }

  /** Pin (never cleared) or unpin an item. A pinned file that can be kept is copied now, not at the next pass. */
  pin(id: string, on: boolean) {
    if (!this.db.prepare('update items set pinned = ? where id = ?').run(on ? 1 : 0, id).changes) return null;
    if (on) {
      const r = this.db.prepare(`select ${KEEP_COLS} from items where id = ?`).get(id) as KeepRow | undefined;
      try { if (r && this.keepable(r)) { this.keepOne(r); this.retain(); } } catch { /* next pass */ }
    }
    return this.item(id);
  }

  /** Where kept copies live: <data dir>/library/<agent>/. */
  private libraryDir(): string { return join(this.o.dataDir, 'library'); }

  /** A file this agent made in scratch space, that the index would show, and is not refused. */
  private keepable(r: KeepRow): boolean {
    return this.keep.enabled && !!r.path && r.made === 1 && r.attribution === 'agent' && SAFE_AGENT.test(r.agent) && !/^\.+$/.test(r.agent)
      && r.kind !== 'link' && this.o.scratch(r.path) && !ignored(r.path);
  }

  /** Copy one item's file into the library (or note it is too large). Unchanged since the last look: nothing to do. */
  private keepOne(r: KeepRow) {
    const st = this.o.stat(r.path);
    if (!st || !st.isFile) return;
    // a cleared copy is made again only once pinned, or when the file has changed since (newer work, now the newest)
    if (r.keep && r.kept_mtime === st.mtimeMs && (r.keep !== 'cleared' || !r.pinned)) return;
    // what the path really is: a link into a refused place is refused too
    let real: string;
    try { real = realpathSync(r.path); } catch { return; }
    if (ignored(real) || !statSync(real).isFile()) return;
    const note = (keep: string) => this.db.prepare('update items set keep = ?, kept_mtime = ? where id = ?').run(keep, st.mtimeMs, r.id);
    if (st.size > this.keep.itemMax) { note('too_large'); return; }
    const dir = join(this.libraryDir(), r.agent);
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const ext = extname(r.path);
    const dest = join(dir, r.id + (/^\.[A-Za-z0-9]{1,8}$/.test(ext) ? ext.toLowerCase() : ''));
    const part = `${dest}.part`;
    try {
      copyFileSync(real, part);
      chmodSync(part, 0o600);
      const size = statSync(part).size;
      if (size > this.keep.itemMax) { unlinkSync(part); note('too_large'); return; }  // it grew while being copied
      renameSync(part, dest);
      this.db.prepare(`update items set keep = 'kept', kept_path = ?, kept_size = ?, kept_at = ?, kept_mtime = ? where id = ?`)
        .run(dest, size, new Date(this.o.now()).toISOString(), st.mtimeMs, r.id);
    } catch { try { unlinkSync(part); } catch { /* none made */ } }
  }

  /** Copy what should be kept, then hold each cap. */
  keepPass() {
    if (!this.keep.enabled) return;
    const rows = this.db.prepare(`select ${KEEP_COLS} from items where made = 1 and attribution = 'agent' and agent != '' and path is not null
      and kind != 'link' and missing = 0`).all() as KeepRow[];
    for (const r of rows) if (this.keepable(r)) { try { this.keepOne(r); } catch { /* next pass */ } }
    this.retain();
  }

  /** Over a cap, clear the oldest unpinned copies (by when the item was made) until under it: each agent's, then the whole library's. */
  retain() {
    const clear = (rows: { id: string; kept_path: string; kept_size: number }[], over: number) => {
      for (const r of rows) {
        if (over <= 0) break;
        try { unlinkSync(r.kept_path); } catch { /* already gone */ }
        this.db.prepare(`update items set keep = 'cleared', kept_path = null, kept_size = null, kept_at = null where id = ?`).run(r.id);
        over -= r.kept_size || 0;
      }
    };
    const oldest = `select id, kept_path, kept_size from items where kept_path is not null and pinned = 0`;
    const order = `order by coalesce(at, first_seen), kept_at, id`;
    const per = this.db.prepare(`select agent, sum(kept_size) bytes from items where kept_path is not null group by agent`).all() as { agent: string; bytes: number }[];
    for (const a of per) {
      if (a.bytes > this.keep.agentCap) clear(this.db.prepare(`${oldest} and agent = ? ${order}`).all(a.agent) as any[], a.bytes - this.keep.agentCap);
    }
    const total = (this.db.prepare(`select coalesce(sum(kept_size), 0) bytes from items where kept_path is not null`).get() as { bytes: number }).bytes;
    if (total > this.keep.totalCap) clear(this.db.prepare(`${oldest} ${order}`).all() as any[], total - this.keep.totalCap);
  }

  /** Mark files that are gone (and back), and link a move: the same agent's newer item with the same name and size. */
  missingPass() {
    const rows = this.db.prepare(`select id, agent, attribution, path, size, missing, moved_to, coalesce(at, first_seen) t from items where path is not null`).all() as
      { id: string; agent: string; attribution: string; path: string; size: number | null; missing: number; moved_to: string | null; t: string }[];
    const now = new Date(this.o.now()).toISOString();
    const gone: typeof rows = [];
    const tx = this.db.transaction(() => {
      for (const r of rows) {
        const st = this.o.stat(r.path);
        const here = !!st && st.isFile;
        if (!here && !r.missing) this.db.prepare('update items set missing = 1, missing_since = ? where id = ?').run(now, r.id);
        if (here && r.missing) this.db.prepare('update items set missing = 0, missing_since = null, moved_to = null where id = ?').run(r.id);
        if (!here) gone.push(r);
      }
      const same = this.db.prepare(`select id, path from items where agent = ? and attribution = 'agent' and id != ? and path is not null and missing = 0
        and size = ? and coalesce(at, first_seen) >= ? order by coalesce(at, first_seen) desc`);
      for (const r of gone) {
        if (r.moved_to || r.attribution !== 'agent' || !r.agent || r.size == null) continue;
        const name = basename(r.path);
        const to = (same.all(r.agent, r.id, r.size, r.t) as { id: string; path: string }[]).find((c) => basename(c.path) === name);
        if (to) this.db.prepare('update items set moved_to = ? where id = ?').run(to.id, r.id);
      }
    });
    tx();
  }

  /** Bytes kept, copies, pins, per agent and in all, with the caps (for the Library tab's usage line). */
  usage() {
    const cur = this.current();
    const agents = (this.db.prepare(`select agent, coalesce(sum(kept_size), 0) bytes, count(kept_path) items, sum(pinned) pinned,
        sum(case when keep = 'too_large' then 1 else 0 end) too_large, sum(case when keep = 'cleared' then 1 else 0 end) cleared
      from items where agent != '' and (kept_path is not null or pinned = 1 or keep is not null) group by agent order by agent`).all() as (KeptCounts & { agent: string })[])
      .map((a) => ({ ...a, retired: !cur.has(String(a.agent)) }));
    const t = this.db.prepare(`select coalesce(sum(kept_size), 0) bytes, count(kept_path) items, coalesce(sum(pinned), 0) pinned,
        coalesce(sum(case when keep = 'too_large' then 1 else 0 end), 0) too_large, coalesce(sum(case when keep = 'cleared' then 1 else 0 end), 0) cleared
      from items`).get() as KeptCounts;
    return { enabled: this.keep.enabled, caps: { item_max_bytes: this.keep.itemMax, agent_cap_bytes: this.keep.agentCap, total_cap_bytes: this.keep.totalCap },
             total: t, agents };
  }

  /** What the content route serves for an item: its file while it exists, else its kept copy (an indexed, never-refused
   *  item only; the kept copy only from inside the library folder). */
  contentOf(id: string): { path: string; original: string; name: string; title: string; kept: boolean } | null {
    const r = this.db.prepare(`select path, title, kept_path from items where id = ? and path is not null`).get(id) as { path: string; title: string; kept_path: string | null } | undefined;
    if (!r || ignored(r.path)) return null;
    const st = this.o.stat(r.path);
    if (st && st.isFile) return { path: r.path, original: r.path, name: basename(r.path), title: r.title, kept: false };
    const k = r.kept_path;
    if (k && k.startsWith(this.libraryDir() + sep) && !k.includes(`${sep}..`) && realStat(k)?.isFile) {
      return { path: k, original: r.path, name: basename(r.path), title: r.title, kept: true };
    }
    return null;
  }

  /** One item's file, when it is a file the index holds and it still exists (the content route opens nothing else). */
  fileOf(id: string): { path: string; title: string } | null {
    const r = this.db.prepare(`select path, title from items where id = ? and path is not null`).get(id) as { path: string; title: string } | undefined;
    if (!r || ignored(r.path)) return null;
    const st = this.o.stat(r.path);
    return st && st.isFile ? r : null;
  }
}

/** The registry's agents and the folder each works in. */
export function registryAgents(dataDir: string): { id: string; cwd?: string }[] {
  try {
    const r = JSON.parse(readFileSync(join(dataDir, 'registry.json'), 'utf-8'));
    const a = r.agents ?? r;
    return Object.entries(a).map(([id, v]: [string, any]) => ({ id, ...(typeof v?.cwd === 'string' ? { cwd: v.cwd } : {}) }));
  } catch { return []; }
}

let running: LibraryIndex | null = null;
export function libraryIndex(): LibraryIndex | null { return running; }

/** Start the index in the API: backfill once (logged with how long it took), then a pass every 60 s. Never throws. */
export function startLibraryIndex(dataDir: string, log: (m: string) => void = console.log): void {
  try {
    running = new LibraryIndex({ dataDir, agents: () => registryAgents(dataDir), keep: keepLimitsFromEnv() });
  } catch (e) { log(`[library] not started: ${String(e)}`); return; }
  setTimeout(() => {
    try {
      const r = running!.backfillIfNeeded();
      if (r) log(`[library] backfill ${BACKFILL_DAYS} days: ${r.logs} logs in ${r.ms} ms`);
    } catch (e) { log(`[library] backfill failed: ${String(e)}`); }
    setInterval(() => { try { running!.pass(1); } catch (e) { log(`[library] pass failed: ${String(e)}`); } }, 60_000).unref();
  }, 5_000).unref();
}
