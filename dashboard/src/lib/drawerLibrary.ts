/**
 * The drawer's Library (components/arturo/DrawerLibrary.tsx): what an agent made, read from the library index's
 * routes (GET /api/library/...). Pure helpers here, tested without a browser: which group an item falls in (Files,
 * Media, Links; Mentioned and "Unclear who made it" kept apart from what it made), Today / This week / Older,
 * a readable title, the unclear line with its candidates, and paging 50 at a time.
 */
import type { FileSource } from './fileWindow.ts';
import type { ProjectFileContent } from './api.ts';

/** One row of GET /api/library/items. `agents` lists the candidates (comma-separated) when nobody is sure. */
export interface LibraryItem {
  id: string;
  agent: string;
  agents: string;
  attribution: 'agent' | 'unclear' | 'unattributed' | string;
  project: string | null;
  kind: 'file' | 'media' | 'link' | string;
  action: string;
  made: number | boolean;
  /** The file's name, or a short description of its content (a note's first heading, a picture's alt text). */
  title: string;
  /** The words a Bash command came with, shown under the title (null: none; absent: an index from before). */
  subtitle?: string | null;
  path: string | null;
  url: string | null;
  source: string;
  at: string | null;
  size: number | null;
}

export type LibraryGroup = 'files' | 'media' | 'links' | 'mentioned' | 'unclear';
export const LIBRARY_GROUPS: LibraryGroup[] = ['files', 'media', 'links', 'mentioned', 'unclear'];
export const GROUP_LABEL: Record<LibraryGroup, string> = {
  files: 'Files', media: 'Media', links: 'Links', mentioned: 'Mentioned', unclear: 'Unclear who made it',
};
/** The chip's shorter name for the same group. */
export const CHIP_LABEL: Record<LibraryGroup, string> = { ...GROUP_LABEL, unclear: 'Unclear' };

/** Views 50 at a time (the index's own page size); the All view shows the newest few of each group. */
export const PAGE = 50;
export const PREVIEW = 5;

export const candidatesOf = (it: Pick<LibraryItem, 'agents'>): string[] =>
  (it.agents || '').split(',').map((s) => s.trim()).filter(Boolean);

/** Where an item goes in this agent's Library, or null when it is not this agent's at all. Only what it created,
 *  edited, uploaded or deployed counts as made; a file it only showed or named is Mentioned; a file the index could
 *  not settle is "Unclear" for each agent it names. */
export function groupOf(it: LibraryItem, agentId: string): LibraryGroup | null {
  if (it.attribution !== 'agent') return it.attribution === 'unclear' && candidatesOf(it).includes(agentId) ? 'unclear' : null;
  if (it.agent !== agentId) return null;
  if (it.kind === 'link') return 'links';
  if (it.action === 'mentioned' || !it.made) return 'mentioned';
  return it.kind === 'media' ? 'media' : 'files';
}

export function groupItems(items: LibraryItem[], agentId: string): Record<LibraryGroup, LibraryItem[]> {
  const out: Record<LibraryGroup, LibraryItem[]> = { files: [], media: [], links: [], mentioned: [], unclear: [] };
  for (const it of items) { const g = groupOf(it, agentId); if (g) out[g].push(it); }
  return out;
}

/** The items query for one of this agent's groups (unclear items are read per project and filtered here, since
 *  the index files them under no agent). */
export function groupQuery(group: Exclude<LibraryGroup, 'unclear'>, agentId: string, offset = 0, limit = PAGE): string {
  const p = new URLSearchParams({ agent: agentId });
  if (group === 'files') { p.set('kind', 'file'); p.set('made', '1'); }
  else if (group === 'media') { p.set('kind', 'media'); p.set('made', '1'); }
  else if (group === 'links') p.set('kind', 'link');
  else p.set('action', 'mentioned');
  p.set('limit', String(limit));
  if (offset) p.set('offset', String(offset));
  return p.toString();
}

/** The projects the index has unclear files in, from GET /api/library/summary. */
export function unclearProjects(summary: { shared_or_unattributed?: { project: string | null; attribution: string; n: number }[] } | null): string[] {
  const rows = summary?.shared_or_unattributed ?? [];
  return [...new Set(rows.filter((r) => r.attribution === 'unclear' && r.project && r.n > 0).map((r) => r.project as string))];
}

