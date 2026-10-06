/**
 * toolGroups.ts — fold one turn's tool calls into a single line (harness-UX plan, C3).
 *
 *   ▸ Worked for 1m 8s — edited 2 files, ran 3 commands
 *
 * Expanding the line shows the per-call rows, and each row expands to its input and output, so
 * a turn reads at three levels instead of one card per call. The summary rules follow T3 Code's
 * work log: file changes and commands outrank reads, and a failure is never counted as a
 * success.
 *
 * Seat transcripts (ToolBlock) and Arturo's live stream (ToolPart) carry different shapes, so
 * both adapt into one neutral WorkItem and share ONE summarizer. Pure functions, no DOM.
 */
import type { RenderNode, ToolBlock } from './transcript';
import type { ToolPart, ToolStatus } from './turnParts';

export interface WorkItem {
  tool: string;
  /** The file the call touched, when the source knows it. Arturo's stream does not. */
  path?: string;
  status: ToolStatus;
  /** When the call started. */
  ts?: string;
  /** When its result came back, when the source knows it. */
  endTs?: string;
}

export interface TurnSummary {
  headline: string;
  failed: number;
  running: number;
  /** Calls that will never finish: the turn moved on, or the agent stopped. See isInterrupted. */
  interrupted: number;
  /** Earliest start to latest result. Absent unless at least two instants parse. */
  durationMs?: number;
}

type Category = 'edit' | 'command' | 'read' | 'search' | 'other';

// Claude Code, Codex, Gemini CLI and Antigravity names, plus Arturo's own tools. Lower-cased
// lookups. Antigravity's come from the API's own toolSummary table (api/src/routes/chat-transcript.ts).
const CATEGORY: Record<string, Category> = {
  edit: 'edit', write: 'edit', multiedit: 'edit', notebookedit: 'edit',
  apply_patch: 'edit', write_file: 'edit', replace: 'edit', edit_file: 'edit', create_file: 'edit',
  replace_file_content: 'edit', write_to_file: 'edit',
  bash: 'command', shell: 'command', run_shell_command: 'command', run_command: 'command',
  exec_command: 'command', exec: 'command',
  read: 'read', read_file: 'read', read_many_files: 'read', view: 'read', notebookread: 'read',
  view_file: 'read',
  grep: 'search', glob: 'search', websearch: 'search', toolsearch: 'search', search: 'search',
  search_file_content: 'search', list_directory: 'search', google_web_search: 'search',
  grep_search: 'search', find_by_name: 'search', search_web: 'search',
};

