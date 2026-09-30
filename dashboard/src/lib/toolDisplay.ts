/**
 * toolDisplay.ts — how a tool call reads on Arturo's tool card (components/arturo/ToolRun.tsx).
 *
 * Plain actions, never raw identifiers. One rule is Arturo's, not styling: its deep brain
 * (gm_command / async_task / inject_message) is Arturo thinking, never a separate "GM" — the
 * system prompt forbids saying so, and the card must not say it either.
 */
import type { ToolPart } from './turnParts';

const LABELS: Record<string, [string, string]> = {
  // name: [label, what it reads as while running]
  list_agents: ['List agents', 'Listing agents'],
  query_roadmap: ['Check roadmap', 'Checking roadmap'],
  gm_command: ['Think it through', 'Thinking it through'],
  async_task: ['Work on it in the background', 'Starting background work'],
  inject_message: ['Message an agent', 'Messaging an agent'],
  agent_message: ['Message an agent', 'Messaging an agent'],
  send_telegram: ['Send Telegram', 'Sending Telegram'],
  spawn_agent: ['Start an agent', 'Starting an agent'],
  kill_agent: ['Stop an agent', 'Stopping an agent'],
  read_file: ['Read file', 'Reading file'],
  run_command: ['Run command', 'Running command'],
  research: ['Research', 'Researching'],
  knowledge: ['Search knowledge', 'Searching knowledge'],
  remember_note: ['Save note', 'Saving note'],
  read_agent_conversation: ['Read conversation', 'Reading conversation'],
  get_agent_output: ['Read agent output', 'Reading agent output'],
  read_screen_context: ['Read screen', 'Reading screen'],
  focus_entity: ['Focus', 'Focusing'],
  answer_menu: ['Answer menu', 'Answering menu'],
  client_briefing: ['Client briefing', 'Preparing client briefing'],
  set_operator_fact: ['Remember about you', 'Remembering'],
};

function sentence(name: string): string {
  const words = name.replace(/[_.-]+/g, ' ').trim().toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : 'Tool';
}

export function toolLabel(name: string): string {
  return LABELS[name]?.[0] ?? sentence(name);
}

export function toolRunningLabel(name: string): string {
  return LABELS[name]?.[1] ?? sentence(name);
}

function show(v: unknown): string {
  if (v === null || v === undefined) return '';
  return typeof v === 'string' ? v : JSON.stringify(v);
}

/** The arguments as words: one value alone, several as `key: value`, never braces. */
export function toolChip(argsSummary: string): string {
  const raw = (argsSummary || '').trim();
  if (!raw || raw === '{}') return '';
  try {
    const obj = JSON.parse(raw);
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
      const entries = Object.entries(obj).filter(([, v]) => show(v) !== '');
      if (entries.length === 0) return '';
      if (entries.length === 1) return show(entries[0][1]);
      return entries.map(([k, v]) => `${k}: ${show(v)}`).join(' · ');
    }
    return show(obj);
  } catch {
    // Cut off by the server's 200-char cap: show what is there as words, and that it goes on.
    const words = raw.replace(/^[{[]/, '').replace(/"([^"]*)"\s*:\s*/g, '$1: ').replace(/"/g, '').trim();
    return words ? `${words}…` : '';
  }
}

/** "Checking roadmap…" while a call runs; afterwards how many ran, and how many failed. */
export function runHeader(tools: ToolPart[]): string {
  const running = tools.find((t) => t.status === 'running');
  if (running) return `${toolRunningLabel(running.name)}…`;
  const n = tools.length;
  const failed = tools.filter((t) => t.status === 'failed').length;
  return `${n} tool call${n === 1 ? '' : 's'}${failed ? ` · ${failed} failed` : ''}`;
}
