/**
 * toolCalls.ts — one tool call as the shared tool card (components/kit/ToolCard.tsx) draws it, whoever ran it.
 *
 * Arturo's stream sends ToolParts (name, a one-line argument summary, ok or failed, a short result); an agent's
 * transcript sends ToolBlocks (the full input and result, start and end times). Both become a ToolCallView, so
 * Arturo's page and every agent chat draw the same card: the agent chat's detail (how long the turn worked, the
 * todo list, file chips, the whole input and output) with Arturo's per-tool icons and running rows.
 */
import type { ToolBlock } from './transcript';
import { toolSummary, toolFilePath } from './transcript';
import type { ToolPart, ToolStatus } from './turnParts';
import { toolChip, toolLabel, toolResultLines } from './toolDisplay';
import { todoItems, isCommandTool, type TodoItem } from './toolCardDetail';
import { summarizeTurn, formatDuration, type WorkItem } from './toolGroups';

export interface ToolCallView {
  id: string;
  name: string;
  status: ToolStatus;
  /** The arguments as one line of words. */
  chip: string;
  /** The file the call read or wrote, when there is one: shown as a chip that copies the path. */
  filePath?: string;
  /** The whole input, when the source has it (an agent's transcript). */
  input?: Record<string, unknown>;
  result: string;
  ts?: string;
  endTs?: string;
  todos: TodoItem[];
  /** One of Arturo's own tools: its result is shown without the sentences written to the model. */
  arturo: boolean;
}

/** A todo list's one line ("3 todos · 1 done"): the list itself opens below the row. */
function todoChip(todos: TodoItem[]): string {
  const done = todos.filter((t) => t.status === 'completed').length;
  return `${todos.length} todo${todos.length === 1 ? '' : 's'}${done ? ` · ${done} done` : ''}`;
}

export function fromBlock(b: ToolBlock): ToolCallView {
  const todos = todoItems(b);
  return {
    id: b.key,
    name: b.tool,
    status: b.isError ? 'failed' : b.result === undefined ? 'running' : 'ok',
    chip: todos.length ? todoChip(todos) : toolSummary(b.tool, b.input),
    ...(toolFilePath(b.tool, b.input) ? { filePath: toolFilePath(b.tool, b.input)! } : {}),
    input: b.input,
    result: b.result ?? '',
    ...(b.ts ? { ts: b.ts } : {}),
    ...(b.resultTs ? { endTs: b.resultTs } : {}),
    todos,
    arturo: false,
  };
}

export function fromPart(p: ToolPart): ToolCallView {
  return { id: p.callId, name: p.name, status: p.status, chip: toolChip(p.argsSummary), result: p.summary ?? '', todos: [], arturo: true };
}

export function workItem(c: ToolCallView): WorkItem {
  return { tool: c.name, status: c.status, ...(c.filePath ? { path: c.filePath } : {}),
           ...(c.ts ? { ts: c.ts } : {}), ...(c.endTs ? { endTs: c.endTs } : {}) };
}

/** The tool's name as the card says it: Arturo's tools in plain words, an agent CLI's tools as the CLI names them
 *  ("Bash", "TodoWrite"), anything else as a sentence. */
export function callLabel(name: string): string {
  const plain = toolLabel(name);
  return plain !== name && /[a-z]_/.test(name) ? plain : /[A-Z]/.test(name) ? name : plain;
}

export type IconKey = 'terminal' | 'read' | 'edit' | 'search' | 'web' | 'agent' | 'message' | 'list' | 'todo' | 'note'
  | 'map' | 'image' | 'tool';

const ICON_BY_NAME: Record<string, IconKey> = {
  // Arturo
  list_agents: 'list', query_roadmap: 'map', spawn_agent: 'agent', kill_agent: 'agent', send_telegram: 'message',
  inject_message: 'message', agent_message: 'message', gm_command: 'message', ask_gm: 'message', async_task: 'agent',
  read_file: 'read', read_agent_conversation: 'read', get_agent_output: 'read', run_command: 'terminal', research: 'web',
  knowledge: 'search', remember_note: 'note', look_at_image: 'image',
  // agent CLIs (lower-cased)
  bash: 'terminal', exec: 'terminal', exec_command: 'terminal', shell: 'terminal', run_shell_command: 'terminal',
  read: 'read', view_file: 'read', read_many_files: 'read', notebookread: 'read',
  edit: 'edit', write: 'edit', multiedit: 'edit', notebookedit: 'edit', apply_patch: 'edit', write_file: 'edit',
  replace: 'edit', replace_file_content: 'edit', write_to_file: 'edit',
  grep: 'search', glob: 'search', search_file_content: 'search', find_by_name: 'search', grep_search: 'search', toolsearch: 'search',
  webfetch: 'web', websearch: 'web', web_fetch: 'web', google_web_search: 'web', read_url_content: 'web', search_web: 'web',
  task: 'agent', agent: 'agent', todowrite: 'todo', todoread: 'todo', askuserquestion: 'message', skill: 'note',
};

export function iconKey(name: string): IconKey {
  return ICON_BY_NAME[name] ?? ICON_BY_NAME[name.toLowerCase()] ?? 'tool';
}

/** What the card shows when a row opens: the todo list, the input when the one-line chip cannot hold it, and the
 *  result (Arturo's without sentences meant for the model). Nothing to show = the row does not open. */
export function callDetail(c: ToolCallView): { todos: TodoItem[]; input: string | null; resultLines: string[] | null; resultText: string } {
  const inputStr = c.input && Object.keys(c.input).length ? JSON.stringify(c.input, null, 2) : '';
  // The chip already says a short input in full; a command's chip is its command, so its input adds nothing.
  const input = inputStr && inputStr.length > 200 && !isCommandTool(c.name) && !c.todos.length ? inputStr : null;
  return {
    todos: c.todos,
    input,
    resultLines: c.arturo ? toolResultLines(c.result) : null,
    resultText: c.arturo ? '' : c.result,
  };
}

export function hasDetail(c: ToolCallView): boolean {
  if (c.status === 'running') return false;
  const d = callDetail(c);
  return d.todos.length > 0 || !!d.input || (d.resultLines ? d.resultLines.length > 0 : d.resultText.length > 0);
}

/** The run's one-line header: "Worked for 1m 8s", "Working", "Stopped after 9s", and what it did. */
export function runSummary(calls: ToolCallView[], interrupted = false): {
  lead: string; headline: string; tone: 'running' | 'stopped' | 'failed' | 'done';
} {
  const s = summarizeTurn(calls.map(workItem), { interrupted });
  const dur = s.durationMs !== undefined ? formatDuration(s.durationMs) : '';
  const lead = s.running > 0 ? 'Working'
    : s.interrupted > 0 ? (dur ? `Stopped after ${dur}` : 'Stopped')
    : (dur ? `Worked for ${dur}` : 'Worked');
  const tone = s.running > 0 ? 'running' : s.interrupted > 0 ? 'stopped'
    : s.failed > 0 && s.failed === calls.length ? 'failed' : 'done';
  return { lead, headline: s.headline, tone };
}