/** A page appended to what is shown: no item twice, and whether a full page means there may be more. */
export function mergePage(prev: LibraryItem[], page: LibraryItem[], limit = PAGE): { items: LibraryItem[]; more: boolean } {
  const seen = new Set(prev.map((i) => i.id));
  return { items: [...prev, ...page.filter((i) => !seen.has(i.id))], more: page.length >= limit };
}

export type Bucket = 'today' | 'week' | 'older';
export const BUCKET_LABEL: Record<Bucket, string> = { today: 'Today', week: 'This week', older: 'Older' };

const startOfDay = (ms: number) => { const d = new Date(ms); d.setHours(0, 0, 0, 0); return d.getTime(); };

/** Today (this calendar day), This week (the six days before it), else Older. An item with no time is Older. */
export function bucketOf(at: string | null, now = Date.now()): Bucket {
  const t = at ? Date.parse(at) : NaN;
  if (!Number.isFinite(t)) return 'older';
  const today = startOfDay(now);
  if (t >= today) return 'today';
  return t >= today - 6 * 86_400_000 ? 'week' : 'older';
}

/** Items under their day headings, newest first, empty headings left out. */
export function byBucket(items: LibraryItem[], now = Date.now()): [Bucket, LibraryItem[]][] {
  const sorted = items.slice().sort((a, b) => (b.at ?? '').localeCompare(a.at ?? ''));
  const out: Record<Bucket, LibraryItem[]> = { today: [], week: [], older: [] };
  for (const it of sorted) out[bucketOf(it.at, now)].push(it);
  return (['today', 'week', 'older'] as Bucket[]).filter((b) => out[b].length).map((b) => [b, out[b]]);
}

/** The row's date: the time today, the weekday this week, else the day ("Oct 24"). */
export function whenLabel(at: string | null, now = Date.now()): string {
  const t = at ? Date.parse(at) : NaN;
  if (!Number.isFinite(t)) return '';
  const b = bucketOf(at, now);
  const d = new Date(t);
  if (b === 'today') return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (b === 'week') return d.toLocaleDateString([], { weekday: 'short' });
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export const baseName = (p: string) => p.slice(p.replace(/\/+$/, '').lastIndexOf('/') + 1);

/** Whether the index's title is only the file's path or name (no description of its own). */
function titleIsPath(title: string, path: string | null): boolean {
  const t = title.trim();
  if (!t) return true;
  if (path && (t === path || path.endsWith(`/${t}`) || t === baseName(path))) return true;
  return !/\s/.test(t) && (t.includes('/') || /\.[A-Za-z0-9]{1,6}$/.test(t));
}

type Titled = Pick<LibraryItem, 'title' | 'path' | 'url' | 'kind'> & Partial<Pick<LibraryItem, 'subtitle' | 'source'>>;

/** An index from before titles were names (no `subtitle` field) titled a Bash-made file with the command's words. */
const oldBashTitle = (it: Titled) => it.subtitle === undefined && it.source === 'bash' && !titleIsPath(it.title ?? '', it.path);

/** The row's title: the file's name, or the index's description of its content (a note's heading); never a
 *  command's words. A link with no page title shows its host and path. */
export function itemTitle(it: Titled): string {
  if (it.kind === 'link') {
    if (it.title?.trim() && it.title.trim() !== it.url) return it.title.trim();
    try { const u = new URL(it.url ?? ''); return `${u.host}${u.pathname === '/' ? '' : u.pathname}`; } catch { return it.url || 'Link'; }
  }
  if (!titleIsPath(it.title ?? '', it.path) && !oldBashTitle(it)) return it.title.trim();
  return baseName(it.path || it.title || 'File');
}

/** The line under the title: the command's words, when it came with some (an older index: its title held them). */
export function itemSubtitle(it: Titled): string {
  if (it.kind === 'link') return '';
  if (oldBashTitle(it)) return it.title.trim();
  return (it.subtitle ?? '').trim();
}

/** A file's path inside its project ("research/raw/x.png"), else its full path. */
export function shortPath(it: Pick<LibraryItem, 'path' | 'project'>): string {
  const p = it.path ?? '';
  if (it.project) {
    const k = p.indexOf(`/projects/${it.project}/`);
    if (k >= 0) return p.slice(k + `/projects/${it.project}/`.length);
  }
  return p;
}

/** A link's host, for the line under its title. */
export function hostOf(url: string | null): string {
  try { return new URL(url ?? '').host; } catch { return ''; }
}

/** Only web links open from the Library. */
export const isWebLink = (url: string | null): boolean => /^https?:\/\//i.test(url ?? '');

const joinNames = (names: string[]) => names.length <= 1 ? names.join('')
  : `${names.slice(0, -1).join(', ')} or ${names[names.length - 1]}`;

/** Who else could have made an unclear file: the other candidates, this agent last ("dev-demo or this agent").
 *  Retired agents are counted, not named, unless asked for. `current` is the agents still on the roster (null
 *  before it has loaded: nobody is called retired). */
export function unclearLine(it: Pick<LibraryItem, 'agents'>, agentId: string, current: Set<string> | null, showRetired: boolean): string {
  const others = candidatesOf(it).filter((a) => a !== agentId);
  const retired = (a: string) => !!current && !current.has(a);
  const named = others.filter((a) => showRetired || !retired(a)).map((a) => (retired(a) ? `${a} (retired)` : a));
  const hidden = others.length - named.length;
  const names = [...named, ...(hidden ? [`${hidden} retired agent${hidden === 1 ? '' : 's'}`] : []), 'this agent'];
  return `Could be ${joinNames(names)}`;
}

/** The agents still on the roster, from GET /api/agents (`{ agents: [...] }`, or a bare list), plus Arturo, who is
 *  never retired. Anyone else a library item names is retired. */
export function rosterOf(resp: unknown, always: string[] = []): Set<string> {
  const list = Array.isArray(resp) ? resp : (resp as { agents?: unknown } | null)?.agents;
  const ids = (Array.isArray(list) ? list : []).map((a) => (a as { id?: unknown })?.id).filter((id): id is string => typeof id === 'string' && !!id);
  return new Set([...always, ...ids]);
}

const BASE = '/api/library';

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal: AbortSignal.timeout(12_000) });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json() as Promise<T>;
}

