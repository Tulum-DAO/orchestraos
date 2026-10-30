/**
 * Pure helpers for Arturo's slide-out panel (components/arturo/ArturoPanel.tsx), kept here so they are tested
 * without a browser: the status line, where it sends you when something is wrong, and each row's value.
 */
import { isArturoDisabled, isStarting, handsFreeReady, type ArturoHealth } from './arturo.ts';
import { DEFAULT_ASSISTANT_NAME } from './assistantName.ts';
import type { ArturoJob } from './arturoJobs.ts';

export type PanelPage = 'voice' | 'memory' | 'activity' | 'settings' | 'brain' | 'device' | 'avatar' | 'name' | 'edit'
  | 'style' | 'model' | 'library' | 'history' | 'setup';

/** The levels to a page from the root, so a jump lands with the right back arrow: Brain sits under Settings. */
export function pathTo(page: PanelPage): PanelPage[] {
  return page === 'brain' || page === 'device' || page === 'name' ? ['settings', page] : [page];
}

/** The status line, and the page that fixes what it reports (null when there is nothing to fix). */
export function panelStatus(h: ArturoHealth | null, brain: string, name = DEFAULT_ASSISTANT_NAME): { text: string; fix: PanelPage | null } {
  if (!h) return { text: 'Checking…', fix: null };
  if (isArturoDisabled(h)) return { text: `${name} is turned off on this server`, fix: 'brain' };
  if (isStarting(h)) return { text: 'Starting…', fix: null };
  if (!h.ok) return { text: `${name}'s brain is offline`, fix: 'brain' };
  if (h.brain?.kind === 'none') return { text: 'No brain connected', fix: 'brain' };
  const ready = brain ? `Ready · ${brain}` : 'Ready';
  if (!handsFreeReady(h)) return { text: `${ready} · Live voice not set up`, fix: 'voice' };
  return { text: ready, fix: null };
}

export function factsValue(count: number | null): string {
  if (count === null) return '';
  return count === 1 ? '1 fact' : `${count} facts`;
}

/** An agent's Activity row: its subagents still running (background tasks and subagents are its jobs). */
export function subagentsValue(n: number | null | undefined): string {
  if (n === null || n === undefined) return '';
  return n ? `${n} running` : 'Idle';
}

export function activityValue(jobs: ArturoJob[] | null): string {
  if (jobs === null) return '';
  const running = jobs.filter((j) => j.status === 'running').length;
  return running ? `${running} running` : 'Idle';
}

/** The drawer's sections: what is running, waiting and happened (History), what it made (Library), how it is set up
 *  (Setup). The header carries its state (the live status line and the context meter). A pill selector on a desktop,
 *  drill-in rows on a phone. */
export type DrawerSection = 'history' | 'library' | 'setup';
export const DRAWER_SECTIONS: DrawerSection[] = ['history', 'library', 'setup'];
export const SECTION_LABEL: Record<DrawerSection, string> = { history: 'History', library: 'Library', setup: 'Setup' };

/** Where the drawer opens: History when something is waiting or running, else the section used last. */
export function openingSection(status: string | undefined, waiting: boolean, last: string | null): DrawerSection {
  if (waiting || status === 'working' || status === 'needs_input') return 'history';
  return (DRAWER_SECTIONS as string[]).includes(last ?? '') ? (last as DrawerSection) : 'history';
}

/** The header's context meter: "62% context used", from the share of the window still left. Null without a reading.
 *  "100% left" is no reading either: Claude Code's status line says 0% used until its first reply after a resume,
 *  and a session that has run at all has used some of its window. */
export function contextUsedLabel(leftPct: number | null | undefined): string | null {
  if (typeof leftPct !== 'number' || !Number.isFinite(leftPct) || leftPct < 0 || leftPct >= 100) return null;
  return `${Math.round(100 - leftPct)}% context used`;
}

/** How long, in words: "under a minute", "3m", "1h 20m". */
export function shortSpan(ms: number): string {
  const m = Math.floor(Math.max(0, ms) / 60_000);
  if (m < 1) return 'under a minute';
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h${m % 60 ? ` ${m % 60}m` : ''}`;
}

/** The header's live status line, from the agent's state (in place of a fixed provider and model line):
 *  "Working · 3m", "Waiting for you", "Idle". `since` is when this page saw the state begin. */
export function liveStatus(status: string | undefined, since: number | undefined, now = Date.now()): string {
  switch (status) {
    case 'working': return since ? `Working · ${shortSpan(now - since)}` : 'Working';
    case 'needs_input': return 'Waiting for you';
    case 'failed': return 'Stopped with an error';
    case 'finished': return 'Just finished';
    default: return 'Idle';
  }
}

/** The Computer row's value on a phone: what its terminal is doing, in one word. */
export function computerValue(status: string | undefined): string {
  return status === 'working' ? 'Working' : status === 'needs_input' ? 'Waiting' : 'Idle';
}
