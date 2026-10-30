/**
 * library-attribution.ts — what an agent made, mentioned or posted, read from its own transcript (parity 4g.1).
 *
 * Pure: lines in, events and items out; the file system comes in as a `stat` function, so every rule is tested on
 * fixtures. library-index.ts does the reading, the storing and the project-folder pass.
 *
 * Made (counts toward an agent's Library and a project's "made by"):
 *   - Write / NotebookEdit / Edit / MultiEdit that succeeded (Write's result says created or updated);
 *   - output paths in its Bash commands (redirects, tee, -o/--output/--screenshot, a script's `path: '…'`, cp/mv
 *     targets) and paths its Bash results print, when the file exists and changed during that call;
 *   - files the operator uploaded into its chat (the upload path in the operator's message);
 *   - files that changed inside a project folder while one of its tool calls was running and no other agent's was
 *     (library-index.ts; overlapping calls make the file "unclear" (Unclear who made it), none "unattributed").
 * Mentioned (never counts as made): files it showed (Read of an image or PDF) or named in a reply, that exist and it
 *   did not make. Scratch and /tmp files count only when it showed or named them; they are never "made" from a
 *   command alone.
 * Links: http(s) URLs it wrote in a reply.
 * Never guess: a path that does not exist, or did not change during the call that names it, is not made.
 */
import { basename, extname, isAbsolute, join, normalize } from 'path';

export type ItemKind = 'file' | 'media' | 'link';
export type ItemAction = 'created' | 'edited' | 'uploaded' | 'deployed' | 'mentioned' | 'posted';

export interface LibraryItem {
  agent: string;
  kind: ItemKind;
  action: ItemAction;
  /** Counts as made: created, edited, uploaded or deployed. */
  made: boolean;
  /** The file's name, or a short description of its content when one is known (an image's alt text from a
   *  reply; library-index.ts adds a markdown note's first heading). Never a command's description. */
  title: string;
  /** The words that came with it (a Bash command's description), shown under the title. */
  subtitle?: string;
  path?: string;
  url?: string;
  /** How it was found: write, edit, bash, upload, reply, read, link. */
  source: string;
  session?: string;
  turn?: string;
  /** When (ISO-8601). */
  at?: string;
  size?: number;
}

export interface Turn { session?: string; turn: string; start: number; end: number; cwd?: string }
/** One tool call's own window: from its tool_use to its tool_result (for a command run in the background, to the
 *  notice that it finished). Files that changed inside it were changed by this call, or by something else running
 *  then; the project-folder pass decides which. */
export interface Call { id: string; tool: string; start: number; end: number; cwd?: string; session?: string; readOnly?: boolean }