export const fetchLibraryItems = (query: string) => getJson<{ items: LibraryItem[] }>(`/items?${query}`).then((r) => r.items ?? []);
export const fetchLibrarySummary = () =>
  getJson<{ shared_or_unattributed?: { project: string | null; attribution: string; n: number }[] }>('/summary?days=3650');

/** An agent's unclear files: each project's items with unclear makers, kept when they name this agent. */
export async function fetchUnclear(agentId: string, retired: boolean): Promise<LibraryItem[]> {
  const projects = unclearProjects(await fetchLibrarySummary());
  const lists = await Promise.all(projects.map((p) =>
    fetchLibraryItems(new URLSearchParams({ project: p, limit: '500', ...(retired ? { retired: '1' } : {}) }).toString())));
  return lists.flat().filter((it) => groupOf(it, agentId) === 'unclear')
    .sort((a, b) => (b.at ?? '').localeCompare(a.at ?? ''));
}

/** A library item's file for the floating window, through GET /api/library/items/:id/content. */
export const librarySource = (it: Pick<LibraryItem, 'id' | 'path'>): FileSource => ({
  key: ['library-file', it.id],
  path: it.path ?? '',
  load: () => getJson<ProjectFileContent>(`/items/${encodeURIComponent(it.id)}/content`),
  rawUrl: `${BASE}/items/${encodeURIComponent(it.id)}/content?raw=1`,
});

// ── A project's files by who made them (pages/ProjectFiles.tsx, the "made by" filter) ──

/** One file's makers, from GET /api/library/projects/:slug/made-by (keyed by the path inside the project). */
export interface MadeByEntry { attribution: 'agent' | 'unclear' | 'unattributed' | string; agents: string[]; at: string | null }
export interface ProjectMadeBy {
  project: string;
  files: Record<string, MadeByEntry>;
  makers: { agent: string; files: number; retired: boolean }[];
  unclear: number;
  unattributed: number;
}

export const fetchProjectMadeBy = (slug: string) => getJson<ProjectMadeBy>(`/projects/${encodeURIComponent(slug)}/made-by`);

