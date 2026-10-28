/**
 * Pure helpers for Arturo's slide-out panel (components/arturo/ArturoPanel.tsx), kept here so they are tested
 * without a browser: the status line, where it sends you when something is wrong, and each row's value.
 */
import { isArturoDisabled, isStarting, handsFreeReady, type ArturoHealth } from './arturo.ts';
import { DEFAULT_ASSISTANT_NAME } from './assistantName.ts';
import type { ArturoJob } from './arturoJobs.ts';

export type PanelPage = 'voice' | 'memory' | 'activity' | 'settings' | 'brain' | 'device' | 'avatar' | 'name' | 'edit';

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

export function activityValue(jobs: ArturoJob[] | null): string {
  if (jobs === null) return '';
  const running = jobs.filter((j) => j.status === 'running').length;
  return running ? `${running} running` : 'Idle';
}
