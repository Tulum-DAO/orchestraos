/**
 * avatarStatus.ts — which of the avatar's five states (working, needs input, finished, failed, idle) an agent
 * is in, from what the dashboard already knows, plus the live step line. Pure, so it is tested.
 */
import { normalizeAgentState } from './agentStatus.ts';
import type { AvatarState } from '../components/avatar/avatarCore.ts';

/** A seat's state from its detector status and pending menu. A stale feed claims nothing (idle grey),
 *  as the rail's own dots do. */
export function avatarStateFor(status: string | undefined, hasPendingMenu: boolean, feedDegraded = false): AvatarState {
  if (feedDegraded) return 'idle';
  if (hasPendingMenu) return 'needs_input';
  switch (normalizeAgentState(status)) {
    case 'working': case 'stalled': return 'working';
    case 'waiting': case 'stranded': case 'queued': return 'needs_input';
    case 'stopped': case 'crashed': return 'failed';
    default: return 'idle';
  }
}

/** Arturo's badge provider, from its actual brain: the brain picked on its page when there is one, else
 *  the brain its service reports (a CLI runtime by its runtime id, an API brain by its provider). Null when
 *  there is no brain yet, so no badge is shown rather than a guessed one. The voice-call vendor is not the
 *  brain and does not set it. */
export function arturoProvider(brain: { kind?: string; runtime?: string; cli?: string; provider?: string } | null | undefined,
                               choice?: { provider?: string } | null): string | null {
  if (choice?.provider) return choice.provider;
  if (!brain || brain.kind === 'none') return null;
  if (brain.kind === 'runtime') return brain.runtime || brain.cli || null;
  return brain.provider || null;
}

/** A step line is current for this long after it was written (a turn that never cleared it is over). */
export const STEP_FRESH_MS = 30_000;

/** Arturo is not a telemetry seat: it is working while a turn is in flight here, while a call is live, or
 *  while its service is reporting steps (a background job). */
export function arturoState(o: { busy: boolean; inCall: boolean; blurb?: string; blurbAt?: number; now: number }): AvatarState {
  if (o.busy || o.inCall) return 'working';
  if (o.blurb && o.blurbAt != null && o.now - o.blurbAt < STEP_FRESH_MS) return 'working';
  return 'idle';
}

/** The hub patch for one `event: step` frame from the status stream (null for a malformed frame). */
export function stepPatch(raw: unknown, nowMs: number): { seat: string; blurb: string; blurbAt: number } | null {
  if (!raw || typeof raw !== 'object') return null;
  const d = raw as Record<string, unknown>;
  if (typeof d.seat !== 'string' || !d.seat || typeof d.text !== 'string') return null;
  // the server's clock stamps the step; the page measures freshness on its own clock, from arrival
  return { seat: d.seat, blurb: d.text.slice(0, 160), blurbAt: nowMs };
}