/** The filter's choices: everyone, one agent (`agent:<id>`), files nobody is sure of, files nobody made. */
export type MakerChoice = 'everyone' | 'unclear' | 'unattributed' | `agent:${string}`;

export interface MakerOption { value: MakerChoice; label: string; count: number }

/** The choices for the files listed now: each agent that made one (most first), then Unclear and Unattributed when
 *  there are any. A file made before the library began has no entry and shows under Everyone only. */
export function makerOptions(paths: string[], m: ProjectMadeBy | null | undefined): MakerOption[] {
  const out: MakerOption[] = [{ value: 'everyone', label: 'Everyone', count: paths.length }];
  if (!m) return out;
  const per = new Map<string, number>();
  let unclear = 0, nobody = 0;
  for (const p of paths) {
    const e = m.files[p];
    if (!e) continue;
    if (e.attribution === 'agent') for (const a of e.agents) per.set(a, (per.get(a) ?? 0) + 1);
    else if (e.attribution === 'unclear') unclear++;
    else nobody++;
  }
  const retired = new Set(m.makers.filter((x) => x.retired).map((x) => x.agent));
  for (const [a, n] of [...per].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))) {
    out.push({ value: `agent:${a}`, label: retired.has(a) ? `${a} (retired)` : a, count: n });
  }
  if (unclear) out.push({ value: 'unclear', label: 'Unclear', count: unclear });
  if (nobody) out.push({ value: 'unattributed', label: 'Unattributed', count: nobody });
  return out;
}

/** Whether a file is shown under a choice. Made only: an agent's choice is the files it made itself. */
export function madeByMatches(e: MadeByEntry | undefined, choice: MakerChoice): boolean {
  if (choice === 'everyone') return true;
  if (!e) return false;
  if (choice === 'unclear') return e.attribution === 'unclear';
  if (choice === 'unattributed') return e.attribution !== 'agent' && e.attribution !== 'unclear';
  return e.attribution === 'agent' && e.agents.includes(choice.slice('agent:'.length));
}

/** A file row's maker label: its maker(s); "Unclear: a or b" naming the candidates; "Unattributed"; none when
 *  the library has no entry for it. */
export function madeByLabel(e: MadeByEntry | undefined): string {
  if (!e) return '';
  if (e.attribution === 'agent') return e.agents.join(', ');
  if (e.attribution === 'unclear') return e.agents.length ? `Unclear: ${joinNames(e.agents)}` : 'Unclear';
  return 'Unattributed';
}

// ── Files a result card names, linked to their library items (components/kit/ResultFiles.tsx) ──

/** What GET /api/library/lookup gives for one path: its library item (and whether it can be opened: a gone file
 *  opens only from a kept copy), or a project file the index does not hold, or null. */
export type LookupEntry = { item: LibraryItem & { missing?: boolean; kept?: boolean }; openable: boolean }
  | { item: null; openable: boolean; project: string; rel: string } | null;

/** The lookup's query: each path once, at most 20 (the route's limit), and the agent whose rows come first. */
export function lookupQuery(paths: string[], agent?: string): string {
  const p = new URLSearchParams();
  for (const f of [...new Set(paths)].slice(0, 20)) p.append('path', f);
  if (agent && /^[\w.-]{1,80}$/.test(agent)) p.set('agent', agent);
  return p.toString();
}

export const fetchLibraryLookup = (paths: string[], agent?: string) =>
  getJson<{ files: Record<string, LookupEntry> }>(`/lookup?${lookupQuery(paths, agent)}`).then((r) => r.files ?? {});

/** Where a named file opens: its library item, a project's file, or nowhere (not known, or gone with no kept copy). */
export type FileTarget = { kind: 'item'; item: LibraryItem; title: string } | { kind: 'project'; slug: string; rel: string; title: string };
export function fileTarget(e: LookupEntry | undefined): FileTarget | null {
  if (!e || !e.openable) return null;
  if (e.item) return { kind: 'item', item: e.item, title: itemTitle(e.item) };
  return { kind: 'project', slug: e.project, rel: e.rel, title: baseName(e.rel) };
}

/** Whether the library knows a named file is gone (and has no copy to show). */
export const isGone = (e: LookupEntry | undefined): boolean => !!e?.item?.missing && !e.openable;
