/**
 * liveStep.ts — what an agent is doing right now, in words, for the one short line under its chat (the agent page,
 * the docked panel, the cards). The API sends the same small object for every seat, whatever runs it
 * (api/src/services/live-step.ts): on the chat's stream as `event: step`, and as each row's `step` in the agent list.
 *
 *   { agent_id, kind: 'tool' | 'thinking' | 'waiting' | 'idle', tool?, summary?, since }
 *
 * `summary` is already short and free of secrets (the server redacts and caps it). `kind: null` means there is no
 * step to show (the agent is stopped, restarting, or in Dev): the line falls back to the agent's state.
 */
export type StepKind = 'tool' | 'thinking' | 'waiting' | 'idle';
export interface AgentStep { agent_id: string; kind: StepKind; tool?: string; summary?: string; since?: string }

const KINDS = new Set(['tool', 'thinking', 'waiting', 'idle']);

/** A step as received, or null when it is none (or not one this page reads). */
export function stepFrom(raw: unknown): AgentStep | null {
  const o = raw as Partial<AgentStep> | null;
  if (!o || typeof o !== 'object' || typeof o.agent_id !== 'string' || !KINDS.has(o.kind as string)) return null;
  return {
    agent_id: o.agent_id, kind: o.kind as StepKind,
    ...(typeof o.tool === 'string' && o.tool ? { tool: o.tool } : {}),
    ...(typeof o.summary === 'string' && o.summary.trim() ? { summary: o.summary.trim() } : {}),
    ...(typeof o.since === 'string' ? { since: o.since } : {}),
  };
}

// Per tool: what it is doing, with its summary and without one.
const TOOL_WORDS: Record<string, [(s: string) => string, string]> = {
  Bash: [(s) => `Running a shell command: ${s}`, 'Running a shell command'],
  Read: [(s) => `Reading ${s}`, 'Reading a file'],
  Write: [(s) => `Writing ${s}`, 'Writing a file'],
  Edit: [(s) => `Editing ${s}`, 'Editing a file'],
  MultiEdit: [(s) => `Editing ${s}`, 'Editing a file'],
  NotebookEdit: [(s) => `Editing ${s}`, 'Editing a notebook'],
  Grep: [(s) => `Searching for ${s}`, 'Searching the files'],
  Glob: [(s) => `Finding files: ${s}`, 'Finding files'],
  WebFetch: [(s) => `Reading a web page: ${s}`, 'Reading a web page'],
  WebSearch: [(s) => `Searching the web: ${s}`, 'Searching the web'],
  Task: [(s) => `Running a subagent: ${s}`, 'Running a subagent'],
  Agent: [(s) => `Running a subagent: ${s}`, 'Running a subagent'],
  Skill: [(s) => `Using a skill: ${s}`, 'Using a skill'],
  TodoWrite: [() => 'Updating its task list', 'Updating its task list'],
};

/** The line's words for a step. */
export function stepText(step: AgentStep): string {
  if (step.kind === 'thinking') return 'Thinking…';
  if (step.kind === 'waiting') return 'Waiting for your answer';
  if (step.kind === 'idle') return 'Idle';
  const tool = step.tool || '';
  const words = TOOL_WORDS[tool];
  if (words) return step.summary ? words[0](step.summary) : words[1];
  if (!tool) return 'Working';
  // A tool this page has no words for (an MCP tool, a newer CLI tool): its name, made readable.
  const name = tool.replace(/^mcp__[^_]+__/, '').replace(/[_-]+/g, ' ');
  return step.summary ? `Using ${name}: ${step.summary}` : `Using ${name}`;
}

/** How long the step has been going ("12s", "3m 4s"), or '' when unknown. */
export function stepElapsed(step: AgentStep, now: number = Date.now()): string {
  const t = step.since ? Date.parse(step.since) : NaN;
  if (Number.isNaN(t)) return '';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}
