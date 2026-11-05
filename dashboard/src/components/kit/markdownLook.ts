/**
 * markdownLook.ts — the thread kit's Markdown looks that both of its drawings share: the finished message
 * (components/kit/Markdown.tsx) and the streamed reply (components/kit/StreamMarkdown.tsx), so they cannot drift.
 */
import type { Skin } from './Markdown';

/** The attributes of an element the reveal switches on (lib/revealStore.ts): its unit, and whether it is a
 *  box that fades with its first word rather than a word. */
export function revealAttrs(unit: number | null | undefined, box = false): Record<string, string | number> {
  if (unit === undefined || unit === null) return {};
  return box ? { 'data-ru': unit, 'data-ru-box': '' } : { 'data-ru': unit };
}

/** An inline code span's look, by skin (Modern: the page's own `code` style, Arturo's .txt code, so his replies keep
 *  their look). */
export function inlineCodeClass(skin: Skin): string | undefined {
  return skin === 'modern' ? undefined
    : skin === 'pane' ? 'px-1 py-0.5 rounded bg-white/10 text-[0.92em] font-mono'
      : 'px-1 py-0.5 rounded bg-tone-800 text-[0.92em] font-mono text-tone-amber-200';
}

export function linkClass(skin: Skin): string {
  return `underline underline-offset-2 decoration-1 hover:opacity-80 break-words ${skin === 'pane' ? 'text-blue-400' : 'text-blue-600 dark:text-blue-400'}`;
}

export function headingClass(level: number): string {
  return level <= 1 ? 'text-[1.15em] font-semibold' : level === 2 ? 'text-[1.05em] font-semibold' : 'font-medium';
}
