import type { ToolBlock } from './transcript';

// The agent pane always renders 'normal': groups folded to one line, a command card folded to its header
// line until opened. The Summary/Normal/Verbose toggle was removed (2026-10-11); the cards keep the
// prop so a caller can still ask for another level.
export type ToolDensity = 'summary' | 'normal' | 'verbose';


export function isCommandTool(tool: string): boolean {
  return ['bash', 'run_command', 'exec', 'exec_command', 'shell'].includes(tool.toLowerCase());
}

export function outputPreview(result: string, lines = 3): { text: string; hiddenLines: number } {
  const all = result.split('\n');
  return { text: all.slice(0, lines).join('\n'), hiddenLines: Math.max(0, all.length - lines) };
}

export interface TodoItem { content: string; status: 'pending' | 'in_progress' | 'completed' }

export function todoItems(block: ToolBlock): TodoItem[] {
  if (!/^todo(write|read)?$/i.test(block.tool)) return [];
  const raw = block.input?.todos;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item): TodoItem[] => {
    if (!item || typeof item !== 'object') return [];
    const value = item as Record<string, unknown>;
    const content = typeof value.content === 'string' ? value.content : typeof value.activeForm === 'string' ? value.activeForm : '';
    const status = value.status === 'completed' || value.status === 'in_progress' ? value.status : 'pending';
    return content ? [{ content, status }] : [];
  });
}