/** Tools that can change a file. Any other tool (a Read, a search, a fetch) is a read-only call. */
export const WRITING_TOOLS = new Set(['Bash', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Agent', 'Task']);

/** Programs that only read. A command made of nothing else (no redirect, no tee, no output flag, no in-place edit) is
 *  a read-only call: it gets no credit for a file changing while it ran. */
const READ_ONLY_PROGRAMS = new Set(['ls', 'stat', 'du', 'df', 'identify', 'file', 'cat', 'head', 'tail', 'wc', 'grep', 'egrep', 'rg',
  'find', 'echo', 'printf', 'pwd', 'which', 'date', 'sleep', 'test', '[', 'md5sum', 'sha256sum', 'sha1sum', 'jq', 'sort', 'uniq',
  'cut', 'tr', 'awk', 'sed', 'tree', 'readlink', 'realpath', 'basename', 'dirname', 'cd', 'true', 'diff', 'cmp', 'xxd', 'od',
  'exiftool', 'pdfinfo', 'ffprobe', 'column', 'nl', 'less', 'more', 'whoami', 'hostname', 'uname', 'env', 'ps', 'pgrep', 'id']);

export function isReadOnlyCommand(command: string): boolean {
  if (/(?<![<>&\d])>{1,2}(?!&)|\btee\b|\s-o\s|--output|--out\b|--outfile|--screenshot|\bsed\s+(?:-\w*\s+)*-i|-delete\b|-exec\b/.test(command)) return false;
  const segments = command.split(/&&|\|\||;|\||\n/).map((s) => s.trim()).filter(Boolean);
  if (!segments.length) return false;
  return segments.every((seg) => {
    const words = seg.replace(/^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+/, '').split(/\s+/);
    const prog = (words[0] || '').replace(/^.*\//, '');
    if (prog === 'git') return /^(status|log|diff|show|rev-parse|branch|ls-files|blame)$/.test(words[1] || '');
    return READ_ONLY_PROGRAMS.has(prog);
  });
}

export type Stat = (path: string) => { mtimeMs: number; birthtimeMs?: number; size: number; isFile: boolean } | null;

type Ev =
  | { t: 'write'; tool: string; path: string; ok: boolean; created: boolean | null; ts: number; turn: string }
  | { t: 'bash'; command: string; description: string; cwd?: string; result: string; ok: boolean; ts: number; resultTs: number; turn: string }
  | { t: 'read'; path: string; ts: number; turn: string }
  | { t: 'reply'; text: string; ts: number; turn: string }
  | { t: 'operator'; text: string; ts: number; turn: string };

const MEDIA_EXT = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.avif', '.heic', '.mp4', '.mov', '.webm', '.mkv', '.mp3', '.wav', '.m4a', '.ogg', '.flac']);
const SHOWN_EXT = new Set([...MEDIA_EXT, '.pdf']);
export const kindOf = (path: string): ItemKind => (MEDIA_EXT.has(extname(path).toLowerCase()) ? 'media' : 'file');

/** Scratch space: a file here is the agent's working copy, not something it made for the operator, unless it
 *  showed or named it. */
export const isScratch = (p: string) => /^\/tmp\//.test(p) || /^\/var\/tmp\//.test(p) || /\/scratchpad\//.test(p) || /^\/dev\//.test(p) || /^\/proc\//.test(p);
/** Never items: the harness's own task output, caches, version control, dependencies; the install's own records
 *  (agents' memory notes, prompts, call records, backups: everything under <data dir>/state but uploads); and
 *  anything that looks like a secret or a key (never even its name). */
const IGNORED = /(^\/dev\/|^\/proc\/|\/\.git\/|\/node_modules\/|\/\.cache\/|\/__pycache__\/|\/tasks\/[^/]+\.output$|\/\.claude\/|\/\.orchestra\/memory\/|\/\.orchestra\/state\/(?!uploads\/)|\/\.ssh\/|\/\.gnupg\/|\/\.config\/)/;
const SECRET = /(^|\/)(\.env[^/]*|[^/]*(secret|token|credential|password|passwd)[^/]*|[^/]*\.(pem|key|p12|pfx|kdbx))$/i;
export const ignored = (p: string) => IGNORED.test(p) || SECRET.test(p);

const tsOf = (o: any): number => { const n = Date.parse(o?.timestamp ?? ''); return Number.isFinite(n) ? n : NaN; };

function blocksOf(msg: any): any[] {
  const c = msg?.content;
  if (typeof c === 'string') return [{ type: 'text', text: c }];
  return Array.isArray(c) ? c : [];
}

function resultText(c: any): string {
  if (typeof c === 'string') return c;
  if (Array.isArray(c)) return c.map((x: any) => (x && typeof x.text === 'string' ? x.text : '')).join('\n');
  return '';
}

/** One Claude Code log, as the events these rules read and the agent's turns (from one opener to the next: the time
 *  it was working). Sidechain (subagent) lines are part of the turn's time but their tools are the subagent's. */
export function parseClaudeLines(lines: string[], session?: string): { events: Ev[]; turns: Turn[]; calls: Call[] } {
  const events: Ev[] = [];
  const turns: Turn[] = [];
  const calls = new Map<string, Call>();
  const background = new Map<string, string>();           // background call id -> its turn, until its notice
  // A background command's notice (<task-notification> with its <tool-use-id>) ends that call's window.
  const noticeEnds = (text: string, ts: number) => {
    const id = text.match(/<tool-use-id>([^<]+)<\/tool-use-id>/)?.[1];
    const c = id ? calls.get(id) : undefined;
    if (c && Number.isFinite(ts)) { c.end = Math.max(c.end, ts); background.delete(id!); }
  };
  const uses = new Map<string, { tool: string; input: any; ts: number; cwd?: string; turn: string }>();
  let cur: Turn | null = null;
  for (const raw of lines) {
    if (!raw.trim()) continue;
    let o: any;
    try { o = JSON.parse(raw); } catch { continue; }
    if (o.type === 'attachment' && typeof o.attachment?.prompt === 'string') { noticeEnds(o.attachment.prompt, tsOf(o)); continue; }
    if (o.type !== 'user' && o.type !== 'assistant') continue;
    const ts = tsOf(o);
    const blocks = blocksOf(o.message);
    const opener = o.type === 'user' && !o.isSidechain && !o.isMeta && blocks.some((b) => b?.type === 'text' && String(b.text || '').trim());
    if (opener || !cur) {
      cur = { session, turn: String(o.uuid || `t${turns.length}`), start: Number.isFinite(ts) ? ts : 0, end: Number.isFinite(ts) ? ts : 0, cwd: o.cwd };
      turns.push(cur);
    }
    if (Number.isFinite(ts)) { cur.end = Math.max(cur.end, ts); if (!cur.start) cur.start = ts; }
    if (o.isSidechain) continue;
    for (const b of blocks) {
      if (!b || typeof b !== 'object') continue;
      if (b.type === 'tool_use' && o.type === 'assistant') {
        uses.set(String(b.id), { tool: String(b.name || ''), input: b.input || {}, ts, cwd: o.cwd, turn: cur.turn });
      } else if (b.type === 'tool_result') {
        const u = uses.get(String(b.tool_use_id));
        if (!u) continue;
        if (Number.isFinite(u.ts)) {
          const readOnly = !WRITING_TOOLS.has(u.tool) || (u.tool === 'Bash' && typeof u.input.command === 'string' && isReadOnlyCommand(u.input.command));
          calls.set(String(b.tool_use_id), { id: String(b.tool_use_id), tool: u.tool, start: u.ts, end: Number.isFinite(ts) ? Math.max(ts, u.ts) : u.ts, cwd: u.cwd, session, readOnly });
          // A command run in the background keeps running after this result: its window stays open until its
          // finished notice, or (none in view) until the end of its turn.
          if (u.input?.run_in_background === true || /^\s*Async agent launched/i.test(resultText(b.content))) background.set(String(b.tool_use_id), u.turn);
        }
        const text = resultText(b.content);
        const ok = !b.is_error && !/^<tool_use_error>/.test(text);
        const tool = u.tool;
        const fp = typeof u.input.file_path === 'string' ? u.input.file_path : typeof u.input.notebook_path === 'string' ? u.input.notebook_path : '';
        if ((tool === 'Write' || tool === 'Edit' || tool === 'MultiEdit' || tool === 'NotebookEdit') && fp) {
          const created = tool === 'Write' ? (/created successfully/i.test(text) ? true : /updated|overwrit/i.test(text) ? false : null) : false;
          events.push({ t: 'write', tool, path: fp, ok, created, ts: u.ts, turn: u.turn });
        } else if (tool === 'Bash' && typeof u.input.command === 'string') {
          events.push({ t: 'bash', command: u.input.command, description: String(u.input.description || ''), cwd: u.cwd, result: text, ok,
                        ts: u.ts, resultTs: Number.isFinite(ts) ? ts : u.ts, turn: u.turn });
        } else if (tool === 'Read' && fp && ok) {
          events.push({ t: 'read', path: fp, ts: u.ts, turn: u.turn });
        }
      } else if (b.type === 'text' && typeof b.text === 'string' && b.text.trim()) {
        if (o.type === 'user' && b.text.includes('<task-notification>')) noticeEnds(b.text, ts);
        if (o.type === 'assistant') events.push({ t: 'reply', text: b.text, ts, turn: cur.turn });
        else if (opener) events.push({ t: 'operator', text: b.text, ts, turn: cur.turn });
      }
    }
  }
  for (const [id, turn] of background) {
    const c = calls.get(id); const t = turns.find((x) => x.turn === turn);
    if (c && t) c.end = Math.max(c.end, t.end);
  }
  return { events, turns, calls: [...calls.values()] };
}

const PATH_CHARS = String.raw`[\w.~@%+,=:/-]+`;
const unquote = (s: string) => s.replace(/^['"]|['"]$/g, '');

/** An absolute or ~ path (or one relative to `cwd`), normalised; null for anything that is not a plain path. */
export function resolveIn(p: string, cwd?: string): string | null {
  let s = unquote(p.trim());
  if (!s || s.startsWith('$') || s.includes('*') || s === '-' || /^\d+$/.test(s) || s.startsWith('&')) return null;
  if (s.startsWith('~/')) s = join(process.env.HOME || '/root', s.slice(2));
  if (!isAbsolute(s)) { if (!cwd) return null; s = join(cwd, s); }
  return normalize(s).replace(/(.)\/+$/, '$1');
}

/** Where a Bash command writes: redirects, tee, output flags, a script's `path: '…'` (a screenshot), cp/mv
 *  targets. A leading `cd <dir> &&` moves where relative paths start. */
export function bashOutputPaths(command: string, cwd?: string): string[] {
  let here = cwd;
  const lead = command.match(/^\s*cd\s+(['"]?)([^'"&;|]+)\1\s*(?:&&|;)/);
  if (lead) here = resolveIn(lead[2].trim(), cwd) ?? here;
  const out = new Set<string>();
  const add = (p: string | undefined) => { if (!p) return; const r = resolveIn(p, here); if (r) out.add(r); };
  const patterns: RegExp[] = [
    new RegExp(String.raw`(?<![<>&\d])>{1,2}\s*(['"]?)(${PATH_CHARS})\1`, 'g'),
    new RegExp(String.raw`\btee\s+(?:-a\s+)?(['"]?)(${PATH_CHARS})\1`, 'g'),
    new RegExp(String.raw`(?:\s-o|--output|--out|--outfile|--output-file|--screenshot|--save|--path)(?:\s+|=)(['"]?)(${PATH_CHARS})\1`, 'g'),
    new RegExp(String.raw`\bpath\s*[:=]\s*(['"\x60])(${PATH_CHARS})\1`, 'g'),
  ];
  for (const re of patterns) for (const m of command.matchAll(re)) add(m[2]);
  for (const m of command.matchAll(new RegExp(String.raw`\b(?:cp|mv|install)\s+(?:-\w+\s+)*(?:${PATH_CHARS}\s+)+(['"]?)(${PATH_CHARS})\1(?=\s*(?:$|&&|;|\||\n))`, 'gm'))) add(m[2]);
  return [...out].filter((p) => !ignored(p) && p !== '/dev/null');
}

/** Folders a Bash command names as where its output goes: OUT=/…, OUTPUT_DIR=…, --out-dir/--output-dir, mkdir -p.
 *  A file that changed inside one of these during that very call is the command's output. */
export function bashOutputDirs(command: string, cwd?: string): string[] {
  const out = new Set<string>();
  const add = (p: string | undefined) => { if (!p) return; const r = resolveIn(p, cwd); if (r && !ignored(r + '/x')) out.add(r); };
  for (const m of command.matchAll(new RegExp(String.raw`\b[A-Z_]*(?:OUT|DIR|DEST)[A-Z_]*=(['"]?)(${PATH_CHARS})\1`, 'g'))) add(m[2]);
  for (const m of command.matchAll(new RegExp(String.raw`--(?:out|output)-?dir(?:\s+|=)(['"]?)(${PATH_CHARS})\1`, 'g'))) add(m[2]);
  for (const m of command.matchAll(new RegExp(String.raw`\bmkdir\s+(?:-\w+\s+)*(['"]?)(${PATH_CHARS})\1`, 'g'))) add(m[2]);
  return [...out];
}

/** Absolute (or ~) file paths with an extension in free text: a reply, a command's output. */
export function pathsInText(text: string): string[] {
  const out = new Set<string>();
  for (const m of text.matchAll(/(?:^|[\s(`"'[\]<>:=])((?:~\/|\/(?!\/))[\w.@%+~-]*(?:\/[\w.@%+,~-]+)*\.[A-Za-z0-9]{1,8})(?=$|[\s)`"',;:\]<>!?]|\.(?:\s|$))/gm)) {
    const r = resolveIn(m[1]);
    if (r && !ignored(r)) out.add(r);
  }
  return [...out];
}

/** http(s) links in a reply, with the words a markdown link gives them. */
export function linksInText(text: string): { url: string; label?: string }[] {
  const seen = new Map<string, string | undefined>();
  for (const m of text.matchAll(/\[([^\]\n]{1,120})\]\((https?:\/\/[^\s)]+)\)/g)) seen.set(m[2].replace(/[.,;:]+$/, ''), m[1]);
  for (const m of text.matchAll(/https?:\/\/[^\s)<>"'`\]]+/g)) {
    const u = m[0].replace(/[.,;:!?]+$/, '');
    if (!seen.has(u)) seen.set(u, undefined);
  }
  return [...seen].map(([url, label]) => ({ url, ...(label ? { label } : {}) }));
}

const MAX_TITLE = 120;
const short = (s: string) => {
  const t = s.replace(/\s+/g, ' ').trim();
  if (t.length <= MAX_TITLE) return t;
  const cut = t.slice(0, MAX_TITLE - 1);
  return `${cut.slice(0, cut.lastIndexOf(' ') > 60 ? cut.lastIndexOf(' ') : cut.length)}…`;
};

/** The title: a short description of the content when one is known (an image's alt text), else the file's name
 *  without its folder. */
export function titleFor(path: string, description?: string): string {
  const d = short(description || '');
  return d || basename(path);
}

/** The line under the title: the words that came with it (a Bash command's description), unless they only repeat
 *  the file's name or its path. */
export function subtitleFor(path: string, words?: string): string | undefined {
  const w = short(words || '');
  if (!w || w === basename(path) || w === path || path.endsWith(`/${w}`)) return undefined;
  return w;
}

/** Words that strip to plain text: emphasis, code marks, links and images keep only their words. */
function plainText(s: string): string {
  return s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/<[^>]+>/g, '')
    .replace(/(\*\*|\*|`|~~)(?=\S)(.*?\S)\1/g, '$2').replace(/(?<!\w)(__|_)(?=\S)(.*?\S)\1(?!\w)/g, '$2')
    .replace(/\s+/g, ' ').trim();
}

/** A markdown note's title: its front matter's `title:`, else its first heading (outside code fences). */
export function markdownTitle(text: string): string | undefined {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  let i = 0;
  if (lines[0]?.trim() === '---') {
    for (i = 1; i < lines.length && lines[i].trim() !== '---'; i++) {
      const m = lines[i].match(/^title:\s*(['"]?)(.+?)\1\s*$/i);
      if (m && plainText(m[2])) return short(plainText(m[2]));
    }
    i++;
  }
  let fence: string | null = null;
  for (; i < lines.length; i++) {
    const l = lines[i];
    const f = l.match(/^\s{0,3}(`{3,}|~{3,})/);
    if (f) { if (!fence) fence = f[1][0]; else if (f[1][0] === fence) fence = null; continue; }
    if (fence) continue;
    const atx = l.match(/^\s{0,3}#{1,6}\s+(.*?)(?:\s+#+)?\s*$/);
    if (atx) { const t = plainText(atx[1]); if (t) return short(t); continue; }
    // a setext heading: a line of text underlined with === or ---
    const next = lines[i + 1];
    if (l.trim() && next && /^\s{0,3}(=+|-+)\s*$/.test(next) && !/^\s*[-*+>|]/.test(l)) { const t = plainText(l); if (t) return short(t); }
  }
  return undefined;
}

/** Images a reply shows with words: `![alt](path)`. The alt text describes the picture. */
export function altTextsIn(text: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of text.matchAll(/!\[([^\]\n]{1,200})\]\(<?((?:~\/|\/)[^\s)>]+)>?(?:\s+"[^"]*")?\)/g)) {
    const p = resolveIn(m[2]);
    const alt = plainText(m[1]);
    if (p && alt && !ignored(p)) out.set(p, alt);
  }
  return out;
}

/** Did the file change during [from, to] (with a little slack for clocks and buffered writes)? */
const changedDuring = (mtimeMs: number, from: number, to: number, slackMs = 5_000) => mtimeMs >= from - slackMs && mtimeMs <= to + slackMs;

const UPLOAD = /\/state\/uploads\/[\w.@%+-]+\.[A-Za-z0-9]{1,8}/;

/** Every item one agent's log yields. */
export function itemsFromEvents(agent: string, events: Ev[], stat: Stat, session?: string, scratch: (p: string) => boolean = isScratch,
                                list: (dir: string) => string[] = () => []): LibraryItem[] {
  const made = new Map<string, LibraryItem>();
  const named = new Map<string, LibraryItem>();
  const links = new Map<string, LibraryItem>();
  const shownOrNamed = new Set<string>();
  const alt = new Map<string, string>();           // a picture's words, from a reply that shows it
  const iso = (ms: number) => (Number.isFinite(ms) ? new Date(ms).toISOString() : undefined);
  for (const e of events) {
    if (e.t === 'read' && SHOWN_EXT.has(extname(e.path).toLowerCase())) shownOrNamed.add(normalize(e.path));
    if (e.t === 'reply') {
      for (const p of pathsInText(e.text)) shownOrNamed.add(p);
      for (const [p, a] of altTextsIn(e.text)) { shownOrNamed.add(p); if (!alt.has(p)) alt.set(p, a); }
    }
  }
  const withSubtitle = (sub: string | undefined) => (sub ? { subtitle: sub } : {});
  const titleOf = (p: string) => titleFor(p, kindOf(p) === 'media' ? alt.get(p) : undefined);
  const rank = (s: string) => (s === 'write' || s === 'edit' || s === 'upload' ? 2 : 1);
  const putMade = (it: LibraryItem) => {
    const prev = made.get(it.path!);
    // The tool that wrote it (Write/Edit) outranks a command that only printed its path; then created over edited;
    // the latest time is kept.
    if (!prev || rank(it.source) > rank(prev.source)) { made.set(it.path!, it); return; }
    if (rank(it.source) < rank(prev.source)) return;
    const later = it.at && (!prev.at || it.at > prev.at) ? it.at : prev.at;
    if (prev.action === 'edited' && it.action === 'created') made.set(it.path!, { ...it, at: later });
    else prev.at = later;
  };
  for (const e of events) {
    if (e.t === 'write') {
      if (!e.ok || ignored(e.path)) continue;
      const p = normalize(e.path);
      if (scratch(p) && !shownOrNamed.has(p)) continue;
      const st = stat(p);
      if (!st || !st.isFile) continue;
      putMade({ agent, kind: kindOf(p), action: e.created === true ? 'created' : e.tool === 'Write' && e.created === null ? 'created' : 'edited',
                made: true, title: titleOf(p), path: p, source: e.tool === 'Write' ? 'write' : 'edit', session, turn: e.turn, at: iso(e.ts), size: st.size });
    } else if (e.t === 'bash') {
      if (!e.ok) continue;
      const fromCmd = bashOutputPaths(e.command, e.cwd);
      const fromOut = pathsInText(e.result);
      const fromDirs = bashOutputDirs(e.command, e.cwd).flatMap((d) => list(d)).filter((p) => !ignored(p));
      const named = new Set([...fromCmd, ...fromDirs]);
      for (const p of new Set([...fromCmd, ...fromOut, ...fromDirs])) {
        if (scratch(p) && !shownOrNamed.has(p)) continue;
        const st = stat(p);
        // A path the command itself names may land a moment after the call returns (buffered writes); one it only
        // printed (a prompt, a listing) must have changed strictly during the call.
        if (!st || !st.isFile || !changedDuring(st.mtimeMs, e.ts, e.resultTs, named.has(p) ? 5_000 : 0)) continue;
        const created = st.birthtimeMs && st.birthtimeMs > 0 ? changedDuring(st.birthtimeMs, e.ts, e.resultTs) : true;
        putMade({ agent, kind: kindOf(p), action: created ? 'created' : 'edited', made: true,
                  title: titleOf(p), ...withSubtitle(fromCmd.length === 1 && fromCmd[0] === p ? subtitleFor(p, e.description) : undefined),
                  path: p, source: 'bash',
                  session, turn: e.turn, at: iso(e.resultTs), size: st.size });
      }
    } else if (e.t === 'operator') {
      for (const p of pathsInText(e.text)) {
        if (!UPLOAD.test(p)) continue;
        const st = stat(p);
        if (!st || !st.isFile) continue;
        putMade({ agent, kind: kindOf(p), action: 'uploaded', made: true, title: titleOf(p), path: p, source: 'upload', session, turn: e.turn, at: iso(e.ts), size: st.size });
      }
    }
  }
  for (const e of events) {
    const mention = (p: string, source: string) => {
      if (made.has(p) || named.has(p)) return;
      const st = stat(p);
      if (!st || !st.isFile) return;
      named.set(p, { agent, kind: kindOf(p), action: 'mentioned', made: false, title: titleOf(p), path: p, source, session, turn: e.turn, at: iso(e.ts), size: st.size });
    };
    if (e.t === 'read' && SHOWN_EXT.has(extname(e.path).toLowerCase())) mention(normalize(e.path), 'read');
    if (e.t === 'reply') {
      for (const p of pathsInText(e.text)) mention(p, 'reply');
      for (const l of linksInText(e.text)) {
        if (links.has(l.url)) continue;
        links.set(l.url, { agent, kind: 'link', action: /\/pull\/\d+|vercel\.app|netlify\.app|pages\.dev|github\.io|fly\.dev|onrender\.com/.test(l.url) ? 'deployed' : 'posted',
                           made: false, title: l.label || l.url.replace(/^https?:\/\//, '').slice(0, 120), url: l.url, source: 'link', session, turn: e.turn, at: iso(e.ts) });
      }
    }
  }
  // A link to something it deployed counts as made; any other link is posted, not made.
  for (const l of links.values()) if (l.action === 'deployed') l.made = true;
  return [...made.values(), ...named.values(), ...links.values()];
}

export interface AgentWindow { agent: string; start: number; end: number; cwd?: string; read_only?: number | boolean | null }

/** Who changed a project-folder file: the agents with a writing call running when it changed (each call's own
 *  window, not the whole turn). One agent: theirs; several: "unclear" (Unclear who made it), naming them. With only
 *  read-only calls running (ls, stat, a Read...): "unclear", naming those agents, since none of them can have written
 *  it but someone did. No call at all: unattributed. Never a guess. */
export function ownersOf(mtimeMs: number, windows: AgentWindow[], slackMs = 2_000): { attribution: 'agent' | 'unclear' | 'unattributed'; agents: string[] } {
  const live = windows.filter((w) => mtimeMs >= w.start - slackMs && mtimeMs <= w.end + slackMs);
  const writers = [...new Set(live.filter((w) => !w.read_only).map((w) => w.agent))].sort();
  if (writers.length === 1) return { attribution: 'agent', agents: writers };
  if (writers.length > 1) return { attribution: 'unclear', agents: writers };
  const readers = [...new Set(live.map((w) => w.agent))].sort();
  return { attribution: readers.length ? 'unclear' : 'unattributed', agents: readers };
}
