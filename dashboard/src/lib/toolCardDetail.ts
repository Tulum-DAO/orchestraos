import type { ToolBlock } from './transcript';

export type ToolDensity = 'summary' | 'normal' | 'verbose';

export const TOOL_DENSITIES: readonly ToolDensity[] = ['summary', 'normal', 'verbose'];
export const TOOL_DENSITY_STORAGE_KEY = 'orchestra.tool-density';

export function storedToolDensity(read: () => string | null): ToolDensity {
  const value = read();
  return TOOL_DENSITIES.includes(value as ToolDensity) ? value as ToolDensity : 'normal';
}

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