function categoryOf(tool: string): Category {
  return CATEGORY[tool.toLowerCase()] || 'other';
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * Count a category's work in the two units it can be counted in.
 *
 * `files` is the DISTINCT KNOWN paths. `unnamed` is the calls whose path cannot be known —
 * Codex's `apply_patch` carries its target inside the patch text, and `read_many_files` takes a
 * list — and `calls` is the total. The old rule counted an unnamed call AS ITS OWN FILE, so two
 * `apply_patch` calls editing one file read "edited 2 files": a false claim about the filesystem,
 * made from a transcript that cannot support it.
 *
 * `calls` is the only one of the three that is ALWAYS exactly knowable, which is why the clause
 * builder falls back to it the moment any path is missing. Not "do not over-claim" but DO NOT
 * CLAIM IN A UNIT YOU CANNOT FILL.
 */
function countWork(items: WorkItem[]): { files: number; unnamed: number; calls: number } {
  const seen = new Set<string>();
  let unnamed = 0;
  for (const it of items) {
    if (it.path) seen.add(it.path);
    else unnamed += 1;
  }
  return { files: seen.size, unnamed, calls: items.length };
}

/**
 * One rule, two shapes: name FILES only when every call in the category named one, else report
 * CALLS for the whole category. A mixed turn reports calls too — joining a file count to a call
 * count ("edited 1 file, 2 more edits") misreports BOTH units, since for four calls over one known
 * path it reads as 3 files (could be 1) and as 3 edits (there were 4).
 */
function workClause(items: WorkItem[], fileVerb: string, callNoun: string): string | undefined {
  const { files, unnamed, calls } = countWork(items);
  if (calls === 0) return undefined;
  if (unnamed > 0) return `made ${plural(calls, callNoun, callNoun + 's')}`;
  return `${fileVerb} ${plural(files, 'file', 'files')}`;
}

function spanMs(items: WorkItem[]): number | undefined {
  // Starts AND ends: ending at the last call's START would drop that call's own runtime.
  const times = items
    .flatMap((it) => [it.ts, it.endTs])
    .map((t) => (t ? Date.parse(t) : NaN))
    .filter((t) => !Number.isNaN(t));
  if (times.length < 2) return undefined;
  return Math.max(...times) - Math.min(...times);
}

export function summarizeTurn(all: WorkItem[], opts: { interrupted?: boolean } = {}): TurnSummary {
  // An interrupted turn's unfinished calls are neither done nor still going.
  const interrupted = opts.interrupted ? all.filter((it) => it.status === 'running').length : 0;
  const items = opts.interrupted ? all.filter((it) => it.status !== 'running') : all;
  const by = (cat: Category, status?: ToolStatus) =>
    items.filter((it) => categoryOf(it.tool) === cat && (!status || it.status === status));

  const failed = items.filter((it) => it.status === 'failed').length;
  const running = items.filter((it) => it.status === 'running').length;
  const durationMs = spanMs(all);

  // Nothing succeeded and nothing is still going: claim nothing as done.
  if (failed > 0 && failed === items.length) {
    const tail = interrupted > 0 ? `, ${interrupted} interrupted` : '';
    return { headline: `${failed} failed${tail}`, failed, running, interrupted, durationMs };
  }

  const clauses: string[] = [];

  const edited = workClause(by('edit', 'ok'), 'edited', 'edit');
  if (edited) clauses.push(edited);

  // Commands carry their own failures inline; a failed command is still a command that ran.
  const cmdDone = by('command', 'ok').length + by('command', 'failed').length;
  const cmdFailed = by('command', 'failed').length;
  if (cmdDone > 0) {
    let c = `ran ${plural(cmdDone, 'command', 'commands')}`;
    if (cmdFailed > 0) c += cmdFailed === cmdDone && cmdDone === 1 ? ' (failed)' : ` (${cmdFailed} failed)`;
    clauses.push(c);
  }
  const cmdRunning = by('command', 'running').length;
  if (cmdRunning > 0) clauses.push(`running ${plural(cmdRunning, 'command', 'commands')}`);

  // Reads and searches only matter when nothing outranks them.
  if (clauses.length === 0) {
    const read = workClause(by('read', 'ok'), 'read', 'read');
    if (read) clauses.push(read);
    const searched = by('search', 'ok').length;
    if (searched > 0) clauses.push(`searched ${plural(searched, 'time', 'times')}`);
  }
  if (clauses.length === 0) {
    const used = items.filter((it) => it.status === 'ok').length;
    if (used > 0) clauses.push(`used ${plural(used, 'tool', 'tools')}`);
  }

  // Failures not already reported inside the command clause.
  const otherFailed = failed - cmdFailed;
  if (otherFailed > 0) clauses.push(`${otherFailed} failed`);

  // Calls still in flight outside the command clause.
  const otherRunning = running - cmdRunning;
  if (otherRunning > 0) clauses.push(`running ${otherRunning}`);

  if (interrupted > 0) clauses.push(`${interrupted} interrupted`);

  return { headline: clauses.join(', '), failed, running, interrupted, durationMs };
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return '<1s';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

// States in which the agent is known NOT to be mid-turn. 'waiting' is deliberately absent: a
// tool call blocked on a permission prompt is unfinished but very much alive. 'stalled' is a long
// turn still working, and 'unknown' is no evidence at all.
const STOPPED_STATES = new Set(['idle', 'stranded', 'stopped', 'crashed', 'offline', 'retired']);

/**
 * Whether a group's unfinished calls can never finish. Only on EVIDENCE: the chat has moved past
 * the group, or the agent is in a known stopped state. Without evidence, claim nothing, so a
 * session killed mid-call stops reading "Working" forever without a live call ever being
 * mislabelled.
 */
export function isInterrupted({ isLast, state }: { isLast: boolean; state: string }): boolean {
  if (!isLast) return true;
  return STOPPED_STATES.has(state);
}

// ---- adapters ------------------------------------------------------------------------------

export function fromToolBlock(b: ToolBlock): WorkItem {
  const raw = b.input?.file_path ?? b.input?.path ?? b.input?.notebook_path
    ?? b.input?.TargetFile ?? b.input?.AbsolutePath;
  const item: WorkItem = {
    tool: b.tool,
    status: b.isError ? 'failed' : b.result === undefined ? 'running' : 'ok',
  };
  if (typeof raw === 'string' && raw) item.path = raw;
  if (b.ts) item.ts = b.ts;
  if (b.resultTs) item.endTs = b.resultTs;
  return item;
}

export function fromToolPart(p: ToolPart): WorkItem {
  // argsSummary is display text, not a reliable path, so each call counts as its own file.
  return { tool: p.name, status: p.status };
}

// ---- grouping ------------------------------------------------------------------------------

export interface ToolGroupNode {
  kind: 'tool_group';
  /** Derived from the first child, so the group keeps its identity as the run grows. */
  key: string;
  /** Tool calls and the thinking between them, in transcript order. */
  children: RenderNode[];
}

export type GroupedNode = RenderNode | ToolGroupNode;

const MIN_GROUP_TOOLS = 2;

/**
 * Fold every run of two or more consecutive tool calls into one ToolGroupNode. Thinking that
 * sits BETWEEN calls joins the group; thinking at either edge stays outside it, and a run with
 * a single call is left exactly as it was. Nothing is dropped or reordered.
 */
export function groupToolRuns(nodes: RenderNode[]): GroupedNode[] {
  const out: GroupedNode[] = [];
  let i = 0;
  while (i < nodes.length) {
    const n = nodes[i];
    if (n.kind !== 'tool' && n.kind !== 'thinking') {
      out.push(n);
      i += 1;
      continue;
    }
    // A maximal run of tool/thinking nodes.
    let j = i;
    while (j < nodes.length && (nodes[j].kind === 'tool' || nodes[j].kind === 'thinking')) j += 1;
    const run = nodes.slice(i, j);
    const first = run.findIndex((x) => x.kind === 'tool');
    const last = run.length - 1 - [...run].reverse().findIndex((x) => x.kind === 'tool');
    const tools = first < 0 ? 0 : run.slice(first, last + 1).filter((x) => x.kind === 'tool').length;
    if (tools >= MIN_GROUP_TOOLS) {
      out.push(...run.slice(0, first));
      const children = run.slice(first, last + 1);
      out.push({ kind: 'tool_group', key: `group:${children[0].key}`, children });
      out.push(...run.slice(last + 1));
    } else {
      out.push(...run);
    }
    i = j;
  }
  return out;
}
